import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
import src.stripe_webhook_service as stripe_service

@pytest.fixture(scope="function")
def mock_rabbitmq():
    with patch("src.stripe_webhook_service.pika.BlockingConnection") as mock_connection:
        yield mock_connection

@pytest.fixture(scope="function")
def allow_unsigned():
    os.environ['ALLOW_UNSIGNED_WEBHOOKS'] = '1'
    yield
    os.environ.pop('ALLOW_UNSIGNED_WEBHOOKS', None)

def test_webhook_event(mock_rabbitmq, allow_unsigned):
    client = stripe_service.app.test_client()
    response = client.post("/stripe_webhook", json={"type": "identity.verification_session.verified"})
    assert response.status_code == 200

def test_unsigned_webhook_rejected_by_default(mock_rabbitmq):
    """Without ALLOW_UNSIGNED_WEBHOOKS, unsigned JSON must fail signature verification."""
    os.environ.pop('ALLOW_UNSIGNED_WEBHOOKS', None)
    client = stripe_service.app.test_client()
    response = client.post("/stripe_webhook", json={"type": "identity.verification_session.verified"})
    assert response.status_code == 400
