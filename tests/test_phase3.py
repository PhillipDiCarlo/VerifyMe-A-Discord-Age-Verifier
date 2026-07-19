import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from datetime import datetime, timezone

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import models
import src.bot as bot_module
from src.bot import sanitize_custom_message, get_message
from models import Server, User


# ---------------------------------------------------------------
# sanitize_custom_message
# ---------------------------------------------------------------

def test_sanitize_strips_zero_width_characters():
    sanitized, invalid = sanitize_custom_message("hel​lo﻿")
    assert sanitized == "hello"
    assert invalid == []


def test_sanitize_neutralizes_everyone_and_here():
    sanitized, _ = sanitize_custom_message("hi @everyone and @here")
    assert "@everyone" not in sanitized
    assert "@here" not in sanitized


def test_sanitize_allows_allowlisted_https_links():
    msg = "See https://discord.com/x and https://esattotech.com/pricing/"
    _, invalid = sanitize_custom_message(msg)
    assert invalid == []


def test_sanitize_flags_other_hosts_and_plain_http():
    msg = "See https://evil.example/x and http://discord.com/y"
    _, invalid = sanitize_custom_message(msg)
    assert "https://evil.example/x" in invalid
    assert "http://discord.com/y" in invalid  # https required


# ---------------------------------------------------------------
# get_message locale resolution
# ---------------------------------------------------------------

def test_get_message_falls_back_to_english_for_unknown_locale():
    assert get_message("settings_saved", locale="xx-XX") == "Settings saved!"


def test_get_message_explicit_locale_beats_interaction_locale():
    interaction = MagicMock()
    interaction.locale = "es-ES"
    # en-US forced explicitly: must return the English template
    msg = get_message("settings_saved", interaction, locale="en-US")
    assert msg == "Settings saved!"


def test_get_message_unknown_key_returns_key():
    assert get_message("this_key_does_not_exist") == "this_key_does_not_exist"


# ---------------------------------------------------------------
# Locale completeness
# ---------------------------------------------------------------

def _format_placeholders(template: str) -> set:
    import string
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_all_locales_complete_and_placeholders_match():
    """Every supported language must define exactly the en-US key set, and each
    template must use exactly the same format placeholders as the English one
    (a missing/mistyped placeholder would raise KeyError at send time)."""
    from src.locales import localizations, LANGUAGE_CODES

    english = localizations["en-US"]
    assert set(localizations) == set(LANGUAGE_CODES)

    for code in LANGUAGE_CODES:
        lang = localizations[code]
        assert set(lang.keys()) == set(english.keys()), (
            f"{code}: missing {set(english) - set(lang)}, extra {set(lang) - set(english)}"
        )
        for key, template in lang.items():
            assert _format_placeholders(template) == _format_placeholders(english[key]), (
                f"{code}.{key}: placeholders differ from en-US"
            )


# ---------------------------------------------------------------
# on_member_join auto-verify
# ---------------------------------------------------------------

@pytest.fixture(scope="function")
def clean_db():
    session = models.Session()
    session.query(Server).delete()
    session.query(User).delete()
    session.commit()
    yield session
    session.query(Server).delete()
    session.query(User).delete()
    session.commit()
    session.close()


def _make_member(guild_id="100", user_id="200"):
    member = MagicMock()
    member.guild.id = guild_id
    member.id = user_id
    return member


def _add_server(session, guild_id="100", auto_verify=True, minimum_age=18):
    session.add(Server(
        server_id=guild_id, owner_id="1", role_id="999",
        subscription_status=True, auto_verify_new_members=auto_verify,
        minimum_age=minimum_age, instructions_locale="en-US",
    ))
    session.commit()


def _add_user(session, user_id="200", verified=True, birth_year=1990):
    dob = bot_module.encrypt_dob(datetime(birth_year, 1, 1))
    session.add(User(discord_id=user_id, verification_status=verified, dob=dob,
                     last_verification_attempt=datetime.now(timezone.utc)))
    session.commit()


@pytest.mark.asyncio
async def test_on_member_join_assigns_role_to_verified_user(clean_db):
    _add_server(clean_db)
    _add_user(clean_db)

    with patch("src.bot.assign_role", new_callable=AsyncMock, return_value=True) as mock_assign:
        await bot_module.on_member_join(_make_member())
        mock_assign.assert_awaited_once()
        args, kwargs = mock_assign.await_args
        assert args[0] == "100" and args[1] == "200" and args[2] == "999"
        assert kwargs.get("notify_success_dm") is True


@pytest.mark.asyncio
async def test_on_member_join_respects_disabled_setting(clean_db):
    _add_server(clean_db, auto_verify=False)
    _add_user(clean_db)

    with patch("src.bot.assign_role", new_callable=AsyncMock) as mock_assign:
        await bot_module.on_member_join(_make_member())
        mock_assign.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_member_join_skips_underage_user(clean_db):
    _add_server(clean_db, minimum_age=21)
    _add_user(clean_db, birth_year=datetime.now(timezone.utc).year - 19)  # ~19 years old

    with patch("src.bot.assign_role", new_callable=AsyncMock) as mock_assign:
        await bot_module.on_member_join(_make_member())
        mock_assign.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_member_join_skips_unverified_user(clean_db):
    _add_server(clean_db)
    _add_user(clean_db, verified=False)

    with patch("src.bot.assign_role", new_callable=AsyncMock) as mock_assign:
        await bot_module.on_member_join(_make_member())
        mock_assign.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_member_join_skips_inactive_subscription(clean_db):
    clean_db.add(Server(server_id="100", owner_id="1", role_id="999",
                        subscription_status=False, auto_verify_new_members=True,
                        minimum_age=18))
    clean_db.commit()
    _add_user(clean_db)

    with patch("src.bot.assign_role", new_callable=AsyncMock) as mock_assign:
        await bot_module.on_member_join(_make_member())
        mock_assign.assert_not_awaited()


# ---------------------------------------------------------------
# Minimum-age modal validation/persistence
# ---------------------------------------------------------------

def _make_modal_interaction(guild_id="300"):
    interaction = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.owner_id = "301"
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_min_age_modal_rejects_out_of_range(clean_db):
    view = MagicMock()
    modal = bot_module.MinimumAgeModal(view)
    modal.age_input._value = "12"

    interaction = _make_modal_interaction()
    await modal.on_submit(interaction)

    assert clean_db.query(Server).filter_by(server_id="300").first() is None
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_min_age_modal_saves_valid_age(clean_db):
    view = MagicMock()
    modal = bot_module.MinimumAgeModal(view)
    modal.age_input._value = "21"

    interaction = _make_modal_interaction()
    await modal.on_submit(interaction)

    clean_db.expire_all()
    server = clean_db.query(Server).filter_by(server_id="300").first()
    assert server is not None and server.minimum_age == 21
    assert view.minimum_age == 21
