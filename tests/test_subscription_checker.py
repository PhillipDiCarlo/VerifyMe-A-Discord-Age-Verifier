import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
import subscription_checker


def test_check_subscriptions_queries_verification_db():
    with patch.object(subscription_checker, "SessionVerification") as mock_session_v:
        subscription_checker.check_subscriptions()
        mock_session_v.assert_called()
        mock_session_v.return_value.query.assert_called()
