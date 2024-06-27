import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import asyncio
import logging

# Load environment variables from .env file
load_dotenv()

# Retrieve environment variables
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Initialize the Discord bot with intents
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True  # Ensure message content intent is enabled

bot = commands.Bot(command_prefix="!", intents=intents)

# Configure logging
logging.basicConfig(level=logging.DEBUG)

@bot.event
async def on_ready():
    logging.info(f'Bot is ready. Logged in as {bot.user}')

@bot.event
async def on_message(message):
    logging.info(f'Message received: "{message.content}" from {message.author} in channel {message.channel}')
    await bot.process_commands(message)

@bot.event
async def on_command(ctx):
    logging.info(f"Command '{ctx.command}' invoked by {ctx.author} in guild {ctx.guild}")

@bot.event
async def on_command_error(ctx, error):
    logging.error(f"Error in command '{ctx.command}': {error}")

@bot.command()
async def ping(ctx):
    logging.info(f"Ping command received from {ctx.author}")
    await ctx.send("Pong!")

def run_discord_bot():
    asyncio.run(bot.start(DISCORD_BOT_TOKEN))

if __name__ == '__main__':
    run_discord_bot()
