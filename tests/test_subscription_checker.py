import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
import subscription_checker


def test_check_subscriptions_queries_verification_db():
    with patch.object(subscription_checker, "session_scope") as mock_scope:
        mock_session = MagicMock()
        mock_scope.return_value.__enter__.return_value = mock_session
        subscription_checker.check_subscriptions()
        mock_scope.assert_called()
        mock_session.query.assert_called()
