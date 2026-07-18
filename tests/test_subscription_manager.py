import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
import src.subscription_manager as subscription_manager
from src.subscription_manager import (
    VRCVerifyServer,
    PRODUCT_ID_VRCVERIFY,
    handle_vrcverify_checkout_session,
    handle_vrcverify_subscription_created,
    handle_vrcverify_subscription_update,
    handle_vrcverify_subscription_deleted,
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
# VRCVerify billing handlers
# (require DATABASE_URL_VRCVERIFY to point at a test database,
#  e.g. sqlite:///:memory:, when running the suite)
# ---------------------------------------------------------------

@pytest.fixture(scope="function")
def vrcverify_db():
    """Provide a clean vrcverify DB session per test."""
    session = subscription_manager.SessionVRCVerify()
    session.query(VRCVerifyServer).delete()
    session.commit()
    yield session
    session.query(VRCVerifyServer).delete()
    session.commit()
    session.close()


def make_checkout_session(guild_id="111", discord_id="222", sub_id="sub_vrc_1"):
    return {
        "customer_details": {"email": "test@example.com"},
        "custom_fields": [
            {"key": "discordserverid", "text": {"value": guild_id}},
            {"key": "discorduseridnotyourusername", "text": {"value": discord_id}},
        ],
        "subscription": sub_id,
        "metadata": {},
    }


def test_vrcverify_checkout_creates_server(vrcverify_db):
    handle_vrcverify_checkout_session(make_checkout_session())

    server = vrcverify_db.query(VRCVerifyServer).filter_by(server_id="111").first()
    assert server is not None
    assert server.subscription_status is True
    assert server.owner_id == "222"
    assert server.stripe_subscription_id == "sub_vrc_1"
    assert server.email == "test@example.com"


def test_vrcverify_subscription_created_upserts(vrcverify_db):
    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": PRODUCT_ID_VRCVERIFY}}]}}
        handle_vrcverify_subscription_created(
            "sub_vrc_2", "active", {"guild_id": "333", "discorduseridnotyourusername": "444"}, None
        )

    server = vrcverify_db.query(VRCVerifyServer).filter_by(server_id="333").first()
    assert server is not None
    assert server.subscription_status is True
    assert server.stripe_subscription_id == "sub_vrc_2"


def test_vrcverify_subscription_update_deactivates_on_non_active(vrcverify_db):
    vrcverify_db.add(VRCVerifyServer(server_id="555", owner_id="666",
                                     subscription_status=True, stripe_subscription_id="sub_vrc_3"))
    vrcverify_db.commit()

    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": PRODUCT_ID_VRCVERIFY}}]}}
        handle_vrcverify_subscription_update("sub_vrc_3", "canceled", {"guild_id": "555"}, None)

    vrcverify_db.expire_all()
    server = vrcverify_db.query(VRCVerifyServer).filter_by(server_id="555").first()
    assert server.subscription_status is False


def test_vrcverify_subscription_deleted_marks_inactive(vrcverify_db):
    """Regression: the deletion handler previously queried the verification DB's
    Server model through a vrcverify session instead of VRCVerifyServer."""
    vrcverify_db.add(VRCVerifyServer(server_id="777", owner_id="888",
                                     subscription_status=True, stripe_subscription_id="sub_vrc_4"))
    vrcverify_db.commit()

    handle_vrcverify_subscription_deleted("sub_vrc_4", {})

    vrcverify_db.expire_all()
    server = vrcverify_db.query(VRCVerifyServer).filter_by(server_id="777").first()
    assert server.subscription_status is False


def test_checkout_routing_vrcverify_exact_match():
    """Regression: 'product_id in PRODUCT_ID_VRCVERIFY' was a substring check;
    routing must only fire on exact product ID equality."""
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"subscription": "sub_route_1"}},
    }

    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve, \
         patch("src.subscription_manager.handle_vrcverify_checkout_session") as mock_handler:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": PRODUCT_ID_VRCVERIFY}}]}}
        process_event(event)
        mock_handler.assert_called_once()

    substring_of_real_id = PRODUCT_ID_VRCVERIFY[:10]
    with patch("src.subscription_manager.stripe.Subscription.retrieve") as mock_retrieve, \
         patch("src.subscription_manager.handle_vrcverify_checkout_session") as mock_handler:
        mock_retrieve.return_value = {"items": {"data": [{"price": {"product": substring_of_real_id}}]}}
        process_event(event)
        mock_handler.assert_not_called()
