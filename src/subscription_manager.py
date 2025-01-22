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
from datetime import datetime, timezone

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

# ------------------------
#  VERIFICATION SERVICE
# ------------------------
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
    last_renewal_date = Column(DateTime(timezone=True), nullable=True)

# ------------------------
#  DJ SERVICE
# ------------------------
class User(BaseDJ):
    __tablename__ = 'users'
    stripe_subscription_id = Column(String(255), primary_key=True)  # Set as primary key
    discord_id = Column(String(50), nullable=True)
    active_subscription = Column(Boolean, default=False)
    email = Column(String(255), nullable=True)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)
    last_renewal_date = Column(DateTime(timezone=True), nullable=True)

# Create tables (or run migrations in production!)
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
    'prod_Ra9LidflO2dgt0': {'tier': 'tier_1', 'tokens': 10},
    'prod_Ra9LxBfXnAUz8o': {'tier': 'tier_2', 'tokens': 25},
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
    """
    Handles Stripe webhook events in a separate thread.
    """
    try:
        event_type = event['type']

        # 1) Checkout Session Completed
        if event_type == 'checkout.session.completed':
            session_obj = event['data']['object']
            subscription_id = session_obj.get('subscription')

            product_id = None
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)
                product_id = subscription['items']['data'][0]['price']['product']

            # DJ Bot
            if product_id == PRODUCT_ID_DJ:
                handle_dj_checkout_session(session_obj)

            # Verification Service
            elif product_id in PRODUCT_ID_TO_TIER:
                handle_verification_checkout_session(session_obj)

        # 2) Subscription Created
        elif event_type == 'customer.subscription.created':
            subscription = event['data']['object']
            product_id = subscription['items']['data'][0]['price']['product']
            subscription_id = subscription['id']
            status = subscription['status']
            metadata = subscription.get('metadata', {})
            current_period_start = subscription.get('current_period_start')

            # Convert Unix timestamp to datetime
            if current_period_start:
                current_period_start_dt = datetime.utcfromtimestamp(current_period_start).replace(tzinfo=timezone.utc)
            else:
                current_period_start_dt = None

            # DJ
            if product_id == PRODUCT_ID_DJ:
                handle_dj_subscription_created(subscription_id, status, metadata, current_period_start_dt)
            # Verification
            elif product_id in PRODUCT_ID_TO_TIER:
                handle_verification_subscription_created(subscription_id, status, metadata, current_period_start_dt)

        # 3) Subscription Updated
        elif event_type == 'customer.subscription.updated':
            subscription = event['data']['object']
            product_id = subscription['items']['data'][0]['price']['product']
            subscription_id = subscription['id']
            status = subscription['status']
            metadata = subscription.get('metadata', {})
            current_period_start = subscription.get('current_period_start')

            # Convert Unix timestamp to datetime
            if current_period_start:
                current_period_start_dt = datetime.utcfromtimestamp(current_period_start).replace(tzinfo=timezone.utc)
            else:
                current_period_start_dt = None

            if product_id == PRODUCT_ID_DJ:
                handle_dj_subscription_update(subscription_id, status, metadata, current_period_start_dt)
            elif product_id in PRODUCT_ID_TO_TIER:
                handle_verification_subscription_update(subscription_id, status, metadata, current_period_start_dt)

        # 4) Subscription Deleted
        elif event_type == 'customer.subscription.deleted':
            subscription = event['data']['object']
            product_id = subscription['items']['data'][0]['price']['product']
            subscription_id = subscription['id']
            metadata = subscription.get('metadata', {})

            if product_id == PRODUCT_ID_DJ:
                handle_dj_subscription_deleted(subscription_id, metadata)
            elif product_id in PRODUCT_ID_TO_TIER:
                handle_verification_subscription_deleted(subscription_id, metadata)

    except Exception as e:
        logging.error(f"Error processing event: {e}")


