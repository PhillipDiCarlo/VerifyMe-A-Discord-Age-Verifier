import os
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

# Database setup for both services
DATABASE_URL_VERIFICATION = os.getenv('DATABASE_URL_VERIFICATION')
DATABASE_URL_DJ = os.getenv('DATABASE_URL_DJ')

engine_verification = create_engine(DATABASE_URL_VERIFICATION)
engine_dj = create_engine(DATABASE_URL_DJ)

BaseVerification = declarative_base()
BaseDJ = declarative_base()

SessionVerification = sessionmaker(bind=engine_verification)
SessionDJ = sessionmaker(bind=engine_dj)

# Define SQLAlchemy models for Verification Service
class Server(BaseVerification):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    server_id = Column(String(30), nullable=False, unique=True)
    owner_id = Column(String(30), nullable=False)
    tier = Column(String(50), nullable=True)
    subscription_status = Column(Boolean, default=False)
    verifications_count = Column(Integer, default=0)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    role_id = Column(String(30), nullable=True)
    email = Column(String(255), nullable=True)

# Define SQLAlchemy models for DJ Service
class User(BaseDJ):
    __tablename__ = 'users'
    stripe_subscription_id = Column(String(255), primary_key=True)  # Set as primary key
    discord_id = Column(String(50), nullable=True)
    active_subscription = Column(Boolean, default=False)
    email = Column(String(255), nullable=True)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)

# Create tables
BaseVerification.metadata.create_all(engine_verification)
BaseDJ.metadata.create_all(engine_dj)

@contextmanager
def session_scope(service_type):
    Session = SessionVerification if service_type == 'verification' else SessionDJ
    session = Session()
    try:
        yield session
        session.commit()
        logging.info("Transaction committed.")
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

# Product IDs for Routing
PRODUCT_ID_DJ = 'prod_R2mETUJ7RTWF4t'
PRODUCT_ID_TO_TIER = {
    'prod_QrCgveExowX4SZ': {'tier': 'tier_0', 'tokens': 0},
    'prod_QtuUxwu41WzrPw': {'tier': 'tier_1', 'tokens': 10},
    'prod_QtuVxykbkplAZw': {'tier': 'tier_2', 'tokens': 25},
    'prod_QtuWzcaMquctfT': {'tier': 'tier_3', 'tokens': 50},
    'prod_QtuXjrcE0cIlLG': {'tier': 'tier_4', 'tokens': 75},
    'prod_QtuYXFfzpKS29k': {'tier': 'tier_5', 'tokens': 100},
    'prod_QtuYlkTvZ0181h': {'tier': 'tier_6', 'tokens': 150},
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
        event_type = event['type']
        if event_type == 'checkout.session.completed':
            session = event['data']['object']
            product_id = None  # product_id is not in session, handled in each function

            # Use the existing logic for DJ and Verification services here
            subscription_id = session.get('subscription')
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)
                product_id = subscription['items']['data'][0]['price']['product']

            if product_id == PRODUCT_ID_DJ:
                handle_dj_checkout_session(session)
            elif product_id in PRODUCT_ID_TO_TIER:
                handle_verification_checkout_session(session)

        elif event_type == 'customer.subscription.updated':
            subscription = event['data']['object']
            product_id = subscription['items']['data'][0]['price']['product']
            subscription_id = subscription['id']
            status = subscription['status']
            metadata = subscription.get('metadata', {})
            
            if product_id == PRODUCT_ID_DJ:
                handle_dj_subscription_update(subscription_id, status, metadata)
            elif product_id in PRODUCT_ID_TO_TIER:
                handle_verification_subscription_update(subscription_id, status, metadata)

    except Exception as e:
        logging.error(f"Error processing event: {e}")


