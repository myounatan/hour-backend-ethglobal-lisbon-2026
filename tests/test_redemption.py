"""Unit tests for the redemption payload: what a QR code says, and what it can't be trusted on.

Nothing here touches a network or a database. Honouring a code is the host app's
``test_reward_service.py`` to prove, since it needs real cards; what is provable on its own is
the format itself -- same split as ``test_zg.py`` and ``test_receipt_photo.py``.
"""

from datetime import timedelta
from uuid import uuid4

import pytest

from hour_rewards.base import utc_now
from hour_rewards.redemption import (
    REDEMPTION_PAYLOAD_VERSION,
    RedemptionPayloadError,
    build_redemption_payload,
    parse_redemption_payload,
    seconds_until,
)


def a_payload(**overrides) -> str:
    fields = {
        "venue_id": uuid4(),
        "punch_card_id": uuid4(),
        "cycle_number": 3,
        "token": "kR3d-tok3n_abc",
    }
    fields.update(overrides)
    return build_redemption_payload(**fields)


def test_a_built_payload_parses_back_to_what_went_in():
    venue_id, card_id = uuid4(), uuid4()
    raw = build_redemption_payload(
        venue_id=venue_id, punch_card_id=card_id, cycle_number=7, token="t0ken"
    )

    payload = parse_redemption_payload(raw)

    assert payload.token == "t0ken"
    assert payload.venue_id == venue_id
    assert payload.punch_card_id == card_id
    assert payload.cycle_number == 7


def test_a_payload_names_the_venue_so_a_scanner_can_check_it_before_asking():
    venue_id = uuid4()
    payload = parse_redemption_payload(a_payload(venue_id=venue_id))

    assert payload.venue_id == venue_id
    assert payload.venue_id != uuid4()


def test_a_bare_token_still_parses_for_codes_issued_before_this_format():
    payload = parse_redemption_payload("Zm9vYmFyX3Rva2VuLTEyMw")

    assert payload.token == "Zm9vYmFyX3Rva2VuLTEyMw"
    # Nothing to check a venue against, so `redeem_scanned_code` falls back to the card's own.
    assert payload.venue_id is None


def test_surrounding_whitespace_from_a_scan_is_ignored():
    assert parse_redemption_payload("  \nt0ken\t ").token == "t0ken"


def test_an_empty_scan_is_not_a_code():
    with pytest.raises(RedemptionPayloadError):
        parse_redemption_payload("")
    with pytest.raises(RedemptionPayloadError):
        parse_redemption_payload("   ")


def test_somebody_elses_qr_code_is_not_a_code():
    with pytest.raises(RedemptionPayloadError, match="not an Hour redemption code"):
        parse_redemption_payload("https://example.com/coupon/123")


def test_a_newer_format_says_so_rather_than_failing_vaguely():
    newer = a_payload().replace(
        f"/v{REDEMPTION_PAYLOAD_VERSION}?", f"/v{REDEMPTION_PAYLOAD_VERSION + 1}?"
    )

    with pytest.raises(RedemptionPayloadError, match="newer version"):
        parse_redemption_payload(newer)


def test_an_unversioned_payload_is_unrecognised():
    with pytest.raises(RedemptionPayloadError, match="unrecognised"):
        parse_redemption_payload("hour://redeem/latest?token=t0ken")


def test_a_payload_without_a_token_carries_nothing_worth_looking_up():
    with pytest.raises(RedemptionPayloadError, match="no token"):
        parse_redemption_payload(f"hour://redeem/v{REDEMPTION_PAYLOAD_VERSION}?venue={uuid4()}")


def test_a_payload_whose_ids_are_not_ids_is_malformed():
    with pytest.raises(RedemptionPayloadError, match="malformed"):
        parse_redemption_payload(
            f"hour://redeem/v{REDEMPTION_PAYLOAD_VERSION}?venue=not-a-uuid&token=t0ken"
        )


def test_a_token_with_url_characters_survives_the_round_trip():
    # `secrets.token_urlsafe` yields `-` and `_`, and a payload is a query string.
    payload = parse_redemption_payload(a_payload(token="a-b_c-d_e"))

    assert payload.token == "a-b_c-d_e"


def test_time_left_counts_down_in_whole_seconds():
    now = utc_now()

    assert seconds_until(now + timedelta(seconds=90), now=now) == 90
    assert seconds_until(now + timedelta(seconds=0.4), now=now) == 0


def test_an_elapsed_deadline_is_no_time_left_rather_than_negative():
    now = utc_now()

    assert seconds_until(now - timedelta(minutes=5), now=now) == 0


def test_a_code_with_no_deadline_is_reported_as_such():
    assert seconds_until(None) == -1
