"""Pure timestamp ordering for commercial opportunity chronology (PR3).

Compares timezone-aware UTC instants. Preserves original source strings in stored
records. Does not invent unsupported sub-day precision for date-only values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Final

MALFORMED_TIMESTAMP_REASON: Final = "malformed_event_timestamp"


@dataclass(frozen=True)
class ParsedEventTime:
    """Parsed business-event time for ordering only.

    ``original`` is always the untouched source string (or None).
    ``instant`` is a timezone-aware UTC datetime when ordering is possible.
    ``precision`` is ``datetime`` or ``date`` when parsed, else None.
    ``error`` is a stable reason code when the value cannot be ordered.
    """

    original: str | None
    instant: datetime | None
    precision: str | None
    error: str | None = None

    @property
    def is_orderable(self) -> bool:
        return self.instant is not None


def parse_event_time(raw: str | None) -> ParsedEventTime:
    """Parse ISO-8601 (incl. ``Z`` / offsets) or date-only ``YYYY-MM-DD``.

    Date-only values become UTC midnight of that calendar day (precision=date).
    Malformed values return ``instant=None`` with ``error=malformed_event_timestamp``.
    """
    if raw is None:
        return ParsedEventTime(original=None, instant=None, precision=None, error=None)
    text = str(raw).strip()
    if not text:
        return ParsedEventTime(original=raw, instant=None, precision=None, error=None)

    # Date-only: explicit contract — UTC midnight, no invented sub-day precision.
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            d = date.fromisoformat(text)
        except ValueError:
            return ParsedEventTime(
                original=raw, instant=None, precision=None, error=MALFORMED_TIMESTAMP_REASON
            )
        instant = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return ParsedEventTime(original=raw, instant=instant, precision="date", error=None)

    normalized = text.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return ParsedEventTime(
            original=raw, instant=None, precision=None, error=MALFORMED_TIMESTAMP_REASON
        )

    if dt.tzinfo is None:
        # Naive datetime: treat as UTC (documented conservative assumption).
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return ParsedEventTime(original=raw, instant=dt, precision="datetime", error=None)


def instant_sort_key(parsed: ParsedEventTime) -> tuple:
    """Ascending chronology key; unorderable values sort after all orderable ones."""
    if parsed.instant is None:
        return (1, datetime.max.replace(tzinfo=timezone.utc))
    return (0, parsed.instant)


def instant_max_key(parsed: ParsedEventTime) -> tuple:
    """Descending chronology key for ``max()`` (latest first among orderable)."""
    if parsed.instant is None:
        return (0, datetime.min.replace(tzinfo=timezone.utc))
    return (1, parsed.instant)
