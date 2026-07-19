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


def test_verification_subscription_deleted_marks_inactive(verification_db):
    verification_db.add(Server(server_id="777", owner_id="888",
                                subscription_status=True, stripe_subscription_id="sub_verify_1"))
    verification_db.commit()

    subscription_manager.handle_verification_subscription_deleted("sub_verify_1", {})

    verification_db.expire_all()
    server = verification_db.query(Server).filter_by(server_id="777").first()
    assert server.subscription_status is False
