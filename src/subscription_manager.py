import os
import json
import stripe
import threading
from flask import Flask, request, jsonify
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from contextlib import contextmanager
import logging
from datetime import datetime, timezone, timedelta

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Stripe configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
endpoint_secret = os.getenv('STRIPE_PAYMENT_WEBHOOK_SECRET')

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL')
engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)

# Define SQLAlchemy models
class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    server_id = Column(String(30), nullable=False, unique=True)
    owner_id = Column(String(30), nullable=False)
    tier = Column(String(50), nullable=True)
    subscription_status = Column(Boolean, default=False)
    verifications_count = Column(Integer, default=0)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    role_id = Column(String(30), nullable=True)  # Updated to allow NULL
    email = Column(String(255), nullable=True)  # New column for storing customer email

Base.metadata.create_all(engine)

@contextmanager
def session_scope():
    session = Session()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logging.error(f"Error during session scope: {e}")
        raise
    finally:
        session.close()

# Logging setup
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Mapping of product IDs to tiers and their corresponding verification tokens
PRODUCT_ID_TO_TIER = {
    'prod_QXNi63ixsJYIke': {'tier': 'tier_1', 'tokens': 10},
    'prod_QXNldB600Dr8RX': {'tier': 'tier_2', 'tokens': 25},
    'prod_QXNnv5WYeieAGZ': {'tier': 'tier_3', 'tokens': 50},
    'prod_QXNpnlGsJn210K': {'tier': 'tier_4', 'tokens': 75},
    'prod_QXNrATAgXjN7Xi': {'tier': 'tier_5', 'tokens': 100},
    'prod_QXNtfHzYQ2EhUx': {'tier': 'tier_6', 'tokens': 150},
}

