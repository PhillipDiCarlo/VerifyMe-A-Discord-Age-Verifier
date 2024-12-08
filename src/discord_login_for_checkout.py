import os
import json
import stripe
from datetime import datetime
from contextlib import contextmanager
from flask import Flask, redirect, request, session, jsonify, url_for
from requests_oauthlib import OAuth2Session
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# OAuth2 Config
DISCORD_CLIENT_ID = os.getenv('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
DISCORD_REDIRECT_URI = 'http://disc.esattotech.com:5433/discord-callback'
DISCORD_API_BASE_URL = 'https://discord.com/api'
DISCORD_AUTHORIZATION_BASE_URL = f'https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={DISCORD_REDIRECT_URI}&scope=identify+guilds'
DISCORD_TOKEN_URL = f'{DISCORD_API_BASE_URL}/oauth2/token'

# Stripe Config
STRIPE_API_KEY = os.getenv('STRIPE_API_KEY')
stripe.api_key = STRIPE_API_KEY

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL_VERIFICATION')
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

Base.metadata.create_all(engine)

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

# Setup logging
logging.basicConfig(level=logging.DEBUG)

@app.route('/login')
def login():
    discord = OAuth2Session(DISCORD_CLIENT_ID, redirect_uri=DISCORD_REDIRECT_URI, scope=['identify', 'guilds'])
    authorization_url, state = discord.authorization_url(DISCORD_AUTHORIZATION_BASE_URL)
    session['oauth_state'] = state
    logging.debug(f"OAuth state set: {state}")
    return redirect(authorization_url)

@app.route('/discord-callback')
def callback():
    if 'oauth_state' not in session:
        logging.error("oauth_state not found in session")
        return jsonify({'error': 'OAuth state not found in session.'}), 400

    discord = OAuth2Session(DISCORD_CLIENT_ID, state=session['oauth_state'], redirect_uri=DISCORD_REDIRECT_URI)
    try:
        token = discord.fetch_token(DISCORD_TOKEN_URL, client_secret=DISCORD_CLIENT_SECRET, authorization_response=request.url)
        session['oauth_token'] = token
        user = discord.get(f'{DISCORD_API_BASE_URL}/users/@me').json()
        guilds = discord.get(f'{DISCORD_API_BASE_URL}/users/@me/guilds').json()
        admin_guilds = [guild for guild in guilds if guild['permissions'] & 0x8]  # Filter guilds where the user is an admin

        return jsonify({'user': user, 'admin_guilds': admin_guilds})
    except Exception as e:
        logging.error(f"Error during OAuth callback: {e}")
        return jsonify({'error': 'OAuth callback failed.'}), 500

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout():
    data = request.json
    user_id = data.get('user_id')
    guild_id = data.get('guild_id')
    tier = data.get('tier')

    if not all([user_id, guild_id, tier]):
        return jsonify({'error': 'Missing required parameters'}), 400

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': tier,  # Use the price ID corresponding to the selected tier
                'quantity': 1,
            }],
            mode='subscription',
            metadata={
                'user_id': user_id,
                'guild_id': guild_id
            },
            success_url='http://disc.esattotech.com:5433/success',
            cancel_url='http://disc.esattotech.com:5433/cancel'
        )
        return jsonify({'id': session.id})
    except Exception as e:
        logging.error(f"Error creating Stripe checkout session: {str(e)}")
        return jsonify({'error': 'Failed to create checkout session'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5433, debug=True)
