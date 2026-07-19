"""Provider-agnostic billing logic shared by both payment rails (Phase 5).

- Stripe (legacy/grandfathered): subscription_manager.py webhook handlers
- Discord Premium Apps: bot.py entitlement handlers

Both call apply_tier() so tier/refill semantics can never drift between
rails. Refill semantics (decided 2026-07-18): on each renewal the monthly
allowance RESETS to the tier amount — unused tokens do not roll over.
Purchased extra-token packs are additive and unrelated to renewals.
"""
import os
import logging
from datetime import timezone

logger = logging.getLogger(__name__)

# Internal tier model. Discord SKUs currently exist for tier_0/1/2/3/5;
# tier_4/6 remain Stripe-only legacy tiers.
TIER_TOKENS = {
    'tier_0': 0,
    'tier_1': 10,
    'tier_2': 25,
    'tier_3': 50,
    'tier_4': 75,
    'tier_5': 100,
    'tier_6': 150,
}

TOKEN_PACK_SIZES = (10, 25, 50, 100)


def _load_sku_tier_map() -> dict:
    """DISCORD_SKU_TIER_<n> env vars -> {sku_id: {'tier': ..., 'tokens': ...}}."""
    mapping = {}
    for tier, tokens in TIER_TOKENS.items():
        sku_id = os.getenv(f'DISCORD_SKU_{tier.upper()}')
        if sku_id:
            mapping[str(sku_id)] = {'tier': tier, 'tokens': tokens}
    return mapping


def _load_sku_token_pack_map() -> dict:
    """DISCORD_SKU_TOKENS_<amount> env vars -> {sku_id: amount}."""
    mapping = {}
    for amount in TOKEN_PACK_SIZES:
        sku_id = os.getenv(f'DISCORD_SKU_TOKENS_{amount}')
        if sku_id:
            mapping[str(sku_id)] = amount
    return mapping


# Discord SKU routing tables (the storefront mirror of subscription_manager's
# PRODUCT_ID_TO_TIER / PRODUCT_ID_TO_EXTRA_TOKENS). Loaded from env so SKU
# IDs never live in code.
SKU_ID_TO_TIER = _load_sku_tier_map()
SKU_ID_TO_EXTRA_TOKENS = _load_sku_token_pack_map()


def apply_tier(server, tier_info, *, active: bool, period_start=None) -> bool:
    """Apply subscription state to a Server row.

    - subscription_status/tier are always updated.
    - When period_start advances past the stored last_renewal_date and the
      subscription is active, the monthly allowance resets to the tier
      amount (no rollover) and last_renewal_date moves forward.
    - Callers that cannot tell whether a new billing period started must
      pass period_start=None (status/tier update only) — passing a period
      start re-triggers the reset.

    Returns True if a renewal reset happened.
    """
    server.subscription_status = bool(active)
    if tier_info:
        server.tier = tier_info['tier']

    renewed = False
    if period_start is not None:
        last_renewal = server.last_renewal_date
        if last_renewal is not None and last_renewal.tzinfo is None:
            # sqlite returns naive datetimes even for tz-aware columns
            last_renewal = last_renewal.replace(tzinfo=timezone.utc)

        is_renewal = last_renewal is None or period_start > last_renewal
        if is_renewal and tier_info and active:
            server.verifications_count = tier_info['tokens']
            renewed = True
            logger.info(
                f"Renewal for guild {server.server_id}: verification count reset to "
                f"{tier_info['tokens']} ({tier_info['tier']})."
            )
        server.last_renewal_date = period_start

    return renewed
