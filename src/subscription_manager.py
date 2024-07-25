import os
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Retrieve environment variables
DATABASE_URL = os.getenv('DATABASE_URL')

# Ensure all required environment variables are set
required_env_vars = ['DATABASE_URL']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Database setup
engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)

# Define SQLAlchemy models
class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    server_id = Column(String(30), nullable=False, unique=True)
    owner_id = Column(String(30), nullable=False)
    role_id = Column(String(30), nullable=False)
    tier = Column(String(50), default='tier_1', nullable=False)
    subscription_status = Column(Boolean, default=False)
    verifications_count = Column(Integer, default=0)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)

# Create tables
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

def reset_verifications_and_check_subscriptions():
    logger.info("Starting the monthly subscription check and reset process")
    with session_scope() as session:
        servers = session.query(Server).all()
        for server in servers:
            if server.subscription_status:
                if server.subscription_start_date:
                    next_reset_date = server.subscription_start_date + timedelta(days=30)
                    if datetime.now() >= next_reset_date:
                        logger.info(f"Resetting verifications count for server {server.server_id}")
                        server.verifications_count = 0
                        server.subscription_start_date = next_reset_date
                else:
                    logger.warning(f"Server {server.server_id} does not have a subscription start date. Setting it to current date.")
                    server.subscription_start_date = datetime.now()

                # Check if the subscription is still active (this is a placeholder for actual subscription validation logic)
                # Replace this with actual logic to check subscription status from your payment processor
                if not is_subscription_active(server):
                    logger.info(f"Setting subscription status to inactive for server {server.server_id}")
                    server.subscription_status = False

def is_subscription_active(server):
    # Placeholder for actual subscription validation logic
    # Implement your logic here to check if the server's subscription is still active
    return True

if __name__ == "__main__":
    reset_verifications_and_check_subscriptions()
