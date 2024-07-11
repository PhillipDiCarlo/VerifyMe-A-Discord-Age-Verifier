from flask import Flask, request, jsonify
import stripe
import os
from dotenv import load_dotenv
import pika
import json
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Stripe setup
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

# RabbitMQ setup
RABBITMQ_URL = os.getenv('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/%2F')
QUEUE_NAME = 'verification_results'

def send_to_queue(message):
    connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    channel.basic_publish(
        exchange='',
        routing_key=QUEUE_NAME,
        body=json.dumps(message),
        properties=pika.BasicProperties(delivery_mode=2)  # Make message persistent
    )
    connection.close()

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

    if event['type'] == 'identity.verification_session.verified':
        handle_verification_verified(event['data']['object'])
    elif event['type'] == 'identity.verification_session.canceled':
        handle_verification_canceled(event['data']['object'])
    else:
        logging.info(f"Unhandled event type: {event['type']}")

    return '', 200

def handle_verification_verified(session):
    metadata = session.get('metadata', {})
    message = {
        'type': 'verification_verified',
        'guild_id': metadata.get('guild_id'),
        'user_id': metadata.get('user_id'),
        'role_id': metadata.get('role_id')
    }
    send_to_queue(message)
    logging.info(f"Verification successful for user {metadata.get('user_id')} in guild {metadata.get('guild_id')}")

def handle_verification_canceled(session):
    metadata = session.get('metadata', {})
    message = {
        'type': 'verification_canceled',
        'guild_id': metadata.get('guild_id'),
        'user_id': metadata.get('user_id'),
        'channel_id': metadata.get('channel_id')
    }
    send_to_queue(message)
    logging.info(f"Verification canceled for user {metadata.get('user_id')} in guild {metadata.get('guild_id')}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5431)