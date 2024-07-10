import discord
from discord import app_commands
from flask import Flask, request, redirect, session, jsonify
import os
from os import environ
import asyncio
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta, timezone
import threading
import logging
from dotenv import load_dotenv
import stripe
from contextlib import contextmanager

# Load environment variables from .env file
load_dotenv()

# Retrieve environment variables
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
SECRET_KEY = os.getenv('SECRET_KEY')
REDIRECT_URI = os.getenv('REDIRECT_URI')
DATABASE_URL = os.getenv('DATABASE_URL')

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
    username = Column(String(100))
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

class VerificationLog(Base):
    __tablename__ = 'verification_logs'
    id = Column(Integer, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    user_id = Column(String(30), nullable=False)
    action = Column(String(50), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)

# Create tables
Base.metadata.create_all(engine)

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

def update_user_verification_status(discord_id, status):
    with session_scope() as session:
        user = session.query(User).filter_by(discord_id=str(discord_id)).first()
        if user:
            user.verification_status = status

def track_verification_attempt(discord_id):
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
        logging.info(f"Checking cooldown for user {discord_id}")
        
        if user and user.last_verification_attempt:
            logging.info(f"Last verification attempt: {user.last_verification_attempt}")
            
            cooldown_end = user.last_verification_attempt + timedelta(seconds=COOLDOWN_PERIOD)
            current_time = datetime.now(timezone.utc)
            
            logging.info(f"Cooldown end: {cooldown_end}")
            logging.info(f"Current time: {current_time}")
            
            if current_time < cooldown_end:
                logging.info("User is in cooldown")
                return True
            else:
                logging.info("User is not in cooldown")
                return False
        else:
            logging.info("No previous verification attempt found")
            return False

# Check if server meets the tier requirement
def check_tier_requirements(guild):
    server_config = get_server_config(guild.id)
    if server_config:
        member_count = guild.member_count
        tier_limit = tier_requirements[server_config.tier]
        if member_count > tier_limit:
            return False, tier_limit
    return True, None

async def assign_role(guild_id, user_id, role_id):
    guild = bot.get_guild(int(guild_id))
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))
    if member and role:
        await member.add_roles(role)

def log_verification_action(guild_id, user_id, action):
    with session_scope() as session:
        new_log = VerificationLog(
            guild_id=str(guild_id),
            user_id=str(user_id),
            action=action,
            timestamp=datetime.now(timezone.utc)
        )
        session.add(new_log)

# Configure logging
logging.basicConfig(level=logging.INFO)

async def generate_stripe_verification_url(guild_id, user_id, role_id, channel_id):
    try:
        verification_session = stripe.identity.VerificationSession.create(
            type='document',
            options={
                'document': {
                    'require_id_number': False,
                    'require_live_capture': True,
                    'require_matching_selfie': True,
                },
            },
            metadata={
                'guild_id': str(guild_id),
                'user_id': str(user_id),
                'role_id': str(role_id),
                'channel_id': str(channel_id)
            }
        )
        logging.info(f"Created verification session with metadata: {verification_session.metadata}")
        return verification_session.url
    
    except stripe.error.StripeError as e:
        logging.error(f"Stripe API error: {str(e)}")
        return None

