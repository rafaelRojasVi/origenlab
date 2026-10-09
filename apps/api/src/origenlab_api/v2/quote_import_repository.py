"""The historical-quotation commands, each in one transaction.

Built on `command_core.CommandTransaction`: one read-write transaction, one receipt per
`(operator, Idempotency-Key)`, one `crm.domain_event` per change. Runs as `origenlab_api`.

`record_historical_quotation` writes, in one transaction or not at all:

1. the `crm.quote` for (case, printed number) if it does not exist yet — `quote.created`;
2. one `crm.quote_revision` for the document, `origin = 'historical_import'`, `status = 'sent'`
   — `quote_revision.historical_recorded`;
3. when asked, the supersession of the earlier revision — `quote_revision.superseded`;
4. the document's `document_reference` assertion resolved `promoted` → the revision —
   `assertion.promoted`.

Refused, with nothing written: a case that is not at `quoting`/`negotiating` or has no
requesting institution; an evidence record that is missing, quarantined, or does not carry the
document; a staged record without the document's `document_reference` assertion (a record the
live Gmail capture wrote carries no assertions at all, and for it step 4 is skipped); a document already recorded on any quote; a number V2 minted; a printed number that is
already a quote on another case, unless the reason for accepting that collision is given.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.case_quotation import (
    DOCUMENT_REFERENCE_PREFIX,
    document_reference_value,
    record_quotation,
)
from origenlab_api.v2.command_core import CommandTransaction, json_payload
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    VOID_HISTORICAL_QUOTE_REVISION,
)


class V2QuoteImportRepository(CommandTransaction):
    """`record_historical_quotation` and `void_historical_quote_revision`."""

    def _void_historical_quote_revision(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        cur.execute(
            """
            select id::text as id, quote_id::text as quote_id, revision_no, status, origin, version
              from crm.quote_revision where id = %s for update
            """,
            (fields["quote_revision_id"],),
        )
        revision = self._row(cur)
        if revision is None:
            raise CommandRefused(404, "quote_revision_not_found", "no such quote revision")
        if revision["origin"] != "historical_import":
            raise CommandRefused(409, "not_historical", "only a historical revision is voided here")
        if int(revision["version"]) != fields["quote_revision_version"]:
            raise CommandRefused(409, "quote_revision_version_conflict", "the revision changed; re-read it")
        if revision["status"] != "sent":
            raise CommandRefused(409, "not_sent", f"the revision is '{revision['status']}', not 'sent'")
        cur.execute(
            """
            update crm.quote_revision set status = 'void', version = version + 1, updated_at = now()
             where id = %s returning version
            """,
            (revision["id"],),
        )
        new_version = self._row(cur)
        assert new_version is not None  # noqa: S101
        event = self._append_event(
            cur, aggregate_kind="quote_revision", aggregate_id=revision["id"],
            event_type="quote_revision.transitioned",
            payload={"from": "sent", "to": "void", "quote_id": revision["quote_id"],
                     "revision_no": int(revision["revision_no"]), "note": fields["note"]},
            operator=operator, receipt_id=receipt_id,
        )
        return {
            "command": VOID_HISTORICAL_QUOTE_REVISION,
            "quote_revision_id": revision["id"],
            "status": "void",
            "quote_revision_version": int(new_version["version"]),
            "event_ids": [event],
        }

    _HANDLERS = {
        # The one writer of a sent quotation, shared with the case command
        # `record_case_quotation` (`case_quotation.py`).
        RECORD_HISTORICAL_QUOTATION: record_quotation,
        VOID_HISTORICAL_QUOTE_REVISION: _void_historical_quote_revision,
    }


__all__ = ["DOCUMENT_REFERENCE_PREFIX", "V2QuoteImportRepository", "document_reference_value", "json_payload"]