# -------------------------------------------------------
#    HANDLERS FOR VERIFICATION SERVICE (SERVER MODEL)
# -------------------------------------------------------
def handle_verification_checkout_session(session):
    """
    Called when checkout.session.completed fires for a new subscription
    to the verification service.
    """
    logging.info("Handling checkout.session.completed event for Verification Service")
    customer_email = session['customer_details'].get('email')
    custom_fields = session.get('custom_fields', [])

    guild_id = next(
        (
            field['text']['value']
            for field in custom_fields
            if field['key'] in ['discordserverid', 'discordserveridnotyourservername', 'discordserveridnotservername']
        ),
        None
    )
    discord_id = next(
        (
            field['text']['value']
            for field in custom_fields
            if field['key'] == 'discorduseridnotyourusername'
        ),
        None
    )
    subscription_id = session.get('subscription')

    line_items = stripe.checkout.Session.list_line_items(session['id'])
    product_id = line_items['data'][0]['price']['product']
    tier_info = PRODUCT_ID_TO_TIER.get(product_id)

    if not guild_id or not discord_id or not tier_info:
        logging.error("Missing guild_id, discord_id, or tier info.")
        return

    try:
        extra_tokens = PRODUCT_ID_TO_EXTRA_TOKENS.get(product_id, 0)
        with session_scope('verification') as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()

            if server:
                server.tier = tier_info['tier']
                server.subscription_status = True
                server.verifications_count += tier_info['tokens']
                server.subscription_start_date = datetime.now(timezone.utc)
                server.stripe_subscription_id = subscription_id
                server.role_id = session['metadata'].get('role_id')
                server.email = customer_email
                # Initialize last_renewal_date to the same as subscription_start_date
                server.last_renewal_date = server.subscription_start_date
            else:
                new_server = Server(
                    server_id=guild_id,
                    owner_id=discord_id,
                    tier=tier_info['tier'],
                    subscription_status=True,
                    verifications_count=tier_info['tokens'],
                    subscription_start_date=datetime.now(timezone.utc),
                    stripe_subscription_id=subscription_id,
                    role_id=session['metadata'].get('role_id'),
                    email=customer_email,
                    last_renewal_date=datetime.now(timezone.utc)
                )
                db_session.add(new_server)

            # If product was a one-time token purchase
            if extra_tokens and server:
                server.verifications_count += extra_tokens

        logging.info(f"Updated verification subscription for guild {guild_id}.")

    except Exception as e:
        logging.error(f"Error updating verification database: {e}")


def handle_verification_subscription_created(subscription_id, status, metadata, current_period_start_dt):
    """
    Called when customer.subscription.created is triggered.
    Sets subscription active, sets tier, and initializes last_renewal_date.
    """
    guild_id = metadata.get('guild_id')
    if not guild_id:
        logging.warning("No guild_id in metadata for verification subscription creation.")
        return

    product_id = None
    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
        product_id = subscription['items']['data'][0]['price']['product']
    except Exception as e:
        logging.error(f"Unable to retrieve subscription {subscription_id}. Error: {e}")
        return

    tier_info = PRODUCT_ID_TO_TIER.get(product_id)
    if not tier_info:
        logging.warning(f"No tier info for product {product_id}")
        return

    logging.info(f"Creating Verification subscription for guild {guild_id} with product {product_id}.")

    try:
        with session_scope('verification') as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()
            if server:
                server.subscription_status = True
                server.tier = tier_info['tier']
                if current_period_start_dt:
                    server.last_renewal_date = current_period_start_dt
                # We preserve subscription_start_date for analytics; if empty, initialize it now
                if not server.subscription_start_date:
                    server.subscription_start_date = current_period_start_dt or datetime.now(timezone.utc)
                server.stripe_subscription_id = subscription_id
            else:
                new_server = Server(
                    server_id=guild_id,
                    owner_id=metadata.get('discorduseridnotyourusername', 'UNKNOWN'),
                    tier=tier_info['tier'],
                    subscription_status=True,
                    verifications_count=tier_info['tokens'],
                    subscription_start_date=current_period_start_dt or datetime.now(timezone.utc),
                    stripe_subscription_id=subscription_id,
                    email=metadata.get('email'),
                    last_renewal_date=current_period_start_dt or datetime.now(timezone.utc)
                )
                db_session.add(new_server)

    except Exception as e:
        logging.error(f"Error creating verification subscription in DB: {e}")


def handle_verification_subscription_update(subscription_id, status, metadata, current_period_start_dt):
    """
    Called when a subscription is updated (e.g., tier change, renewal).
    """
    guild_id = metadata.get('guild_id')
    logging.info(f"Updating verification subscription {subscription_id} for guild {guild_id} with status {status}")

    if not guild_id:
        logging.error("Guild ID not found in metadata for verification subscription update.")
        return

    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
        product_id = subscription['items']['data'][0]['price']['product']
        tier_info = PRODUCT_ID_TO_TIER.get(product_id)

        with session_scope('verification') as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()
            if server:
                server.subscription_status = (status == 'active')

                # If there's a tier change, update it
                if tier_info:
                    server.tier = tier_info['tier']

                # Update last_renewal_date if we have a new current_period_start
                if current_period_start_dt:
                    server.last_renewal_date = current_period_start_dt

                # Keep subscription_start_date for analytics, no immediate changes
                server.stripe_subscription_id = subscription_id

                logging.info(f"Updated verification guild {guild_id}, active: {server.subscription_status}, tier: {server.tier}")

    except Exception as e:
        logging.error(f"Error updating verification database for subscription update: {e}")


def handle_verification_subscription_deleted(subscription_id, metadata):
    """
    Called when Stripe deletes/cancels a subscription immediately (or at period end).
    We mark subscription_status as inactive, but keep last_renewal_date for history.
    """
    logging.info(f"Deleting verification subscription {subscription_id}.")

    try:
        with session_scope('verification') as db_session:
            server = db_session.query(Server).filter_by(stripe_subscription_id=subscription_id).first()
            if server:
                server.subscription_status = False
                # We do NOT reset last_renewal_date, we keep it for historical reference
                logging.info(f"Verification subscription marked inactive for subscription_id={subscription_id}.")
            else:
                # If no server is found, log a warning or error
                logging.warning(f"No server found matching stripe_subscription_id={subscription_id}.")
    except Exception as e:
        logging.error(f"Error handling verification subscription deletion: {e}")