@bot.tree.command(name="verify", description="Start the age verification process")
async def verify(interaction: discord.Interaction):
    try:
        logging.info(f"Received /verify command from user {interaction.user.id} in guild {interaction.guild.id}")
        
        guild_id = str(interaction.guild.id)
        member_count = interaction.guild.member_count

        with session_scope() as session:
            # Reset cooldown if it's the first attempt after bot startup
            if not bot.last_startup_time:
                bot.last_startup_time = datetime.now(timezone.utc)
            
            user = session.query(User).filter_by(discord_id=str(interaction.user.id)).first()
            if user and user.last_verification_attempt:
                if user.last_verification_attempt < bot.last_startup_time:
                    logging.info(f"Resetting cooldown for user {interaction.user.id}")
                    user.last_verification_attempt = None

            required_tier = get_required_tier(member_count)
            server_config = session.query(Server).filter_by(server_id=guild_id).first()

            if not server_config:
                await interaction.response.send_message("This server is not configured for verification. Please ask an admin to set up the server using `/set_role`.", ephemeral=True)
                return

            if not server_config.role_id:
                logging.warning(f"No verification role set for guild {guild_id}")
                await interaction.response.send_message("The verification role has not been set for this server. Please ask an admin to set up the role using `/set_role`.", ephemeral=True)
                return

            # Check if the role still exists in the guild
            verification_role = interaction.guild.get_role(int(server_config.role_id))
            if not verification_role:
                logging.warning(f"Verification role {server_config.role_id} not found in guild {guild_id}")
                await interaction.response.send_message("The configured verification role no longer exists. Please ask an admin to set up the role again using `/set_role`.", ephemeral=True)
                return

            if not server_config.subscription_status:
                logging.warning(f"No active subscription for guild {guild_id}")
                await interaction.response.send_message("This server does not have an active verification subscription.", ephemeral=True)
                return
            
            subscribed_tier = server_config.tier

            if subscribed_tier not in tier_requirements:
                logging.warning(f"Invalid subscription tier {subscribed_tier} for guild {guild_id}")
                await interaction.response.send_message("Invalid verification tier configured for this server. Please ask an admin to update the subscription.", ephemeral=True)
                return

            if tier_requirements[subscribed_tier] < tier_requirements[required_tier]:
                logging.warning(f"Subscription tier {subscribed_tier} does not cover {member_count} members")
                await interaction.response.send_message(f"This server's verification subscription ({subscribed_tier}) does not cover {member_count} members. Please ask an admin to upgrade to {required_tier}.", ephemeral=True)
                return

            if is_user_in_cooldown(interaction.user.id):
                logging.info(f"User {interaction.user.id} is in cooldown period")
                await interaction.response.send_message(f"You're in a cooldown period. Please wait before attempting to verify again.", ephemeral=True)
                return

            if user and user.verification_status:
                logging.info(f"User {interaction.user.id} is already verified")
                await assign_role(guild_id, interaction.user.id, server_config.role_id)
                await interaction.response.send_message("You are already verified. Your role has been assigned.", ephemeral=True)
                return
            
            log_verification_action(interaction.guild.id, interaction.user.id, "Started Verification")
            verification_url = await generate_stripe_verification_url(
                guild_id,
                interaction.user.id,
                server_config.role_id,
                str(interaction.channel.id))
            
            if not verification_url:
                logging.error(f"Failed to generate verification URL for user {interaction.user.id} in guild {guild_id}")
                await interaction.response.send_message("Failed to initiate the verification process. Please try again later or contact support.", ephemeral=True)
                return

            track_verification_attempt(interaction.user.id)
            track_command_usage(guild_id, interaction.user.id, "verify")
            logging.info(f"Generated verification URL for user {interaction.user.id} in guild {guild_id}: {verification_url}")
            await interaction.response.send_message(f"Click the link below to verify your age. This link is private and should not be shared:\n\n{verification_url}", ephemeral=True)

    except Exception as e:
        logging.error(f"Unexpected error in verify command: {str(e)}")
        await interaction.response.send_message("An unexpected error occurred. Please try again later or contact support.", ephemeral=True)
        

