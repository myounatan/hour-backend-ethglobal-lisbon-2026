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

# with 0G receipt verification (adds an OpenAI client for the 0G Router)
pip install "hour-rewards-sdk[migrations,hedera,zg] @ git+https://github.com/myounatan/hour-backend-ethglobal-lisbon-2026.git"
```

## The model

```python
from hour_rewards.models import PunchCard, PunchEvent, PunchEventStatus, RewardProgram
```

| Table | What it holds |
| --- | --- |
| `reward_programs` | One row per opted-in venue: `punches_required`, `reward_description`, `is_enabled` |
| `punch_cards` | One row per user per venue, with a `cycle_number` and denormalized `punch_count` |
| `punch_events` | One receipt submission, plus the 0G verification that judged it |
| `reward_redemption_codes` | QR tokens issued for a full card, valid while `PENDING`, unexpired and in-cycle |
| `reward_redemptions` | Completed claims, snapshotting the program's terms at redemption time |
| `hedera_accounts` | The custodial Hedera account holding one user's card NFTs (Hedera layer only) |

Three decisions are worth knowing before you build on it:

- **Cards are never replaced.** Redeeming bumps `cycle_number` and resets `punch_count`;
  every punch keeps the cycle it was earned under, so history survives each reset.
- **Verified punches are idempotent per venue.** A verified `punch_events.dedupe_hash` is
  unique per venue, so the same receipt can't earn a punch twice, even from a different
  account. Refused attempts remain on file but can be retried. `punch_count` is recounted
  from the cycle's verified events rather than incremented, so a retried request can't
  inflate a card either.
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
result = await RewardService.submit_receipt(session, user_id, venue_id, receipt_text)  # see "0G"
code = await RewardService.generate_redemption_code(session, user_id, venue_id)  # raises RewardServiceError if under threshold
scan = await RewardService.redeem_scanned_code(session, qr_payload=..., venue_id=..., redeemed_by_owner_id=...)  # see "Redemption"
```

Authentication and authorization -- who the current user is, whether they own the venue
scanning a code -- are the host's job, done before calling in. The one exception is
`redeem_code`: a token alone doesn't carry its venue, so call
`RewardService.get_redemption_code_venue_id(session, token)` first to resolve it and run
your own authorization check, then call `redeem_code`. `redeem_scanned_code` is the way
round that for a venue's own scanner, where the venue is known before the code is.

`RewardServiceError` (a `ValueError`) is raised for rule violations: an under-threshold
card, an already-redeemed or wrong-cycle code, a missing program. Hosts typically map it
to a 409 Conflict.

## Redemption — what a scan endpoint does

A full card is claimed in person, so `hour_rewards.redemption` covers that whole step: what the
QR code says, whether this venue may honour it, and what to tell the person holding the phone.

The customer's app asks for a code and shows `qr_payload` as a QR image. Nothing else about the
response needs rendering:

```python
from hour_rewards import redemption_code_response

code = await RewardService.generate_redemption_code(session, user.id, venue_id)
return redemption_code_response(code, venue_id=venue_id)
# {"qr_payload": "hour://redeem/v1?venue=…&card=…&cycle=3&token=…",
#  "expires_in_seconds": 300, "status": "pending", …}
```

Codes live `DEFAULT_REDEMPTION_TTL_SECONDS` (5 minutes) by default -- pass `ttl_seconds` to
change it. Asking again while one is alive returns the *same* code rather than minting another,
so a customer reopening the sheet doesn't leave a trail of working tokens behind. Count down
`expires_in_seconds`, not `expires_at`: the timestamp columns here are naive UTC, so a duration
is the only unambiguous, clock-skew-proof form.

The venue's app sends back whatever its camera read, exactly as read:

```python
from hour_rewards import RedemptionScanRequest, RewardService

# after authorizing the caller as an owner of `venue_id`
scan = await RewardService.redeem_scanned_code(
    session, qr_payload=body.qr_payload, venue_id=venue_id, redeemed_by_owner_id=owner_id
)
# {"approved": true, "reward_description": "Free drink", "redemption": {…}}
# {"approved": false, "reason": "wrong_venue"}
```

`venue_id` is the venue doing the scanning, and the code's claim to it is checked twice: the
payload's own `venue` first, so a code from elsewhere is refused before a token is looked up,
then the card's, inside `redeem_code`. A host that authorizes per venue has effectively checked
this already; a host that doesn't shouldn't be able to cross-redeem by omission.

A refusal is a verdict, not an exception -- `reason` is one of `REDEMPTION_REFUSAL_REASONS`
(`wrong_venue`, `code_not_found`, `code_expired`, `already_redeemed`, `stale_cycle`,
`card_missing`, `program_missing`), for a host to phrase for staff. The one exception is
`RedemptionPayloadError`: a scan of something that was never one of our codes, which a host
maps to a 400. `RewardService.redeem_code` raises the same refusals as
`RedemptionRefusedError` (a `RewardServiceError` carrying `.reason`) for hosts that redeem by
token directly.

