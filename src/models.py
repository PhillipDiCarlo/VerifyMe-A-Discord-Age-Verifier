"""Single source of truth for VerifyMe's database schema and connection.

Every service imports its models, engine, and session handling from here.
Do not redefine these models in a service module — the three divergent
copies that used to exist (bot.py, stripe_webhook_service.py,
subscription_manager.py) disagreed on column lengths and nullability, and
whichever service started first won.

This repo is scoped to verify_me_database ONLY (DATABASE_URL_VERIFICATION).
Never add an engine for any other database here.

Schema changes are managed with Alembic (alembic/ at the repo root), not
create_all at import time. init_db() exists for tests and local sqlite use.
"""
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL_VERIFICATION')
if not DATABASE_URL:
    raise EnvironmentError("DATABASE_URL_VERIFICATION is not set")

engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String(50), nullable=False)
    verification_status = Column(Boolean, default=False)
    last_verification_attempt = Column(DateTime(timezone=True), nullable=True)
    dob = Column(String(255), nullable=True)  # Fernet-encrypted date of birth

    @staticmethod
    def get_current_time():
        return datetime.now(timezone.utc)

    def set_verification_attempt(self):
        self.last_verification_attempt = self.get_current_time()


class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    server_id = Column(String(30), unique=True, nullable=False)
    owner_id = Column(String(30), nullable=False)
    role_id = Column(String(30), nullable=True)
    tier = Column(String(50), nullable=True)
    subscription_status = Column(Boolean, default=False)
    verifications_count = Column(Integer, default=0)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    minimum_age = Column(Integer, nullable=False, default=18)
    email = Column(String(255), nullable=True)
    last_renewal_date = Column(DateTime(timezone=True), nullable=True)
    instructions_channel_id = Column(String(30), nullable=True)
    instructions_message_id = Column(String(30), nullable=True)


class CommandUsage(Base):
    __tablename__ = 'command_usage'
    id = Column(Integer, primary_key=True)
    server_id = Column(String(30), nullable=False)
    user_id = Column(String(30), nullable=False)
    command = Column(String(50), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)


def init_db():
    """Create all tables on the current engine.

    For tests and throwaway local databases only — real deployments manage
    schema through Alembic migrations.
    """
    Base.metadata.create_all(engine)
