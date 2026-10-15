"""Quote numbers: parse, format, and mint the next free one.

There is no numbering table. The next number is one past the highest serial already known, never
below the configured seed (`catalog.cost_parameter` key `quote_number_seed`), skipping the reserved
serials. "Known" is everything that could already be on a customer's desk: every `crm.quote` number
(whatever its origin) and every number the Gmail readings proposed from sent mail.

Minting takes a transaction-scoped advisory lock so two operators saving at once cannot be handed the
same number; the lock is released when the caller's transaction ends, so the caller must insert the
`crm.quote` row in that same transaction (the unique key on `quote_number` is the backstop).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.crm_workspace import MAIL_QUOTE_NUMBERS_SQL

#: Serials never handed out.
RESERVED_SERIALS = frozenset({1500})

SANTIAGO = ZoneInfo("America/Santiago")

#: `01268-26`, `1268-26`, `01268A-26` — a serial of up to five digits, an optional revision letter,
#: and the two-digit year. A bare number (`2026`, `01268`) is not a quote number: it is as likely a
#: year, a phone fragment or an amount, and a wrong serial here would push every future number up.
_PRINTED = re.compile(r"^(\d{1,5})[A-Z]?-\d{2}$")
#: `CN01268`, `CN01268A`, `cn 01268` — the file-name form.
_CN = re.compile(r"^CN\s?(\d{1,5})[A-Z]?$")

_NOT_CONFIGURED = "Falta configurar la numeración de cotizaciones (parámetro quote_number_seed)."

SEED_SQL = """
select value_numeric from catalog.cost_parameter
 where key = 'quote_number_seed' and value_numeric is not null and valid_from <= clock_timestamp()
 order by valid_from desc limit 1
"""


def parse_serial(text: str) -> int | None:
    """The serial inside any printed form of a quote number, or None."""
    if not isinstance(text, str):
        return None
    candidate = text.strip().upper()
    for pattern in (_PRINTED, _CN):
        match = pattern.match(candidate)
        if match:
            return int(match.group(1))
    return None


def format_number(serial: int, yy: int) -> str:
    return f"{serial:05d}-{yy:02d}"


def _split(quote_number: str) -> tuple[int, int]:
    serial = parse_serial(quote_number)
    if serial is None or "-" not in quote_number:
        raise ValueError("not_a_quote_number")
    return serial, int(quote_number.strip().rsplit("-", 1)[1])


def revision_label(quote_number: str, revision_no: int) -> str:
    """Revision 1 is the number itself; 2 is `…A`, up to 27 for `…Z`."""
    serial, yy = _split(quote_number)
    if revision_no < 1:
        raise ValueError("revision_no_invalid")
    if revision_no == 1:
        return quote_number
    if revision_no > 27:
        raise ValueError("revision_letters_exhausted")
    return f"{serial:05d}{chr(ord('A') + revision_no - 2)}-{yy:02d}"


def cn_label(quote_number: str, revision_no: int) -> str:
    """The file-name form: `CN01268`, then `CN01268A` for revision 2."""
    serial, _ = _split(quote_number)
    if revision_no < 1:
        raise ValueError("revision_no_invalid")
    if revision_no > 27:
        raise ValueError("revision_letters_exhausted")
    suffix = "" if revision_no == 1 else chr(ord("A") + revision_no - 2)
    return f"CN{serial:05d}{suffix}"


def next_serial(known: Iterable[int], seed: int) -> int:
    taken = set(known)
    candidate = max(seed, max(taken) + 1) if taken else seed
    while candidate in RESERVED_SERIALS or candidate in taken:
        candidate += 1
    return candidate


def santiago_yy(now: datetime) -> int:
    """Two-digit year in Chile at `now` (a naive datetime is read as UTC)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(SANTIAGO).year % 100


def _seed(cur: Any) -> int | None:
    cur.execute(SEED_SQL)
    row = cur.fetchone()
    return None if row is None else int(row[0])


def _known_serials(cur: Any) -> set[int]:
    serials: set[int] = set()
    cur.execute("select quote_number from crm.quote")
    serials.update(s for (n,) in cur.fetchall() if (s := parse_serial(n)) is not None)
    cur.execute(MAIL_QUOTE_NUMBERS_SQL)
    serials.update(s for (n, *_rest) in cur.fetchall() if (s := parse_serial(n)) is not None)
    return serials


def allocate_quote_number(cur: Any, now: datetime) -> str:
    """Mint the next number. Run inside the caller's transaction and insert the quote before commit."""
    cur.execute("select pg_advisory_xact_lock(hashtextextended('crm.quote_number', 0))")
    seed = _seed(cur)
    if seed is None:
        raise CommandRefused(409, "quote_numbering_not_configured", _NOT_CONFIGURED)
    return format_number(next_serial(_known_serials(cur), seed), santiago_yy(now))


def numbering_status(cur: Any, now: datetime) -> dict[str, Any]:
    """What the next number would be, without taking the lock or reserving anything."""
    seed = _seed(cur)
    known = _known_serials(cur)
    return {
        "configured": seed is not None,
        "seed": seed,
        "next_number": None if seed is None else format_number(next_serial(known, seed), santiago_yy(now)),
        "highest_known": max(known) if known else None,
    }
