import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
import models
import src.subscription_manager as subscription_manager
from src.subscription_manager import (
    Server,
    PRODUCT_ID_TO_TIER,
    process_event,
)

@pytest.fixture(scope="function")
def mock_external_systems():
    with patch("src.subscription_manager.stripe.Webhook.construct_event") as mock_webhook, \
         patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve:
        mock_webhook.return_value = {"type": "checkout.session.completed"}
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": "prod_test"}}]}}
        yield {"mock_webhook": mock_webhook, "mock_retrieve": mock_retrieve}

def test_stripe_webhook(mock_external_systems):
    client = subscription_manager.app.test_client()
    response = client.post("/stripe-webhook", json={"id": "test_id"}, headers={"Stripe-Signature": "test_signature"})
    assert response.status_code == 200


# ---------------------------------------------------------------
# Verification (VerifyMe) billing handlers.
#
# This service is scoped to the verification database only -- the DJ and
# VRCVerify products no longer bill through Stripe/this repo, so there is
# nothing to route to besides PRODUCT_ID_TO_TIER.
# ---------------------------------------------------------------

@pytest.fixture(scope="function")
def verification_db():
    """Provide a clean verification DB session per test."""
    session = models.Session()
    session.query(Server).delete()
    session.commit()
    yield session
    session.query(Server).delete()
    session.commit()
    session.close()


def any_tier_product_id():
    return next(iter(PRODUCT_ID_TO_TIER))


def test_checkout_routing_ignores_unknown_product():
    """A product ID that isn't in PRODUCT_ID_TO_TIER must not be processed."""
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"subscription": "sub_route_1"}},
    }

    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve, \
         patch("src.subscription_manager.handle_verification_checkout_session") as mock_handler:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": "prod_totally_unrelated"}}]}}
        process_event(event)
        mock_handler.assert_not_called()


def test_checkout_routing_verification_product():
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"subscription": "sub_route_2"}},
    }

    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve, \
         patch("src.subscription_manager.handle_verification_checkout_session") as mock_handler:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": any_tier_product_id()}}]}}
        process_event(event)
        mock_handler.assert_called_once()


def _tier_product_and_tokens():
    product_id = next(pid for pid, info in PRODUCT_ID_TO_TIER.items() if info["tokens"] > 0)
    return product_id, PRODUCT_ID_TO_TIER[product_id]["tokens"]


def test_renewal_resets_tokens_to_tier_amount(verification_db):
    """On renewal (billing period advanced), verifications_count resets to the
    tier amount — no rollover of unused tokens."""
    from datetime import datetime, timezone, timedelta

    product_id, tier_tokens = _tier_product_and_tokens()
    old_renewal = datetime(2026, 6, 1, tzinfo=timezone.utc)
    verification_db.add(Server(server_id="900", owner_id="901", subscription_status=True,
                               stripe_subscription_id="sub_renew_1", verifications_count=3,
                               last_renewal_date=old_renewal))
    verification_db.commit()

    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": product_id}}]}}
        subscription_manager.handle_verification_subscription_update(
            "sub_renew_1", "active", {"guild_id": "900"},
            old_renewal + timedelta(days=31),
        )

    verification_db.expire_all()
    server = verification_db.query(Server).filter_by(server_id="900").first()
    assert server.verifications_count == tier_tokens


def test_non_renewal_update_does_not_reset_tokens(verification_db):
    """An update within the same billing period (e.g. metadata change) must not
    touch the remaining token count."""
    from datetime import datetime, timezone

    product_id, _ = _tier_product_and_tokens()
    period_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    verification_db.add(Server(server_id="910", owner_id="911", subscription_status=True,
                               stripe_subscription_id="sub_renew_2", verifications_count=7,
                               last_renewal_date=period_start))
    verification_db.commit()

    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": product_id}}]}}
        subscription_manager.handle_verification_subscription_update(
            "sub_renew_2", "active", {"guild_id": "910"}, period_start,
        )

    verification_db.expire_all()
    server = verification_db.query(Server).filter_by(server_id="910").first()
    assert server.verifications_count == 7


def test_verification_subscription_deleted_marks_inactive(verification_db):
    verification_db.add(Server(server_id="777", owner_id="888",
                                subscription_status=True, stripe_subscription_id="sub_verify_1"))
    verification_db.commit()

    subscription_manager.handle_verification_subscription_deleted("sub_verify_1", {})

    verification_db.expire_all()
    server = verification_db.query(Server).filter_by(server_id="777").first()
    assert server.subscription_status is False
