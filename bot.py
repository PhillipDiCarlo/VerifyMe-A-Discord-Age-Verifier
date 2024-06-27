import discord
from discord.ext import commands
from discord.ext.commands import BucketType
from flask import Flask, request, redirect, session, jsonify
import requests
from dotenv import load_dotenv
import os
import asyncio
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Boolean, TIMESTAMP
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import threading
import logging

# Load environment variables from .env file
load_dotenv()

# Retrieve environment variables
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ONFIDO_API_TOKEN = os.getenv('ONFIDO_API_TOKEN')
SECRET_KEY = os.getenv('SECRET_KEY')
REDIRECT_URI = os.getenv('REDIRECT_URI')
DATABASE_URL = os.getenv('DATABASE_URL')

# Database setup
engine = create_engine(DATABASE_URL)
metadata = MetaData()
Session = sessionmaker(bind=engine)
db_session = Session()

# Initialize the Discord bot with intents
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True  # Ensure message content intent is enabled

bot = commands.Bot(command_prefix="!", intents=intents)

# Flask app setup
app = Flask(__name__)
app.secret_key = SECRET_KEY

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

# Define tables using SQLAlchemy
users = Table(
    'users', metadata,
    Column('id', Integer, primary_key=True),
    Column('discord_id', String(30), nullable=False),
    Column('username', String(100)),
    Column('verification_status', Boolean, default=False),
    Column('last_verification_attempt', TIMESTAMP)
)

servers = Table(
    'servers', metadata,
    Column('id', Integer, primary_key=True),
    Column('server_id', String(30), nullable=False),
    Column('owner_id', String(30), nullable=False),
    Column('role_id', String(30), nullable=False),
    Column('tier', String(1), default='A'),
    Column('subscription_status', Boolean, default=False)
)

command_usage = Table(
    'command_usage', metadata,
    Column('id', Integer, primary_key=True),
    Column('server_id', String(30), nullable=False),
    Column('user_id', String(30), nullable=False),
    Column('command', String(50), nullable=False),
    Column('timestamp', TIMESTAMP, nullable=False)
)

# Create tables in the database
metadata.create_all(engine)

# Fetch server configuration from the database
def get_server_config(guild_id):
    server_config = db_session.query(servers).filter_by(server_id=str(guild_id)).first()
    return server_config

# Fetch user verification status from the database
def get_user_verification_status(discord_id):
    user = db_session.query(users).filter_by(discord_id=str(discord_id)).first()
    return user

# Update user verification status in the database
def update_user_verification_status(discord_id, status):
    user = db_session.query(users).filter_by(discord_id=str(discord_id)).first()
    if user:
        user.verification_status = status
        db_session.commit()

# Track user verification attempts in the database
def track_verification_attempt(discord_id):
    user = db_session.query(users).filter_by(discord_id=str(discord_id)).first()
    if user:
        user.last_verification_attempt = datetime.utcnow()
        db_session.commit()
    else:
        # If user does not exist, create one
        insert_stmt = users.insert().values(
            discord_id=str(discord_id),
            verification_status=False,
            last_verification_attempt=datetime.utcnow()
        )
        db_session.execute(insert_stmt)
        db_session.commit()

# Track command usage in the database for analytics
def track_command_usage(server_id, user_id, command):
    insert_stmt = command_usage.insert().values(
        server_id=str(server_id),
        user_id=str(user_id),
        command=command,
        timestamp=datetime.utcnow()
    )
    db_session.execute(insert_stmt)
    db_session.commit()

# Check if user is within the cooldown period
def is_user_in_cooldown(discord_id):
    user = db_session.query(users).filter_by(discord_id=str(discord_id)).first()
    if user and user.last_verification_attempt:
        cooldown_end = user.last_verification_attempt + timedelta(seconds=COOLDOWN_PERIOD)
        if datetime.utcnow() < cooldown_end:
            return True
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

# Configure logging
logging.basicConfig(level=logging.INFO)

# Add logging inside your commands
@bot.command()
@commands.cooldown(1, COOLDOWN_PERIOD, BucketType.user)

