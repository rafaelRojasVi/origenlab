"""Reading OrigenLab's own email for a closing note (owner decision 2026-11-16).

When Tatiana writes «lo dejo en pausa», «quedo atenta para cuando…» or «no le volveré a escribir», the
case should pause by itself until the day the email names (else 30 days; the API's rule R22 decides
that). This module only reads: phrases and a day, from the new part of the email (quoted client text
is cut first, so a client's «lo dejo en pausa» is never taken for ours). Plain rules, no model:
ordinary sign-offs («quedo atenta a sus comentarios») must not match.

The reading is stored as a `message_triage` assertion with value_norm `outbound_intent:v1`
(the same kind the people and case-match readings use), value `{closing, until, phrase}`.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from origenlab_worker.mail_text import reply_text

OUTBOUND_INTENT_VALUE_NORM = "outbound_intent:v1"

_CLOSING = [
    r"\b(?:lo |la |los |las )?(?:dejo|dejamos|dejaremos)\b[^.\n]{0,40}\ben pausa\b",
    r"\bqued(?:o|amos) atent[oa]s? (?:para|a) cuando\b",
    r"\bno (?:le|les) (?:volvere|volveremos) a escribir\b",
    r"\b(?:doy|damos) por cerrad[oa]\b",
    r"\b(?:lo |la )?retom(?:o|amos|aremos|emos) (?:en|el|la|a|para) ",
    r"\bcuando (?:tengan|tenga) [^.\n]{0,60}\b(?:me|nos) (?:avis|escrib)",
]
_CLOSING_RE = [re.compile(p) for p in _CLOSING]

_MONTHS = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
     "noviembre", "diciembre"], start=1)}
_NUMBERS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
            "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12}
_MONTH_RE = re.compile(r"\b(?:en|hasta|para|a partir de|desde|el mes de|de) (" + "|".join(_MONTHS) + r")\b")
_NEXT_YEAR_RE = re.compile(r"\b(?:proximo ano|el otro ano|ano que viene|siguiente ano)\b")
_IN_RE = re.compile(r"\ben (\d{1,2}|" + "|".join(_NUMBERS) + r") (semana|semanas|mes|meses)\b")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[ \t]+", " ", text)


def _add_months(day: dt.date, months: int) -> dt.date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    for d in (day.day, 30, 29, 28):
        try:
            return dt.date(year, month, d)
        except ValueError:
            continue
    raise ValueError(day)


def _first_working_day_of_year(year: int) -> dt.date:
    day = dt.date(year, 1, 2)  # 1 January is a holiday
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def _until(text: str, sent: dt.date) -> dt.date | None:
    """The day the email names, or None. A named month means its first day, this year if still
    ahead, else next year; «el próximo año» the first working day of January; «en N semanas/meses»
    that long after the email."""
    m = _IN_RE.search(text)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMBERS[m.group(1)]
        return sent + dt.timedelta(weeks=n) if m.group(2).startswith("semana") else _add_months(sent, n)
    m = _MONTH_RE.search(text)
    if m:
        month = _MONTHS[m.group(1)]
        year = sent.year if month > sent.month else sent.year + 1
        return dt.date(year, month, 1)
    if _NEXT_YEAR_RE.search(text):
        return _first_working_day_of_year(sent.year + 1)
    return None


def read_outbound_intent(text: str, sent: dt.date) -> dict | None:
    """`{closing: True, until: "YYYY-MM-DD" | None, phrase}` when the new part of our email closes the
    follow-up, else None."""
    own = _plain(reply_text(text or ""))
    for pattern in _CLOSING_RE:
        m = pattern.search(own)
        if m:
            until = _until(own, sent)
            return {"closing": True, "until": until.isoformat() if until else None,
                    "phrase": m.group(0).strip()[:80]}
    return None


__all__ = ["OUTBOUND_INTENT_VALUE_NORM", "read_outbound_intent"]
