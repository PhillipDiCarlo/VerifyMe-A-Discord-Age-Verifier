# VerifyMe — Discord Age Verification

VerifyMe is a commercial Discord bot that verifies users' ages with real
government ID checks via **Stripe Identity**, then assigns a server-configured
role to verified users. Server owners subscribe to a monthly tier that grants a
number of verification tokens; already-verified users can join any subscribed
server and get their role instantly without re-verifying.

## Architecture

Four services (each its own Docker image) around PostgreSQL and RabbitMQ:

| Service | Entry point | Role |
|---|---|---|
| discord-bot | `src/bot.py` | Slash commands, verification flow, role assignment, RabbitMQ consumer |
| stripe-webhook | `src/stripe_webhook_service.py` (gunicorn) | Receives Stripe Identity webhooks, stores encrypted DOB, queues results |
| subscription-manager | `src/subscription_manager.py` (gunicorn) | Receives Stripe Billing webhooks, manages tiers/tokens/renewals |
| subscription-checker | `src/subscription_checker.py` | Scheduled job that deactivates lapsed subscriptions |

Shared plumbing:

- **`src/models.py`** — single source of truth for the schema (`users`,
  `servers`, `command_usage`), the engine, and `session_scope()`. This repo
  connects to **`verify_me_database` only** (`DATABASE_URL_VERIFICATION`);
  never add another database.
- **Alembic** (`alembic/`) — schema migrations. Deploys run
  `alembic upgrade head`; `alembic check` must report zero drift.
- **`src/locales.py`** — all user-facing strings, 12 languages. Lookup order:
  server-configured language → user's Discord client language → English.
- **RabbitMQ** — `stripe-webhook` publishes verification results; the bot
  consumes them and assigns roles.
- Dates of birth are stored **Fernet-encrypted** (`DOB_KEY`); the bot decrypts
  only to compare against a server's minimum age.

## Slash commands

| Command | Who | What |
|---|---|---|
| `/verifyme` | anyone | Start verification (or instantly re-assign the role if already verified and old enough) |
| `/setupverify role minimum_age [unverified-role]` | admin | Set the verified role, minimum age, and optional role to remove on success |
| `/settings` | admin | Paged settings: minimum age, language, auto-verify on join, custom success DM, unverified role |
| `/instructions` | admin | Post/update the instruction panel with its persistent **Verify Me** button |
| `/server_info` | admin | Current configuration, tier, and remaining verifications |
| `/get_verify_bot` | anyone | Link to add the bot |
| `/get_subscription` | admin | Subscription/pricing link |
| `/ping` | anyone | Liveness check |

The bot also auto-verifies already-verified users when they join a server
(`on_member_join`, requires the **Server Members** privileged intent; can be
disabled per server in `/settings`).

## Environment variables

Copy `.env.example` to `.env` and fill it in. Required:

- `DISCORD_BOT_TOKEN`
- `STRIPE_SECRET_KEY`, `STRIPE_RESTRICTED_SECRET_KEY`,
  `STRIPE_WEBHOOK_SECRET` (Identity), `STRIPE_PAYMENT_WEBHOOK_SECRET` (Billing)
- `DATABASE_URL_VERIFICATION` — the only database URL this repo reads
- `RABBITMQ_HOST` / `RABBITMQ_PORT` / `RABBITMQ_USERNAME` /
  `RABBITMQ_PASSWORD` / `RABBITMQ_VHOST` / `RABBITMQ_QUEUE_NAME`
- `DOB_KEY` — Fernet key for DOB encryption

Optional tuning knobs (RabbitMQ heartbeats/retries, REST member-fetch cache,
instruction-panel refresh trigger, log levels) are documented inline in
`.env.example`. Never set `ALLOW_UNSIGNED_WEBHOOKS` outside local testing.

## Running

**Docker (production):**

```bash
docker compose -f config/other_configs/docker-compose.yml up -d --build
```

Each image installs only its own pinned dependency set
(`config/other_configs/requirements-<service>.txt`).

**Local development:**

```bash
python -m venv venv
venv/Scripts/pip install -r config/other_configs/requirements-dev.txt
python src/bot.py                     # bot
python src/stripe_webhook_service.py  # webhook service, etc.
```

**Database migrations** (first deploy of a schema change):

```bash
alembic upgrade head   # reads DATABASE_URL_VERIFICATION from .env
alembic check          # should report no drift
```

## Tests

```bash
pytest tests/
```

The suite is fully self-contained: `tests/conftest.py` forces an in-memory
sqlite database *before any source import* and hard-fails if any service ever
binds to a non-sqlite engine, so tests can never touch a real database. CI
(`.github/workflows/tests.yml`) runs the suite plus an Alembic
upgrade-from-zero/drift check on every PR.

## Stripe configuration

- **Identity webhook** → `/stripe_webhook` (stripe-webhook service): send
  `identity.verification_session.verified` / `...processing` / `...canceled`.
- **Billing webhook** → `/stripe-webhook` (subscription-manager service): send
  `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted`.

## License

Proprietary software owned by Esatto Technologies LLC. All rights reserved.
See [LICENSE.md](LICENSE.md).
