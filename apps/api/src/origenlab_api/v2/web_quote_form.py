"""POST /v2/web/quote-request — website «Solicitar cotización» form → notification mail.

Input is sanitised, timing- and honeypot-checked, deduplicated and rate-capped before
a single self-addressed notification is dispatched as contacto@origenlab.cl with the
visitor's address in Reply-To.  No copy is sent to the visitor.

Caps (in-process; the global window is re-seeded at start-up from the captured form mails):
  - Global: ≤ 40 form mails in any rolling 24-hour window.
  - Per-address: ≤ 3 form mails per visitor e-mail in any rolling 24-hour window.
Idempotency: a ``submission_id`` seen in the last 10 minutes silently returns success
without re-sending.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from origenlab_api.v2.gmail_send import MAX_ADDRESS_LENGTH, _ADDRESS, build_web_quote_message

#: Subject prefix of every form notification (see gmail_send.build_web_quote_message).
SUBJECT_PREFIX = "Solicitud de cotización — sitio web — "

# ---------------------------------------------------------------------------
# Input sanitisation
# ---------------------------------------------------------------------------

#: Control characters that must not appear in user-visible fields.
#: Does NOT include CR (\x0d) or LF (\x0a) — those are rejected by the
#: header-injection guard in gmail_send._safe_header if they ever reach a
#: header value, and legitimate multi-line body content is normalised below.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]")


def strip_control(s: str) -> str:
    """Remove ASCII control characters (except CR/LF) from a string."""
    return _CONTROL_CHARS.sub("", s)


def _normalise_body_text(s: str) -> str:
    """Strip control chars and normalise CRLF/LF/CR to space for use in body lines."""
    return re.sub(r"[\r\n]+", " ", strip_control(s)).strip()


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class WebQuoteFormRequest(BaseModel):
    submission_id: str
    need: str
    email: str
    org: str
    name: str | None = None
    phone: str | None = None
    city: str | None = None
    deadline: str | None = None
    render_at: float
    #: Honeypot field — must be absent or empty from real browsers.
    hp: str = ""

    @field_validator("need")
    @classmethod
    def validate_need(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 4000):
            raise ValueError("need must be 1–4000 characters")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip()
        if len(v) > MAX_ADDRESS_LENGTH or not _ADDRESS.fullmatch(v):
            raise ValueError("email must be a valid email address")
        return v

    @field_validator("org")
    @classmethod
    def validate_org(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 200):
            raise ValueError("org must be 1–200 characters")
        return v


# ---------------------------------------------------------------------------
# Rate-cap logic
# ---------------------------------------------------------------------------


class CapExceeded(Exception):
    """Raised by WebQuoteFormCaps when a cap would be exceeded."""


class WebQuoteFormCaps:
    """In-process, deque-based rate caps for the web quote form.

    Global:      ≤ 40 notifications per rolling 24-hour window.
    Per-address: ≤  3 notifications per visitor e-mail per rolling 24-hour window.
    """

    GLOBAL_LIMIT = 40
    PER_ADDRESS_LIMIT = 3
    WINDOW = timedelta(hours=24)

    def __init__(self) -> None:
        self.global_deque: deque[datetime] = deque()
        self.per_address: dict[str, deque[datetime]] = {}

    @staticmethod
    def _prune(q: deque[datetime], cutoff: datetime) -> None:
        while q and q[0] < cutoff:
            q.popleft()

    def check_and_record(self, email: str) -> None:
        """Check caps and, if not exceeded, record this send.

        Raises CapExceeded without modifying state if any cap would be exceeded.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - self.WINDOW

        self._prune(self.global_deque, cutoff)
        if len(self.global_deque) >= self.GLOBAL_LIMIT:
            raise CapExceeded("global cap exceeded")

        addr_q = self.per_address.setdefault(email, deque())
        self._prune(addr_q, cutoff)
        if len(addr_q) >= self.PER_ADDRESS_LIMIT:
            raise CapExceeded("per-address cap exceeded")

        # Record only after both checks pass.
        self.global_deque.append(now)
        addr_q.append(now)

    def seed_from_timestamps(self, sent_at: Sequence[datetime]) -> None:
        """Pre-populate the global window with form mails already sent (their real send times).

        Used at startup with the V2 record of captured form mails, so a restart does not reset
        the cap. Anything older than the window is ignored; at most GLOBAL_LIMIT are kept.
        """
        cutoff = datetime.now(timezone.utc) - self.WINDOW
        recent = sorted(t for t in sent_at if t >= cutoff)
        for t in recent[-self.GLOBAL_LIMIT:]:
            self.global_deque.append(t)


