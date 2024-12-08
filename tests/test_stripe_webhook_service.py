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

def test_webhook_event(mock_rabbitmq):
    client = stripe_service.app.test_client()
    response = client.post("/stripe_webhook", json={"type": "identity.verification_session.verified"})
    assert response.status_code == 200
