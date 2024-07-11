from flask import Flask, request, jsonify
import stripe
import os
from dotenv import load_dotenv
import pika
import json
import logging
from typing import Dict, Any
from pika.exceptions import AMQPError

# Load environment variables
load_dotenv()

# Configure logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Stripe setup
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

if not STRIPE_WEBHOOK_SECRET:
    raise ValueError("STRIPE_WEBHOOK_SECRET must be set in the environment variables")

# RabbitMQ setup
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST')
RABBITMQ_PORT = os.getenv('RABBITMQ_PORT')
RABBITMQ_USERNAME = os.getenv('RABBITMQ_USERNAME')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD')
RABBITMQ_VHOST = os.getenv('RABBITMQ_VHOST')
RABBITMQ_QUEUE_NAME = os.getenv('RABBITMQ_QUEUE_NAME')

# Ensure all required environment variables are set
required_env_vars = ['RABBITMQ_HOST', 'RABBITMQ_PORT', 'RABBITMQ_USERNAME', 'RABBITMQ_PASSWORD', 'RABBITMQ_VHOST', 'RABBITMQ_QUEUE_NAME']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Convert RABBITMQ_PORT to integer after ensuring it is set
RABBITMQ_PORT = int(RABBITMQ_PORT)

# Create a connection pool
credentials = pika.PlainCredentials(RABBITMQ_USERNAME, RABBITMQ_PASSWORD)
connection_params = pika.ConnectionParameters(
    host=RABBITMQ_HOST,
    port=RABBITMQ_PORT,
    virtual_host=RABBITMQ_VHOST,
    credentials=credentials
)
connection_pool = pika.pool.QueuedConnectionPool(create=lambda: pika.BlockingConnection(connection_params), max_size=10, max_overflow=10)

def send_to_queue(message: Dict[str, Any], max_retries: int = 3) -> None:
    retries = 0
    while retries < max_retries:
        try:
            with connection_pool.acquire() as connection:
                channel = connection.channel()
                channel.queue_declare(queue=RABBITMQ_QUEUE_NAME, durable=True)
                channel.basic_publish(
                    exchange='',
                    routing_key=RABBITMQ_QUEUE_NAME,
                    body=json.dumps(message),
                    properties=pika.BasicProperties(delivery_mode=2)  # Make message persistent
                )
            logger.info("Message sent to queue successfully")
            return
        except AMQPError as e:
            logger.error(f"Failed to send message to queue: {str(e)}")
            retries += 1
    
    logger.error(f"Failed to send message to queue after {max_retries} attempts")

@app.route('/stripe_webhook', methods=['POST'])
def stripe_webhook() -> tuple:
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.error(f"Invalid payload: {str(e)}")
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {str(e)}")
        return 'Invalid signature', 400

    if event['type'] == 'identity.verification_session.verified':
        handle_verification_verified(event['data']['object'])
    elif event['type'] == 'identity.verification_session.canceled':
        handle_verification_canceled(event['data']['object'])
    else:
        logger.info(f"Unhandled event type: {event['type']}")

    return '', 200

def handle_verification_verified(session: Dict[str, Any]) -> None:
    metadata = session.get('metadata', {})
    message = {
        'type': 'verification_verified',
        'guild_id': metadata.get('guild_id'),
        'user_id': metadata.get('user_id'),
        'role_id': metadata.get('role_id')
    }
    send_to_queue(message)
    logger.info(f"Verification successful for user {metadata.get('user_id')} in guild {metadata.get('guild_id')}")

def handle_verification_canceled(session: Dict[str, Any]) -> None:
    metadata = session.get('metadata', {})
    message = {
        'type': 'verification_canceled',
        'guild_id': metadata.get('guild_id'),
        'user_id': metadata.get('user_id'),
        'channel_id': metadata.get('channel_id')
    }
    send_to_queue(message)
    logger.info(f"Verification canceled for user {metadata.get('user_id')} in guild {metadata.get('guild_id')}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5431)
