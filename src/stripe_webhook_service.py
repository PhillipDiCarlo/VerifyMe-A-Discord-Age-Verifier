import hashlib
import os
import json
import logging
from datetime import datetime
from contextlib import contextmanager
from typing import Dict, Any

import pika
import stripe
from flask import Flask, request
from dotenv import load_dotenv
from pika.exceptions import AMQPError
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from cryptography.fernet import Fernet

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

# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')
engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)

# Define Users model
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String(50), nullable=False)
    verification_status = Column(Boolean, default=False)
    dob = Column(String(255), nullable=True)  # Store the encrypted DOB
    last_verification_attempt = Column(DateTime(timezone=True), nullable=False, default=datetime.now)

# Create tables
Base.metadata.create_all(engine)

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
parameters = pika.ConnectionParameters(
    host=RABBITMQ_HOST,
    port=RABBITMQ_PORT,
    virtual_host=RABBITMQ_VHOST,
    credentials=credentials
)

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

def send_to_queue(message: Dict[str, Any], max_retries: int = 3) -> None:
    retries = 0
    while retries < max_retries:
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue=RABBITMQ_QUEUE_NAME, durable=True)
            channel.basic_publish(
                exchange='',
                routing_key=RABBITMQ_QUEUE_NAME,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)  # Make message persistent
            )
            connection.close()
            logger.debug("Message sent to queue successfully")
            logger.debug(f"Sent message to queue: {message}")
            return
        except AMQPError as e:
            logger.warning(f"Failed to send message to queue: {str(e)}. Retry {retries + 1}/{max_retries}")
            retries += 1
    
    logger.error(f"Failed to send message to queue after {max_retries} attempts")

@app.route('/stripe_webhook', methods=['POST'])
def stripe_webhook() -> tuple:
    logger.info("Received a webhook from Stripe")
    payload = request.data.decode('utf-8')
    sig_header = request.headers.get('Stripe-Signature')

    logger.debug(f"Payload: {payload}")
    logger.debug(f"Signature Header: {sig_header}")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        logger.debug(f"Constructed Event: {event}")
    except ValueError as e:
        logger.error(f"Invalid payload: {str(e)}")
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {str(e)}")
        return 'Invalid signature', 400

    logger.info(f"Webhook event type: {event['type']}")

    if event['type'] == 'identity.verification_session.verified':
        handle_verification_verified(event['data']['object']['id'])
    elif event['type'] == 'identity.verification_session.canceled':
        handle_verification_canceled(event['data']['object'])
    else:
        logger.info(f"Unhandled event type: {event['type']}")

    return '', 200

def handle_verification_verified(session_id: str) -> None:
    try:
        # Retrieve the verification session from Stripe, expanding to include DOB
        session = stripe.identity.VerificationSession.retrieve(
            session_id,
            expand=['verified_outputs.dob']
        )
        logger.debug(f"Verification session: {session}")
        logger.debug(f"Verified outputs: {session.verified_outputs}")
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