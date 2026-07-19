import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import models
import billing
import src.bot as bot_module
import subscription_checker
from models import Server


@pytest.fixture(scope="function")
def clean_db():
    session = models.Session()
    session.query(Server).delete()
    session.commit()
    yield session
    session.query(Server).delete()
    session.commit()
    session.close()


# ---------------------------------------------------------------
# billing.apply_tier — shared refill semantics
# ---------------------------------------------------------------

TIER_2 = {'tier': 'tier_2', 'tokens': 25}


def _server(**kwargs):
    defaults = dict(server_id="1", owner_id="2", subscription_status=True,
                    verifications_count=7,
                    last_renewal_date=datetime(2026, 6, 1, tzinfo=timezone.utc))
    defaults.update(kwargs)
    return Server(**defaults)


def test_apply_tier_renewal_resets_to_tier_amount():
    server = _server()
    renewed = billing.apply_tier(server, TIER_2, active=True,
                                 period_start=datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert renewed is True
    assert server.verifications_count == 25
    assert server.tier == 'tier_2'
    assert server.last_renewal_date == datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_apply_tier_no_period_start_never_touches_tokens():
    server = _server()
    renewed = billing.apply_tier(server, TIER_2, active=True, period_start=None)
    assert renewed is False
    assert server.verifications_count == 7


def test_apply_tier_same_period_does_not_reset():
    period = datetime(2026, 6, 1, tzinfo=timezone.utc)
    server = _server(last_renewal_date=period)
    renewed = billing.apply_tier(server, TIER_2, active=True, period_start=period)
    assert renewed is False
    assert server.verifications_count == 7


def test_apply_tier_inactive_never_resets():
    server = _server()
    renewed = billing.apply_tier(server, TIER_2, active=False,
                                 period_start=datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert renewed is False
    assert server.subscription_status is False
    assert server.verifications_count == 7


# ---------------------------------------------------------------
# billing SKU map loaders (env-driven)
# ---------------------------------------------------------------

def test_sku_maps_load_from_env():
    env = {
        'DISCORD_SKU_TIER_1': '111', 'DISCORD_SKU_TIER_5': '555',
        'DISCORD_SKU_TOKENS_25': '2525',
    }
    # clear=True so any real DISCORD_SKU_* values from a developer's .env
    # can't leak into the assertion.
    with patch.dict(os.environ, env, clear=True):
        tiers = billing._load_sku_tier_map()
        packs = billing._load_sku_token_pack_map()
    assert tiers == {'111': {'tier': 'tier_1', 'tokens': 10},
                     '555': {'tier': 'tier_5', 'tokens': 100}}
    assert packs == {'2525': 25}


# ---------------------------------------------------------------
# bot.process_entitlement — guild subscriptions
# ---------------------------------------------------------------

SKU_SUB = '9000'
SKU_PACK = '9010'


def _sub_entitlement(ent_id="e1", guild_id="500", ends_at=None, deleted=False):
    return SimpleNamespace(
        id=ent_id, sku_id=SKU_SUB, guild_id=guild_id, user_id="42",
        starts_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ends_at=ends_at, deleted=deleted, consumed=False,
    )


def _patched_tier_map():
    return patch.dict(billing.SKU_ID_TO_TIER, {SKU_SUB: {'tier': 'tier_1', 'tokens': 10}}, clear=True)


@pytest.mark.asyncio
async def test_entitlement_first_grant_activates_and_fills(clean_db):
    ends = datetime.now(timezone.utc) + timedelta(days=30)
    with _patched_tier_map():
        await bot_module.process_entitlement(_sub_entitlement(ends_at=ends))

    server = clean_db.query(Server).filter_by(server_id="500").first()
    assert server is not None
    assert server.subscription_status is True
    assert server.tier == 'tier_1'
    assert server.verifications_count == 10
    assert server.payment_provider == 'discord'
    assert server.discord_entitlement_id == "e1"
    assert server.discord_sku_id == SKU_SUB


@pytest.mark.asyncio
async def test_entitlement_reprocessing_is_idempotent(clean_db):
    """The startup sweep re-delivers the same entitlement state; it must not
    refill tokens the guild already spent this period."""
    ends = datetime.now(timezone.utc) + timedelta(days=30)
    ent = _sub_entitlement(ends_at=ends)
    with _patched_tier_map():
        await bot_module.process_entitlement(ent)

        # guild spends some tokens mid-period
        server = clean_db.query(Server).filter_by(server_id="500").first()
        server.verifications_count = 4
        clean_db.commit()

        await bot_module.process_entitlement(ent)

    clean_db.expire_all()
    server = clean_db.query(Server).filter_by(server_id="500").first()
    assert server.verifications_count == 4  # untouched


@pytest.mark.asyncio
async def test_entitlement_renewal_extends_and_refills(clean_db):
    now = datetime.now(timezone.utc)
    first = _sub_entitlement(ends_at=now + timedelta(days=1))
    with _patched_tier_map():
        await bot_module.process_entitlement(first)
        server = clean_db.query(Server).filter_by(server_id="500").first()
        server.verifications_count = 2
        clean_db.commit()

        renewed = _sub_entitlement(ends_at=now + timedelta(days=31))
        await bot_module.process_entitlement(renewed)

    clean_db.expire_all()
    server = clean_db.query(Server).filter_by(server_id="500").first()
    assert server.verifications_count == 10  # refilled on new period


@pytest.mark.asyncio
async def test_entitlement_delete_deactivates(clean_db):
    clean_db.add(Server(server_id="600", owner_id="1", subscription_status=True,
                        payment_provider='discord', discord_entitlement_id="e9"))
    clean_db.commit()

    await bot_module.on_entitlement_delete(SimpleNamespace(id="e9"))

    clean_db.expire_all()
    server = clean_db.query(Server).filter_by(server_id="600").first()
    assert server.subscription_status is False


# ---------------------------------------------------------------
# bot.process_entitlement — consumable token packs
# ---------------------------------------------------------------

def _pack_entitlement(ent_id="p1", user_id="42", guild_id=None, consumed=False):
    return SimpleNamespace(
        id=ent_id, sku_id=SKU_PACK, guild_id=guild_id, user_id=user_id,
        starts_at=None, ends_at=None, deleted=False, consumed=consumed,
        consume=AsyncMock(),
    )


def _patched_pack_map():
    return patch.dict(billing.SKU_ID_TO_EXTRA_TOKENS, {SKU_PACK: 25}, clear=True)


@pytest.mark.asyncio
async def test_token_pack_granted_via_purchase_context(clean_db):
    clean_db.add(Server(server_id="700", owner_id="99", subscription_status=True,
                        verifications_count=3))
    clean_db.commit()

    bot_module._record_purchase_context("42", "700")
    ent = _pack_entitlement()
    with _patched_pack_map():
        await bot_module.process_entitlement(ent)

    clean_db.expire_all()
    server = clean_db.query(Server).filter_by(server_id="700").first()
    assert server.verifications_count == 28
    ent.consume.assert_awaited_once()


@pytest.mark.asyncio
async def test_token_pack_unattributable_is_left_unconsumed(clean_db):
    """No purchase context and no sole-owner match: don't guess, don't consume."""
    bot_module._purchase_context.clear()
    ent = _pack_entitlement(user_id="424242")
    with _patched_pack_map():
        await bot_module.process_entitlement(ent)

    ent.consume.assert_not_awaited()
    assert clean_db.query(Server).count() == 0


@pytest.mark.asyncio
async def test_token_pack_sole_owner_fallback(clean_db):
    clean_db.add(Server(server_id="710", owner_id="55", subscription_status=True,
                        verifications_count=0))
    clean_db.commit()

    bot_module._purchase_context.clear()
    ent = _pack_entitlement(user_id="55")
    with _patched_pack_map():
        await bot_module.process_entitlement(ent)

    clean_db.expire_all()
    server = clean_db.query(Server).filter_by(server_id="710").first()
    assert server.verifications_count == 25
    ent.consume.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_consumed_pack_is_skipped(clean_db):
    clean_db.add(Server(server_id="720", owner_id="66", subscription_status=True,
                        verifications_count=1))
    clean_db.commit()

    bot_module._record_purchase_context("66", "720")
    ent = _pack_entitlement(user_id="66", consumed=True)
    with _patched_pack_map():
        await bot_module.process_entitlement(ent)

    clean_db.expire_all()
    server = clean_db.query(Server).filter_by(server_id="720").first()
    assert server.verifications_count == 1
    ent.consume.assert_not_awaited()


# ---------------------------------------------------------------
# subscription_checker — provider-aware lapse rules
# ---------------------------------------------------------------

def test_checker_lapses_by_provider(clean_db):
    now = datetime.now(timezone.utc)
    clean_db.add_all([
        # stripe, renewed long ago -> lapse
        Server(server_id="801", owner_id="1", subscription_status=True,
               payment_provider='stripe', last_renewal_date=now - timedelta(days=40)),
        # stripe, fresh -> stays
        Server(server_id="802", owner_id="1", subscription_status=True,
               payment_provider='stripe', last_renewal_date=now - timedelta(days=5)),
        # discord, entitlement long expired -> lapse
        Server(server_id="803", owner_id="1", subscription_status=True,
               payment_provider='discord', last_renewal_date=now - timedelta(days=40),
               entitlement_ends_at=now - timedelta(days=10)),
        # discord, entitlement current -> stays (even with old last_renewal_date)
        Server(server_id="804", owner_id="1", subscription_status=True,
               payment_provider='discord', last_renewal_date=now - timedelta(days=40),
               entitlement_ends_at=now + timedelta(days=10)),
        # discord, test entitlement without end date -> stays
        Server(server_id="805", owner_id="1", subscription_status=True,
               payment_provider='discord', last_renewal_date=now - timedelta(days=40),
               entitlement_ends_at=None),
    ])
    clean_db.commit()

    subscription_checker.check_subscriptions()

    clean_db.expire_all()
    status = {s.server_id: s.subscription_status
              for s in clean_db.query(Server).all()}
    assert status == {"801": False, "802": True, "803": False, "804": True, "805": True}
