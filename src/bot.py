import os
import json
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import pika
import stripe
from cryptography.fernet import Fernet

try:
    from .models import User, Server, CommandUsage, session_scope
except ImportError:
    from models import User, Server, CommandUsage, session_scope

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

# Reduce noise from pika unless explicitly overridden
PIKA_LOG_LEVEL = os.getenv('PIKA_LOG_LEVEL', 'WARNING').upper()
logging.getLogger('pika').setLevel(getattr(logging, PIKA_LOG_LEVEL, logging.WARNING))

# Retrieve environment variables
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')


# RabbitMQ Configuration
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))
RABBITMQ_USERNAME = os.getenv('RABBITMQ_USERNAME')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD')
RABBITMQ_VHOST = os.getenv('RABBITMQ_VHOST', '/')
RABBITMQ_QUEUE_NAME = os.getenv('RABBITMQ_QUEUE_NAME', 'verification_results')

# Ensure all required environment variables are set
required_env_vars = ['DISCORD_BOT_TOKEN', 'STRIPE_SECRET_KEY', 'DATABASE_URL_VERIFICATION', 
                     'RABBITMQ_HOST', 'RABBITMQ_USERNAME', 'RABBITMQ_PASSWORD']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Initialize the Discord bot with intents (avoid heavy member chunking at startup)
intents = discord.Intents.default()

# TTL cache and concurrency limit for REST member fetches (no Members intent needed)
REST_TTL_SECONDS = int(os.getenv('REST_TTL_SECONDS', '180'))
REST_CACHE_MAX = int(os.getenv('REST_CACHE_MAX', '10000'))
REST_CONCURRENCY = int(os.getenv('REST_CONCURRENCY', '8'))


class _TTLCache:
    def __init__(self, maxsize: int, ttl: int):
        self.maxsize = maxsize
        self.ttl = ttl
        self._store = {}

    def get(self, key):
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < asyncio.get_running_loop().time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key, value):
        if len(self._store) >= self.maxsize:
            try:
                self._store.pop(next(iter(self._store)))
            except StopIteration:
                pass
        self._store[key] = (asyncio.get_running_loop().time() + self.ttl, value)


_member_fetch_cache = _TTLCache(REST_CACHE_MAX, REST_TTL_SECONDS)
_rest_semaphore = asyncio.Semaphore(REST_CONCURRENCY)


async def fetch_member_cached(guild, user_id: int):
    """Fetch a guild member via REST with a short TTL cache and bounded concurrency.

    Raises discord.NotFound / discord.HTTPException like guild.fetch_member.
    """
    key = (guild.id, user_id)
    cached = _member_fetch_cache.get(key)
    if cached:
        return cached
    async with _rest_semaphore:
        member = await guild.fetch_member(user_id)
        _member_fetch_cache.set(key, member)
        return member


class MyBot(discord.Client):
    def __init__(self):
        # Disable guild member chunking at startup to speed up readiness
        super().__init__(intents=intents, chunk_guilds_at_startup=False)
        self.tree = app_commands.CommandTree(self)
        self.last_startup_time = None
        self.background_tasks_started = False

    async def setup_hook(self):
        # Register persistent views so buttons on existing messages work after restarts
        self.add_view(InstructionsPersistentView())
        await self.tree.sync()

