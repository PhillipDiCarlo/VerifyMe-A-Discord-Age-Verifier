import os
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any

import pika
import stripe
from flask import Flask, request
from dotenv import load_dotenv
from pika.exceptions import AMQPError
from cryptography.fernet import Fernet

try:
    from .models import User, session_scope
except ImportError:
    from models import User, session_scope

# Load environment variables
load_dotenv()

# Configure logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Stripe setup
stripe.api_key = os.getenv('STRIPE_RESTRICTED_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

if not STRIPE_WEBHOOK_SECRET:
    raise ValueError("STRIPE_WEBHOOK_SECRET must be set in the environment variables")

# RabbitMQ setup
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT'))
RABBITMQ_USERNAME = os.getenv('RABBITMQ_USERNAME')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD')
RABBITMQ_VHOST = os.getenv('RABBITMQ_VHOST')
RABBITMQ_QUEUE_NAME = os.getenv('RABBITMQ_QUEUE_NAME')

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

# Encryption/Decryption setup
DOB_KEY = os.getenv('DOB_KEY')
if not DOB_KEY:
    raise ValueError("DOB_KEY not found in environment variables")

cipher = Fernet(DOB_KEY)

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

def send_to_queue(message: Dict[str, Any], max_retries: int = None) -> None:
    max_retries = max_retries if max_retries is not None else int(os.getenv('RABBITMQ_PUBLISH_TRIES', '3'))
    last_exc = None
    for attempt in range(1, max_retries + 1):
        connection = None
        try:
            connection = pika.BlockingConnection(_rabbitmq_parameters())
            channel = connection.channel()
            channel.queue_declare(queue=RABBITMQ_QUEUE_NAME, durable=True)
            channel.basic_publish(
                exchange='',
                routing_key=RABBITMQ_QUEUE_NAME,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)  # Make message persistent
            )
            logger.debug("Message sent to queue successfully")
            logger.debug(f"Sent message to queue: {message}")
            return
        except AMQPError as e:
            last_exc = e
            logger.warning(f"Failed to send message to queue (attempt {attempt}/{max_retries}); retrying...", exc_info=True)
            time.sleep(min(10.0, 1.5 * attempt))
        finally:
            try:
                if connection and connection.is_open:
                    connection.close()
            except Exception:
                pass

    logger.error(f"Failed to send message to queue after {max_retries} attempts", exc_info=last_exc)

@app.route('/stripe_webhook', methods=['POST'])
def stripe_webhook() -> tuple:
    logger.info("Received a webhook from Stripe")
    payload = request.data.decode('utf-8')
    sig_header = request.headers.get('Stripe-Signature')

    # Signature verification is mandatory. ALLOW_UNSIGNED_WEBHOOKS is an explicit
    # opt-in for local testing only and must never be set in production.
    if os.getenv('ALLOW_UNSIGNED_WEBHOOKS', '').lower() in ('1', 'true', 'yes') and request.is_json:
        event = request.json
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except ValueError as e:
            logger.error(f"Invalid payload: {str(e)}")
            return 'Invalid payload', 400
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid signature: {str(e)}")
            return 'Invalid signature', 400

    logger.info(f"Webhook event type: {event['type']}")

    etype = event.get('type')
    if etype == 'identity.verification_session.verified':
        obj = event.get('data', {}).get('object', {})
        if isinstance(obj, dict):
            session_id = obj.get('id') or event.get('id')
            if session_id:
                handle_verification_verified(session_id)
    elif etype == 'identity.verification_session.canceled':
        obj = event.get('data', {}).get('object', {})
        if obj:
            handle_verification_canceled(obj)
    else:
        logger.info(f"Unhandled event type: {event['type']}")

    return '', 200

def handle_verification_verified(session_id: str) -> None:
    try:
        # Retrieve the verification session from Stripe, expanding to include DOB
        # Note: do not log the session or verified_outputs — they contain PII (DOB, document data)
        session = stripe.identity.VerificationSession.retrieve(
            session_id,
            expand=['verified_outputs.dob']
        )
    except Exception as e:
        logger.error(f"Failed to retrieve verification session: {str(e)}")
        return

    # Extract metadata from the session (guild_id, user_id, role_id)
    metadata = session.get('metadata', {})
    guild_id = metadata.get('guild_id')
    user_id = metadata.get('user_id')
    role_id = metadata.get('role_id')
    
    if not user_id:
        logger.error(f"Missing user_id in metadata: {metadata}")
        return

    # Extract and format the date of birth (DOB)
    dob = session.verified_outputs.get('dob', {})
    birthdate = f"{dob.get('year', '')}-{dob.get('month', ''):02d}-{dob.get('day', ''):02d}"

    # Encrypt the DOB before storing it in the database
    encrypted_dob = encrypt_dob(datetime.strptime(birthdate, "%Y-%m-%d"))
    verification_status = True

    # Check if the user already exists in the database, then update or create a new user
    with session_scope() as db_session:
        user = db_session.query(User).filter_by(discord_id=user_id).first()
        if user:
            user.verification_status = verification_status
            user.dob = encrypted_dob  # Store the encrypted DOB
            user.last_verification_attempt = datetime.now()
            logger.info(f"User {user_id} marked as verified with encrypted DOB")
        else:
            # Add new user if not found
            new_user = User(
                discord_id=user_id,
                verification_status=verification_status,
                dob=encrypted_dob,  # Store the encrypted DOB
                last_verification_attempt=datetime.now()
            )
            db_session.add(new_user)
            logger.info(f"New user {user_id} added with encrypted DOB")

    # Send the verification success message to the queue for role assignment in Discord
    message = {
        'type': 'verification_verified',
        'guild_id': guild_id,
        'user_id': user_id,
        'role_id': role_id
    }
    send_to_queue(message)
    logger.info(f"Verification successful for user {user_id} in guild {guild_id}")

def handle_verification_canceled(session: Dict[str, Any]) -> None:
    metadata = session.get('metadata', {})
    guild_id = metadata.get('guild_id')
    user_id = metadata.get('user_id')
    role_id = metadata.get('role_id')

    if not user_id:
        logger.error(f"Missing user_id in metadata: {metadata}")
        return

    verification_status = False

    # Check if user exists and update, otherwise create a new user
    with session_scope() as db_session:
        user = db_session.query(User).filter_by(discord_id=user_id).first()
        if user:
            user.verification_status = verification_status
            user.last_verification_attempt = datetime.now()
            logger.info(f"User {user_id} verification attempt canceled")
        else:
            # Add new user if not found
            new_user = User(
                discord_id=user_id,
                verification_status=verification_status,
                last_verification_attempt=datetime.now()
            )
            db_session.add(new_user)
            logger.info(f"New user {user_id} added with verification attempt canceled")

    message = {
        'type': 'verification_canceled',
        'guild_id': guild_id,
        'user_id': user_id,
        'role_id': role_id
    }
    send_to_queue(message)
    logger.info(f"Verification canceled for user {user_id} in guild {guild_id}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5431)