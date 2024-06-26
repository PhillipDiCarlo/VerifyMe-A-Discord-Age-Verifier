import discord
from discord.ext import commands
from flask import Flask, request, redirect, session
import requests
from dotenv import load_dotenv
import os
import asyncio

# Load environment variables from .env file
load_dotenv()

# Retrieve environment variables
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ID_ME_CLIENT_ID = os.getenv('ID_ME_CLIENT_ID')
ID_ME_CLIENT_SECRET = os.getenv('ID_ME_CLIENT_SECRET')
SECRET_KEY = os.getenv('SECRET_KEY')
REDIRECT_URI = os.getenv('REDIRECT_URI')

# Initialize the Discord bot
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Flask app for handling OAuth flow
app = Flask(__name__)
app.secret_key = SECRET_KEY

# In-memory storage for active subscriptions (for demonstration)
active_subscriptions = {
    "your_guild_id": "tier_A",
    # Add other guild IDs and their corresponding subscription tiers here
}

# Subscription tier requirements
tier_requirements = {
    "tier_A": 250,
    "tier_B": 500,
    "tier_C": 1000,
    "tier_D": 5000,
    "tier_E": float('inf')  # No upper limit
}

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

@app.route('/callback')
def callback():
    code = request.args.get('code')
    guild_id = session.get('guild_id')
    user_id = session.get('user_id')
    role_id = session.get('role_id')

    token_url = 'https://api.id.me/oauth/token'
    token_data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'client_id': ID_ME_CLIENT_ID,
        'client_secret': ID_ME_CLIENT_SECRET,
    }
    token_response = requests.post(token_url, data=token_data)
    tokens = token_response.json()

    user_info_url = 'https://api.id.me/api/public/v3/attributes'
    headers = {'Authorization': f'Bearer {tokens["access_token"]}'}
    user_info_response = requests.get(user_info_url, headers=headers)
    user_info = user_info_response.json()

    # Check if the user is over 18
    if user_info.get("age_verified"):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(assign_role(guild_id, user_id, role_id))
        return "Verification successful, role assigned!"
    return "Verification failed."

async def assign_role(guild_id, user_id, role_id):
    guild = bot.get_guild(int(guild_id))
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))
    if member and role:
        await member.add_roles(role)

@bot.command()
async def verify(ctx, role: discord.Role):
    guild_id = str(ctx.guild.id)
    member_count = ctx.guild.member_count

    required_tier = get_required_tier(member_count)
    subscribed_tier = active_subscriptions.get(guild_id)

    if not subscribed_tier:
        await ctx.send("This server does not have an active subscription.")
        return

    if tier_requirements[subscribed_tier] < tier_requirements[required_tier]:
        await ctx.send(f"This server's subscription ({subscribed_tier}) does not cover {member_count} members. Please upgrade to {required_tier}.")
        return

    button = discord.ui.Button(label="Verify Age", url=f"http://localhost:5000/start_verification?guild_id={guild_id}&user_id={ctx.author.id}&role_id={role.id}")
    view = discord.ui.View()
    view.add_item(button)
    await ctx.send(f"This server has {member_count} members. Click the button below to verify your age:", view=view)

@app.route('/start_verification')
def start_verification():
    guild_id = request.args.get('guild_id')
    user_id = request.args.get('user_id')
    role_id = request.args.get('role_id')
    session['guild_id'] = guild_id
    session['user_id'] = user_id
    session['role_id'] = role_id

    authorization_url = (
        f'https://api.id.me/oauth/authorize?response_type=code&client_id={ID_ME_CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope=openid'
    )
    return redirect(authorization_url)

if __name__ == '__main__':
    bot.loop.create_task(bot.start(DISCORD_BOT_TOKEN))
    app.run(port=5000)