@bot.tree.command(name="reverify", description="Start the reverification process")
async def reverify(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    member_count = interaction.guild.member_count

    required_tier = get_required_tier(member_count)
    server_config = get_server_config(guild_id)

    if not server_config:
        await interaction.response.send_message("No configuration found for this server. Please ask an admin to set up the server using `/set_role`.", ephemeral=True)
        return

    if not server_config.subscription_status:
        await interaction.response.send_message("This server does not have an active subscription.", ephemeral=True)
        return

    subscribed_tier = server_config.tier

    if subscribed_tier not in tier_requirements:
        await interaction.response.send_message("Invalid subscription tier configured for this server. Please ask an admin to correctly subscribe to the appropriate tier.", ephemeral=True)
        return

    if tier_requirements[subscribed_tier] < tier_requirements[required_tier]:
        await interaction.response.send_message(f"This server's subscription ({subscribed_tier}) does not cover {member_count} members. Please upgrade to {required_tier}.", ephemeral=True)
        return

    if is_user_in_cooldown(interaction.user.id):
        await interaction.response.send_message(f"You are currently in a cooldown period. Please wait before attempting to verify again.", ephemeral=True)
        return

    log_verification_action(interaction.guild.id, interaction.user.id, "Started Reverification")

    verification_url = await generate_stripe_verification_url(
        guild_id,
        interaction.user.id,
        server_config.role_id,
        str(interaction.channel.id))

    if not verification_url:
        await interaction.response.send_message("Failed to initiate verification process. Please try again later or contact support.", ephemeral=True)
        return
    
    track_verification_attempt(interaction.user.id)
    track_command_usage(guild_id, interaction.user.id, "reverify")
    await interaction.response.send_message(f"Click the link below to verify your age: {verification_url}", ephemeral=True)

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
            # Create new server config
            new_server = Server(
                server_id=guild_id,
                owner_id=str(interaction.guild.owner_id),
                role_id=str(role.id),
                subscription_status=True  # Assuming subscription is active for testing
            )
            session.add(new_server)
        else:
            # Update existing server config
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
        logs = session.query(VerificationLog).filter_by(guild_id=guild_id).order_by(VerificationLog.timestamp.desc()).limit(limit).all()

    if not logs:
        await interaction.response.send_message("No verification logs found for this server.", ephemeral=True)
        return

    embed = discord.Embed(title="Recent Verification Actions", color=discord.Color.blue())
    
    for log in logs:
        user = interaction.guild.get_member(int(log.user_id))
        user_name = user.name if user else f"Unknown User ({log.user_id})"
        embed.add_field(name=f"{log.action} - {log.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                        value=f"User: {user_name}",
                        inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)
    
@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)

@bot.event
async def on_ready():
    logging.info(f'Bot is ready. Logged in as {bot.user}')
    bot.last_startup_time = datetime.now(timezone.utc)
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")

@app.route('/stripe_webhook', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logging.error(f"Invalid payload: {str(e)}")
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        logging.error(f"Invalid signature: {str(e)}")
        return 'Invalid signature', 400

    logging.info(f"Received event type: {event['type']}")
    logging.info(f"Event data: {event['data']}")

    if event['type'] == 'identity.verification_session.verified':
        handle_verification_verified(event['data']['object'])
    elif event['type'] == 'identity.verification_session.canceled':
        handle_verification_canceled(event['data']['object'])
    else:
        logging.info(f"Unhandled event type: {event['type']}")

    return '', 200

def handle_verification_verified(session):
    metadata = session.get('metadata', {})
    guild_id = metadata.get('guild_id')
    user_id = metadata.get('user_id')
    role_id = metadata.get('role_id')
    
    if guild_id and user_id and role_id:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(assign_role(guild_id, user_id, role_id))
        update_user_verification_status(user_id, True)
        logging.info(f"Verification successful, role assigned for user {user_id} in guild {guild_id}")
    else:
        logging.warning(f"Missing metadata in verified session. Metadata: {metadata}")

def handle_verification_canceled(session):
    metadata = session.get('metadata', {})
    guild_id = metadata.get('guild_id')
    user_id = metadata.get('user_id')
    channel_id = metadata.get('channel_id')
    
    if guild_id and user_id and channel_id:
        message = f"Verification canceled for user <@{user_id}>"
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_discord_message(channel_id, message))
        logging.info(f"Cancellation message sent for user {user_id} in guild {guild_id}")
    else:
        logging.warning(f"Missing metadata in canceled session. Metadata: {metadata}")

async def send_discord_message(channel_id, message):
    channel = bot.get_channel(int(channel_id))
    if channel:
        await channel.send(message)

@app.route('/analytics')
def analytics():
    with session_scope() as session:
        result = session.query(CommandUsage).all()
        analytics_data = [{"server_id": row.server_id, "user_id": row.user_id, "command": row.command, "timestamp": row.timestamp} for row in result]
    return jsonify(analytics_data)

def run_flask():
    app.run(host='0.0.0.0', port=5431, ssl_context='adhoc')

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    bot.run(DISCORD_BOT_TOKEN)