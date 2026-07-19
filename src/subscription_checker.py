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
    Runs once a week (Sunday 23:59) as a fallback for missed webhook /
    gateway events. Lapse rules per payment provider:

    - stripe: last_renewal_date older than 31 days (webhooks normally
      renew it monthly).
    - discord: entitlement_ends_at more than ENTITLEMENT_GRACE_DAYS in the
      past (on_entitlement_update normally extends it each period). A NULL
      entitlement_ends_at (e.g. test entitlements) never lapses here.
    """
    logging.info("[CHECKER] Starting weekly subscription check...")

    now = datetime.now(timezone.utc)
    one_month_ago = now - timedelta(days=31)
    grace_days = int(os.getenv('ENTITLEMENT_GRACE_DAYS', '3'))
    entitlement_cutoff = now - timedelta(days=grace_days)

    try:
        with session_scope() as db_session_v:
            stripe_lapsed = db_session_v.query(Server).filter(
                Server.subscription_status == True,
                Server.payment_provider != 'discord',
                Server.last_renewal_date <= one_month_ago
            ).all()

            discord_lapsed = db_session_v.query(Server).filter(
                Server.subscription_status == True,
                Server.payment_provider == 'discord',
                Server.entitlement_ends_at != None,
                Server.entitlement_ends_at <= entitlement_cutoff
            ).all()

            for server in stripe_lapsed + discord_lapsed:
                logging.info(
                    f"[VERIFY_DB] Marking server {server.server_id} as inactive "
                    f"(lapsed, provider={server.payment_provider})."
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
