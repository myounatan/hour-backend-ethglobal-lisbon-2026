# hour-rewards-sdk

Punch-card rewards for venue loyalty programs, as an installable SQLModel package.

A venue opts into a program by declaring how many punches earn what reward. Users earn
punches by photographing receipts, and a completed card is claimed by showing a QR code
that venue staff scan. This package owns that data model — the tables, their constraints,
and the rules baked into them — so it can be dropped into any FastAPI/SQLModel app that
already has users, venues and venue owners.

Built for ETHGlobal Lisbon 2026, and consumed by the [Hour](https://hourapp.co) mobile app.

## Install

```bash
pip install git+https://github.com/myounatan/hour-backend-ethglobal-lisbon-2026.git

# with the Alembic helpers for creating the tables
pip install "hour-rewards-sdk[migrations] @ git+https://github.com/myounatan/hour-backend-ethglobal-lisbon-2026.git"

# with the Hedera layer (adds hiero-sdk-python)
pip install "hour-rewards-sdk[migrations,hedera] @ git+https://github.com/myounatan/hour-backend-ethglobal-lisbon-2026.git"
```

## The model

```python
from hour_rewards.models import PunchCard, PunchEvent, PunchEventStatus, RewardProgram
```

| Table | What it holds |
| --- | --- |
| `reward_programs` | One row per opted-in venue: `punches_required`, `reward_description`, `is_enabled` |
| `punch_cards` | One row per user per venue, with a `cycle_number` and denormalized `punch_count` |
| `punch_events` | One receipt submission, `PENDING_REVIEW` until the AI pipeline verifies it |
| `reward_redemption_codes` | QR tokens issued for a full card, valid while `PENDING` and in-cycle |
| `reward_redemptions` | Completed claims, snapshotting the program's terms at redemption time |
| `hedera_accounts` | The custodial Hedera account holding one user's card NFTs (Hedera layer only) |

Three decisions are worth knowing before you build on it:

- **Cards are never replaced.** Redeeming bumps `cycle_number` and resets `punch_count`;
  every punch keeps the cycle it was earned under, so history survives each reset.
- **Punches are idempotent per venue.** `punch_events.dedupe_hash` is unique per venue, so
  the same receipt can't be claimed twice, even from a different account.
- **Redemptions snapshot their terms.** Changing a program's threshold or reward copy
  applies to in-progress cards immediately, but never rewrites past redemptions.

## Service layer

`RewardService` implements the rules above as a static-async service, operating on an
`AsyncSession` and this package's own models only -- it never imports the host app, same
as the models.

```python
from hour_rewards.service import RewardService, RewardServiceError

program = await RewardService.create_or_update_reward_program(session, create_model)
summary = await RewardService.get_punch_card_summary(session, user_id, venue_id)  # or None
history = await RewardService.get_punch_history(session, user_id, venue_id)
code = await RewardService.generate_redemption_code(session, user_id, venue_id)  # raises RewardServiceError if under threshold
redemption = await RewardService.redeem_code(session, token, redeemed_by_owner_id)
```

Authentication and authorization -- who the current user is, whether they own the venue
scanning a code -- are the host's job, done before calling in. The one exception is
`redeem_code`: a token alone doesn't carry its venue, so call
`RewardService.get_redemption_code_venue_id(session, token)` first to resolve it and run
your own authorization check, then call `redeem_code`.

`RewardServiceError` (a `ValueError`) is raised for rule violations: an under-threshold
card, an already-redeemed or wrong-cycle code, a missing program. Hosts typically map it
to a 409 Conflict.

## Hedera

Punch cards are also real assets: a venue that opts in gets **its own HTS NFT collection**,
each user's card is **one serial in it**, and every punch and claim is **published to that
venue's HCS topic**. Native services only — no Solidity, no smart contracts — via
[`hiero-sdk-python`](https://github.com/hiero-ledger/hiero-sdk-python).

| Moment | On Hedera |
| --- | --- |
| Venue opts in | `TokenCreateTransaction` (NFT collection, metadata key set) + `TopicCreateTransaction` |
| User's first punch | `AccountCreateTransaction` (custodial) + `TokenMintTransaction` + `TransferTransaction` |
| Each verified punch | `TopicMessageSubmitTransaction` + `TokenUpdateNftsTransaction` |
| Reward claimed | `TopicMessageSubmitTransaction` + `TokenUpdateNftsTransaction` (next cycle) |

Four decisions worth knowing:

- **The card NFT is minted once, not per cycle.** Redeeming rewrites its metadata via
  HIP-657's metadata key, so one durable card accrues a venue's whole history — the same
  reasoning as `cycle_number` incrementing in place rather than replacing the row.
- **Metadata is a URI, not a document.** HIP-657 caps on-chain metadata at 100 bytes, so
  the minted bytes point at the host's API (`{metadata_base_url}/{card_id}?v={cycle}-{count}`)
  and `build_card_metadata()` serves HIP-412 JSON from the live card. The version in the
  query string means each state change is its own signed transaction rather than a silent
  edit behind a stable pointer.
- **Users are custodial, and their identifiers aren't published.** Punch-card users sign in
  with Google or Apple and bring no wallet, so an account is created for them on first punch
  and its key stored Fernet-encrypted in `hedera_accounts`. Topic messages carry a salted
  hash of the user id, never the id itself.
- **The ledger mirrors the database; it never gates it.** Every call is best-effort: a
  failure is logged, the `hedera_*` columns stay null, and the next punch retries what's
  missing. A Hedera outage cannot fail a punch or a redemption.

A host enables it by handing over credentials once at startup; nothing here reads the
host's environment:

```python
from hour_rewards.hedera import HederaConfig, close_hedera_clients, configure_hedera

configure_hedera(
    HederaConfig.build(
        operator_id=settings.HEDERA_OPERATOR_ID,          # pays and signs; treasury of every collection
        operator_key=settings.HEDERA_OPERATOR_KEY,
        metadata_base_url=settings.HEDERA_METADATA_BASE_URL,  # must leave the URI under 100 bytes
        key_encryption_secret=settings.HEDERA_KEY_ENCRYPTION_SECRET,
        network="testnet",
    )
)
...
close_hedera_clients()  # on shutdown
```

`HederaConfig.build` returns `None` when any credential is missing, and `configure_hedera(None)`
leaves the layer dormant — which is how the punch cards stay fully usable, tests included,
with no Hedera account at all.

The host also serves the metadata URI it configured, using the payload this package builds:

```python
@router.get("/rewards/nft/{punch_card_id}")
async def punch_card_nft_metadata(punch_card_id: UUID, db: DBSession) -> dict:
    return await build_card_metadata(db, punch_card_id) or {}
```

`RewardService` calls the ledger for you: opting a venue in creates its collection and topic,
`record_verified_punch` mints the card on the first punch and then publishes each one, and
`redeem_code` publishes the claim. `HederaLedger` is available directly if a host wants to
drive it itself.

See the whole lifecycle run against testnet, with HashScan links for each step:

```bash
export HEDERA_OPERATOR_ID=0.0.xxxxx HEDERA_OPERATOR_KEY=0x...   # portal.hedera.com
python scripts/hedera_demo.py
```


## Host contract

The rewards tables reference tables the host application owns, and SQLAlchemy resolves
those links by class name once both sets of models share one SQLModel registry. A host
must therefore provide:

**Tables:** `users`, `venues`, `owners`, `user_images`.

**Models** registered with SQLModel's metadata before the first query — `User`, `Venue`,
`Owner` and `UserImage` — with these back-references:

```python
class User(SQLModel, table=True):
    punch_cards: List["PunchCard"] = Relationship(back_populates="user")

class Venue(SQLModel, table=True):
    # `reward_programs.venue_id` is unique, so a venue has at most one program
    reward_program: Optional["RewardProgram"] = Relationship(
        back_populates="venue", sa_relationship_kwargs={"uselist": False}
    )
    punch_cards: List["PunchCard"] = Relationship(back_populates="venue")
```

`Owner` and `UserImage` need no declarations of their own; they only have to be imported
somewhere so their classes are registered when mappers are configured.

`hour_rewards.host_models` holds type-only stand-ins for these four classes. Nothing in
this package imports the host app at runtime.

## Creating the tables

Call the packaged Alembic operations from a revision file in your own app, so the
revision chain stays yours while the schema lives here:

```python
from hour_rewards.migrations import downgrade as rewards_downgrade
from hour_rewards.migrations import upgrade as rewards_upgrade

revision = "y2z3a4b5c6d7"
down_revision = "x1y2z3a4b5c6"

def upgrade() -> None:
    rewards_upgrade()

def downgrade() -> None:
    rewards_downgrade()
```

The Hedera table and columns are a second pair, `upgrade_hedera` / `downgrade_hedera`, so a
host can take the punch cards without the ledger.

Every operation checks for each table and column before touching it, so they are safe to run
against a database where the schema was built straight from the models.

## Licence

MIT