`parse_redemption_payload` / `build_redemption_payload` are exported so a client library can
read and write the same format -- the companion `hour-rewards-ui` package mirrors them, which
is what lets a scanner reject a wrong-venue code without a round trip. A bare token parses too,
so codes issued before this format still redeem.

## Receipt photos — what an upload endpoint does

A punch starts as a photo, so `hour_rewards.receipt_photo` covers that whole step: what an
upload has to be, reading it, and judging it. A host's endpoint authenticates the caller and
hands the bytes over.

```python
from hour_rewards import MAX_RECEIPT_IMAGE_BYTES, submit_receipt_photo

raw = await file.read(MAX_RECEIPT_IMAGE_BYTES + 1)  # one byte past the cap is enough
result = await submit_receipt_photo(session, user_id=user.id, venue_id=venue_id, image=raw)
```

JPEG, PNG and WebP, up to `MAX_RECEIPT_IMAGE_BYTES` (6 MB), taken from the bytes' own magic
numbers rather than from what the upload claims to be. Three exceptions, and everything else is
a verdict rather than a failure: `ReceiptImageTooLargeError` and `ReceiptImageError` are a host's
413 and 400, and `ReceiptScanError` its 502 — that last one means the receipt was never judged,
so retrying the same photo is fair.

The photo is read and dropped. A punch keeps the verdict, the receipt's dedupe hash and the
attestation of the run that decided it (below), never the image — pass `receipt_image_id` to
`RewardService.submit_receipt` yourself if your host stores uploads and wants them linked.

**Reading it is the one step handed back**, because a host running receipt photos already has a
document pipeline and this package has no business holding a second set of OCR credentials. A
host installs its reader once at startup, as it configures the other two layers:

```python
from hour_rewards import configure_receipt_reader

async def read_receipt_text(image: bytes) -> str:  # Azure, Textract, Tesseract, ...
    return await my_document_pipeline(image)

configure_receipt_reader(read_receipt_text)
```

Until one is installed, every photo raises `ReceiptScanError`. A host that already has the text
skips all of this and calls `RewardService.submit_receipt` directly.

## 0G — how a receipt becomes a punch

Punches aren't handed out by staff: a user photographs their receipt and it has to pass
verification. That judgement runs on [0G Compute](https://docs.0g.ai/developer-hub/building-on-0g/compute-network),
an OpenAI-compatible endpoint served by nodes that run in a TDX enclave and **sign every
response** from inside it — so what comes back is not just an answer but a checkable one, and
what makes it checkable is kept on the punch.

Judgement starts from the text, whether a photo was read for it (above) or a host had it
already. What the text is checked against — the venue's name and address — is read from its
`venues` row rather than passed in, so a submission can't describe the venue it claims to come
from:

```python
from hour_rewards.service import RewardService

result = await RewardService.submit_receipt(session, user_id, venue_id, receipt_text)
result.approved            # True -> the card moved
result.reason              # "venue_mismatch", "duplicate_receipt", "low_confidence", ...
result.summary             # progress after the punch, ready to return to a client
result.zg_request_id       # 0G's response key -> the run's public signature (below)
result.zg_tee_verified     # whether the provider that served it ran the model attested
```

Four guards decide it, and the first that fails is the answer: it must **be** a receipt, it
must **name this venue**, it must carry a **total**, and it must carry a **date or receipt
number** so one visit is distinguishable from another. Three things are worth knowing about how
that verdict is reached:

- **The venue-name guard is checked, not trusted.** The model is asked whether the venue is
  named in the text, and then `venue_name_in_text()` looks for itself; an approval the text
  doesn't support becomes `venue_mismatch`. It wants half a name's distinctive words, rounding
  up, which is lenient enough for how receipts print ("VIP Billiards Bloor" as `VIP BILLIARDS`)
  without letting one word carry a claim — a receipt from Japas at 692 Bloor St. West is not a
  receipt from VIP Billiards Bloor. It can only ever downgrade an approval, never rescue a
  refusal.
- **A receipt passes or it doesn't.** There is no review queue: below `min_confidence` (0.75 by
  default) a submission is refused as `low_confidence`, so the answer to a blurry photo is a
  better photo. Refused attempts are retained for review, but the same receipt can be
  photographed again. `verifier_unavailable` is not filed at all, since an unreachable or
  unconfigured endpoint should cost a retry and not the receipt.
- **A verified receipt is single-use per venue.** `punch_events.dedupe_hash` is built from the
  receipt's own number, or its date and total when it has no number. A partial unique index
  applies it to verified rows, so a receipt cannot earn a second punch or be claimed from a
  second account, while a refused attempt can be retried.

A host enables it the same way as the ledger, once at startup, and nothing here reads the
host's environment:

