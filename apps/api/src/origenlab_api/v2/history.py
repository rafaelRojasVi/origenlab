"""«Historial»: the decisions recorded in the CRM, in plain Spanish, most recent first.

A decision is a completed `platform.command_receipt`. Each item says who, what (a fixed Spanish
sentence per command, never the raw command name), on which case or institution, and when. Only
whitelisted fields of the receipt's response are read — the case id, the institution id, the new
stage and the quote number — so a note, a reason or an address in the body can never reach the
page. A command with no sentence here is left out.

An automatic mail action («apply_mail_rule») can be undone from here until it is; every other
decision is undone, where that exists, from its own case.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The commands «Historial» shows, and how each reads.
COMMAND_LABELS: dict[str, str] = {
    "advance_case_stage": "Cambió la etapa",
    "correct_case_stage": "Corrigió la etapa",
    "open_commercial_case": "Abrió el caso",
    "record_case_won": "Marcó ganada",
    "record_case_quotation": "Registró una cotización",
    "record_historical_quotation": "Registró una cotización",
    "resolve_current_revision": "Eligió la revisión vigente",
    "add_case_organization": "Asignó la institución",
    "set_case_organization_role": "Cambió el papel de la institución",
    "link_case_evidence": "Vinculó un correo al caso",
    "unlink_case_evidence": "Desvinculó un correo del caso",
    "create_task": "Programó un seguimiento",
    "complete_task": "Marcó hecho un seguimiento",
    "cancel_task": "Canceló un seguimiento",
    "create_organization": "Registró una institución",
    "update-organization": "Editó una institución",
    "confirm-organization-record": "Confirmó una institución",
    "add-organization-domain": "Agregó un dominio a una institución",
    "create-person": "Registró una persona",
    "add-note": "Agregó una nota",
    "review_triage": "Revisó una sugerencia del correo",
    "apply_mail_rule": "Acción automática del correo",
    "undo_mail_rule_action": "Deshizo una acción automática",
    "set_auto_mail_rules": "Cambió las acciones automáticas del correo",
}

#: Stage names as the board shows them.
STAGE_LABELS: dict[str, str] = {
    "lead": "Solicitada",
    "qualifying": "Solicitada",
    "qualified": "En estudio",
    "quoting": "Enviada",
    "negotiating": "Conversación",
    "won": "Ganada",
    "lost": "Perdida",
    "abandoned": "Perdida",
}

_STAGE_COMMANDS = {"advance_case_stage", "correct_case_stage"}
_QUOTE_COMMANDS = {"record_case_won", "record_case_quotation", "record_historical_quotation", "resolve_current_revision"}

HISTORY_SQL = """
select r.id::text as receipt_id, r.command_name, r.completed_at::text as completed_at,
       o.display_name as operator, r.response_body,
       u.completed_at::text as undone_at
  from platform.command_receipt r
  join platform.operator o on o.id = r.operator_id
  left join platform.command_receipt u
         on r.command_name = 'apply_mail_rule'
        and u.command_name = 'undo_mail_rule_action' and u.status = 'completed'
        and u.response_body ->> 'undoes_receipt_id' = r.id::text
 where r.status = 'completed'
   and r.command_name = any(%s)
   and r.completed_at > now() - interval '60 days'
 order by r.completed_at desc
 limit %s
"""


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def compose_history(
    rows: Iterable[Mapping[str, Any]],
    *,
    case_titles: Mapping[str, str],
    org_names: Mapping[str, str],
) -> list[dict[str, Any]]:
    """One plain item per known decision; unknown commands are left out."""
    items: list[dict[str, Any]] = []
    for row in rows:
        command = row["command_name"]
        label = COMMAND_LABELS.get(command)
        if label is None:
            continue
        body = row.get("response_body") if isinstance(row.get("response_body"), Mapping) else {}
        case_id = _text(body.get("opportunity_id")) or _text(body.get("case_id"))
        org_id = _text(body.get("organization_id"))
        action = label
        stage = _text(body.get("stage"))
        if command in _STAGE_COMMANDS and stage in STAGE_LABELS:
            action = f"{label} a «{STAGE_LABELS[stage]}»"
        number = _text(body.get("quote_number"))
        if command in _QUOTE_COMMANDS and number:
            action = f"{label} · {number}"
        undone_at = row.get("undone_at")
        items.append({
            "receipt_id": row["receipt_id"],
            "at": row["completed_at"],
            "operator": row["operator"],
            "action": action,
            "case": {"opportunity_id": case_id, "title": case_titles[case_id]} if case_id in case_titles else None,
            "organization": (
                {"organization_id": org_id, "name": org_names[org_id]}
                if org_id in org_names and case_id not in case_titles else None
            ),
            "undo": (
                {"kind": "mail_rule", "receipt_id": row["receipt_id"]}
                if command == "apply_mail_rule" and not undone_at else None
            ),
            "undone_at": undone_at if command == "apply_mail_rule" else None,
        })
    return items


def read_history(cur: Any, limit: int) -> dict[str, Any]:
    """The decisions of the last 60 days, newest first, in one read transaction's cursor."""
    cur.execute(HISTORY_SQL, (list(COMMAND_LABELS), limit))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    case_ids: set[str] = set()
    org_ids: set[str] = set()
    for row in rows:
        body = row["response_body"] if isinstance(row["response_body"], Mapping) else {}
        for key in ("opportunity_id", "case_id"):
            if _text(body.get(key)):
                case_ids.add(body[key])
        if _text(body.get("organization_id")):
            org_ids.add(body["organization_id"])
    case_titles: dict[str, str] = {}
    org_names: dict[str, str] = {}
    if case_ids:
        cur.execute("select id::text, title from crm.opportunity where id::text = any(%s)", (sorted(case_ids),))
        case_titles = {r[0]: r[1] for r in cur.fetchall()}
    if org_ids:
        cur.execute("select id::text, name from crm.organization where id::text = any(%s)", (sorted(org_ids),))
        org_names = {r[0]: r[1] for r in cur.fetchall()}
    return {"items": compose_history(rows, case_titles=case_titles, org_names=org_names)}