# -------------------------------------------------------
#       HANDLERS FOR DJ SERVICE (USER MODEL)
# -------------------------------------------------------
def handle_dj_checkout_session(session):
    """
    Called when checkout.session.completed for the DJ product.
    """
    logging.info("Handling DJ checkout session...")
    customer_email = session['customer_details'].get('email')
    discord_id = next(
        (field['text']['value'] for field in session.get('custom_fields', [])
         if field['key'] == 'discorduseridnotyourusername'),
        None
    )
    subscription_id = session.get('subscription')

    try:
        with session_scope('dj') as db_session:
            user = db_session.query(User).filter_by(stripe_subscription_id=subscription_id).first()
            if user:
                user.discord_id = discord_id
                user.email = customer_email
                user.subscription_start_date = datetime.now(timezone.utc)
                user.active_subscription = True
                # Initialize last_renewal_date to the same as subscription_start_date
                user.last_renewal_date = user.subscription_start_date
                logging.info(f"Finalized setup for user {discord_id} with subscription ID {subscription_id}.")
            else:
                new_user = User(
                    discord_id=discord_id,
                    stripe_subscription_id=subscription_id,
                    email=customer_email,
                    active_subscription=True,
                    subscription_start_date=datetime.now(timezone.utc),
                    last_renewal_date=datetime.now(timezone.utc)
                )
                db_session.add(new_user)
                logging.info(f"Added new user on completed session for subscription ID {subscription_id}")
    except Exception as e:
        logging.error(f"Error updating DJ database during checkout session: {e}")

def handle_dj_subscription_created(subscription_id, status, metadata, current_period_start_dt):
    """
    Called when customer.subscription.created is triggered for the DJ service.
    """
    discord_id = metadata.get('discorduseridnotyourusername', None)
    logging.info(f"[DJ] Subscription created: {subscription_id}, user {discord_id}")

    try:
        with session_scope('dj') as db_session:
            user = db_session.query(User).filter_by(stripe_subscription_id=subscription_id).first()
            if user:
                user.active_subscription = True
                user.discord_id = discord_id or user.discord_id
                if not user.subscription_start_date:
                    user.subscription_start_date = current_period_start_dt or datetime.now(timezone.utc)
                user.last_renewal_date = current_period_start_dt or datetime.now(timezone.utc)
            else:
                new_user = User(
                    stripe_subscription_id=subscription_id,
                    discord_id=discord_id,
                    active_subscription=True,
                    subscription_start_date=current_period_start_dt or datetime.now(timezone.utc),
                    last_renewal_date=current_period_start_dt or datetime.now(timezone.utc)
                )
                db_session.add(new_user)
    except Exception as e:
        logging.error(f"Error handling DJ subscription creation in DB: {e}")

def handle_dj_subscription_update(subscription_id, status, metadata, current_period_start_dt):
    """
    Handles updates to the DJ subscription (renewals, tier changes, etc.).
    """
    discord_id = metadata.get('discorduseridnotyourusername', None)
    logging.info(f"Updating DJ subscription {subscription_id} for user {discord_id} with status {status}")

    try:
        with session_scope('dj') as db_session:
            user = db_session.query(User).filter_by(stripe_subscription_id=subscription_id).first()
            if user:
                # Active if status is 'active'; let Stripe handle grace periods
                user.active_subscription = (status == 'active')
                if discord_id:
                    user.discord_id = discord_id

                # Update last_renewal_date if we have a new current_period_start
                if current_period_start_dt:
                    user.last_renewal_date = current_period_start_dt

                logging.info(f"Updated existing user for subscription ID {subscription_id}, active={user.active_subscription}")
            else:
                new_user = User(
                    discord_id=discord_id,
                    stripe_subscription_id=subscription_id,
                    active_subscription=(status == 'active'),
                    subscription_start_date=current_period_start_dt or datetime.now(timezone.utc),
                    last_renewal_date=current_period_start_dt or datetime.now(timezone.utc)
                )
                db_session.add(new_user)
                logging.info(f"Created new user for subscription ID {subscription_id}")

    except Exception as e:
        logging.error(f"Error updating DJ database for subscription update: {e}")

def handle_dj_subscription_deleted(subscription_id, metadata):
    """
    Marks a DJ subscription as inactive (immediately) but preserves last_renewal_date.
    """
    discord_id = metadata.get('discorduseridnotyourusername', None)
    logging.info(f"[DJ] Subscription deleted: {subscription_id} for user {discord_id}")

    try:
        with session_scope('dj') as db_session:
            user = db_session.query(User).filter_by(stripe_subscription_id=subscription_id).first()
            if user:
                user.active_subscription = False
                logging.info(f"Marked DJ subscription {subscription_id} inactive.")
    except Exception as e:
        logging.error(f"Error handling DJ subscription deletion: {e}")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5433)
