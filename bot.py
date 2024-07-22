import os
import json
import asyncio
import threading
import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import discord
from discord import app_commands
from flask import Flask, jsonify
from dotenv import load_dotenv
import pika
import stripe
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
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
SECRET_KEY = os.getenv('SECRET_KEY')
REDIRECT_URI = os.getenv('REDIRECT_URI')
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
intents.message_content = True

class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.last_startup_time = None

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# Flask app setup
app = Flask(__name__)
app.secret_key = SECRET_KEY

# Stripe setup
stripe.api_key = STRIPE_SECRET_KEY

# Subscription tier requirements
tier_requirements = {
    "tier_A": 250,
    "tier_B": 500,
    "tier_C": 1000,
    "tier_D": 5000,
    "tier_E": float('inf')  # No upper limit
}

# Cooldown period (seconds)
COOLDOWN_PERIOD = 60  # 1 minute cooldown for demonstration purposes

# Define SQLAlchemy models
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String(30), nullable=False)
    # username = Column(String(100))
    verification_status = Column(Boolean, default=False)
    last_verification_attempt = Column(DateTime(timezone=True))

class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    server_id = Column(String(30), nullable=False, unique=True)
    owner_id = Column(String(30), nullable=False)
    role_id = Column(String(30), nullable=False)
    tier = Column(String(50), default='tier_A', nullable=False)
    subscription_status = Column(Boolean, default=False)

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

def get_required_tier(member_count):
    if member_count <= tier_requirements["tier_A"]:
        return "tier_A"
    elif member_count <= tier_requirements["tier_B"]:
        return "tier_B"
    elif member_count <= tier_requirements["tier_C"]:
        return "tier_C"
    elif member_count <= tier_requirements["tier_D"]:
        return "tier_D"
    else:
        return "tier_E"

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


def track_verification_attempt(discord_id):
    logger.info(f"Tracking verification attempt for user {discord_id}")
    try:
        with session_scope() as session:
            user = session.query(User).filter_by(discord_id=str(discord_id)).first()
            if user:
                logger.debug(f"Updating existing user {discord_id}")
                user.last_verification_attempt = datetime.now(timezone.utc)
            else:
                logger.debug(f"Creating new user record for {discord_id}")
                new_user = User(
                    discord_id=str(discord_id),
                    verification_status=False,
                    last_verification_attempt=datetime.now(timezone.utc)
                )
                session.add(new_user)
            
            logger.debug(f"Attempting to commit changes for user {discord_id}")
            commit_successful = [False]
            
            def commit_with_timeout():
                try:
                    session.commit()
                    commit_successful[0] = True
                except Exception as e:
                    logger.error(f"Error during commit for user {discord_id}: {str(e)}", exc_info=True)

            commit_thread = threading.Thread(target=commit_with_timeout)
            commit_thread.start()
            commit_thread.join(timeout=10)  # Wait for up to 10 seconds

            if commit_successful[0]:
                logger.debug(f"Successfully committed changes for user {discord_id}")
            else:
                logger.error(f"Commit operation timed out for user {discord_id}")
                raise TimeoutError("Database commit operation timed out")

    except Exception as e:
        logger.error(f"Error tracking verification attempt for user {discord_id}: {str(e)}", exc_info=True)
        raise

    logger.debug(f"Successfully tracked verification attempt for user {discord_id}")

def track_command_usage(server_id, user_id, command):
    with session_scope() as session:
        new_usage = CommandUsage(
            server_id=str(server_id),
            user_id=str(user_id),
            command=command,
            timestamp=datetime.now(timezone.utc)
        )
        session.add(new_usage)

def is_user_in_cooldown(discord_id):
    with session_scope() as session:
        user = session.query(User).filter_by(discord_id=str(discord_id)).first()
        if user and user.last_verification_attempt:
            cooldown_end = user.last_verification_attempt + timedelta(seconds=COOLDOWN_PERIOD)
            return datetime.now(timezone.utc) < cooldown_end
    return False

async def assign_role(guild_id, user_id, role_id):
    guild = bot.get_guild(int(guild_id))
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))
    if member and role:
        await member.add_roles(role)

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
        member_count = interaction.guild.member_count

        with session_scope() as session:
            user = session.query(User).filter_by(discord_id=str(interaction.user.id)).first()
            if user and user.last_verification_attempt:
                if user.last_verification_attempt < bot.last_startup_time:
                    logger.debug(f"Resetting cooldown for user {interaction.user.id}")
                    user.last_verification_attempt = None
                elif is_user_in_cooldown(user.last_verification_attempt):
                    await interaction.followup.send(f"You're in a cooldown period. Please wait before attempting to verify again.", ephemeral=True)
                    return

            required_tier = get_required_tier(member_count)
            server_config = session.query(Server).filter_by(server_id=guild_id).first()

            if not server_config or not server_config.role_id or not server_config.subscription_status:
                await send_error_response(interaction, server_config, guild_id)
                return

            verification_role = interaction.guild.get_role(int(server_config.role_id))
            if not verification_role:
                logger.warning(f"Verification role {server_config.role_id} not found in guild {guild_id}")
                await interaction.followup.send("The configured verification role no longer exists. Please ask an admin to set up the role again using `/set_role`.", ephemeral=True)
                return

            subscribed_tier = server_config.tier
            if not await validate_subscription(subscribed_tier, required_tier, member_count, interaction):
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

        # Move track_verification_attempt to a background task
        bot.loop.create_task(track_verification_attempt(interaction.user.id))
        
        # Move track_command_usage to a background task
        bot.loop.create_task(track_command_usage(guild_id, interaction.user.id, "verify"))

        logger.debug(f"About to send verification URL to user {interaction.user.id}")
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

