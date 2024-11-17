import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
import src.subscription_manager as subscription_manager

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
