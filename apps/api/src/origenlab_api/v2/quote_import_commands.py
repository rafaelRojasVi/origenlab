"""The two historical-quotation commands — requests, refusals, vocabulary.

A historical quotation is a PDF OrigenLab already sent, before V2 existed, that a named
operator confirmed as a customer quotation in `document_decisions.jsonl`. These commands record
that fact in `crm.quote` / `crm.quote_revision` under the `historical_import` origin of
migration `20260925200000`:

| Command | What durable fact it records |
|---|---|
| `record_historical_quotation` | this document, carrying this printed number, was sent on this case |
| `void_historical_quote_revision` | that record was wrong; it stays, marked void, with the reason |

**What neither can do.** Neither mints a number: the number is refused unless the document
itself prints it (`printed_quote_numbers`), exactly, character for character. Neither carries
a price, a currency, a total or an approval — the document was never parsed for them and
`extra="forbid"` refuses a caller who guesses a field name. Neither deletes anything: the only
way back is `void`, which is a new state on the same row plus an event.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

from origenlab_api.v2.commands import CommandRefused, DecisionBody, as_uuid

RECORD_HISTORICAL_QUOTATION = "record_historical_quotation"
VOID_HISTORICAL_QUOTE_REVISION = "void_historical_quote_revision"

QUOTE_IMPORT_COMMAND_NAMES: tuple[str, ...] = (
    RECORD_HISTORICAL_QUOTATION,
    VOID_HISTORICAL_QUOTE_REVISION,
)

#: `crm.quote.quote_number_shape`, restated so a malformed number is a 422 naming the rule.
QUOTE_NUMBER_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: A case may carry a quote only once an operator has decided who is asking (§3.6.5).
STAGES_THAT_MAY_CARRY_A_QUOTE: tuple[str, ...] = ("quoting", "negotiating")


def _sha(value: str, what: str) -> str:
    folded = value.strip().lower()
    if not _SHA256.match(folded):
        raise ValueError(f"{what} must be a lower-case SHA-256 hex digest")
    return folded


class RecordHistoricalQuotationBody(DecisionBody):
    """This document, printing this number, was sent to the customer on this case.

    `printed_quote_numbers` is what the document prints, as the identity report read it.
    `quote_number` must be one of them exactly: the command cannot record a number the
    document does not carry, so "do not invent numbers" is a refusal, not a promise.

    `supersedes_document_sha256` names an earlier revision of the same quote (same number,
    same case) that this document replaces — the owner's canonical choice. It is recorded once
    and never undone.

    `number_on_other_opportunity_reason` is required when the same printed number is already
    a quote on another case. Gate G1 allows it (the number is unique per case), but it is the
    collision the owner reviewed, so it costs a sentence.
    """

    opportunity_id: str
    quote_number: Annotated[str, Field(min_length=1, max_length=32)]
    printed_quote_numbers: Annotated[list[str], Field(min_length=1, max_length=20)]
    document_sha256: str
    sent_at: datetime
    origin_source_record_id: str
    ledger_decision_id: Annotated[str, Field(min_length=1, max_length=100)]
    filename: Annotated[str, Field(min_length=1, max_length=400)] | None = None
    supersedes_document_sha256: str | None = None
    number_on_other_opportunity_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None = None

    @field_validator("document_sha256")
    @classmethod
    def _document(cls, value: str) -> str:
        return _sha(value, "document_sha256")

    @field_validator("supersedes_document_sha256")
    @classmethod
    def _supersedes(cls, value: str | None) -> str | None:
        return None if value is None else _sha(value, "supersedes_document_sha256")

    @field_validator("sent_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("sent_at must carry a UTC offset; a naive time is a guess")
        return value

    @model_validator(mode="after")
    def _number_is_printed(self) -> RecordHistoricalQuotationBody:
        if not QUOTE_NUMBER_SHAPE.match(self.quote_number):
            raise ValueError("quote_number does not have the shape crm.quote accepts")
        if self.quote_number not in self.printed_quote_numbers:
            raise ValueError(
                "quote_number must be one of printed_quote_numbers exactly — a historical "
                "number is the one the document prints, never a normalised or new one"
            )
        if self.supersedes_document_sha256 == self.document_sha256:
            raise ValueError("a document cannot supersede itself")
        return self


class VoidHistoricalQuoteRevisionBody(DecisionBody):
    """The append-only rollback: this historical revision should not have been recorded."""

    quote_revision_id: str
    quote_revision_version: Annotated[int, Field(ge=1)]


QUOTE_IMPORT_BODY_BY_COMMAND: dict[str, type[DecisionBody]] = {
    RECORD_HISTORICAL_QUOTATION: RecordHistoricalQuotationBody,
    VOID_HISTORICAL_QUOTE_REVISION: VoidHistoricalQuoteRevisionBody,
}


def validated_quote_import(command_name: str, body: DecisionBody) -> dict[str, Any]:
    """Everything checkable without the database. Row facts are checked in the transaction."""
    fields: dict[str, Any] = {"note": body.note.strip()}
    if isinstance(body, RecordHistoricalQuotationBody):
        fields.update(
            opportunity_id=as_uuid(body.opportunity_id, "opportunity_id"),
            quote_number=body.quote_number,
            printed_quote_numbers=list(body.printed_quote_numbers),
            document_sha256=body.document_sha256,
            sent_at=body.sent_at,
            origin_source_record_id=as_uuid(body.origin_source_record_id, "origin_source_record_id"),
            ledger_decision_id=body.ledger_decision_id,
            filename=body.filename,
            supersedes_document_sha256=body.supersedes_document_sha256,
            number_on_other_opportunity_reason=(body.number_on_other_opportunity_reason or "").strip() or None,
        )
        return fields
    if isinstance(body, VoidHistoricalQuoteRevisionBody):
        fields.update(
            quote_revision_id=as_uuid(body.quote_revision_id, "quote_revision_id"),
            quote_revision_version=int(body.quote_revision_version),
        )
        return fields
    raise CommandRefused(404, "unknown_command", f"no such command '{command_name}'")
