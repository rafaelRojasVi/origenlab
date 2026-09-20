"""Free-text redaction for migration evidence bundles.

The V1 outbound-safety tables mix two very different kinds of column:

* **structured safety fields** — a state, a closed reason code, a timestamp, a
  normalized address, an operator id. These are what a V2 loader needs in order
  to reproduce V1 behaviour, and they travel verbatim;
* **unrestricted operator free text** — a note, a justification, an evidence
  paragraph, a raw API error string. Nobody constrained what an operator (or an
  exception's ``str()``) could put there, so it may carry third-party names,
  addresses, quoted message content or anything else. **It never leaves the
  source database.**

For a free-text column ``c`` this module emits exactly two derived fields:

``c_present``
    ``true`` when the source value was a non-empty string after normalization.
    This preserves the provenance fact "a note existed here".

``c_sha256``
    SHA-256 over the *normalized* original (present only when
    ``c_present`` is true). Two rows carrying the same note share the digest, so
    a later reconciliation can prove equality — **but a digest cannot
    reconstruct the content**. It is a one-way function over text that is not
    guessable from the bundle, and the bundle carries no dictionary to test
    candidates against. Treat it as proof of sameness, never as a recoverable
    copy.

The original string is never written to a file, never placed in a manifest,
never logged and never printed.

Closed vocabularies are the deliberate exception: ``suppression_reason_code``,
``block_reason``, ``selection_reason`` and ``error_code`` carry defined
operational semantics drawn from a fixed code list, and V2 needs them to set a
block's ``purpose``. They are exported **only** when the observed value is in
the declared vocabulary; anything else is treated as free text and digested,
because an out-of-vocabulary value is by definition unconstrained.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from origenlab_email_pipeline.candidate_export_gate import (
    REASON_DOMAIN_SUPPRESSION,
    REASON_INTERNAL_DOMAIN,
    REASON_INVALID_EMAIL,
    REASON_NOISE_EMAIL,
    REASON_NOISE_ORGANIZATION,
    REASON_OUTREACH_CONTACTED,
    REASON_OUTREACH_REPLIED,
    REASON_OUTREACH_SNOOZED,
    REASON_SENT_HISTORY,
    REASON_SUPPLIER_DOMAIN,
    REASON_SUPPRESSION,
)
from origenlab_email_pipeline.contact_email_suppression import SUPPRESSION_REASON_CODES

#: Unrestricted free-text columns, by table. Exported as ``_present`` + digest.
#:
#: ``outbound_send_attempt.error_detail`` is ``str(exc)`` from the Gmail API
#: call (``outbound_campaign_sender``): an exception string that can embed the
#: recipient address and arbitrary API text. It is free text, not a code.
FREE_TEXT_COLUMNS: dict[str, tuple[str, ...]] = {
    "outbound_campaign": (),
    "outbound_campaign_recipient": (),
    "outbound_send_attempt": ("error_detail",),
    "manual_contact_status": ("reason", "evidence"),
    "contact_email_suppression": ("suppression_reason_text",),
    "contact_domain_suppression": ("suppression_reason_text",),
    "outreach_contact_state": ("notes",),
}

#: The gate reason codes ``outbound_campaign_store`` writes into
#: ``block_reason``, plus its ``"ineligible"`` fallback and the
#: ``"gate_eligible"`` literal it writes into ``selection_reason``.
GATE_REASON_CODES: tuple[str, ...] = (
    REASON_INVALID_EMAIL,
    REASON_INTERNAL_DOMAIN,
    REASON_SUPPRESSION,
    REASON_DOMAIN_SUPPRESSION,
    REASON_SENT_HISTORY,
    REASON_OUTREACH_CONTACTED,
    REASON_OUTREACH_REPLIED,
    REASON_OUTREACH_SNOOZED,
    REASON_SUPPLIER_DOMAIN,
    REASON_NOISE_EMAIL,
    REASON_NOISE_ORGANIZATION,
    "ineligible",
)

#: Columns whose values carry defined semantics *only* while they stay inside a
#: declared vocabulary. A value outside it is unconstrained text and is
#: digested like any other free-text field.
CLOSED_VOCABULARY_COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "outbound_campaign_recipient": {
        "block_reason": GATE_REASON_CODES,
        "selection_reason": ("gate_eligible",),
    },
    "outbound_send_attempt": {
        "error_code": GATE_REASON_CODES,
    },
    "contact_email_suppression": {
        "suppression_reason_code": SUPPRESSION_REASON_CODES,
    },
}

_WHITESPACE = re.compile(r"\s+")


def normalize_free_text(value: str) -> str:
    """Unicode NFC, trimmed, internal whitespace runs collapsed to one space.

    Normalizing before hashing means two notes that differ only in line
    endings, trailing blanks or Unicode composition share a digest. Case is
    **not** folded: the digest identifies the text as written.
    """
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value)).strip()


def free_text_digest(value: str) -> str:
    """SHA-256 over :func:`normalize_free_text`. One-way; never reversible."""
    return hashlib.sha256(normalize_free_text(value).encode("utf-8")).hexdigest()


def _redact_one(record: dict[str, Any], column: str) -> None:
    """Replace ``column`` in place with its ``_present`` / ``_sha256`` pair."""
    raw = record.pop(column, None)
    text = normalize_free_text(raw) if isinstance(raw, str) else ""
    record[f"{column}_present"] = bool(text)
    if text:
        record[f"{column}_sha256"] = free_text_digest(raw)


def redact_row(table: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return ``record`` with every free-text column of ``table`` replaced.

    Out-of-vocabulary values in a closed-vocabulary column are demoted to free
    text and digested, so an unconstrained value can never reach the bundle by
    claiming to be a reason code.
    """
    out = dict(record)
    for column in FREE_TEXT_COLUMNS.get(table, ()):
        if column in out:
            _redact_one(out, column)
    for column, allowed in CLOSED_VOCABULARY_COLUMNS.get(table, {}).items():
        if column not in out:
            continue
        value = out[column]
        if value is None:
            continue
        if not isinstance(value, str) or value.strip() not in allowed:
            _redact_one(out, column)
    return out


