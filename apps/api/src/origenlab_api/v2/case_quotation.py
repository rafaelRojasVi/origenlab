"""The one writer of a sent quotation on a case: `crm.quote` + a `historical_import` revision.

Three callers, one implementation:

* **The historical import** — `record_historical_quotation` (`quote_import_repository.py`), from
  the owner's reviewed `document_decisions.jsonl`.
* **The email → cases rules** — R3/R4, a quote PDF sent from contacto@ (`mail_rules_repository.py`
  runs `record_historical_quotation` through the import repository's handler).
* **A person** — `POST /v2/commands/record-case-quotation` (`case_command_routes.py`), the case
  drawer's «Registrar cotización»: a quote already sent from Gmail, on a message already linked
  to the case.

What a recorded quotation *is* — the printed number (`number_origin = 'printed_historical'`), a
`sent` revision carrying the exact document and the message that carried it, one document never
two revisions, a number never shared with a minted quote nor silently with another case — is
decided only here, so the import, a rule and a person cannot record three different kinds of
quote. Who acted is the identity the caller passes (`CommandTransaction._append_event`).

`repo` is the `CommandTransaction` whose transaction and receipt this runs on: it supplies
`_row` and `_append_event` to `record_quotation`. `record_case_quotation` — the person's entry —
also needs the `V2CaseCommandRepository`'s `_live_case` (the same compare-and-set and closed-case
refusal as every case command) and `bump_case_version`.

Writes, in the caller's transaction or not at all:

1. the `crm.quote` for (case, printed number) if it does not exist yet — `quote.created`;
2. one `crm.quote_revision` for the document, `origin = 'historical_import'`, `status = 'sent'`
   — `quote_revision.historical_recorded`;
3. when asked, the supersession of the earlier revision — `quote_revision.superseded`;
4. the document's `document_reference` assertion resolved `promoted` → the revision —
   `assertion.promoted` (a record the live Gmail capture wrote carries no assertions; for it
   this step is skipped).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from origenlab_api.v2.case_commands import RECORD_CASE_QUOTATION
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    STAGES_THAT_MAY_CARRY_A_QUOTE,
)

DOCUMENT_REFERENCE_PREFIX = "sha256:"


def document_reference_value(sha256: str) -> str:
    """`evidence.assertion.value_norm` of the `document_reference` naming one exact document."""
    return f"{DOCUMENT_REFERENCE_PREFIX}{sha256}"


def record_quotation(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Record one sent document as a revision of the case's quote carrying its printed number.

    Refused, with nothing written: a case that is not at `quoting`/`negotiating` or has no
    requesting institution; an evidence record that is missing, quarantined, or does not carry
    the document; a staged record without the document's `document_reference` assertion; a
    document already recorded on any quote; a number V2 minted; a printed number that is already
    a quote on another case, unless `number_on_other_opportunity_reason` says why both are real.
    """
    # ── the case: it exists, it is quoting, someone decided who is asking ─────────────
    cur.execute(
        """
        select id::text as id, stage, organization_id::text as organization_id, version
          from crm.opportunity where id = %s for share
        """,
        (fields["opportunity_id"],),
    )
    case = repo._row(cur)
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
    record = repo._row(cur)
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
    if (existing := repo._row(cur)) is not None:
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
    assertion = repo._row(cur)
    # Gmail capture records can acquire independent message_triage assertions later.
    # Those annotations do NOT turn a live captured PDF into a historical-import
    # document. Only document_reference assertions indicate the import's staged
    # document ledger: a missing reference for this SHA must still refuse.
    # The exact PDF remains bound to this Gmail record by carries_document above.
    live_capture = (
        record["kind"] == "gmail_message"
        and assertion is None
        and not _has_document_reference_assertions(cur, record["id"])
    )
    if assertion is None and not live_capture:
        raise CommandRefused(
            409,
            "document_reference_not_staged",
            "the evidence record has no document_reference assertion for this document",
        )
    if assertion is not None and assertion["resolution"] != "unresolved":
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
        quote = repo._row(cur)
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
        quote = repo._row(cur)
        assert quote is not None  # noqa: S101
        created.append("quote")
        events.append(repo._append_event(
            cur, aggregate_kind="quote", aggregate_id=quote["id"], event_type="quote.created",
            payload={
                "opportunity_id": case["id"],
                "quote_number": fields["quote_number"],
                "number_origin": "printed_historical",
                "printed_quote_numbers": fields["printed_quote_numbers"],
                "origin_source_record_id": record["id"],
                "ledger_decision_id": fields["ledger_decision_id"],
                "quote_number_derivation": fields.get("quote_number_derivation"),
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
        prior = repo._row(cur)
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
    next_row = repo._row(cur)
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
    revision = repo._row(cur)
    assert revision is not None  # noqa: S101
    created.append("quote_revision")
    events.append(repo._append_event(
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
            "quote_number_derivation": fields.get("quote_number_derivation"),
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
        events.append(repo._append_event(
            cur, aggregate_kind="quote_revision", aggregate_id=prior["id"],
            event_type="quote_revision.superseded",
            payload={"quote_id": quote["id"], "revision_no": int(prior["revision_no"]),
                     "superseded_by_revision_no": revision_no,
                     "superseded_by_pdf_sha256": fields["document_sha256"], "note": fields["note"]},
            operator=operator, receipt_id=receipt_id,
        ))

    if assertion is None:
        return _recorded(case, quote, revision, revision_no, prior, None, created, events, fields)

    cur.execute(
        """
        update evidence.assertion
           set resolution = 'promoted', resolved_kind = 'quote_revision', resolved_id = %s,
               resolved_at = now(), resolved_by_operator_id = %s, updated_at = now()
         where id = %s
        """,
        (revision["id"], operator.operator_id, assertion["id"]),
    )
    events.append(repo._append_event(
        cur, aggregate_kind="assertion", aggregate_id=assertion["id"],
        event_type="assertion.promoted",
        payload={"kind": "document_reference", "resolved_kind": "quote_revision",
                 "resolved_id": revision["id"], "source_record_id": record["id"],
                 "note": fields["note"]},
        operator=operator, receipt_id=receipt_id,
    ))

    return _recorded(case, quote, revision, revision_no, prior, assertion["id"], created, events, fields)





def record_case_quotation(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """«Registrar cotización» / «Nueva revisión»: a quote already sent, on a message linked here.

    This handler decides only what is particular to a person recording it from the case
    drawer — the message must already be linked to *this* case, be a Gmail message, carry the
    document, and say when it was sent — and then hands the quote to the one writer
    (`case_quotation.record_quotation`), the same one the historical import and rules R3/R4
    use. Everything that writer refuses stays refused here, and in particular a printed number
    that is already a quote on another case (`number_on_other_opportunity`, 409): this request
    has no field for the reason that would accept the collision.

    `supersedes_revision_no` makes it a new revision of the case's quote with that number,
    replacing the named one; without it, a second document under an existing number is a
    second current revision, which the card then asks the operator to resolve.
    """
    case = repo._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])
    cur.execute(
        """
        select sr.id::text as id, sr.kind, sr.payload ->> 'sent_at' as sent_at,
               coalesce(sr.payload -> 'documents', '[]'::jsonb) as documents
          from crm.opportunity_evidence e
          join evidence.source_record sr on sr.id = e.source_record_id
         where e.opportunity_id = %s and e.source_record_id = %s and e.unlinked_at is null
         limit 1
        """,
        (case["id"], fields["source_record_id"]),
    )
    message = repo._row(cur)
    if message is None:
        raise CommandRefused(
            404, "message_not_on_case", "that message is not linked to this case; link it first"
        )
    if message["kind"] != "gmail_message":
        raise CommandRefused(
            409, "not_a_gmail_message", "a sent quote is recorded from the Gmail message that carried it"
        )
    documents = message["documents"]
    if isinstance(documents, str):  # pragma: no cover - driver-dependent jsonb decoding
        documents = json.loads(documents)
    document = next(
        (d for d in documents or []
         if isinstance(d, dict) and str(d.get("sha256", "")).lower() == fields["document_sha256"]),
        None,
    )
    if document is None:
        raise CommandRefused(
            409, "source_record_does_not_carry_document", "that message does not carry this document"
        )
    sent_at = _aware_datetime(message["sent_at"])
    if sent_at is None:
        raise CommandRefused(
            409, "message_has_no_sent_at", "that message records no sending time; it cannot evidence a send"
        )

    supersedes_sha: str | None = None
    if fields["supersedes_revision_no"] is not None:
        cur.execute(
            """
            select r.pdf_sha256
              from crm.quote_revision r join crm.quote q on q.id = r.quote_id
             where q.opportunity_id = %s and q.quote_number = %s and r.revision_no = %s
            """,
            (case["id"], fields["quote_number"], fields["supersedes_revision_no"]),
        )
        prior = repo._row(cur)
        if prior is None or prior["pdf_sha256"] is None:
            raise CommandRefused(
                404,
                "superseded_revision_not_on_case",
                f"quote {fields['quote_number']} on this case has no revision "
                f"{fields['supersedes_revision_no']} with a document to replace",
            )
        supersedes_sha = prior["pdf_sha256"]

    recorded = record_quotation(
        repo, cur, operator,
        {
            "opportunity_id": case["id"],
            "quote_number": fields["quote_number"],
            # The operator read this number on the document; it is printed, never minted.
            "printed_quote_numbers": [fields["quote_number"]],
            "quote_number_derivation": None,
            "document_sha256": fields["document_sha256"],
            "sent_at": sent_at,
            "origin_source_record_id": message["id"],
            "ledger_decision_id": f"case-command:{receipt_id}",
            "filename": document.get("filename"),
            "supersedes_document_sha256": supersedes_sha,
            "number_on_other_opportunity_reason": None,
            "note": fields["note"],
        },
        receipt_id,
    )
    return {
        **recorded,
        "command": RECORD_CASE_QUOTATION,
        "opportunity_version": repo.bump_case_version(cur, case),
    }


def _recorded(
    case: dict[str, Any], quote: dict[str, Any], revision: dict[str, Any], revision_no: int,
    prior: dict[str, Any] | None, assertion_id: str | None, created: list[str], events: list[str],
    fields: dict[str, Any],
) -> dict[str, Any]:
    return {
        "command": RECORD_HISTORICAL_QUOTATION,
        "opportunity_id": case["id"],
        "quote_id": quote["id"],
        "quote_number": fields["quote_number"],
        "quote_revision_id": revision["id"],
        "revision_no": revision_no,
        "quote_revision_version": int(revision["version"]),
        "superseded_revision_id": prior["id"] if prior else None,
        "assertion_id": assertion_id,
        "created": created,
        "event_ids": events,
    }


def _has_document_reference_assertions(cur: Any, source_record_id: str) -> bool:
    """Only the document-reference ledger distinguishes a staged PDF from live capture.

    Message triage and requester-classification assertions are independent annotations.
    A staged source with a document_reference for another PDF must not silently promote
    an unasserted PDF as live capture.
    """
    cur.execute(
        "select 1 from evidence.assertion where source_record_id = %s "
        "and kind = 'document_reference' limit 1",
        (source_record_id,),
    )
    return cur.fetchone() is not None


def _aware_datetime(raw: Any) -> datetime | None:
    """The Gmail capture's `sent_at` (ISO-8601 with an offset), or None when absent or naive."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else None
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None