bot = MyBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle common slash-command errors without noisy tracebacks."""
    original = getattr(error, "original", error)

    if isinstance(original, app_commands.MissingPermissions):
        missing = ", ".join(original.missing_permissions)
        msg = f"You don't have permission to use this command. Missing permission(s): {missing}."
    elif isinstance(original, app_commands.NoPrivateMessage):
        msg = "This command can only be used in a server (not in DMs)."
    elif isinstance(original, app_commands.CheckFailure):
        msg = "You can't use this command here."
    else:
        logger.error("Unhandled app command error", exc_info=original)
        msg = "Something went wrong while running that command."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        # If we can't respond (e.g., already responded and followup failed), just swallow.
        pass

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

# RabbitMQ setup
credentials = pika.PlainCredentials(RABBITMQ_USERNAME, RABBITMQ_PASSWORD)


def _rabbitmq_parameters() -> pika.ConnectionParameters:
    """Build connection parameters with heartbeats/timeouts so stale connections get detected."""
    heartbeat = int(os.getenv('RABBITMQ_HEARTBEAT', '60'))
    blocked_timeout = int(os.getenv('RABBITMQ_BLOCKED_TIMEOUT', '60'))
    connection_attempts = int(os.getenv('RABBITMQ_CONN_ATTEMPTS', '3'))
    retry_delay = float(os.getenv('RABBITMQ_RETRY_DELAY', '2'))
    socket_timeout = float(os.getenv('RABBITMQ_SOCKET_TIMEOUT', '10'))

    return pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        virtual_host=RABBITMQ_VHOST,
        credentials=credentials,
        heartbeat=heartbeat,
        blocked_connection_timeout=blocked_timeout,
        connection_attempts=connection_attempts,
        retry_delay=retry_delay,
        socket_timeout=socket_timeout,
    )


def _rabbitmq_connect_with_retry(max_tries: int = 0) -> pika.BlockingConnection:
    """Connect to RabbitMQ with retries.

    max_tries=0 means retry forever (used by the long-running consumer).
    """
    params = _rabbitmq_parameters()
    attempt = 0
    while True:
        attempt += 1
        try:
            return pika.BlockingConnection(params)
        except pika.exceptions.AMQPConnectionError:
            if max_tries and attempt >= max_tries:
                raise
            delay = min(30.0, 2.0 * attempt)
            logger.warning(f"RabbitMQ connection failed; retrying in {delay:.1f}s (attempt {attempt})")
            time.sleep(delay)


def get_rabbitmq_channel():
    connection = _rabbitmq_connect_with_retry(max_tries=1)
    channel = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE_NAME, durable=True)
    return channel

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
        # Fallback to REST fetch to avoid requiring Members intent/cache
        try:
            member = await fetch_member_cached(guild, int(user_id))
            logger.debug(f"Fetched member {user_id} via REST in guild {guild_id}")
        except discord.NotFound:
            logger.error(f"Member {user_id} not found in guild {guild_id}")
            return
        except discord.HTTPException as e:
            logger.error(f"HTTP error fetching member {user_id} in guild {guild_id}: {e}")
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
        logger.debug(f"Decrementing verification count for guild {guild_id} after successful verification.")
        await decrement_verifications_count(guild_id)
        logger.info(f"Verification count decremented for guild {guild_id}.")
        await assign_role(guild_id, user_id, role_id)
        await update_user_verification_status(user_id, True)

        # Attempt to DM the user with a success embed
        try:
            user_obj = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
            if user_obj:
                embed = discord.Embed(
                    title="You're verified",
                    description="Your age was verified and your role was assigned.",
                    color=discord.Color.green(),
                )
                await user_obj.send(embed=embed)
        except Exception as e:
            logger.debug(f"Unable to DM user {user_id} after verification: {e}")
    elif data['type'] == 'verification_canceled':
        guild_id = data['guild_id']
        user_id = data['user_id']
        channel_id = data.get('channel_id')
        if channel_id:
            channel = bot.get_channel(int(channel_id))
            if channel:
                await channel.send(f"Verification canceled for user <@{user_id}>")

# Global variable to store the event loop
main_loop = None

async def consume_queue():
    global main_loop
    main_loop = asyncio.get_running_loop()

    def sync_callback(ch, method, properties, body):
        try:
            json.loads(body)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"Invalid JSON on {RABBITMQ_QUEUE_NAME}; dropping message")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        asyncio.run_coroutine_threadsafe(process_verification_result(body), main_loop)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def do_blocking_consume():
        while True:
            connection = None
            try:
                connection = _rabbitmq_connect_with_retry(max_tries=0)
                channel = connection.channel()
                channel.queue_declare(queue=RABBITMQ_QUEUE_NAME, durable=True)
                channel.basic_qos(prefetch_count=10)
                logger.info(f"Listening for verification results on '{RABBITMQ_QUEUE_NAME}'...")
                channel.basic_consume(queue=RABBITMQ_QUEUE_NAME, on_message_callback=sync_callback)
                channel.start_consuming()
            except (pika.exceptions.AMQPConnectionError, pika.exceptions.StreamLostError, OSError):
                logger.warning("RabbitMQ consumer disconnected; reconnecting soon...", exc_info=True)
                time.sleep(3)
            except Exception:
                logger.exception("Unexpected error in RabbitMQ consumer; restarting consumer loop")
                time.sleep(3)
            finally:
                try:
                    if connection and connection.is_open:
                        connection.close()
                except Exception:
                    pass

    await main_loop.run_in_executor(None, do_blocking_consume)

# -------------------------------------------------------------------
# Persistent View for Instructions (survives restarts)
# -------------------------------------------------------------------
class InstructionsPersistentView(discord.ui.View):
    """Persistent view to keep the 'Verify Me' button working across restarts."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Me",
        style=discord.ButtonStyle.success,
        custom_id="ageverify:instructions:verify",
    )
    async def verify_me(self, interaction: discord.Interaction, button: discord.ui.Button):
        await verify(interaction)