async def validate_subscription(subscribed_tier, required_tier, member_count, interaction):
    if subscribed_tier not in tier_requirements:
        logger.warning(f"Invalid subscription tier {subscribed_tier}")
        await interaction.followup.send("Invalid verification tier configured for this server. Please ask an admin to update the subscription.", ephemeral=True)
        return False

    if tier_requirements[subscribed_tier] < tier_requirements[required_tier]:
        logger.warning(f"Subscription tier {subscribed_tier} does not cover {member_count} members")
        await interaction.followup.send(f"This server's verification subscription ({subscribed_tier}) does not cover {member_count} members. Please ask an admin to upgrade to {required_tier}.", ephemeral=True)
        return False
    return True


@bot.tree.command(name="set_role", description="Set the role for verified users")
@app_commands.describe(role="The role to assign to verified users")
async def set_role(interaction: discord.Interaction, role: discord.Role):
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
                subscription_status=True  # Assuming subscription is active for testing
            )
            session.add(new_server)
        else:
            server_config.role_id = str(role.id)

    await interaction.response.send_message(f"Verification role set to: {role.name}", ephemeral=True)

@bot.tree.command(name="set_subscription", description="Set the subscription tier for the server")
@app_commands.describe(tier="The subscription tier to set")
async def set_subscription(interaction: discord.Interaction, tier: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    with session_scope() as session:
        server_config = session.query(Server).filter_by(server_id=guild_id).first()

        if not server_config:
            await interaction.response.send_message("This server does not have an active subscription.", ephemeral=True)
            return

        if tier not in tier_requirements:
            await interaction.response.send_message(f"Invalid tier. Available tiers: {', '.join(tier_requirements.keys())}", ephemeral=True)
            return

        server_config.tier = tier

    await interaction.response.send_message(f"Subscription tier set to: {tier}", ephemeral=True)

@bot.tree.command(name="server_info", description="Display current server configuration for verification")
@app_commands.checks.has_permissions(administrator=True)
async def server_info(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    server_config = get_server_config(guild_id)

    if not server_config:
        await interaction.response.send_message("This server is not configured for verification.", ephemeral=True)
        return

    verification_role = interaction.guild.get_role(int(server_config.role_id)) if server_config.role_id else None
    
    embed = discord.Embed(title="Server Verification Configuration", color=discord.Color.blue())
    embed.add_field(name="Verification Role", value=verification_role.name if verification_role else "Not set", inline=False)
    embed.add_field(name="Subscription Tier", value=server_config.tier, inline=True)
    embed.add_field(name="Subscription Status", value="Active" if server_config.subscription_status else "Inactive", inline=True)
    embed.add_field(name="Member Count", value=str(interaction.guild.member_count), inline=True)
    embed.add_field(name="Required Tier", value=get_required_tier(interaction.guild.member_count), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="subscription_status", description="Show detailed information about the server's verification subscription")
@app_commands.checks.has_permissions(administrator=True)
async def subscription_status(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    server_config = get_server_config(guild_id)

    if not server_config:
        await interaction.response.send_message("This server is not configured for verification.", ephemeral=True)
        return

    current_tier = server_config.tier
    required_tier = get_required_tier(interaction.guild.member_count)

    embed = discord.Embed(title="Verification Subscription Status", color=discord.Color.green())
    embed.add_field(name="Current Tier", value=current_tier, inline=True)
    embed.add_field(name="Subscription Status", value="Active" if server_config.subscription_status else "Inactive", inline=True)
    embed.add_field(name="Member Limit", value=str(tier_requirements[current_tier]), inline=True)
    embed.add_field(name="Current Member Count", value=str(interaction.guild.member_count), inline=True)
    embed.add_field(name="Required Tier", value=required_tier, inline=True)

    if tier_requirements[current_tier] < interaction.guild.member_count:
        embed.add_field(name="⚠️ Warning", value="Current tier does not cover all members. Please upgrade.", inline=False)

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

# Flask routes
@app.route('/analytics')
def analytics():
    with session_scope() as session:
        result = session.query(CommandUsage).all()
        analytics_data = [{"server_id": row.server_id, "user_id": row.user_id, "command": row.command, "timestamp": row.timestamp} for row in result]
    return jsonify(analytics_data)

# Configure logging
logging.basicConfig(level=logger.info)

def run_flask():
    app.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    bot.run(DISCORD_BOT_TOKEN)