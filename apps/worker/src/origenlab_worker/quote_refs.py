"""Quote numbers named in what the sender wrote (spec 2026-10-14, «Hoy» automations).

The API's rules read a subject and attachment names only; a payment notice, an order or a reply
often names its quote in the body («Transferencia por cotización N°09259-26»). The triage reads
the sender's own text (no quoted history, no signature: `mail_text.MailText.body`) for the same
tokens the subject reader knows (`mail_rules.quote_number_tokens`) and stores them as the
`message_triage` assertion `quote_refs:v1` — the tokens only, never the text. The rules
(`mail_rules.py` R2) then file the email on the case of that quote.

A body is noisier than a subject: a phone «2299-1234» has the shape of a number with a year, so a
four-digit year must be 20xx and a two-digit year 20–39.
"""

from __future__ import annotations

import re

from origenlab_api.v2.mail_rules import quote_number_tokens

QUOTE_REFS_VALUE_NORM = "quote_refs:v1"
_YEAR = re.compile(r"-(\d{2}|\d{4})$")


def _plausible(token: str) -> bool:
    m = _YEAR.search(token)
    if m is None:
        return token.startswith("CN")
    year = m.group(1)
    return year.startswith("20") if len(year) == 4 else 20 <= int(year) <= 39


def read_quote_refs(subject: str | None, body: str | None) -> list[str]:
    """The quote tokens of the body, in order, once each. The subject is the rules' own reading."""
    del subject  # the rules read the subject themselves (`subject_quote_keys`)
    out: list[str] = []
    for token in quote_number_tokens(body):
        if _plausible(token) and token not in out:
            out.append(token)
    return out


__all__ = ["QUOTE_REFS_VALUE_NORM", "read_quote_refs"]
