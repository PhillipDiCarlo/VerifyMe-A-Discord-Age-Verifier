import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import pytest
from unittest.mock import patch, MagicMock
import src.bot as bot_module

@pytest.mark.asyncio
async def test_verify_command():
    mock_interaction = MagicMock()
    mock_interaction.guild.id = 123
    mock_interaction.user.id = 456

    with patch("src.bot.session_scope") as mock_session:
        await bot_module.verify(mock_interaction)
        mock_session.assert_called()
