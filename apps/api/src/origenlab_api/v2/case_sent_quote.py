"""«Completar con la cotización del correo» (2026-10-13): a quote contacto@ sent becomes a case in
«Enviada» with its institution and its quote — one command, one transaction, one receipt.

It invents nothing: each step is the existing writer, run in this transaction under this
command's receipt — `_open_commercial_case` (when no case is named), the shared «por confirmar»
writer `register_machine_organization` (when the institution is new, exactly as rules R4/R10
create one), `_add_case_organization` as requesting institution, `_advance_case_stage` along the
ordinary edges to `quoting`, and `record_case_quotation`, the one writer of a sent quote. Every
refusal of every step stays a refusal, and any refusal rolls the whole command back.

The quote number is read from the message's one quote PDF the way the email rules read it
(`mail_rules.gmail_quote_key` over its CN token and the sending year), so a person confirms the
institution, never retypes the number.
"""

from __future__ import annotations

import json
from typing import Any

from origenlab_api.v2.case_commands import COMPLETE_CASE_FROM_SENT_QUOTE
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

#: Open stages and the edges they walk to «Enviada» (`quoting`); `STAGE_TRANSITIONS` allows each.
_TO_QUOTING: dict[str, tuple[str, ...]] = {
    "lead": ("qualifying", "qualified", "quoting"),
    "qualifying": ("qualified", "quoting"),
    "qualified": ("quoting",),
    "quoting": (),
    "negotiating": (),
}


def _quote_pdf(record: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    """(document sha256, canonical number, printed token, derivation) of the message's one quote PDF.
    Documents are taken in R3's order (file name, sha) so the same message always gives the same one."""
    from origenlab_api.v2.mail_rules import _derivation, canonical_quote_number, gmail_quote_key

    payload = record["payload"]
    if isinstance(payload, str):  # pragma: no cover - driver-dependent jsonb decoding
        payload = json.loads(payload)
    found: dict[str, tuple[str, str, str, dict[str, Any]]] = {}
    docs = [d for d in payload.get("documents") or [] if isinstance(d, dict)]
    for doc in sorted(docs, key=lambda d: (str(d.get("filename") or ""), str(d.get("sha256") or ""))):
        if not str(doc.get("filename") or "").lower().endswith(".pdf"):
            continue
        for token in doc.get("cn_tokens") or []:
            key = gmail_quote_key(str(token), payload.get("sent_at"))
            if key is not None and doc.get("sha256"):
                number = canonical_quote_number(key)
                found.setdefault(number, (str(doc["sha256"]).lower(), number, str(token),
                                          _derivation(str(token), payload.get("sent_at"))))
    if len(found) != 1:
        raise CommandRefused(409, "not_one_quote_pdf",
                             f"the message carries {len(found)} quote PDFs; this completes a message with exactly one")
    return next(iter(found.values()))


def _refuse_another_year(cur: Any, number: str) -> None:
    """R3's guard: the year is read from when the email was sent, so a correlative the CRM already
    holds under another year (a December quote sent in January) waits for a person."""
    from origenlab_api.v2.mail_rules import quote_key

    key = quote_key(number, None)
    cur.execute("select quote_number from crm.quote")
    for (existing,) in cur.fetchall():
        other = quote_key(existing, None) or quote_key(existing, 0)
        if other is not None and key is not None and other[1] == key[1] and other[0] != key[0]:
            raise CommandRefused(409, "correlative_exists_other_year",
                                 f"{number}: the correlative already exists as {existing}; a person decides which year it is")


def complete_case_from_sent_quote(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    from origenlab_api.v2.case_quotation import record_case_quotation
    from origenlab_api.v2.mail_rules_repository import register_machine_organization

    note = fields["note"]
    cur.execute("select id::text as id, kind, payload from evidence.source_record where id = %s for share",
                (fields["source_record_id"],))
    record = repo._row(cur)
    if record is None or record["kind"] != "gmail_message":
        raise CommandRefused(404, "source_record_not_found", "that Gmail message does not exist")
    sha, number, token, derivation = _quote_pdf(record)
    _refuse_another_year(cur, number)

    if fields["opportunity_id"] is None:
        cur.execute("select 1 from crm.opportunity_evidence e join crm.opportunity o on o.id = e.opportunity_id "
                    "where e.source_record_id = %s and e.unlinked_at is null and o.closed_at is null limit 1",
                    (record["id"],))
        if cur.fetchone() is not None:
            raise CommandRefused(409, "message_already_on_a_case",
                                 "that message is already on an open case; complete it from that case")

    if fields["opportunity_id"] is None:
        opened = repo._open_commercial_case(cur, operator, {
            "title": f"Cotización {number}", "origin_source_record_id": record["id"], "note": note}, receipt_id)
        case_id, version = opened["opportunity_id"], int(opened["opportunity_version"])
    else:
        case_id, version = fields["opportunity_id"], int(fields["opportunity_version"])

    if fields["organization_id"] is not None:
        organization_id, organization_version = fields["organization_id"], int(fields["organization_version"])
    else:
        name = fields["new_organization_name"]
        cur.execute("select id::text, version from crm.organization where status = 'active' "
                    "and merged_into_organization_id is null and lower(name) = lower(%s) order by created_at limit 2",
                    (name,))
        same = cur.fetchall()
        if len(same) == 1:
            organization_id, organization_version = same[0][0], int(same[0][1])
        else:
            made = register_machine_organization(repo, cur, operator, {
                "name": name, "domain": None, "origin_source_record_id": record["id"], "note": note}, receipt_id)
            organization_id, organization_version = made["organization_id"], int(made["organization_version"])

    case = repo._live_case(cur, case_id, version)
    if case.get("organization_id") != organization_id:
        added = repo._add_case_organization(cur, operator, {
            "opportunity_id": case_id, "opportunity_version": version, "organization_id": organization_id,
            "organization_version": organization_version, "role": "requesting_institution",
            "supplier_exception_reason": None, "note": note}, receipt_id)
        version = int(added["opportunity_version"])
        case = repo._live_case(cur, case_id, version)

    if case["stage"] not in _TO_QUOTING:
        raise CommandRefused(409, "case_not_open_for_a_quote",
                             f"the case is {case['stage']}; a sent quote completes an open case")
    for stage in _TO_QUOTING[case["stage"]]:
        moved = repo._advance_case_stage(cur, operator, {
            "opportunity_id": case_id, "opportunity_version": version, "stage": stage,
            "close_reason": None, "note": note}, receipt_id)
        version = int(moved["opportunity_version"])

    recorded = record_case_quotation(repo, cur, operator, {
        "opportunity_id": case_id, "opportunity_version": version, "source_record_id": record["id"],
        "document_sha256": sha, "quote_number": number, "supersedes_revision_no": None, "note": note,
        "printed_quote_numbers": [token], "quote_number_derivation": derivation}, receipt_id)
    return {
        "command": COMPLETE_CASE_FROM_SENT_QUOTE,
        "opportunity_id": case_id,
        "opportunity_version": int(recorded.get("opportunity_version") or version),
        "organization_id": organization_id,
        "quote_number": number,
        "quote_id": recorded.get("quote_id"),
        "quote_revision_id": recorded.get("quote_revision_id"),
        "stage": "quoting",
    }