@bot.event
async def on_ready():
    bot.last_startup_time = datetime.now(timezone.utc)

    # on_ready also fires on gateway reconnects; only start background work once
    if not bot.background_tasks_started:
        bot.background_tasks_started = True
        bot.loop.create_task(consume_queue())

        # Log reinitialization for any guilds that have stored instruction panel IDs
        try:
            with session_scope() as session:
                servers_with_panels = (
                    session.query(Server)
                    .filter(
                        Server.instructions_channel_id.isnot(None),
                        Server.instructions_message_id.isnot(None)
                    )
                    .all()
                )
                for s in servers_with_panels:
                    # Requested log format
                    logger.info(f"Reinitializing instruction panel for guild ID: {s.server_id}")
        except Exception as e:
            logger.debug(f"Unable to enumerate servers for instruction panel reinitialization logs: {e}")

    # # Set the bot's bio/status
    # bio_message = "Use `/get_verify_bot` to add this bot to your discord."
    # await bot.change_presence(activity=discord.Game(name=bio_message))

    logger.info(f'Bot is ready. Logged in as {bot.user}')

async def verify(interaction: discord.Interaction):
    """Plain function containing the verify flow; reusable by buttons and tests."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "Please use this in the server you want to verify in, not in DMs.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)  # Acknowledge the interaction early
    logger.debug(f"Received verify flow from user {interaction.user.id} in guild {interaction.guild.id}")

    try:
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        with session_scope() as session:
            user = session.query(User).filter_by(discord_id=user_id).first()
            server_config = session.query(Server).filter_by(server_id=guild_id).first()
            # Copy needed fields before session closes to avoid DetachedInstanceError
            local_role_id = str(server_config.role_id) if server_config and server_config.role_id else None

            if not server_config or not local_role_id or not server_config.subscription_status:
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
                    
                    # Calculate the user's age in calendar years (leap-year safe)
                    user_age = relativedelta(datetime.now(timezone.utc), decrypted_dob).years
                    
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

            # Directly generate a Stripe verification URL and send it (no second button)
            logger.debug(f"Generating Stripe verification URL for user {interaction.user.id}")
            verification_url = await generate_stripe_verification_url(
                guild_id, interaction.user.id, local_role_id, str(interaction.channel.id)
            )
            if not verification_url:
                logger.error(f"Failed to generate Stripe verification URL for user {interaction.user.id}")
                await interaction.followup.send("Failed to initiate the verification process. Please try again later or contact support.", ephemeral=True)
                return

            # Track verification attempt and usage
            await track_verification_attempt(user_id)
            bot.loop.create_task(track_command_usage(guild_id, interaction.user.id, "verify"))

            await interaction.followup.send(
                f"Click the link below to verify your age. This link is private and should not be shared:\n\n{verification_url}",
                ephemeral=True,
            )
            logger.debug(f"Sent verification link to user {interaction.user.id}")

    except Exception as e:
        logger.error(f"Unexpected error in verify command: {str(e)}", exc_info=True)
        await interaction.followup.send("An unexpected error occurred. Please try again later or contact support.", ephemeral=True)

    logger.debug(f"Verify flow completed for user {interaction.user.id}")

@bot.tree.command(name="verifyme", description="Start the age verification process")
@app_commands.guild_only()
async def verify_command(interaction: discord.Interaction):
    """Slash command wrapper that calls the plain verify() function."""
    await verify(interaction)

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
    def _embed(title: str, desc: str, color: discord.Color) -> discord.Embed:
        e = discord.Embed(title=title, description=desc, color=color)
        e.set_footer(text="Age Verification Service")
        return e

    if not server_config:
        await interaction.followup.send(
            embed=_embed(
                "Not configured",
                "This server is not set up. Ask an admin to run /setupverify.",
                discord.Color.orange(),
            ),
            ephemeral=True,
        )
    elif not server_config.role_id:
        logger.warning(f"No verification role set for guild {guild_id}")
        await interaction.followup.send(
            embed=_embed(
                "Role not set",
                "Admins: configure the role with /setupverify before users can verify.",
                discord.Color.orange(),
            ),
            ephemeral=True,
        )
    elif not server_config.subscription_status:
        logger.warning(f"No active subscription for guild {guild_id}")
        await interaction.followup.send(
            embed=_embed(
                "Subscription inactive",
                "This server doesn't have an active verification subscription.",
                discord.Color.red(),
            ),
            ephemeral=True,
        )

@bot.tree.command(name="setupverify", description="Set the role and minimum age for verified users")
@app_commands.guild_only()
@app_commands.describe(role="The role to assign to verified users", minimum_age="The minimum age required for verification")
async def setupVerify(interaction: discord.Interaction, role: discord.Role, minimum_age: int):
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
@app_commands.guild_only()
async def get_subscription(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    await interaction.response.send_message("To activate a subscription, please visit: https://esattotech.com/pricing/", ephemeral=True)

@bot.tree.command(name="server_info", description="Display current server configuration for verification")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def server_info(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    
    with session_scope() as session:
        server_config = session.query(Server).filter_by(server_id=guild_id).first()

        if not server_config:
            await interaction.response.send_message("This server is not configured for verification. Please type /setupverify to configure.", ephemeral=True)
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

# @bot.tree.command(name="subscription_status", description="Show detailed information about the server's verification subscription")
# @app_commands.checks.has_permissions(administrator=True)
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

# @bot.tree.command(name="verification_logs", description="View recent verification actions")
# @app_commands.checks.has_permissions(administrator=True)
# async def verification_logs(interaction: discord.Interaction, limit: int = 10):
#     guild_id = str(interaction.guild.id)
    
#     with session_scope() as session:
#         logs = session.query(CommandUsage).filter_by(server_id=guild_id).order_by(CommandUsage.timestamp.desc()).limit(limit).all()

#     if not logs:
#         await interaction.response.send_message("No verification logs found for this server.", ephemeral=True)
#         return

#     embed = discord.Embed(title="Recent Verification Actions", color=discord.Color.blue())
    
#     for log in logs:
#         user = interaction.guild.get_member(int(log.user_id))
#         user_name = user.name if user else f"Unknown User ({log.user_id})"
#         embed.add_field(name=f"{log.command} - {log.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
#                         value=f"User: {user_name}",
#                         inline=False)

#     await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)


@bot.tree.command(name="instructions", description="Admin: Post verification instructions with a button")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def instructions(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Age Verification — What to expect",
        description=(
            "Already verified before? We'll automatically add the role if you meet this server's age requirement.\n\n"
            "Not verified yet? Click the 'Verify Me' button. You'll receive a private link to start the secure process."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="How it works",
        value=(
            "1) Click 'Verify Me' to receive a private verification link.\n"
            "2) Open the link and follow the steps powered by Stripe Identity.\n"
            "3) You'll be asked to take photos of your ID (front and back) and a selfie.\n"
            "4) Stripe checks that your selfie matches your ID and confirms your age.\n"
            "5) Once complete, return to Discord—if you meet the minimum age, the role is assigned automatically."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy & Safety",
        value=(
            "• Your link is private—do not share it.\n"
            "• Verification is handled by Stripe Identity.\n"
            "• Server staff only see your verification status (pass/fail) and apply roles accordingly."
        ),
        inline=False,
    )

    view = InstructionsPersistentView()

    # Upsert canonical instructions message per guild
    guild_id = str(interaction.guild.id)
    channel_to_use = interaction.channel

    with session_scope() as session:
        server = session.query(Server).filter_by(server_id=guild_id).first()
        # Try updating existing panel if we have stored IDs
        if server and server.instructions_channel_id and server.instructions_message_id:
            try:
                ch = interaction.guild.get_channel(int(server.instructions_channel_id))
                if ch is None:
                    ch = bot.get_channel(int(server.instructions_channel_id))
                if ch is not None:
                    # Requested log format
                    logger.info(f"Reinitializing instruction panel for guild ID: {guild_id}")
                    msg = await ch.fetch_message(int(server.instructions_message_id))
                    await msg.edit(embed=embed, view=view)
                    await interaction.response.send_message("Updated existing instructions message.", ephemeral=True)
                    return
            except Exception as e:
                logger.info(f"Existing instructions message not found or not editable; posting new. Reason: {e}")

        # Post a new message and store IDs
        sent = await channel_to_use.send(embed=embed, view=view)
        if not server:
            server = Server(
                server_id=guild_id,
                owner_id=str(interaction.guild.owner_id),
                role_id=None,
                tier="tier_0",
                subscription_status=False,
                minimum_age=18,
            )
            session.add(server)
        server.instructions_channel_id = str(sent.channel.id)
        server.instructions_message_id = str(sent.id)
        # Respond to the admin
        await interaction.response.send_message("Posted instructions message and saved its location.", ephemeral=True)

if __name__ == '__main__':
    bot.run(DISCORD_BOT_TOKEN)
