import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import src.bot as bot_module


@pytest.mark.asyncio
async def test_verify_command_missing_server_config():
    """With no server config in the DB, verify() should defer, look the server
    up via session_scope, and report the error to the user via followup."""
    mock_interaction = MagicMock()
    mock_interaction.guild.id = 123
    mock_interaction.user.id = 456
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()

    mock_session = MagicMock()
    mock_session.query.return_value.filter_by.return_value.first.return_value = None

    with patch("src.bot.session_scope") as mock_session_scope:
        mock_session_scope.return_value.__enter__.return_value = mock_session
        await bot_module.verify(mock_interaction)

    mock_session_scope.assert_called()
    mock_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    mock_interaction.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_verify_command_dm_rejected():
    """verify() must refuse to run outside a guild instead of raising on
    interaction.guild.id."""
    mock_interaction = MagicMock()
    mock_interaction.guild = None
    mock_interaction.response.send_message = AsyncMock()

    with patch("src.bot.session_scope") as mock_session_scope:
        await bot_module.verify(mock_interaction)
        mock_session_scope.assert_not_called()

    mock_interaction.response.send_message.assert_awaited_once()
