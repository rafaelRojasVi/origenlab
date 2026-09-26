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
document; a document already recorded on any quote; a number V2 minted; a printed number that is
already a quote on another case, unless the reason for accepting that collision is given.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.command_core import CommandTransaction, json_payload
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    STAGES_THAT_MAY_CARRY_A_QUOTE,
    VOID_HISTORICAL_QUOTE_REVISION,
)

DOCUMENT_REFERENCE_PREFIX = "sha256:"


def document_reference_value(sha256: str) -> str:
    """`evidence.assertion.value_norm` of the `document_reference` naming one exact document."""
    return f"{DOCUMENT_REFERENCE_PREFIX}{sha256}"


class V2QuoteImportRepository(CommandTransaction):
    """`record_historical_quotation` and `void_historical_quote_revision`."""

    def _record_historical_quotation(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        # ── the case: it exists, it is quoting, someone decided who is asking ─────────────
        cur.execute(
            """
            select id::text as id, stage, organization_id::text as organization_id, version
              from crm.opportunity where id = %s for share
            """,
            (fields["opportunity_id"],),
        )
        case = self._row(cur)
        if case is None:
            raise CommandRefused(404, "opportunity_not_found", "no such case")
        if case["stage"] not in STAGES_THAT_MAY_CARRY_A_QUOTE:
            raise CommandRefused(
                409,
                "case_not_quoting",
                f"the case is at '{case['stage']}'; a quote is recorded on a case at "
                f"{' or '.join(STAGES_THAT_MAY_CARRY_A_QUOTE)}",
            )
        if case["organization_id"] is None:
            raise CommandRefused(
                409,
                "case_has_no_requesting_institution",
                "a quote exists only on a case whose requesting institution an operator confirmed",
            )

        # ── the evidence: exists, not quarantined, carries this exact document ────────────
        cur.execute(
            """
            select id::text as id, kind, is_quarantined,
                   coalesce(payload -> 'documents', '[]'::jsonb) @> jsonb_build_array(
                       jsonb_build_object('sha256', %s::text)) as carries_document
              from evidence.source_record where id = %s for share
            """,
            (fields["document_sha256"], fields["origin_source_record_id"]),
        )
        record = self._row(cur)
        if record is None:
            raise CommandRefused(404, "source_record_not_found", "no such evidence record")
        if record["is_quarantined"]:
            raise CommandRefused(409, "source_record_quarantined", "the evidence record is quarantined")
        if not record["carries_document"]:
            raise CommandRefused(
                409,
                "source_record_does_not_carry_document",
                "the evidence record does not list this document; a quotation is recorded "
                "only from the message that carried it",
            )
        # ── one document is one revision, anywhere ────────────────────────────────────────
        cur.execute(
            """
            select r.id::text as id, q.quote_number, q.opportunity_id::text as opportunity_id
              from crm.quote_revision r join crm.quote q on q.id = r.quote_id
             where r.pdf_sha256 = %s
            """,
            (fields["document_sha256"],),
        )
        if (existing := self._row(cur)) is not None:
            raise CommandRefused(
                409,
                "document_already_recorded",
                f"this document is already revision {existing['id']} of quote "
                f"{existing['quote_number']}",
            )

        cur.execute(
            """
            select id::text as id, resolution from evidence.assertion
             where source_record_id = %s and kind = 'document_reference' and value_norm = %s
               for update
            """,
            (record["id"], document_reference_value(fields["document_sha256"])),
        )
        assertion = self._row(cur)
        if assertion is None:
            raise CommandRefused(
                409,
                "document_reference_not_staged",
                "the evidence record has no document_reference assertion for this document",
            )
        if assertion["resolution"] != "unresolved":
            raise CommandRefused(
                409,
                "document_reference_already_resolved",
                f"the document_reference assertion is already '{assertion['resolution']}'",
            )

        # ── the quote: (case, printed number) ─────────────────────────────────────────────
        cur.execute(
            """
            select id::text as id, number_origin, opportunity_id::text as opportunity_id
              from crm.quote where quote_number = %s
            """,
            (fields["quote_number"],),
        )
        same_number = [
            dict(zip([d[0] for d in cur.description], row, strict=True)) for row in cur.fetchall()
        ]
        if any(q["number_origin"] == "minted" for q in same_number):
            raise CommandRefused(
                409,
                "number_is_a_minted_quote",
                "V2 minted this number for one of its own quotes; a printed historical "
                "document cannot share it",
            )
        others = [q for q in same_number if q["opportunity_id"] != case["id"]]
        if others and fields["number_on_other_opportunity_reason"] is None:
            raise CommandRefused(
                409,
                "number_on_other_opportunity",
                "this printed number is already a quote on another case (gate G1 allows it "
                "per case); number_on_other_opportunity_reason must say why both are real",
            )
        mine = [q for q in same_number if q["opportunity_id"] == case["id"]]

        events: list[str] = []
        created: list[str] = []
        if mine:
            cur.execute("select id::text as id from crm.quote where id = %s for update", (mine[0]["id"],))
            quote = self._row(cur)
            assert quote is not None  # noqa: S101
        else:
            cur.execute(
                """
                insert into crm.quote
                    (opportunity_id, quote_number, number_origin, created_by_operator_id,
                     origin_source_record_id)
                values (%s, %s, 'printed_historical', %s, %s)
                returning id::text as id
                """,
                (case["id"], fields["quote_number"], operator.operator_id, record["id"]),
            )
            quote = self._row(cur)
            assert quote is not None  # noqa: S101
            created.append("quote")
            events.append(self._append_event(
                cur, aggregate_kind="quote", aggregate_id=quote["id"], event_type="quote.created",
                payload={
                    "opportunity_id": case["id"],
                    "quote_number": fields["quote_number"],
                    "number_origin": "printed_historical",
                    "printed_quote_numbers": fields["printed_quote_numbers"],
                    "origin_source_record_id": record["id"],
                    "ledger_decision_id": fields["ledger_decision_id"],
                    "number_on_other_opportunity_reason": fields["number_on_other_opportunity_reason"],
                    "other_opportunities_with_number": sorted(q["opportunity_id"] for q in others),
                    "note": fields["note"],
                },
                operator=operator, receipt_id=receipt_id,
            ))

        # ── the revision ──────────────────────────────────────────────────────────────────
        prior: dict[str, Any] | None = None
        if fields["supersedes_document_sha256"] is not None:
            cur.execute(
                """
                select id::text as id, revision_no, status, superseded_by_revision_no, version
                  from crm.quote_revision
                 where quote_id = %s and pdf_sha256 = %s and origin = 'historical_import'
                   for update
                """,
                (quote["id"], fields["supersedes_document_sha256"]),
            )
            prior = self._row(cur)
            if prior is None:
                raise CommandRefused(
                    409,
                    "superseded_document_not_on_this_quote",
                    "the document this one supersedes is not a revision of the same quote",
                )
            if prior["superseded_by_revision_no"] is not None:
                raise CommandRefused(409, "already_superseded", "that revision is already superseded")
            if prior["status"] != "sent":
                raise CommandRefused(409, "superseded_revision_not_sent", "only a sent revision is superseded")

        cur.execute(
            "select coalesce(max(revision_no), 0) + 1 as next from crm.quote_revision where quote_id = %s",
            (quote["id"],),
        )
        next_row = self._row(cur)
        assert next_row is not None  # noqa: S101
        revision_no = int(next_row["next"])
        cur.execute(
            """
            insert into crm.quote_revision
                (quote_id, revision_no, status, origin, origin_source_record_id, pdf_sha256,
                 sent_at, created_by_operator_id)
            values (%s, %s, 'sent', 'historical_import', %s, %s, %s, %s)
            returning id::text as id, version
            """,
            (quote["id"], revision_no, record["id"], fields["document_sha256"], fields["sent_at"],
             operator.operator_id),
        )
        revision = self._row(cur)
        assert revision is not None  # noqa: S101
        created.append("quote_revision")
        events.append(self._append_event(
            cur, aggregate_kind="quote_revision", aggregate_id=revision["id"],
            event_type="quote_revision.historical_recorded",
            payload={
                "quote_id": quote["id"],
                "quote_number": fields["quote_number"],
                "revision_no": revision_no,
                "pdf_sha256": fields["document_sha256"],
                "sent_at": fields["sent_at"].isoformat(),
                "origin_source_record_id": record["id"],
                "ledger_decision_id": fields["ledger_decision_id"],
                "filename": fields["filename"],
                "note": fields["note"],
            },
            operator=operator, receipt_id=receipt_id,
        ))

        if prior is not None:
            cur.execute(
                """
                update crm.quote_revision
                   set superseded_by_revision_no = %s, superseded_at = now(),
                       version = version + 1, updated_at = now()
                 where id = %s
                """,
                (revision_no, prior["id"]),
            )
            events.append(self._append_event(
                cur, aggregate_kind="quote_revision", aggregate_id=prior["id"],
                event_type="quote_revision.superseded",
                payload={"quote_id": quote["id"], "revision_no": int(prior["revision_no"]),
                         "superseded_by_revision_no": revision_no,
                         "superseded_by_pdf_sha256": fields["document_sha256"], "note": fields["note"]},
                operator=operator, receipt_id=receipt_id,
            ))

        cur.execute(
            """
            update evidence.assertion
               set resolution = 'promoted', resolved_kind = 'quote_revision', resolved_id = %s,
                   resolved_at = now(), resolved_by_operator_id = %s, updated_at = now()
             where id = %s
            """,
            (revision["id"], operator.operator_id, assertion["id"]),
        )
        events.append(self._append_event(
            cur, aggregate_kind="assertion", aggregate_id=assertion["id"],
            event_type="assertion.promoted",
            payload={"kind": "document_reference", "resolved_kind": "quote_revision",
                     "resolved_id": revision["id"], "source_record_id": record["id"],
                     "note": fields["note"]},
            operator=operator, receipt_id=receipt_id,
        ))

        return {
            "command": RECORD_HISTORICAL_QUOTATION,
            "opportunity_id": case["id"],
            "quote_id": quote["id"],
            "quote_number": fields["quote_number"],
            "quote_revision_id": revision["id"],
            "revision_no": revision_no,
            "quote_revision_version": int(revision["version"]),
            "superseded_revision_id": prior["id"] if prior else None,
            "assertion_id": assertion["id"],
            "created": created,
            "event_ids": events,
        }

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
        RECORD_HISTORICAL_QUOTATION: _record_historical_quotation,
        VOID_HISTORICAL_QUOTE_REVISION: _void_historical_quote_revision,
    }


__all__ = ["V2QuoteImportRepository", "document_reference_value", "json_payload"]