async def verify(ctx, role: discord.Role):
    logging.info(f"Received !verify command from user {ctx.author} in guild {ctx.guild}")
    guild_id = str(ctx.guild.id)
    member_count = ctx.guild.member_count

    required_tier = get_required_tier(member_count)
    server_config = get_server_config(guild_id)

    if not server_config:
        logging.warning(f"No active subscription for guild {guild_id}")
        await ctx.send("This server does not have an active subscription.")
        return

    subscribed_tier = server_config.tier

    if tier_requirements[subscribed_tier] < tier_requirements[required_tier]:
        logging.warning(f"Subscription tier {subscribed_tier} does not cover {member_count} members")
        await ctx.send(f"This server's subscription ({subscribed_tier}) does not cover {member_count} members. Please upgrade to {required_tier}.")
        return

    if is_user_in_cooldown(ctx.author.id):
        logging.info(f"User {ctx.author.id} is in cooldown period")
        await ctx.send(f"You are currently in a cooldown period. Please wait before attempting to verify again.")
        return

    user = get_user_verification_status(ctx.author.id)
    if user and user.verification_status:
        logging.info(f"User {ctx.author.id} is already verified")
        await assign_role(guild_id, ctx.author.id, role.id)
        await ctx.send("You are already verified. Role has been assigned.")
        return

    verification_url = generate_onfido_verification_url(guild_id, ctx.author.id, role.id)
    track_verification_attempt(ctx.author.id)
    track_command_usage(guild_id, ctx.author.id, "verify")
    logging.info(f"Generated verification URL for user {ctx.author.id}: {verification_url}")
    await ctx.send(f"This server has {member_count} members. Click the link below to verify your age: {verification_url}")

@bot.command()
@commands.cooldown(1, COOLDOWN_PERIOD, BucketType.user)
async def reverify(ctx, role: discord.Role):
    guild_id = str(ctx.guild.id)
    member_count = ctx.guild.member_count

    required_tier = get_required_tier(member_count)
    server_config = get_server_config(guild_id)

    if not server_config:
        await ctx.send("This server does not have an active subscription.")
        return

    subscribed_tier = server_config.tier

    if tier_requirements[subscribed_tier] < tier_requirements[required_tier]:
        await ctx.send(f"This server's subscription ({subscribed_tier}) does not cover {member_count} members. Please upgrade to {required_tier}.")
        return

    if is_user_in_cooldown(ctx.author.id):
        await ctx.send(f"You are currently in a cooldown period. Please wait before attempting to verify again.")
        return

    verification_url = generate_onfido_verification_url(guild_id, ctx.author.id, role.id)
    track_verification_attempt(ctx.author.id)
    track_command_usage(guild_id, ctx.author.id, "reverify")
    await ctx.send(f"This server has {member_count} members. Click the link below to verify your age: {verification_url}")

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

@bot.event
async def on_ready():
    logging.info(f'Bot is ready. Logged in as {bot.user}')


def generate_onfido_verification_url(guild_id, user_id, role_id):
    applicant_data = {
        "first_name": "User",
        "last_name": "Test",
        "redirect_uri": REDIRECT_URI,
        "applicant_id": f"{guild_id}-{user_id}-{role_id}"
    }
    response = requests.post("https://api.onfido.com/v3/applicants", json=applicant_data, headers={"Authorization": f"Token token={ONFIDO_API_TOKEN}"})
    applicant_id = response.json()["id"]

    check_data = {
        "applicant_id": applicant_id,
        "report_names": ["identity_enhanced"]
    }
    response = requests.post("https://api.onfido.com/v3/checks", json=check_data, headers={"Authorization": f"Token token={ONFIDO_API_TOKEN}"})
    check_id = response.json()["id"]

    return f"https://your_verification_page_url/start?check_id={check_id}&applicant_id={applicant_id}"

@app.route('/callback', methods=['POST'])
def callback():
    payload = request.json
    if payload['payload']['resource_type'] == 'check' and payload['payload']['action'] == 'completed':
        check_id = payload['payload']['object']['id']
        check_result = payload['payload']['object']['result']
        if check_result == 'clear':
            applicant_id = payload['payload']['object']['applicant_id']
            guild_id, user_id, role_id = applicant_id.split('-')
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(assign_role(guild_id, user_id, role_id))
            update_user_verification_status(user_id, True)
            return "Verification successful, role assigned!"
    return "Verification failed."

@app.route('/start_verification')
def start_verification():
    guild_id = request.args.get('guild_id')
    user_id = request.args.get('user_id')
    role_id = request.args.get('role_id')
    session['guild_id'] = guild_id
    session['user_id'] = user_id
    session['role_id'] = role_id

    authorization_url = (
        f'https://api.onfido.com/v3/applicants?client_id={ONFIDO_API_TOKEN}&redirect_uri={REDIRECT_URI}&scope=openid'
    )
    return redirect(authorization_url)

@app.route('/analytics')
def analytics():
    result = db_session.query(command_usage).all()
    analytics_data = [{"server_id": row.server_id, "user_id": row.user_id, "command": row.command, "timestamp": row.timestamp} for row in result]
    return jsonify(analytics_data)

def run_flask():
    app.run(port=5000)

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    asyncio.run(bot.start(DISCORD_BOT_TOKEN))
