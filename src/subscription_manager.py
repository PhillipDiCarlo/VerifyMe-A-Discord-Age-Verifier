import os
import json
import stripe
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
logging.basicConfig(level=logging.DEBUG)

# Mapping of product IDs to tiers
PRODUCT_ID_TO_TIER = {
    'prod_QXNi63ixsJYIke': 'tier_1', #10 tokens/month
    'prod_QXNldB600Dr8RX': 'tier_2', #25 tokens/month
    'prod_QXNnv5WYeieAGZ': 'tier_3', #50 tokens/month
    'prod_QXNpnlGsJn210K': 'tier_4', #75 tokens/month
    'prod_QXNrATAgXjN7Xi': 'tier_5', #100 tokens/month 
    'prod_QXNtfHzYQ2EhUx': 'tier_6', #150 tokens/month
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

    try:
        # Handle the event
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            handle_checkout_session(session)
        elif event['type'].startswith('subscription_schedule'):
            handle_subscription_schedule(event)
    except Exception as e:
        logging.error(f"Error handling webhook event: {e}")
        return jsonify({'error': 'Error handling event'}), 500

    return jsonify({'status': 'success'}), 200

def handle_checkout_session(session):
    logging.info("Handling checkout.session.completed event")
    customer_email = session['customer_details'].get('email')
    custom_fields = session.get('custom_fields', [])
    
    # Extract custom fields
    user_id = next((field['text']['value'] for field in custom_fields if field['key'] == 'discorduserid'), None)
    guild_id = next((field['text']['value'] for field in custom_fields if field['key'] == 'discordserverid'), None)
    subscription_id = session.get('subscription')

    # Fetch the session's line items
    try:
        line_items = stripe.checkout.Session.list_line_items(session['id'])
        product_id = line_items['data'][0]['price']['product']
        tier = PRODUCT_ID_TO_TIER.get(product_id)
    except Exception as e:
        logging.error(f"Error fetching line items: {e}")
        return

    if not all([user_id, guild_id, subscription_id, tier]):
        logging.error("Missing necessary metadata")
        return

    try:
        with session_scope() as db_session:
            server = db_session.query(Server).filter_by(server_id=guild_id).first()
            if server:
                server.owner_id = user_id
                server.tier = tier
                server.subscription_status = True
                server.subscription_start_date = datetime.now(timezone.utc)
                server.stripe_subscription_id = subscription_id
                server.email = customer_email
            else:
                server = Server(
                    server_id=guild_id,
                    owner_id=user_id,
                    tier=tier,
                    subscription_status=True,
                    verifications_count=0,
                    subscription_start_date = datetime.now(timezone.utc),
                    stripe_subscription_id=subscription_id,
                    email=customer_email
                )
                db_session.add(server)
            logging.info(f"Updated server {guild_id} with new subscription data")
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

            logging.info(f"Updated server {server.server_id} subscription status to {server.subscription_status}")
    except Exception as e:
        logging.error(f"Error updating database for subscription schedule: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5433, debug=True)
