import os
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

try:
    from .models import Server, session_scope
except ImportError:
    from models import Server, session_scope

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
#  Weekly Fallback Check
# -------------------------------------------------------------------
def check_subscriptions():
    """
    Runs once a week (Sunday 23:59) as a fallback.
    Checks servers in the Verification DB for lapses using last_renewal_date.
    """
    logging.info("[CHECKER] Starting weekly subscription check...")

    # We define "lapsed" as last_renewal_date older than 31 days, for example.
    now = datetime.now(timezone.utc)
    one_month_ago = now - timedelta(days=31)

    try:
        with session_scope() as db_session_v:
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
        scheduler._event.wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logging.info("Subscription checker stopped.")
