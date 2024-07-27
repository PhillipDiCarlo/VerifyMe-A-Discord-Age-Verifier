import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import discord
from discord import app_commands
from dotenv import load_dotenv
import pika
import stripe
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Load environment variables
load_dotenv()

# Set up logging
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Retrieve environment variables
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

# RabbitMQ Configuration
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))
RABBITMQ_USERNAME = os.getenv('RABBITMQ_USERNAME')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD')
RABBITMQ_VHOST = os.getenv('RABBITMQ_VHOST', '/')
RABBITMQ_QUEUE_NAME = os.getenv('RABBITMQ_QUEUE_NAME', 'verification_results')

# Ensure all required environment variables are set
required_env_vars = ['DISCORD_BOT_TOKEN', 'STRIPE_SECRET_KEY', 'DATABASE_URL', 
                     'RABBITMQ_HOST', 'RABBITMQ_USERNAME', 'RABBITMQ_PASSWORD']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Database setup
engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)

# Initialize the Discord bot with intents
intents = discord.Intents.default()
intents.members = True  # Enable the members intent

class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.last_startup_time = None

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# Stripe setup
stripe.api_key = STRIPE_SECRET_KEY

# Subscription tier requirements
tier_requirements = {
    "tier_1": 10,
    "tier_2": 25,
    "tier_3": 50,
    "tier_4": 75,
    "tier_5": 100,
    "tier_6": 150
}

# Cooldown period (seconds)
COOLDOWN_PERIOD = 60  # 1 minute cooldown for demonstration purposes

# Define SQLAlchemy models
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String(30), nullable=False)
    verification_status = Column(Boolean, default=False)
    last_verification_attempt = Column(DateTime(timezone=True))

class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    server_id = Column(String, unique=True, nullable=False)
    owner_id = Column(String, nullable=False)
    role_id = Column(String, nullable=True)
    tier = Column(String, nullable=False)
    subscription_status = Column(Boolean, default=False)
    verifications_count = Column(Integer, default=0)
    subscription_start_date = Column(DateTime, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    minimum_age = Column(Integer, nullable=False, default=18)


class CommandUsage(Base):
    __tablename__ = 'command_usage'
    id = Column(Integer, primary_key=True)
    server_id = Column(String(30), nullable=False)
    user_id = Column(String(30), nullable=False)
    command = Column(String(50), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)

# Create tables
Base.metadata.create_all(engine)

# RabbitMQ setup
credentials = pika.PlainCredentials(RABBITMQ_USERNAME, RABBITMQ_PASSWORD)
parameters = pika.ConnectionParameters(
    host=RABBITMQ_HOST,
    port=RABBITMQ_PORT,
    virtual_host=RABBITMQ_VHOST,
    credentials=credentials
)

def get_rabbitmq_channel():
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE_NAME, durable=True)
    return channel

@contextmanager
def session_scope():
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

def get_server_config(guild_id):
    with session_scope() as session:
        return session.query(Server).filter_by(server_id=str(guild_id)).first()

def get_user_verification_status(discord_id):
    with session_scope() as session:
        return session.query(User).filter_by(discord_id=str(discord_id)).first()

async def update_user_verification_status(discord_id, status):
    def db_update():
        with session_scope() as session:
            user = session.query(User).filter_by(discord_id=str(discord_id)).first()
            if user:
                user.verification_status = status
    await main_loop.run_in_executor(None, db_update)

async def decrement_verifications_count(server_id):
    def db_update():
        with session_scope() as session:
            server = session.query(Server).filter_by(server_id=str(server_id)).first()
            if server and server.verifications_count > 0:
                server.verifications_count -= 1
    await main_loop.run_in_executor(None, db_update)

# async def reset_verifications_count():
#     def db_update():
#         with session_scope() as session:
#             servers = session.query(Server).all()
#             for server in servers:
#                 server.verifications_count = 0
#     await main_loop.run_in_executor(None, db_update)

