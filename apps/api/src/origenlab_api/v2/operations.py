"""«Operaciones»: the state of each part of the system, as Render shows its services (spec
2026-10-13-operaciones-design.md, delivery 1).

Each card is ``{key, title, status, summary, at, facts, events}``; ``status`` is ``ok`` |
``warn`` | ``bad`` | ``info``. The ``*_card`` functions are pure: they classify facts the read
model gathered. Nothing here carries a message body, an address, a subject or a secret.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CORREO_WARN_MIN = 5
CORREO_BAD_MIN = 20
TRIAGE_WAIT_MIN = 15
TRIAGE_BAD_COUNT = 20
PENDING_DAY_MIN = 1440
RULES_STALE_MIN = 15

REPO_URL = "https://github.com/rafaelRojasVi/origenlab"


def card(key: str, title: str, status: str, summary: str, at: str | None = None,
         facts: list[dict[str, Any]] | None = None, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"key": key, "title": title, "status": status, "summary": summary, "at": at,
            "facts": facts or [], "events": events or []}


def _fact(label: str, value: Any) -> dict[str, Any]:
    return {"label": label, "value": value}


def _ago(minutes: int | None) -> str:
    if minutes is None:
        return "nunca"
    if minutes < 1:
        return "hace menos de 1 min"
    if minutes < 120:
        return f"hace {minutes} min"
    return f"hace {minutes // 60} h"


def correo_card(f: Mapping[str, Any]) -> dict[str, Any]:
    state = f.get("authorization_state")
    minutes = f.get("minutes_since_sync")
    today = int(f.get("today") or 0)
    facts = [_fact("Correos hoy", today)]
    if state is None:
        return card("correo", "Correo", "bad", "Gmail no configurado", facts=facts)
    if state != "authorized":
        return card("correo", "Correo", "bad", "Gmail desconectado: hay que volver a autorizar la cuenta",
                    f.get("last_synced_at"), facts, f.get("events"))
    if minutes is None or minutes > CORREO_BAD_MIN:
        status = "bad"
    elif minutes > CORREO_WARN_MIN:
        status = "warn"
    else:
        status = "ok"
    summary = f"Última lectura {_ago(minutes)} · {today} correo{'s' if today != 1 else ''} hoy"
    return card("correo", "Correo", status, summary, f.get("last_synced_at"), facts, f.get("events"))


def clasificacion_card(f: Mapping[str, Any]) -> dict[str, Any]:
    waiting = int(f.get("waiting") or 0)
    oldest = f.get("oldest_wait_min")
    today = int(f.get("today") or 0)
    if waiting > TRIAGE_BAD_COUNT:
        status = "bad"
    elif waiting and oldest is not None and oldest > TRIAGE_WAIT_MIN:
        status = "warn"
    else:
        status = "ok"
    summary = (f"Nada esperando · {today} clasificados hoy" if not waiting
               else f"{waiting} correo{'s' if waiting != 1 else ''} esperando, el más antiguo {_ago(oldest)}")
    return card("clasificacion", "Clasificación", status, summary, f.get("newest_at"),
                [_fact("Esperando", waiting), _fact("Clasificados hoy", today)], f.get("events"))


def _pending_card(key: str, title: str, done_label: str, f: Mapping[str, Any]) -> dict[str, Any]:
    pending = int(f.get("pending") or 0)
    late = int(f.get("pending_over_a_day") or 0)
    today = int(f.get("today") or 0)
    status = "warn" if late else "ok"
    if not pending:
        summary = f"Nada pendiente · {today} hoy"
    elif late:
        summary = f"{pending} pendiente{'s' if pending != 1 else ''}, {late} desde hace más de un día"
    else:
        summary = f"{pending} pendiente{'s' if pending != 1 else ''} · {today} hoy"
    return card(key, title, status, summary, f.get("newest_at"),
                [_fact(done_label, today), _fact("Pendientes", pending)], f.get("events"))


def cotizaciones_card(f: Mapping[str, Any]) -> dict[str, Any]:
    return _pending_card("cotizaciones", "Lector de cotizaciones", "Leídas hoy", f)


def drive_card(f: Mapping[str, Any]) -> dict[str, Any]:
    return _pending_card("drive", "Archivo Drive", "Archivadas hoy", f)


def reglas_card(state: Mapping[str, Any] | None, actions_today: int) -> dict[str, Any]:
    facts = [_fact("Acciones hoy", actions_today)]
    if state is None:
        return card("reglas", "Reglas automáticas", "info", "No se ejecutan en este servidor", facts=facts)
    if not state.get("enabled"):
        return card("reglas", "Reglas automáticas", "warn",
                    "Apagadas: vincular correos y abrir casos espera a una persona", facts=facts)
    run = state.get("last_run")
    if not run:
        return card("reglas", "Reglas automáticas", "warn",
                    "Aún no pasa desde el reinicio del servidor (pasa cada 5 min)", facts=facts)
    minutes = run.get("minutes_ago")
    status = "bad" if minutes is None or minutes > RULES_STALE_MIN else "ok"
    return card("reglas", "Reglas automáticas", status,
                f"Encendidas · última pasada {_ago(minutes)} · {actions_today} accion{'es' if actions_today != 1 else ''} hoy",
                run.get("at"), facts)


def version_card(commit: str | None) -> dict[str, Any]:
    if not commit:
        return card("version", "Versión", "info", "Versión no informada (fuera de Render)")
    return card("version", "Versión", "info", f"En producción: {commit[:7]}",
                facts=[_fact("GitHub", f"{REPO_URL}/commit/{commit}")])


def respaldo_card() -> dict[str, Any]:
    return card("respaldo", "Respaldo y publicación", "info", "Se verá aquí en la próxima entrega",
                facts=[_fact("GitHub Actions", f"{REPO_URL}/actions/workflows/production-db-migrations.yml")])


# ─────────────────────────────────────────────────────────────── the read model ──
# Read-only, as the API role, in one transaction. «Today» is the Santiago day. No column that
# holds a body, an address or a subject is ever selected.
_TODAY = "(date_trunc('day', now() at time zone 'America/Santiago') at time zone 'America/Santiago')"
_MINUTES = "floor(extract(epoch from now() - {col}) / 60)::int"

_MAILBOX_SQL = f"""
select authorization_state, last_synced_at, {_MINUTES.format(col='last_synced_at')}
  from comms.mailbox order by last_synced_at desc nulls last limit 1
