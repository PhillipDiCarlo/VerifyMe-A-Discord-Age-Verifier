import os
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from contextlib import contextmanager

# -------------------------------------------------------------------
#  Load environment variables and set up logging
# -------------------------------------------------------------------
load_dotenv()
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# -------------------------------------------------------------------
#  Verification DB Setup
# -------------------------------------------------------------------
DATABASE_URL_VERIFICATION = os.getenv('DATABASE_URL_VERIFICATION')
engine_verification = create_engine(DATABASE_URL_VERIFICATION)
BaseVerification = declarative_base()
SessionVerification = sessionmaker(bind=engine_verification)

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
    # Must exist in your actual DB via migration:
    last_renewal_date = Column(DateTime(timezone=True), nullable=True)

BaseVerification.metadata.create_all(engine_verification)

@contextmanager
def session_scope_verification():
    """Context manager for the Verification DB session."""
    session = SessionVerification()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logging.error(f"[Verification DB] Error during session scope: {e}")
        raise
    finally:
        session.close()


# -------------------------------------------------------------------
#  DJ DB Setup
# -------------------------------------------------------------------
DATABASE_URL_DJ = os.getenv('DATABASE_URL_DJ')
engine_dj = create_engine(DATABASE_URL_DJ)
BaseDJ = declarative_base()
SessionDJ = sessionmaker(bind=engine_dj)

class User(BaseDJ):
    __tablename__ = 'users'
    stripe_subscription_id = Column(String(255), primary_key=True)
    discord_id = Column(String(50), nullable=True)
    active_subscription = Column(Boolean, default=False)
    email = Column(String(255), nullable=True)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)
    # Must exist in your actual DB via migration:
    last_renewal_date = Column(DateTime(timezone=True), nullable=True)

BaseDJ.metadata.create_all(engine_dj)

@contextmanager
def session_scope_dj():
    """Context manager for the DJ DB session."""
    session = SessionDJ()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logging.error(f"[DJ DB] Error during session scope: {e}")
        raise
    finally:
        session.close()

# -------------------------------------------------------------------
#  Weekly Fallback Check
# -------------------------------------------------------------------
def check_subscriptions():
    """
    Runs once a week (Sunday 23:59) as a fallback.
    1) Checks servers in the Verification DB for lapses using last_renewal_date.
    2) Checks users in the DJ DB for lapses (active_subscription + last_renewal_date).
    """
    logging.info("[CHECKER] Starting weekly subscription check...")

    # We define "lapsed" as last_renewal_date older than 31 days, for example.
    now = datetime.now(timezone.utc)
    one_month_ago = now - timedelta(days=31)

    # ----- 1) Verification DB -----
    try:
        with session_scope_verification() as db_session_v:
            servers = db_session_v.query(Server).filter(
                Server.subscription_status == True,
                Server.last_renewal_date <= one_month_ago
            ).all()

            for server in servers:
                logging.info(
                    f"[VERIFY_DB] Marking server {server.server_id} as inactive (lapsed)."
                )
                server.subscription_status = False

    except Exception as e:
        logging.error(f"[VERIFY_DB] Error checking servers: {e}")

    # ----- 2) DJ DB -----
    try:
        with session_scope_dj() as db_session_dj:
            dj_users = db_session_dj.query(User).filter(
                User.active_subscription == True,
                User.last_renewal_date <= one_month_ago
            ).all()

            for user in dj_users:
                logging.info(
                    f"[DJ_DB] Marking user {user.discord_id} (sub={user.stripe_subscription_id}) as inactive (lapsed)."
                )
                user.active_subscription = False

    except Exception as e:
        logging.error(f"[DJ_DB] Error checking users: {e}")

    logging.info("[CHECKER] Weekly check completed.")

# -------------------------------------------------------------------
#  Main (Schedule the Weekly Check)
# -------------------------------------------------------------------
if __name__ == '__main__':
    scheduler = BackgroundScheduler()
    # Runs every Sunday at 23:59
    scheduler.add_job(
        check_subscriptions,
        'cron',
        day_of_week='sun',
        hour=23,
        minute=59
    )

    scheduler.start()
    logging.info("Subscription checker started. Press Ctrl+C to exit.")

    try:
        while True:
            pass
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logging.info("Subscription checker stopped.")
