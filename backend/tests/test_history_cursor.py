"""The history cursor: what it carries, and what it refuses.

Tested on its own because everything above it trusts two things — that a cursor
round-trips exactly, and that anything else is refused rather than passed to a
query. The route turns the refusal into a ``VALIDATION_ERROR``; whether it is
raised at all is decided here.
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.recordings.cursor import (
    HistoryCursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)

MOMENT = datetime(2026, 8, 17, 10, 11, 12, 131415, tzinfo=UTC)
RECORDING = "0c07991ba858449e976cb93f933f5dde"


def test_a_cursor_round_trips_exactly() -> None:
    """Down to the microsecond: two recordings a microsecond apart must not merge."""
    decoded = decode_cursor(encode_cursor(MOMENT, RECORDING))

    assert decoded == HistoryCursor(created_at=MOMENT, recording_id=RECORDING)
    assert decoded.created_at.microsecond == 131415


def test_a_cursor_survives_being_put_in_a_url() -> None:
    """base64url, unpadded: nothing here needs escaping as a query parameter."""
    encoded = encode_cursor(MOMENT, RECORDING)

    assert "=" not in encoded
    assert "+" not in encoded and "/" not in encoded
    assert decode_cursor(encoded).recording_id == RECORDING


def test_an_offset_time_zone_is_preserved_as_an_instant() -> None:
    """The same instant, written differently, is the same cursor."""
    elsewhere = MOMENT.astimezone(timezone(timedelta(hours=9)))

    assert decode_cursor(encode_cursor(elsewhere, RECORDING)).created_at == MOMENT


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("!!!!", "not base64url at all"),
        ("YWJj", "base64 of 'abc' — no separator, so it names no recording"),
        (encode_cursor(MOMENT, RECORDING).replace("M", "X", 1), "damaged in transit"),
    ],
)
def test_a_malformed_cursor_is_refused(value: str, why: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(value)


def test_a_cursor_carrying_something_other_than_a_recording_id_is_refused() -> None:
    """The id is interpolated into a query parameter, so its shape is checked here too."""
    for identifier in ["", "not-hex", uuid.uuid4().hex.upper(), "0" * 31, "0" * 33]:
        raw = f"{MOMENT.isoformat()}|{identifier}"
        forged = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(forged)


def test_a_naive_timestamp_is_refused() -> None:
    """A timestamp with no zone compares against TIMESTAMPTZ under the session
    time zone, which is a silently different query rather than an error."""
    raw = f"{MOMENT.replace(tzinfo=None).isoformat()}|{RECORDING}"
    forged = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    with pytest.raises(InvalidCursorError):
        decode_cursor(forged)


def test_a_cursor_carrying_no_timestamp_is_refused() -> None:
    raw = f"yesterday|{RECORDING}"
    forged = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    with pytest.raises(InvalidCursorError):
        decode_cursor(forged)