async def assign_role(guild_id, user_id, role_id):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        logger.error(f"Guild {guild_id} not found")
        return
    
    logger.debug(f"Attempting to find member {user_id} in guild {guild_id}")
    member = guild.get_member(int(user_id))
    if not member:
        logger.error(f"Member {user_id} not found in guild {guild_id}")
        return

    logger.debug(f"Found member {user_id} in guild {guild_id}, attempting to find role {role_id}")
    role = guild.get_role(int(role_id))
    if not role:
        logger.error(f"Role {role_id} not found in guild {guild_id}")
        return

    logger.debug(f"Attempting to assign role {role.name} to user {member.name}")

    try:
        await member.add_roles(role)
        logger.info(f"Successfully assigned role {role.name} to user {member.name}")
    except discord.Forbidden:
        logger.error(f"Bot does not have permission to assign role {role.name} in guild {guild_id}")
    except discord.HTTPException as e:
        logger.error(f"Failed to assign role {role.name} to user {member.name}: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error assigning role {role.name} to user {member.name}: {str(e)}")

async def generate_stripe_verification_url(guild_id, user_id, role_id, channel_id):
    try:
        logger.debug(f"Creating Stripe verification session for user {user_id}")
        verification_session = stripe.identity.VerificationSession.create(
            type='document',
            metadata={
                'guild_id': str(guild_id),
                'user_id': str(user_id),
                'role_id': str(role_id),
                'channel_id': str(channel_id)
            },
            options={
                'document': {
                    'require_id_number': False,
                    'require_live_capture': True,
                    'require_matching_selfie': True
                }
            }
        )
        logger.debug(f"Successfully created Stripe verification session for user {user_id}")
        return verification_session.url
    except stripe.error.StripeError as e:
        logger.error(f"Stripe API error for user {user_id}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in generate_stripe_verification_url for user {user_id}: {str(e)}", exc_info=True)
        return None

async def process_verification_result(message):
    data = json.loads(message)
    logger.debug(f"Received message from RabbitMQ: {data}")
    if data['type'] == 'verification_verified':
        guild_id = data['guild_id']
        user_id = data['user_id']
        role_id = data['role_id']
        await assign_role(guild_id, user_id, role_id)
        await update_user_verification_status(user_id, True)
    elif data['type'] == 'verification_canceled':
        guild_id = data['guild_id']
        user_id = data['user_id']
        channel_id = data['channel_id']
        channel = bot.get_channel(int(channel_id))
        if channel:
            await channel.send(f"Verification canceled for user <@{user_id}>")

# Global variable to store the event loop
main_loop = None

async def consume_queue():
    global main_loop
    main_loop = asyncio.get_running_loop()

    def sync_callback(ch, method, properties, body):
        asyncio.run_coroutine_threadsafe(process_verification_result(body), main_loop)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    connection = await main_loop.run_in_executor(None, pika.BlockingConnection, parameters)
    channel = await main_loop.run_in_executor(None, connection.channel)
    
    await main_loop.run_in_executor(
        None, lambda: channel.queue_declare(queue=RABBITMQ_QUEUE_NAME, durable=True)
    )

    await main_loop.run_in_executor(
        None, lambda: channel.basic_consume(queue=RABBITMQ_QUEUE_NAME, on_message_callback=sync_callback)
    )

    print(' [*] Waiting for messages. To exit press CTRL+C')
    
    # Run start_consuming in a separate thread
    await main_loop.run_in_executor(None, channel.start_consuming)

