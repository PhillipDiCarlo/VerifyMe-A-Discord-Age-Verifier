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


def pytest_runtest_setup(item):
    """Hard safety net, re-checked before every single test: if any
    already-imported service module ended up bound to a non-sqlite database
    engine -- for any reason, including future code changes that bypass the
    override above -- abort immediately instead of risking a write to a
    real database.
    """
    for modname in ("subscription_manager", "subscription_checker", "bot", "stripe_webhook_service"):
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
