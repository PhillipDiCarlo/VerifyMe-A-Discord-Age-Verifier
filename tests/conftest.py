"""
Test isolation safety net.

INCIDENT (2026-07-18): a test fixture ran DELETE against the real VRCVerify
production database because the test process loaded the developer's real
.env file, which points DATABASE_URL_VRCVERIFY at a live, reachable Postgres
host. python-dotenv's load_dotenv() does not override variables that are
already set in the environment, so setting this here -- before any src/
module is imported -- guarantees the service binds to an isolated
in-memory sqlite database for the whole test session, regardless of what a
developer's .env contains.

This repo is scoped to verify_me_database only (DATABASE_URL_VERIFICATION);
DJ and VRCVerify no longer bill through Stripe/this repo and no code here
reads DATABASE_URL_DJ or DATABASE_URL_VRCVERIFY at all anymore, so there's
nothing else to isolate.

pytest always imports conftest.py before collecting test modules, so this
runs first.
"""
import os
import sys

os.environ["DATABASE_URL_VERIFICATION"] = "sqlite:///:memory:"

# Also keep RabbitMQ pointed somewhere inert. No current test connects to a
# real broker (pika.BlockingConnection is always mocked), but this closes
# the door on a future test doing so by accident.
os.environ.setdefault("RABBITMQ_HOST", "invalid.test.local")

# Deterministic Fernet key so DOB encrypt/decrypt round-trips in tests work
# regardless of (and without needing) the developer's real .env. Set before
# any src import — load_dotenv() does not override existing env vars.
from cryptography.fernet import Fernet  # noqa: E402
os.environ["DOB_KEY"] = Fernet.generate_key().decode()

# The services hard-require these at import (bot.py's required_env_vars
# check, stripe_webhook_service's STRIPE_WEBHOOK_SECRET guard and
# int(RABBITMQ_PORT) cast); give inert values so the test suite runs on
# machines/CI without a populated .env.
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("STRIPE_RESTRICTED_SECRET_KEY", "rk_test_dummy")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
os.environ.setdefault("STRIPE_PAYMENT_WEBHOOK_SECRET", "whsec_test_dummy")
os.environ.setdefault("RABBITMQ_PORT", "5672")
os.environ.setdefault("RABBITMQ_USERNAME", "test")
os.environ.setdefault("RABBITMQ_PASSWORD", "test")
os.environ.setdefault("RABBITMQ_VHOST", "/")
os.environ.setdefault("RABBITMQ_QUEUE_NAME", "test_queue")

# Make both import styles used across test files work regardless of how
# pytest is invoked: "import models" needs src/ on the path, and
# "import src.bot" needs the repo root. (`python -m pytest` adds the CWD
# to sys.path but a bare `pytest` — e.g. in CI — does not.)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

# Services no longer run create_all at import (schema is managed by Alembic
# in real deployments), so create the tables on the shared test engine here.
import models  # noqa: E402  (must come after the env override above)

# Test modules import services both as plain modules ("import subscription_checker")
# and as package submodules ("import src.subscription_manager"). Without this
# alias, Python would create two separate module objects for models.py, each
# with its own engine and its own in-memory sqlite database. Pin one identity
# so every service — however imported — shares the same test database.
sys.modules.setdefault("src.models", models)

# Same dual-identity pinning for the shared billing module, so patching
# billing.SKU_ID_TO_* in a test also affects what src.bot sees.
import billing  # noqa: E402
sys.modules.setdefault("src.billing", billing)

models.init_db()


def pytest_runtest_setup(item):
    """Hard safety net, re-checked before every single test: if any
    already-imported service module ended up bound to a non-sqlite database
    engine -- for any reason, including future code changes that bypass the
    override above -- abort immediately instead of risking a write to a
    real database.
    """
    for modname in ("models", "subscription_manager", "subscription_checker", "bot", "stripe_webhook_service"):
        mod = sys.modules.get(modname) or sys.modules.get(f"src.{modname}")
        if mod is None:
            continue
        for attr_name in dir(mod):
            if not attr_name.startswith("engine"):
                continue
            engine = getattr(mod, attr_name, None)
            url = getattr(engine, "url", None)
            if url is not None and "sqlite" not in str(url):
                raise RuntimeError(
                    f"UNSAFE TEST ENVIRONMENT: {modname}.{attr_name} is bound to a "
                    f"non-sqlite database ({url}). Refusing to run '{item.nodeid}' "
                    f"to prevent touching a real database."
                )
