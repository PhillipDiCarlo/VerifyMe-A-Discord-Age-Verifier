import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from subscription_checker import subscription_checker

@pytest.fixture(scope="function")
def mock_db():
    with patch("src.subscription_checker.create_engine") as mock_engine:
        mock_sessionmaker = MagicMock()
        mock_engine.return_value = mock_sessionmaker
        yield mock_sessionmaker

def test_check_subscriptions(mock_db):
    with patch("src.subscription_checker.datetime") as mock_datetime:
        mock_datetime.now.return_value = datetime(2024, 1, 31, tzinfo=timezone.utc)
        subscription_checker.check_subscriptions()
        mock_db().query.assert_called()
        mock_db().commit.assert_called()
