import hashlib
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
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet

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
    "tier_0": 0,
    "tier_1": 10,
    "tier_2": 25,
    "tier_3": 50,
    "tier_4": 75,
    "tier_5": 100,
    "tier_6": 150
}

# Cooldown period (seconds)
COOLDOWN_PERIOD = 60  # 1 minute cooldown for demonstration purposes

# Encryption/Decryption setup
DOB_KEY = os.getenv('DOB_KEY')
if not DOB_KEY:
    raise ValueError("DOB_KEY not found in environment variables")

cipher = Fernet(DOB_KEY)

# Hashing function for the DOB
def encrypt_dob(dob: datetime) -> str:
    """Encrypt the date of birth using Fernet symmetric encryption."""
    dob_str = dob.strftime('%Y-%m-%d')  # Convert DOB to string
    dob_bytes = dob_str.encode('utf-8')  # Convert to bytes
    encrypted_dob = cipher.encrypt(dob_bytes)  # Encrypt the DOB
    return encrypted_dob.decode('utf-8')  # Return encrypted DOB as string

def decrypt_dob(encrypted_dob: str) -> datetime:
    """Decrypt the encrypted DOB back to a datetime object."""
    dob_bytes = cipher.decrypt(encrypted_dob.encode('utf-8'))  # Decrypt the DOB
    dob_str = dob_bytes.decode('utf-8')  # Convert bytes back to string
    return datetime.strptime(dob_str, '%Y-%m-%d')  # Convert string to datetime object

# Modify User model to store the encrypted DOB
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String(30), nullable=False)
    verification_status = Column(Boolean, default=False)
    last_verification_attempt = Column(DateTime(timezone=True))
    dob = Column(String(255), nullable=True)  # Store encrypted DOB

    @staticmethod
    def get_current_time():
        return datetime.now(timezone.utc)

    def set_verification_attempt(self):
        self.last_verification_attempt = self.get_current_time()

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
    
    # Set the bot's bio/status
    bio_message = "Use `/get_verify_bot` to add this bot to your discord."
    await bot.change_presence(activity=discord.Game(name=bio_message))

    try:
        synced = await bot.tree.sync()
        logger.debug(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

@bot.tree.command(name="verifyme", description="Start the age verification process")
async def verify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)  # Acknowledge the interaction early
    logger.debug(f"Received /verify command from user {interaction.user.id} in guild {interaction.guild.id}")

    try:
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        with session_scope() as session:
            user = session.query(User).filter_by(discord_id=user_id).first()
            server_config = session.query(Server).filter_by(server_id=guild_id).first()

            if not server_config or not server_config.role_id or not server_config.subscription_status:
                await send_error_response(interaction, server_config, guild_id)
                return

            # Check if the server is on tier_0
            if server_config.tier == "tier_0":
                if user and user.verification_status:
                    # User is already verified, assign the role
                    await assign_role(guild_id, interaction.user.id, server_config.role_id)
                    await interaction.followup.send("You are already verified. Your role has been assigned.", ephemeral=True)
                    return
                else:
                    # New users cannot verify in tier_0
                    await interaction.followup.send("This tier does not support new user verification. Please contact the server owner or admin for assistance.", ephemeral=True)
                    return

            # Check if the user is already verified
            if user and user.verification_status:
                # Decrypt the DOB to verify the age requirement
                if user.dob:
                    decrypted_dob = decrypt_dob(user.dob)  # Decrypt the stored DOB
                    
                    # Make the decrypted_dob timezone-aware (UTC)
                    decrypted_dob = decrypted_dob.replace(tzinfo=timezone.utc)
                    
                    # Calculate the user's age
                    user_age = (datetime.now(timezone.utc) - decrypted_dob).days // 365
                    
                    if user_age < server_config.minimum_age:
                        await interaction.followup.send(f"You must be at least {server_config.minimum_age} years old to be added to the role.", ephemeral=True)
                        return

                # Assign role if age requirement is met
                await assign_role(guild_id, interaction.user.id, server_config.role_id)
                await interaction.followup.send("You are already verified. Your role has been assigned.", ephemeral=True)
                return

            # Check cooldown if user exists and is not verified
            if user and user.last_verification_attempt:
                logger.debug(f"User last verification attempt (UTC): {user.last_verification_attempt}")

                current_time_utc = datetime.now(timezone.utc)
                logger.debug(f"Current time (UTC): {current_time_utc}")

                if current_time_utc - user.last_verification_attempt < timedelta(seconds=COOLDOWN_PERIOD):
                    cooldown_end = user.last_verification_attempt + timedelta(seconds=COOLDOWN_PERIOD)
                    logger.debug(f"User {interaction.user.id} is in cooldown period until: {cooldown_end}")
                    await interaction.followup.send(f"You're in a cooldown period. Please wait before attempting to verify again.", ephemeral=True)
                    return

            # Check if there are available verifications for the server
            if server_config.verifications_count <= 0:
                await interaction.followup.send("This server has reached its monthly verification limit. Please contact an admin to upgrade the plan or wait until next month.", ephemeral=True)
                return

            # Proceed with verification if no cooldown or new user
            logger.debug(f"Generating Stripe verification URL for user {interaction.user.id}")
            verification_url = await generate_stripe_verification_url(guild_id, interaction.user.id, server_config.role_id, str(interaction.channel.id))

            if not verification_url:
                logger.error(f"Failed to generate Stripe verification URL for user {interaction.user.id}")
                await interaction.followup.send("Failed to initiate the verification process. Please try again later or contact support.", ephemeral=True)
                return

            logger.debug(f"Successfully generated Stripe verification URL for user {interaction.user.id}")

            # Track verification attempt for cooldown purposes
            if not user:
                user = User(discord_id=user_id, verification_status=False)
                session.add(user)
            user.set_verification_attempt()
            session.commit()

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

def is_user_in_cooldown(last_verification_attempt):
    if last_verification_attempt:
        cooldown_end = last_verification_attempt + timedelta(seconds=COOLDOWN_PERIOD)
        current_time_utc = datetime.now(timezone.utc)
        logger.debug(f"Current time: {current_time_utc}, Cooldown end time: {cooldown_end}")
        return current_time_utc < cooldown_end
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

@bot.tree.command(name="get_verify_bot", description="Get the link to add this bot to your server")
async def get_verify_bot(interaction: discord.Interaction):
    message = (
        "Click the link to add this bot to your server: "
        "[Age Verification Solution](https://esattotech.com/age-verification-solution/)"
    )
    await interaction.response.send_message(message, ephemeral=True)

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
        tier = server_config.tier
        max_verifications = tier_requirements.get(tier, "Tier not set")  # Provide a default value if tier is None

        embed = discord.Embed(title="Server Verification Configuration", color=discord.Color.blue())
        embed.add_field(name="Verification Role", value=verification_role.name if verification_role else "Not set", inline=False)
        embed.add_field(name="Server's Minimum Age", value=server_config.minimum_age, inline=False)
        embed.add_field(name="Subscription Tier", value=tier if tier else "Not set", inline=True)
        embed.add_field(name="Subscription Status", value="Active" if server_config.subscription_status else "Inactive", inline=True)
        embed.add_field(name="Verifications Remaining", value=str(server_config.verifications_count), inline=True)
        embed.add_field(name="Max Verifications/Month", value=str(max_verifications), inline=True)

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
