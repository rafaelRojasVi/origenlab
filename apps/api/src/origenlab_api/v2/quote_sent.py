"""A Cotizador quote went out by email: its approved revision becomes «sent» (rule R16, owner 2026-10-21).

The Cotizador approves a revision and prints its PDF; Tatiana attaches that PDF in Gmail. The capture
links the email to the case, but nothing told the revision it was sent, so the case stayed in
«Solicitada». This is the one writer of that fact, a step of the email rules' action.

**What it trusts.** The revision's own content: what the PDF says is what the Cotizador approved, so
the PDF is never read (no quote reader, no model call). What it checks is that the email is a
captured `gmail_message` that carries a PDF with exactly that hash whose file name names the
revision's number — the planner matched number and revision letter; this re-checks the number
against the stored email, in this transaction.

**What it writes.** The revision `approved → sent` with `sent_at` (the email's), `sent_message_id`
(the `comms.message` the capture stored for it) and `pdf_sha256` (the attachment's) — the columns
`crm.quote_revision_authored_frozen()` lets change once, on exactly that move — and one
`quote_revision.transitioned` event. No case, link or stage: the rule's other steps do those, on the
case this step answers (its id and current version), so one email's PDFs of two cases move both.
"""

from __future__ import annotations

import json
from typing import Any

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

_REVISION_SQL = """
select r.id::text as id, r.version, r.status, r.origin, r.sent_at, r.pdf_sha256, r.approved_at,
       r.superseded_by_revision_no, q.id::text as quote_id, q.quote_number, q.opportunity_id::text as opportunity_id
  from crm.quote_revision r
  join crm.quote q on q.id = r.quote_id
 where r.id = %s
   for update of r
"""


def record_authored_quote_sent(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                               receipt_id: str) -> dict[str, Any]:
    from origenlab_api.v2.mail_rules import named_revisions, quote_key, year_before

    cur.execute(_REVISION_SQL, (fields["quote_revision_id"],))
    row = cur.fetchone()
    if row is None:
        raise CommandRefused(404, "quote_revision_not_found", "that quote revision does not exist")
    rev = dict(zip([d[0] for d in cur.description], row, strict=True))
    if rev["origin"] != "authored":
        raise CommandRefused(409, "not_a_cotizador_quote", "only a revision the Cotizador issued is marked sent")
    if rev["status"] != "approved" or rev["sent_at"] is not None or rev["superseded_by_revision_no"] is not None:
        raise CommandRefused(409, "revision_not_awaiting_sending",
                             f"the revision is {rev['status']}; only an approved one not yet sent is marked sent")
    if int(rev["version"]) != int(fields["quote_revision_version"]):
        raise CommandRefused(409, "stale_version", "the revision changed since the rules read it")

    cur.execute("select kind, payload from evidence.source_record where id = %s for share",
                (fields["source_record_id"],))
    record = cur.fetchone()
    if record is None or record[0] != "gmail_message":
        raise CommandRefused(404, "source_record_not_found", "that Gmail message does not exist")
    payload = record[1]
    if isinstance(payload, str):  # pragma: no cover - driver-dependent jsonb decoding
        payload = json.loads(payload)
    sha = str(fields["document_sha256"]).lower()
    want = quote_key(rev["quote_number"], None)
    carried = [d for d in payload.get("documents") or [] if isinstance(d, dict)
               and str(d.get("sha256") or "").lower() == sha
               and str(d.get("filename") or "").lower().endswith(".pdf")]
    def names_it(d: dict[str, Any]) -> bool:
        for key, _ in named_revisions(d.get("filename"), payload.get("sent_at")):
            if key == want or year_before(key) == want:
                return True
        return False

    if not any(names_it(d) for d in carried):
        raise CommandRefused(409, "pdf_not_in_message",
                             f"the message carries no PDF of {rev['quote_number']} with that hash")
    if rev["pdf_sha256"] is not None and str(rev["pdf_sha256"]).lower() != sha:
        raise CommandRefused(409, "different_document", "the revision already names another PDF")

    cur.execute("select id::text from comms.message where provider_message_id = %s order by created_at limit 1",
                (payload.get("gmail_message_id"),))
    message = cur.fetchone()
    if message is None:
        raise CommandRefused(409, "message_not_captured", "the capture holds no stored copy of that message yet")

    cur.execute(
        """
        update crm.quote_revision
           set status = 'sent', sent_at = %s, sent_message_id = %s, pdf_sha256 = %s,
               version = version + 1, updated_at = now()
         where id = %s and version = %s
        returning version
        """,
        (payload.get("sent_at"), message[0], sha, rev["id"], rev["version"]),
    )
    updated = cur.fetchone()
    if updated is None:  # pragma: no cover - the row is locked above
        raise CommandRefused(409, "stale_version", "concurrent modification")
    repo._append_event(cur, aggregate_kind="quote_revision", aggregate_id=rev["id"],
                       event_type="quote_revision.transitioned",
                       payload={"from": "approved", "to": "sent", "source_record_id": fields["source_record_id"],
                                "document_sha256": sha, "note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    # The case the next steps of the action act on (the link, the walk to «Enviada»), as it is now.
    cur.execute("select version from crm.opportunity where id = %s for update", (rev["opportunity_id"],))
    case_version = int(cur.fetchone()[0])
    return {"command": "record_authored_quote_sent", "quote_revision_id": rev["id"], "quote_id": rev["quote_id"],
            "quote_number": rev["quote_number"], "version": int(updated[0]),
            "opportunity_id": rev["opportunity_id"], "opportunity_version": case_version}
