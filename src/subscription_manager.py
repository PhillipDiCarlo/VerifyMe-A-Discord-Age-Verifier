import os
import stripe
import threading
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import logging
from datetime import datetime, timezone

try:
    from .models import Server, session_scope
    from .billing import apply_tier
except ImportError:
    from models import Server, session_scope
    from billing import apply_tier

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Stripe configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
endpoint_secret = os.getenv('STRIPE_PAYMENT_WEBHOOK_SECRET')

# Logging setup
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Product IDs for Routing
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

    try:
        # Do not log the raw payload — it contains customer emails and billing details
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        logging.info(f"Received Stripe webhook event: {event['type']}")
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
    Handles Stripe webhook events for the VerifyMe verification service.
    """
    try:
        event_type = event['type']

        # 1) Checkout Session Completed
        if event_type == 'checkout.session.completed':
            session_obj = event['data']['object']
            subscription_id = session_obj.get('subscription')
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)
                product_id = subscription['items']['data'][0]['price']['product']

                if product_id in PRODUCT_ID_TO_TIER:
                    handle_verification_checkout_session(session_obj)

        elif event_type == 'customer.subscription.created':
            subscription = event['data']['object']
            product_id = subscription['items']['data'][0]['price']['product']
            subscription_id = subscription['id']
            status = subscription['status']
            metadata = subscription.get('metadata', {})
            current_period_start = subscription.get('current_period_start')

            # Convert Unix timestamp to datetime
            current_period_start_dt = None
            if current_period_start:
                current_period_start_dt = datetime.fromtimestamp(current_period_start, tz=timezone.utc)

            if product_id in PRODUCT_ID_TO_TIER:
                handle_verification_subscription_created(subscription_id, status, metadata, current_period_start_dt)

        elif event_type == 'customer.subscription.updated':
            subscription = event['data']['object']
            product_id = subscription['items']['data'][0]['price']['product']
            subscription_id = subscription['id']
            status = subscription['status']
            metadata = subscription.get('metadata', {})
            current_period_start = subscription.get('current_period_start')
            # Convert Unix timestamp to datetime
            current_period_start_dt = None
            if current_period_start:
                current_period_start_dt = datetime.fromtimestamp(current_period_start, tz=timezone.utc)

            if product_id in PRODUCT_ID_TO_TIER:
                handle_verification_subscription_update(subscription_id, status, metadata, current_period_start_dt)

        elif event_type == 'customer.subscription.deleted':
            subscription = event['data']['object']
            product_id = subscription['items']['data'][0]['price']['product']
            subscription_id = subscription['id']
            metadata = subscription.get('metadata', {})

            if product_id in PRODUCT_ID_TO_TIER:
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
        with session_scope() as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()

            if server:
                server.tier = tier_info['tier']
                server.subscription_status = True
                server.verifications_count += tier_info['tokens']
                server.subscription_start_date = datetime.now(timezone.utc)
                server.stripe_subscription_id = subscription_id
                server.role_id = session['metadata'].get('role_id')
                server.email = customer_email
                server.payment_provider = 'stripe'
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
        with session_scope() as db_session:
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
                server.payment_provider = 'stripe'
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

        with session_scope() as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()
            if server:
                # Shared renewal/refill semantics (billing.apply_tier): on
                # each renewal the allowance resets to the tier amount.
                apply_tier(
                    server,
                    tier_info,
                    active=(status == 'active'),
                    period_start=current_period_start_dt,
                )

                # Keep subscription_start_date for analytics, no immediate changes
                server.stripe_subscription_id = subscription_id
                server.payment_provider = 'stripe'

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
        with session_scope() as db_session:
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5433)