# Mapping of product IDs to one-time purchase token amounts
PRODUCT_ID_TO_EXTRA_TOKENS = {
    'prod_QXmfTZh1Gn0P8L': 10,
    'prod_QXmgiGMLNpSNZt': 25,
    'prod_QXmiBjX9MWZtPw': 50,
    'prod_QXmiFdyIb5mN17': 100,
}

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    logging.info(f"Received webhook: {payload}")
    logging.info(f"Signature header: {sig_header}")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        # Invalid payload
        logging.error(f"Invalid payload: {e}")
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logging.error(f"Invalid signature: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as e:
        # Other errors
        logging.error(f"Error verifying webhook signature: {e}")
        return jsonify({'error': 'Webhook verification failed'}), 400

    # Process the event asynchronously
    thread = threading.Thread(target=process_event, args=(event,))
    thread.start()

    return jsonify({'status': 'success'}), 200

def process_event(event):
    try:
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            handle_checkout_session(session)
        elif event['type'] in ['invoice.payment_failed', 'customer.subscription.updated', 'invoice.payment_succeeded']:
            handle_subscription_status(event)
        elif event['type'].startswith('subscription_schedule'):
            handle_subscription_schedule(event)
    except Exception as e:
        logging.error(f"Error handling webhook event: {e}")

def handle_checkout_session(session):
    logging.info("Handling checkout.session.completed event")
    customer_email = session['customer_details'].get('email')
    custom_fields = session.get('custom_fields', [])
    
    # Extract custom fields
    user_id = next((field['text']['value'] for field in custom_fields if field['key'] == 'discorduseridnotyourusername'), None)
    guild_id = next((field['text']['value'] for field in custom_fields if field['key'] in ['discordserverid', 'discordserveridnotservername']), None)
    subscription_id = session.get('subscription')

    # Fetch the session's line items
    try:
        line_items = stripe.checkout.Session.list_line_items(session['id'])
        product_id = line_items['data'][0]['price']['product']
        tier_info = PRODUCT_ID_TO_TIER.get(product_id)
        extra_tokens = PRODUCT_ID_TO_EXTRA_TOKENS.get(product_id, 0)
    except Exception as e:
        logging.error(f"Error fetching line items: {e}")
        return

    if not all([user_id, guild_id]):
        logging.error("Missing necessary metadata")
        return

    try:
        with session_scope() as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()
            if not server:
                server = db_session.query(Server).filter_by(stripe_subscription_id=subscription_id).first()
            
            if tier_info:
                tier = tier_info['tier']
                tokens = tier_info['tokens']
                if server:
                    old_tokens = PRODUCT_ID_TO_TIER.get(server.tier, {}).get('tokens', 0)
                    server.owner_id = user_id
                    server.tier = tier
                    server.subscription_status = True
                    server.subscription_start_date = datetime.now(timezone.utc)
                    server.stripe_subscription_id = subscription_id
                    server.email = customer_email

                    # Reset the verification count if it's a new subscription or upgrade
                    if tokens >= old_tokens:
                        server.verifications_count = tokens - (old_tokens - server.verifications_count)
                    else:
                        server.verifications_count = tokens
                else:
                    server = Server(
                        server_id=guild_id,
                        owner_id=user_id,
                        tier=tier,
                        subscription_status=True,
                        verifications_count=tokens,
                        subscription_start_date=datetime.now(timezone.utc),
                        stripe_subscription_id=subscription_id,
                        email=customer_email
                    )
                    db_session.add(server)
            elif extra_tokens:
                if server:
                    server.verifications_count += extra_tokens
                else:
                    logging.error(f"No server found for one-time purchase tokens. Server ID: {guild_id}")

            logging.info(f"Updated server {guild_id} with new subscription data or added extra tokens")
    except Exception as e:
        logging.error(f"Error updating database for checkout session: {e}")

def handle_subscription_schedule(event):
    logging.info(f"Handling {event['type']} event")
    schedule = event['data']['object']
    subscription_id = schedule.get('subscription')
    status = schedule.get('status')

    if not subscription_id:
        logging.error("Missing subscription ID in schedule event")
        return

    try:
        with session_scope() as db_session:
            server = db_session.query(Server).filter_by(stripe_subscription_id=subscription_id).first()
            if not server:
                logging.error(f"No server found with subscription ID {subscription_id}")
                return

            if event['type'] == 'subscription_schedule.canceled':
                server.subscription_status = False
            elif event['type'] == 'subscription_schedule.completed':
                server.subscription_status = True
                if server.subscription_start_date and datetime.now(timezone.utc) - server.subscription_start_date >= timedelta(days=30):
                    tokens = PRODUCT_ID_TO_TIER.get(server.tier, {}).get('tokens', 0)
                    server.verifications_count = tokens
                    server.subscription_start_date = datetime.now(timezone.utc)

            logging.info(f"Updated server {server.server_id} subscription status to {server.subscription_status}")
    except Exception as e:
        logging.error(f"Error updating database for subscription schedule: {e}")

def handle_subscription_status(event):
    logging.info("Handling subscription status event")
    subscription = event['data']['object']
    subscription_id = subscription.get('id')
    status = subscription.get('status')
    items = subscription.get('items', {}).get('data', [])
    
    # Extract product_id from subscription items
    product_id = items[0]['plan']['product'] if items else None
    tier_info = PRODUCT_ID_TO_TIER.get(product_id)

    if not subscription_id:
        logging.error("Missing subscription ID in status event")
        return

    try:
        with session_scope() as db_session:
            server = db_session.query(Server).filter((Server.server_id == subscription['metadata'].get('discordserverid')) | 
                                                     (Server.stripe_subscription_id == subscription_id)).first()
            if not server:
                logging.error(f"No server found with subscription ID {subscription_id}")
                return

            if status in ['canceled', 'unpaid', 'incomplete', 'incomplete_expired', 'past_due']:
                server.subscription_status = False
            elif status == 'active':
                server.subscription_status = True

                # Reset or update verification tokens based on the event type
                if event['type'] == 'customer.subscription.updated':
                    if tier_info:
                        old_tokens = PRODUCT_ID_TO_TIER.get(server.tier, {}).get('tokens', 0)
                        server.tier = tier_info['tier']
                        tokens = tier_info['tokens']
                        if tokens >= old_tokens:
                            server.verifications_count = tokens - (old_tokens - server.verifications_count)
                        else:
                            server.verifications_count = tokens
                elif event['type'] == 'invoice.payment_succeeded':
                    if tier_info:
                        tokens = tier_info['tokens']
                        server.verifications_count = tokens  # Reset the verifications count

            logging.info(f"Updated server {server.server_id} subscription status to {server.subscription_status}")
    except Exception as e:
        logging.error(f"Error updating database for subscription status: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5433)
