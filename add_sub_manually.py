from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Retrieve environment variables
DATABASE_URL = os.getenv('DATABASE_URL')

# Database setup
engine = create_engine(DATABASE_URL)
metadata = MetaData()
Session = sessionmaker(bind=engine)
db_session = Session()

# Define the servers table (same as in your main bot script)
servers = Table(
    'servers', metadata,
    Column('id', Integer, primary_key=True),
    Column('server_id', String(30), nullable=False, unique=True),
    Column('owner_id', String(30), nullable=False),
    Column('role_id', String(30), nullable=False),
    Column('tier', String(1), default='A'),
    Column('subscription_status', Boolean, default=False)
)

# Add your server to the subscription list
def add_subscription(server_id, owner_id, role_id, tier='A'):
    db_session.execute(servers.insert().values(
        server_id=server_id,
        owner_id=owner_id,
        role_id=role_id,
        tier=tier,
        subscription_status=True
    ))
    db_session.commit()
    print(f"Server {server_id} added to subscription list with tier {tier}.")

# Replace with your actual server ID, owner ID, and role ID
add_subscription('956841761035157604', '149033260016467968', '1255705679671595008', 'A')



-- Set default value for the `tier` column
ALTER TABLE servers ALTER COLUMN tier SET DEFAULT 'tier_A';

-- Ensure all existing entries have a valid tier
UPDATE servers
SET tier = 'tier_A'
WHERE tier IS NULL OR tier NOT IN ('tier_A', 'tier_B', 'tier_C', 'tier_D', 'tier_E');