def handle_verification_checkout_session(session):
    logging.info("Handling checkout.session.completed event")
    customer_email = session['customer_details'].get('email')
    custom_fields = session.get('custom_fields', [])

    guild_id = next((field['text']['value'] for field in custom_fields if field['key'] in ['discordserverid', 'discordserveridnotyourservername', 'discordserveridnotservername']), None)
    discord_id = next((field['text']['value'] for field in custom_fields if field['key'] == 'discorduseridnotyourusername'), None)
    subscription_id = session.get('subscription')

    line_items = stripe.checkout.Session.list_line_items(session['id'])
    product_id = line_items['data'][0]['price']['product']
    tier_info = PRODUCT_ID_TO_TIER.get(product_id)

    if not guild_id or not discord_id or not tier_info:
        logging.error("Missing guild_id, discord_id, or tier info.")
        return

    try:
        # line_items = stripe.checkout.Session.list_line_items(session['id'])
        # product_id = line_items['data'][0]['price']['product']
        extra_tokens = PRODUCT_ID_TO_EXTRA_TOKENS.get(product_id, 0)

        if tier_info:
            with session_scope('verification') as db_session:
                server = db_session.query(Server).filter_by(server_id=guild_id).first()
                if server:
                    server.tier = tier_info['tier']
                    server.subscription_status = True
                    server.verifications_count += tier_info['tokens']
                    server.subscription_start_date = datetime.now(timezone.utc)
                    server.stripe_subscription_id = session.get('subscription')
                    server.role_id = session['metadata'].get('role_id')
                    server.email = session['customer_details'].get('email')
                else:
                    server = Server(
                        server_id=guild_id,
                        owner_id=discord_id,
                        tier=tier_info['tier'],
                        subscription_status=True,
                        verifications_count=tier_info['tokens'],
                        subscription_start_date=datetime.now(timezone.utc),
                        stripe_subscription_id=subscription_id,
                        role_id=session['metadata'].get('role_id'),
                        email=session['customer_details'].get('email')
                    )
                    db_session.add(server)
                logging.info(f"Updated verification subscription for guild {guild_id}.")
        
        elif extra_tokens:
            with session_scope('verification') as db_session:
                if server:
                    server.verifications_count += extra_tokens
                else:
                    logging.error(f"No server found for one-time purchase tokens. Server ID: {guild_id}")

    except Exception as e:
        logging.error(f"Error updating verification database: {e}")

def handle_dj_checkout_session(session):
    logging.info("Handling DJ checkout session...")
    customer_email = session['customer_details'].get('email')
    discord_id = next((field['text']['value'] for field in session.get('custom_fields', []) if field['key'] == 'discorduseridnotyourusername'), None)
    subscription_id = session.get('subscription')  # Retrieve the subscription ID

    try:
        with session_scope('dj') as db_session:
            user = db_session.query(User).filter_by(stripe_subscription_id=subscription_id).first()
            if user:
                # Finalize setup with all available details
                user.discord_id = discord_id
                user.email = customer_email
                user.subscription_start_date = datetime.now(timezone.utc)
                logging.info(f"Finalized setup for user {discord_id} with subscription ID {subscription_id}.")
            else:
                # Handle the rare case where no user row exists; create a new row as a backup
                new_user = User(
                    discord_id=discord_id,
                    stripe_subscription_id=subscription_id,
                    email=customer_email,
                    active_subscription=True,
                    subscription_start_date=datetime.now(timezone.utc)
                )
                db_session.add(new_user)
                logging.info(f"Added new user on completed session for subscription ID {subscription_id}")
    except Exception as e:
        logging.error(f"Error updating DJ database during checkout session: {e}")

def handle_dj_subscription_update(subscription_id, status, metadata):
    discord_id = metadata.get('discorduseridnotyourusername')
    logging.info(f"Updating DJ subscription {subscription_id} for user {discord_id} with status {status}")

    try:
        with session_scope('dj') as db_session:
            user = db_session.query(User).filter_by(stripe_subscription_id=subscription_id).first()
            if user:
                # Update status and ensure it reflects the correct active state
                user.active_subscription = (status == 'active')
                if discord_id:
                    user.discord_id = discord_id
                logging.info(f"Updated existing user for subscription ID {subscription_id}")
            else:
                # Create a new user with limited details
                new_user = User(
                    discord_id=discord_id if discord_id else None,
                    stripe_subscription_id=subscription_id,
                    active_subscription=(status == 'active')
                )
                db_session.add(new_user)
                logging.info(f"Created new user for subscription ID {subscription_id}")
    except Exception as e:
        logging.error(f"Error updating DJ database for subscription update: {e}")

def handle_verification_subscription_update(subscription_id, status, metadata):
    guild_id = metadata.get('guild_id')
    logging.info(f"Updating verification subscription {subscription_id} for guild {guild_id} with status {status}")

    if not guild_id:
        logging.error("Guild ID not found in metadata.")
        return

    try:
        with session_scope('verification') as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()
            if server:
                server.subscription_status = (status == 'active')
                server.stripe_subscription_id = subscription_id
                logging.info(f"Updated verification guild {guild_id} with active status: {server.subscription_status}")
    except Exception as e:
        logging.error(f"Error updating verification database for subscription update: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5433)