```python
from hour_rewards.zg import ZGConfig, configure_zg

configure_zg(
    ZGConfig.build(
        api_key=settings.ZG_ROUTER_API_KEY,        # pc.0g.ai -> Dashboard -> Apps ("app-sk-...")
        base_url=settings.ZG_ROUTER_BASE_URL,      # the gateway that key was issued for
        model=settings.ZG_ROUTER_MODEL,            # e.g. "qwen/qwen2.5-omni-7b"
        verify_tee=settings.ZG_VERIFY_TEE,
        min_confidence=settings.ZG_MIN_CONFIDENCE,
    )
)
```

`ZGConfig.build` returns `None` without an API key, and `configure_zg(None)` leaves the layer
dormant — punch cards, and every test here, work with no 0G account at all. An `app-sk-` key
only works against the gateway it was issued for, so pass `base_url` alongside it.

**What makes a punch checkable.** Two response headers come back with every completion:
`Provider`, the serving node's on-chain address, and `ZG-Res-Key`, 0G's key for that response
(the completion id is that key with `chatcmpl-` in front). They land on the punch event as
`zg_provider_address` and `zg_request_id`, and ride along to Hedera in the punch's topic message
(see below) — which is the point of doing it this way, because the key is enough for anyone to
check that exact inference afterwards, with no API key at all:

```bash
# 1. what the node signed for this response
curl https://<node>/v1/proxy/signature/<zg_request_id>
# {"text": "<sha256(request)>:<sha256(response)>:centralized:aliyun:<tls_fingerprint>",
#  "signature": "0x7d19…", "signing_address": "0x83df…", "signing_algo": "ecdsa", …}

# 2. the enclave that holds that signing key
curl https://<node>/v1/quote
# {"quote": "0400020081…", "report_data": "MHg4M2RmNEI4RWJB…", "event_log": […], "tcb_info": …}
#  base64-decode report_data -> "0x83df…", the signing_address above
```

With `verify_tee` on (the default) `zg_tee_verified` is that join: the response is signed by a
key whose address the node's TDX quote commits to, so an attested enclave served it. Null means
one of the two fetches failed — unknown, which is not the same as no. Two things this does *not*
do, both being public data anyone can redo from the response key: verify the quote against
Intel's PCS, and recover the ECDSA signature.

`provider_type` in that receipt is about the model, not the enclave: `"centralized"` means the
node ran the weights on a hosted API over TeeTLS (hence the TLS fingerprint it signed alongside)
rather than in-enclave. It is not evidence against a TEE — the quote is what answers that.

See a verdict for yourself, no database involved:

```bash
export ZG_ROUTER_API_KEY=app-sk-...
export ZG_ROUTER_BASE_URL=https://<gateway>/v1/proxy
python scripts/zg_demo.py --venue "Japas 1" --text-file receipt.txt
```

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
  hash of the user id, never the id itself, and a punch carries its receipt's hash and its
  0G attestation rather than anything printed on the receipt — enough to prove *why* the
  punch was granted without publishing what anyone bought.
- **The ledger mirrors the database; it never gates it.** Every call is best-effort: a
  failure is logged and the `hedera_*` columns stay null. A Hedera outage cannot fail a punch
  or a redemption. What keeps that from losing anything is that each id is committed the
  moment it exists — a token before its topic is attempted, a serial before it is transferred
  — so nothing on the ledger is missing from the database, and what's left undone is a null
  column `HederaLedger.reconcile()` can finish later.

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

After an outage, one pass catches everything up — programs with no collection or topic, cards
with no NFT or still holding it in the treasury, verified punches and claims that never
reached a topic (republished with the timestamp they were earned at and a `backfill` flag),
and each affected card's metadata:

```python
counts = await HederaLedger.reconcile(session)  # {"programs": 0, "mints": 1, "punches": 3, ...}
```

Nothing schedules it: run it from a script when you need it. A replayed message can duplicate
one an ambiguous timeout already landed, so consumers reading a venue's topic deduplicate on
the `event` or `redemption` uuid every message carries.

See the whole lifecycle run against testnet, with HashScan links for each step:

```bash
export HEDERA_OPERATOR_ID=0.0.xxxxx HEDERA_OPERATOR_KEY=0x...   # portal.hedera.com
python scripts/hedera_demo.py
```


## Host contract

The rewards tables reference tables the host application owns, and SQLAlchemy resolves
those links by class name once both sets of models share one SQLModel registry. A host
must therefore provide:

**Tables:** `users`, `venues`, `owners`, `user_images`. Two of their columns are read by name
rather than through a model — `venues.name` and `venues.address`, both in
`hour_rewards.host_queries` — for naming a venue's NFT collection and for checking its
receipts.

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

The Hedera table and columns are a second pair, `upgrade_hedera` / `downgrade_hedera`, and the
0G verification columns a third, `upgrade_zg` / `downgrade_zg` — so a host can take the punch
cards without the ledger, or without either.

Every operation checks for each table and column before touching it, so they are safe to run
against a database where the schema was built straight from the models.

## Licence

MIT