def assert_no_free_text_remains(table: str, record: dict[str, Any]) -> None:
    """Refuse a record that still carries a declared free-text column.

    The last guard before a row is written: a redaction that silently did
    nothing would otherwise ship the note.
    """
    leaked = sorted(set(FREE_TEXT_COLUMNS.get(table, ())) & set(record))
    if leaked:
        raise ValueError(f"free-text column(s) survived redaction in {table}: {leaked}")


def free_text_policy_document() -> dict[str, Any]:
    """The policy as bundled evidence, so a reader need not read this module."""
    return {
        "free_text_columns": {t: list(c) for t, c in FREE_TEXT_COLUMNS.items() if c},
        "closed_vocabulary_columns": {
            table: {column: list(values) for column, values in columns.items()}
            for table, columns in CLOSED_VOCABULARY_COLUMNS.items()
        },
        "emitted_fields": {
            "<column>_present": "true when a non-empty value existed in the source row",
            "<column>_sha256": (
                "SHA-256 of the normalized original, emitted only when _present is true"
            ),
        },
        "normalization": "Unicode NFC, trimmed, internal whitespace collapsed to one space",
        "digest_semantics": (
            "The digest proves two values were equal. It CANNOT reconstruct the "
            "content: SHA-256 is one-way and the bundle carries no candidate "
            "dictionary to test against. It is provenance, not a copy."
        ),
        "out_of_vocabulary_policy": (
            "A closed-vocabulary column holding a value outside its declared "
            "vocabulary is unconstrained text and is digested, never exported."
        ),
        "never_exported": (
            "The original free text is never written to a file, a manifest, a "
            "report, a log line or the terminal."
        ),
    }
