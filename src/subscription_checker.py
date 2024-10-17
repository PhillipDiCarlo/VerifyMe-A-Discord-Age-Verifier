import os
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from contextlib import contextmanager

# Load environment variables
load_dotenv()

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

def check_subscriptions():
    logging.info("Checking subscriptions for lapses...")
    try:
        with session_scope() as db_session:
            now = datetime.now(timezone.utc)
            one_month_ago = now - timedelta(days=31)  # One month + 1 day ago
            servers = db_session.query(Server).filter(Server.subscription_status == True, Server.subscription_start_date <= one_month_ago).all()
            
            for server in servers:
                logging.info(f"Subscription lapsed for server {server.server_id}. Setting subscription status to false.")
                server.subscription_status = False
    except Exception as e:
        logging.error(f"Error checking subscriptions: {e}")

if __name__ == '__main__':
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_subscriptions, 'interval', days=1)
    scheduler.start()
    logging.info("Subscription checker started. Press Ctrl+C to exit.")

    try:
        # Keep the script running
        while True:
            pass
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logging.info("Subscription checker stopped.")