"""
_MESSAGES_TODAY_SQL = f"select count(*) from comms.message where created_at >= {_TODAY}"
_MESSAGE_EVENTS_SQL = "select created_at, direction from comms.message order by created_at desc limit 10"
#: Captured mail with no triage reading at any version yet, between 15 minutes and 14 days old —
#: the release postflight's rule without its version (a version bump re-reads in the background).
_TRIAGE_WAITING_SQL = f"""
select count(*), max({_MINUTES.format(col='m.created_at')})
  from comms.message m
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id and sr.kind = 'gmail_message'
 where m.direction = 'inbound' and m.parse_status = 'parsed' and m.eml_storage_path is not null
   and m.created_at between now() - interval '14 days' and now() - interval '{TRIAGE_WAIT_MIN} minutes'
   and not exists (select 1 from evidence.assertion a where a.source_record_id = sr.id
                    and a.kind = 'message_triage' and a.value_norm like 'triage:v%')
"""
_TRIAGE_DONE_SQL = f"""
select count(*) filter (where created_at >= {_TODAY}), max(created_at)
  from evidence.assertion where kind = 'message_triage' and value_norm like 'triage:v%'
"""
_TRIAGE_EVENTS_SQL = """
select created_at, value ->> 'class' from evidence.assertion
 where kind = 'message_triage' and value_norm like 'triage:v%' order by created_at desc limit 10
"""
#: The worker's own candidate rules (quote_reader / drive_filing): a live revision with a PDF.
_REVISIONS = "crm.quote_revision r where r.status <> 'void' and r.pdf_sha256 is not null"
_QUOTES_PENDING_SQL = f"""
select count(*), count(*) filter (where r.created_at < now() - interval '1 day')
  from {_REVISIONS} and r.origin_source_record_id is not null
   and not exists (select 1 from evidence.source_record d
                    where d.kind = 'quote_document' and d.dedupe_key = 'quote_document:' || lower(r.pdf_sha256))
   and not exists (select 1 from evidence.assertion a
                    where a.kind = 'quote_read' and a.value_norm like '%:' || lower(r.pdf_sha256))
"""
_QUOTES_DONE_SQL = f"""
select count(distinct source_record_id) filter (where created_at >= {_TODAY}), max(created_at)
  from evidence.document_line
"""
_DRIVE_PENDING_SQL = f"""
select count(*), count(*) filter (where r.created_at < now() - interval '1 day')
  from {_REVISIONS}
   and not exists (select 1 from evidence.source_record d where d.dedupe_key = 'drive_file:' || r.pdf_sha256)
"""
_DRIVE_DONE_SQL = f"""
select count(*) filter (where created_at >= {_TODAY}), max(created_at)
  from evidence.source_record where kind = 'drive_file'
"""
_ACTIONS_SQL = """
select r.id::text, r.completed_at, r.response_body,
       exists (select 1 from platform.command_receipt u
                where u.command_name = 'undo_mail_rule_action' and u.status = 'completed'
                  and u.response_body ->> 'undoes_receipt_id' = r.id::text) as undone,
       o.title
  from platform.command_receipt r
  left join crm.opportunity o on o.id::text = r.response_body ->> 'case_id'
 where r.command_name = 'apply_mail_rule' and r.status = 'completed'
   and r.completed_at > now() - interval '7 days'
 order by r.completed_at desc
 limit 100
"""
_COUNTS_SQL = """
select (select count(*) from crm.opportunity where closed_at is null and organization_id is null),
       (select count(*) from crm.organization where confirmation = 'machine_proposed'
          and status = 'active' and merged_into_organization_id is null)
