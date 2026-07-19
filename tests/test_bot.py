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


# ---------------------------------------------------------------
# Phase 0: REST member-fetch TTL cache
# ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_ttl_cache_returns_value_then_expires():
    import asyncio
    cache = bot_module._TTLCache(maxsize=10, ttl=0.05)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    await asyncio.sleep(0.1)
    assert cache.get("k") is None


@pytest.mark.asyncio
async def test_ttl_cache_evicts_oldest_at_maxsize():
    cache = bot_module._TTLCache(maxsize=2, ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # over capacity: oldest ("a") is evicted
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


@pytest.mark.asyncio
async def test_fetch_member_cached_hits_rest_only_once():
    """A second lookup for the same (guild, user) within the TTL must be
    served from the cache, not another REST call."""
    guild = MagicMock()
    guild.id = 987654  # unique so the module-level cache can't collide
    member = MagicMock()
    guild.fetch_member = AsyncMock(return_value=member)

    first = await bot_module.fetch_member_cached(guild, 111)
    second = await bot_module.fetch_member_cached(guild, 111)

    assert first is member and second is member
    guild.fetch_member.assert_awaited_once()