#: Form mails the API sent, as the Gmail capture stored them: self-sent contacto@ → contacto@
#: with the form's subject prefix. The subject is read straight from `comms.message` (written by
#: the capture itself), not from the triage reading, so a lagging triage cannot undercount.
_SENT_LAST_24H_SQL = """
select m.internal_date
  from comms.message m
 where m.subject like %s
   and m.internal_date > now() - interval '24 hours'
   and exists (select 1 from comms.message_participant p
                where p.message_id = m.id and p.role = 'from' and p.address_norm = %s)
   and exists (select 1 from comms.message_participant p
                where p.message_id = m.id and p.role = 'to' and p.address_norm = %s)
 order by m.internal_date
"""


def sent_timestamps_last_24h(connect: Any, dsn: str) -> list[datetime]:
    """Send times of the form mails captured in the last 24 h; [] (and a warning) on any error."""
    from origenlab_api.v2.gmail_send import SENDER_ADDRESS

    try:
        with connect(dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(_SENT_LAST_24H_SQL,
                            (SUBJECT_PREFIX + "%", SENDER_ADDRESS, SENDER_ADDRESS))
                rows = [r[0] for r in cur.fetchall()]
            conn.rollback()
        return rows
    except Exception as exc:
        logging.getLogger(__name__).warning("web quote form cap seed failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Idempotency cache
# ---------------------------------------------------------------------------

#: submission_id → monotonic expiry time (time.monotonic() + 600 s).
_IDEMPOTENCY_CACHE: dict[str, float] = {}

_IDEMPOTENCY_TTL = 600.0  # 10 minutes


def _check_idempotency(submission_id: str) -> bool:
    """Return True if this submission_id was already processed (duplicate).

    Prunes expired entries on every call.
    """
    now = time.monotonic()
    stale = [k for k, exp in _IDEMPOTENCY_CACHE.items() if exp < now]
    for k in stale:
        del _IDEMPOTENCY_CACHE[k]
    return submission_id in _IDEMPOTENCY_CACHE


def _record_idempotency(submission_id: str) -> None:
    _IDEMPOTENCY_CACHE[submission_id] = time.monotonic() + _IDEMPOTENCY_TTL


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

web_quote_form_router = APIRouter()

_BOT_TIMING_THRESHOLD_SECONDS = 3.0
_log = logging.getLogger(__name__)


@web_quote_form_router.post("/v2/web/quote-request")
def post_web_quote_request(body: WebQuoteFormRequest, request: Request) -> Any:
    """Accept a «Solicitar cotización» form submission and dispatch one notification mail.

    Silently succeeds (no mail sent) for:
    - Submissions faster than 3 s after render_at (bot guard).
    - Non-empty honeypot field (bot guard).
    - Duplicate submission_id seen within the last 10 minutes (idempotency).

    Returns 429 when a rate cap is exceeded.
    """
    # --- bot guards (fail-open: pretend to send, reveal nothing) ---
    elapsed = time.time() - body.render_at
    if elapsed < _BOT_TIMING_THRESHOLD_SECONDS:
        return {"status": "sent"}

    if body.hp:
        return {"status": "sent"}

    # --- idempotency ---
    if _check_idempotency(body.submission_id):
        return {"status": "sent"}

    # --- rate caps ---
    caps: WebQuoteFormCaps = request.app.state.web_quote_form_caps
    email_key = body.email.lower()
    try:
        caps.check_and_record(email_key)
    except CapExceeded:
        return JSONResponse(status_code=429, content={"error": {"code": "rate_limited"}})

    # --- build and send the notification ---
    extra: dict[str, str] = {}
    if body.name:
        extra["Nombre"] = strip_control(body.name)
    if body.phone:
        extra["Teléfono"] = strip_control(body.phone)
    if body.city:
        extra["Ciudad"] = strip_control(body.city)
    if body.deadline:
        extra["Plazo"] = strip_control(body.deadline)

    submitted_at = datetime.now(ZoneInfo("America/Santiago")).isoformat(timespec="seconds")

    try:
        raw = build_web_quote_message(
            reply_to=body.email,
            institution=strip_control(body.org),
            need=strip_control(body.need),
            extra=extra,
            submitted_at=submitted_at,
        )
    except ValueError:
        return JSONResponse(status_code=400, content={"error": {"code": "invalid_input"}})

    sender = request.app.state.web_quote_form_sender
    try:
        sender.send(raw)
    except Exception as exc:
        _log.error("web quote form send failed: %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"error": {"code": "send_failed"}})

    _record_idempotency(body.submission_id)

    return {"status": "sent"}