"""

TRIAGE_CLASS_LABEL = {
    "quote_request": "solicitud de cotización", "purchase_order": "orden de compra", "bounce": "rebote",
    "outbound": "enviado por OrigenLab", "business_other": "otro (persona)", "tender_notice": "licitación",
    "bulk": "masivo", "auto_reply": "respuesta automática", "unsubscribe": "baja", "notification": "notificación",
    "lost": "perdido",
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _json(value: Any) -> Any:
    import json

    return json.loads(value) if isinstance(value, str) else (value or {})


def read_operation_facts(cur: Any) -> dict[str, Any]:
    """Every fact the cards classify, from one read-only transaction."""
    cur.execute(_MAILBOX_SQL)
    mailbox = cur.fetchone()
    cur.execute(_MESSAGES_TODAY_SQL)
    messages_today = int(cur.fetchone()[0])
    cur.execute(_MESSAGE_EVENTS_SQL)
    message_events = [{"at": _iso(at), "text": "Correo recibido" if d == "inbound" else "Correo enviado"}
                      for at, d in cur.fetchall()]
    cur.execute(_TRIAGE_WAITING_SQL)
    waiting, oldest = cur.fetchone()
    cur.execute(_TRIAGE_DONE_SQL)
    triaged_today, triaged_newest = cur.fetchone()
    cur.execute(_TRIAGE_EVENTS_SQL)
    triage_events = [{"at": _iso(at), "text": f"Clasificado como {TRIAGE_CLASS_LABEL.get(c, c or 'sin clase')}"}
                     for at, c in cur.fetchall()]
    cur.execute(_QUOTES_PENDING_SQL)
    q_pending, q_late = cur.fetchone()
    cur.execute(_QUOTES_DONE_SQL)
    q_today, q_newest = cur.fetchone()
    cur.execute(_DRIVE_PENDING_SQL)
    d_pending, d_late = cur.fetchone()
    cur.execute(_DRIVE_DONE_SQL)
    d_today, d_newest = cur.fetchone()
    cur.execute(_ACTIONS_SQL)
    actions = []
    for receipt_id, at, body, undone, title in cur.fetchall():
        b = _json(body)
        attribution = b.get("attribution") or {}
        actions.append({
            "receipt_id": receipt_id, "at": _iso(at), "rule_id": b.get("rule_id"),
            "automatic": (attribution.get("trigger") if isinstance(attribution, dict) else None) == "automatic"
                         or bool(b.get("automatic")),
            "case_id": b.get("case_id"), "case_title": title or b.get("case_title"),
            "quote_number": b.get("quote_number"), "reasons": list(b.get("reasons") or []), "undone": bool(undone),
        })
    cur.execute(_COUNTS_SQL)
    no_institution, por_confirmar = cur.fetchone()
    return {
        "correo": {"authorization_state": mailbox[0] if mailbox else None,
                   "last_synced_at": _iso(mailbox[1]) if mailbox else None,
                   "minutes_since_sync": mailbox[2] if mailbox else None,
                   "today": messages_today, "events": message_events},
        "clasificacion": {"waiting": int(waiting or 0), "oldest_wait_min": oldest, "today": int(triaged_today or 0),
                          "newest_at": _iso(triaged_newest), "events": triage_events},
        "cotizaciones": {"pending": int(q_pending or 0), "pending_over_a_day": int(q_late or 0),
                         "today": int(q_today or 0), "newest_at": _iso(q_newest), "events": []},
        "drive": {"pending": int(d_pending or 0), "pending_over_a_day": int(d_late or 0),
                  "today": int(d_today or 0), "newest_at": _iso(d_newest), "events": []},
        "actions": actions,
        "counts": {"cases_without_institution": int(no_institution), "institutions_por_confirmar": int(por_confirmar)},
    }


def build_operations(facts: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    """The repository's part: the four database cards, the actions and the counts. The route adds
    the cards only the running process knows (rules timer, deployed commit)."""
    today = generated_at[:10]
    return {
        "generated_at": generated_at,
        "cards": [correo_card(facts["correo"]), clasificacion_card(facts["clasificacion"]),
                  cotizaciones_card(facts["cotizaciones"]), drive_card(facts["drive"])],
        "actions": list(facts["actions"]),
        "actions_today": sum(1 for a in facts["actions"] if (a.get("at") or "").startswith(today)),
        "counts": dict(facts["counts"]),
    }


def rules_state_for_card(state: Mapping[str, Any] | None, now_iso: str) -> dict[str, Any] | None:
    """The auto-rules timer state with the last pass's age in minutes (the card's input)."""
    if state is None:
        return None
    from datetime import datetime

    run = state.get("last_run")
    if run and run.get("at"):
        at = datetime.fromisoformat(str(run["at"]).replace("Z", "+00:00"))
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        run = {**run, "minutes_ago": max(0, int((now - at).total_seconds() // 60))}
    return {"enabled": bool(state.get("enabled")), "last_run": run}