@bot.event
async def on_ready():
    logger.info(f'Bot is ready. Logged in as {bot.user}')
    bot.last_startup_time = datetime.now(timezone.utc)
    bot.loop.create_task(consume_queue())
    # bot.loop.create_task(reset_verifications_count())

    try:
        synced = await bot.tree.sync()
        logger.debug(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

@bot.tree.command(name="verify", description="Start the age verification process")
async def verify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    logger.debug(f"Received /verify command from user {interaction.user.id} in guild {interaction.guild.id}")

    try:
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        with session_scope() as session:
            user = session.query(User).filter_by(discord_id=user_id).first()
            server_config = session.query(Server).filter_by(server_id=guild_id).first()

            if user and user.last_verification_attempt:
                if user.last_verification_attempt < bot.last_startup_time:
                    logger.debug(f"Resetting cooldown for user {interaction.user.id}")
                    user.last_verification_attempt = None
                elif is_user_in_cooldown(user.last_attempt):
                    await interaction.followup.send(f"You're in a cooldown period. Please wait before attempting to verify again.", ephemeral=True)
                    return

            if not server_config or not server_config.role_id or not server_config.subscription_status:
                await send_error_response(interaction, server_config, guild_id)
                return

            # Check if the user meets the minimum age requirement
            if user and user.dob:
                user_age = (datetime.now(timezone.utc) - user.dob).days // 365
                if user_age < server_config.minimum_age:
                    await interaction.followup.send(f"You must be at least {server_config.minimum_age} years old to be added to the role.", ephemeral=True)
                    return

            if server_config.verifications_count <= 0 and (not user or not user.verification_status):
                await interaction.followup.send("This server has reached its monthly verification limit. Please contact an admin to upgrade the plan or wait until next month.", ephemeral=True)
                return

            verification_role = interaction.guild.get_role(int(server_config.role_id))
            if not verification_role:
                logger.warning(f"Verification role {server_config.role_id} not found in guild {guild_id}")
                await interaction.followup.send("The configured verification role no longer exists. Please ask an admin to set up the role again using `/set_role`.", ephemeral=True)
                return

            if user and user.verification_status:
                await assign_role(guild_id, interaction.user.id, server_config.role_id)
                await interaction.followup.send("You are already verified. Your role has been assigned.", ephemeral=True)
                return

            logger.debug(f"Generating Stripe verification URL for user {interaction.user.id}")
            verification_url = await generate_stripe_verification_url(guild_id, interaction.user.id, server_config.role_id, str(interaction.channel.id))

        if not verification_url:
            logger.error(f"Failed to generate Stripe verification URL for user {interaction.user.id}")
            await interaction.followup.send("Failed to initiate the verification process. Please try again later or contact support.", ephemeral=True)
            return

        logger.debug(f"Successfully generated Stripe verification URL for user {interaction.user.id}")

        # Decrement the verifications count if the user is new or not verified
        if not user or not user.verification_status:
            await decrement_verifications_count(guild_id)
        bot.loop.create_task(track_verification_attempt(interaction.user.id))
        bot.loop.create_task(track_command_usage(guild_id, interaction.user.id, "verify"))

        await interaction.followup.send(f"Click the link below to verify your age. This link is private and should not be shared:\n\n{verification_url}", ephemeral=True)
        logger.debug(f"Sent verification URL to user {interaction.user.id}")

    except Exception as e:
        logger.error(f"Unexpected error in verify command: {str(e)}", exc_info=True)
        await interaction.followup.send("An unexpected error occurred. Please try again later or contact support.", ephemeral=True)

    logger.debug(f"Verify command completed for user {interaction.user.id}")

async def track_verification_attempt(discord_id):
    logger.debug(f"Tracking verification attempt for user {discord_id}")
    try:
        with session_scope() as session:
            user = session.query(User).filter_by(discord_id=str(discord_id)).first()
            if user:
                user.last_verification_attempt = datetime.now(timezone.utc)
            else:
                new_user = User(
                    discord_id=str(discord_id),
                    verification_status=False,
                    last_verification_attempt=datetime.now(timezone.utc)
                )
                session.add(new_user)
            session.commit()
        logger.info(f"Successfully tracked verification attempt for user {discord_id}")
    except Exception as e:
        logger.error(f"Error tracking verification attempt for user {discord_id}: {str(e)}", exc_info=True)

async def track_command_usage(server_id, user_id, command):
    try:
        with session_scope() as session:
            new_usage = CommandUsage(
                server_id=str(server_id),
                user_id=str(user_id),
                command=command,
                timestamp=datetime.now(timezone.utc)
            )
            session.add(new_usage)
            session.commit()
        logger.debug(f"Successfully tracked command usage for user {user_id}")
    except Exception as e:
        logger.error(f"Error tracking command usage for user {user_id}: {str(e)}", exc_info=True)

def is_user_in_cooldown(last_attempt):
    if last_attempt:
        cooldown_end = last_attempt + timedelta(seconds=COOLDOWN_PERIOD)
        return datetime.now(timezone.utc) < cooldown_end
    return False

async def send_error_response(interaction, server_config, guild_id):
    if not server_config:
        await interaction.followup.send("This server is not configured for verification. Please ask an admin to set up the server using `/set_role`.", ephemeral=True)
    elif not server_config.role_id:
        logger.warning(f"No verification role set for guild {guild_id}")
        await interaction.followup.send("The verification role has not been set for this server. Please ask an admin to set up the role using `/set_role`.", ephemeral=True)
    elif not server_config.subscription_status:
        logger.warning(f"No active subscription for guild {guild_id}")
        await interaction.followup.send("This server does not have an active verification subscription.", ephemeral=True)

@bot.tree.command(name="set_role", description="Set the role and minimum age for verified users")
@app_commands.describe(role="The role to assign to verified users", minimum_age="The minimum age required for verification")
async def set_role(interaction: discord.Interaction, role: discord.Role, minimum_age: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    with session_scope() as session:
        server_config = session.query(Server).filter_by(server_id=guild_id).first()

        if not server_config:
            new_server = Server(
                server_id=guild_id,
                owner_id=str(interaction.guild.owner_id),
                role_id=str(role.id),
                minimum_age=minimum_age,
                subscription_status=False
            )
            session.add(new_server)
        else:
            server_config.role_id = str(role.id)
            server_config.minimum_age = minimum_age

    await interaction.response.send_message(f"Verification role set to: {role.name} with minimum age {minimum_age}", ephemeral=True)


@bot.tree.command(name="get_subscription", description="Get the subscription link for the server")
async def get_subscription(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    await interaction.response.send_message("To activate a subscription, please visit: https://esattotech.com/pricing/", ephemeral=True)

@bot.tree.command(name="server_info", description="Display current server configuration for verification")
@app_commands.checks.has_permissions(administrator=True)
async def server_info(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    
    with session_scope() as session:
        server_config = session.query(Server).filter_by(server_id=guild_id).first()

        if not server_config:
            await interaction.response.send_message("This server is not configured for verification. Please type /set_role to configure.", ephemeral=True)
            return

        verification_role = interaction.guild.get_role(int(server_config.role_id)) if server_config.role_id else None

        embed = discord.Embed(title="Server Verification Configuration", color=discord.Color.blue())
        embed.add_field(name="Verification Role", value=verification_role.name if verification_role else "Not set", inline=False)
        embed.add_field(name="Server's Minimum Age", value=server_config.minimum_age, inline=False)
        embed.add_field(name="Subscription Tier", value=server_config.tier, inline=True)
        embed.add_field(name="Subscription Status", value="Active" if server_config.subscription_status else "Inactive", inline=True)
        embed.add_field(name="Verifications Remaining", value=str(server_config.verifications_count), inline=True)
        embed.add_field(name="Max Verifications/Month", value=str(tier_requirements[server_config.tier]), inline=True)

        if server_config.verifications_count == 0:
            embed.add_field(name="Warning", value="You have reached the maximum number of verifications for this month.", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="subscription_status", description="Show detailed information about the server's verification subscription")
@app_commands.checks.has_permissions(administrator=True)
async def subscription_status(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)

    with session_scope() as session:
        server_config = session.query(Server).filter_by(server_id=guild_id).first()

        if not server_config:
            await interaction.response.send_message("This server is not configured for verification.", ephemeral=True)
            return

        current_tier = server_config.tier

        embed = discord.Embed(title="Verification Subscription Status", color=discord.Color.green())
        embed.add_field(name="Current Tier", value=current_tier, inline=True)
        embed.add_field(name="Subscription Status", value="Active" if server_config.subscription_status else "Inactive", inline=True)
        embed.add_field(name="Verifications Count", value=str(server_config.verifications_count), inline=True)
        embed.add_field(name="Max Verifications/Month", value=str(tier_requirements[current_tier]), inline=True)

        if server_config.verifications_count <=0:
            embed.add_field(name="⚠️ Warning", value="Current tier has reached the maximum number of verifications. Please upgrade.", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="verification_logs", description="View recent verification actions")
@app_commands.checks.has_permissions(administrator=True)
async def verification_logs(interaction: discord.Interaction, limit: int = 10):
    guild_id = str(interaction.guild.id)
    
    with session_scope() as session:
        logs = session.query(CommandUsage).filter_by(server_id=guild_id).order_by(CommandUsage.timestamp.desc()).limit(limit).all()

    if not logs:
        await interaction.response.send_message("No verification logs found for this server.", ephemeral=True)
        return

    embed = discord.Embed(title="Recent Verification Actions", color=discord.Color.blue())
    
    for log in logs:
        user = interaction.guild.get_member(int(log.user_id))
        user_name = user.name if user else f"Unknown User ({log.user_id})"
        embed.add_field(name=f"{log.command} - {log.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                        value=f"User: {user_name}",
                        inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)

if __name__ == '__main__':
    bot.run(DISCORD_BOT_TOKEN)
