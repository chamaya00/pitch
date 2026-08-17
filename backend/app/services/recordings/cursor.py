"""Where a page of history stopped.

A history page is bounded, and until Step 10.13 that was the end of the story:
an owner with 137 recordings was sent the newest 50 and told nothing, and an
owner with more than 200 could not reach the older ones at any ``limit``. This
module is the "where was I" that makes the rest of them reachable.

**Keyset, not offset.** The cursor names the last row of the previous page and
the next page asks for rows *after* it, rather than asking the database to count
past the first N and throw them away. Two properties follow, and both matter
here:

* the work is the same for page 30 as for page 1, because
  ``recordings_owner_created_idx`` seeks straight to the key; and
* uploading a recording between two page requests cannot shift the window, so
  nothing is silently skipped or shown twice. With ``OFFSET`` it would be — a
  new row at the top pushes row 50 down to 51, and page 2 starts by repeating
  it.

**The ordering it encodes is the query's ordering**, ``created_at DESC, id
DESC``: the server's clock, never the client's, with the id as a tie-break so
two recordings created in the same instant still have one total order. A cursor
carrying only a timestamp would lose rows in that tie.

**It is opaque, not secret.** Base64url of two values a client already has —
nothing here is a capability. A forged cursor selects a different slice of *the
caller's own* recordings, because ownership stays in the ``WHERE`` clause and no
cursor reaches it. What a bad cursor must not do is reach the database as
nonsense, so decoding validates both halves and refuses anything else.
"""

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

#: Recording ids are ``uuid4().hex``, the same shape every other layer checks.
_ID_PATTERN: Final = re.compile(r"\A[0-9a-f]{32}\Z")

#: The character set base64url produces, with padding stripped. Constraining the
#: *shape* at the route means an obviously malformed value is refused before it
#: is decoded; this is the check for what decoding produces.
CURSOR_PATTERN: Final = r"^[A-Za-z0-9_-]{1,256}$"

_SEPARATOR: Final = "|"


class InvalidCursorError(ValueError):
    """The cursor was not one this server issued, or was damaged in transit."""


@dataclass(frozen=True, slots=True)
class HistoryCursor:
    """The last row of a page: everything the next page needs to resume."""

    created_at: datetime
    recording_id: str

    def encode(self) -> str:
        """Render as the opaque string a client sends back."""
        raw = f"{self.created_at.isoformat()}{_SEPARATOR}{self.recording_id}"
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def encode_cursor(created_at: datetime, recording_id: str) -> str:
    return HistoryCursor(created_at=created_at, recording_id=recording_id).encode()


def decode_cursor(value: str) -> HistoryCursor:
    """Parse a cursor, or refuse it.

    Raises:
        InvalidCursorError: it is not base64url, does not hold exactly two
            fields, or either field is not the shape this server writes. The
            caller turns that into the documented ``VALIDATION_ERROR`` envelope;
            nothing malformed is passed to a query "to see what happens".
    """
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError("cursor is not valid base64url") from exc

    timestamp, separator, recording_id = raw.partition(_SEPARATOR)
    if not separator:
        raise InvalidCursorError("cursor does not name a recording")
    if not _ID_PATTERN.match(recording_id):
        raise InvalidCursorError("cursor does not carry a recording id")

    try:
        created_at = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise InvalidCursorError("cursor does not carry a timestamp") from exc
    if created_at.tzinfo is None:
        # Every timestamp this server writes is timezone-aware. A naive one
        # would compare against TIMESTAMPTZ under the session time zone, which
        # is a silently different query.
        raise InvalidCursorError("cursor timestamp has no time zone")

    return HistoryCursor(created_at=created_at, recording_id=recording_id)


__all__ = [
    "CURSOR_PATTERN",
    "HistoryCursor",
    "InvalidCursorError",
    "decode_cursor",
    "encode_cursor",
]
