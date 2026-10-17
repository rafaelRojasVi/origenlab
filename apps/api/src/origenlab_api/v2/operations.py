"""«Operaciones»: the state of each part of the system, as Render shows its services (spec
2026-10-13-operaciones-design.md, delivery 1).

Each card is ``{key, title, status, summary, at, facts, events}``; ``status`` is ``ok`` |
``warn`` | ``bad`` | ``info``. The ``*_card`` functions are pure: they classify facts the read
model gathered. Nothing here carries a message body, an address, a subject or a secret.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from origenlab_api.v2.quote_documents import QUOTE_READ_REREAD_CODES, QUOTE_READER_PREVIOUS_VERSION, QUOTE_READER_VERSION

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
    if state.get("unreadable"):
        return card("reglas", "Reglas automáticas", "warn", "No se pudo leer el estado de las reglas", facts=facts)
    if not state.get("enabled"):
        return card("reglas", "Reglas automáticas", "warn",
                    "Apagadas: vincular correos y abrir casos espera a una persona", facts=facts)
    run = state.get("last_run")
    if state.get("blocked") or (run or {}).get("skipped") == "operator_not_admin":
        why = state.get("blocked") or "quien las activó ya no es administrador"
        return card("reglas", "Reglas automáticas", "bad", f"Detenidas: {why}", (run or {}).get("at"), facts)
    if not run:
        return card("reglas", "Reglas automáticas", "warn",
                    "Aún no pasa desde el reinicio del servidor (pasa cada 5 min)", facts=facts)
    minutes = run.get("minutes_ago")
    refused = int(run.get("refused") or 0)
    status = "bad" if minutes is None or minutes > RULES_STALE_MIN else "warn" if refused else "ok"
    summary = f"Encendidas · última pasada {_ago(minutes)} · {actions_today} accion{'es' if actions_today != 1 else ''} hoy"
    if refused:
        summary += f" · {refused} rechazada{'s' if refused != 1 else ''} en la última pasada"
    return card("reglas", "Reglas automáticas", status, summary, run.get("at"), facts)


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
#: Captured mail with no triage reading at any version yet, **received** between 15 minutes and
#: 14 days ago — the triage sweep's own window (`internal_date`), not the capture time: an old
#: email a backfill fetches today is outside it and is never triaged (2026-10-13 false alarm).
_TRIAGE_WAITING_SQL = f"""
select count(*), max({_MINUTES.format(col='m.internal_date')})
  from comms.message m
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id and sr.kind = 'gmail_message'
 where m.direction = 'inbound' and m.parse_status = 'parsed' and m.eml_storage_path is not null
   and m.internal_date between now() - interval '14 days' and now() - interval '{TRIAGE_WAIT_MIN} minutes'
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
#: The workers' own candidate rule (quote_reader / drive_filing): a live revision with a PDF whose
#: originating email is a captured message with its file stored. A revision without one (the
#: historical import) can never be read or filed, so it never counts as pending.
_REVISIONS = """crm.quote_revision r where r.status <> 'void' and r.pdf_sha256 is not null
   and exists (select 1 from evidence.source_record sr
                 join comms.message cm on cm.provider_message_id = sr.payload ->> 'gmail_message_id'
                                      and cm.eml_storage_path is not null
                where sr.id = r.origin_source_record_id and sr.kind = 'gmail_message')"""
#: Pending: neither read nor refused by the current reader version — nor refused by the previous
#: one for a reason the current version does not re-read (`quote_documents`). A bump re-reads the
#: rest, so they count as pending again.
_REREAD_CODES = ", ".join(f"'{c}'" for c in QUOTE_READ_REREAD_CODES)
_QUOTES_PENDING_SQL = f"""
select count(*), count(*) filter (where r.created_at < now() - interval '1 day')
  from {_REVISIONS}
   and not exists (select 1 from evidence.source_record d
                    where d.kind = 'quote_document' and d.dedupe_key = 'quote_document:' || lower(r.pdf_sha256))
   and not exists (select 1 from evidence.assertion a
                    where a.kind = 'quote_read' and a.value_norm like '%:' || lower(r.pdf_sha256)
                      and (a.value_norm like '{QUOTE_READER_VERSION}:%'
                           or (a.value_norm like '{QUOTE_READER_PREVIOUS_VERSION}:%'
                               and coalesce(a.value ->> 'code', '') not in ({_REREAD_CODES}))))
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
_ACTIONS_SQL = f"""
select r.id::text, r.completed_at, r.response_body, r.completed_at >= {_TODAY} as today,
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
#: Quotes contacto@ sent in the last 60 days (a CN/COT PDF) whose email no case holds — neither
#: linked as evidence nor the origin of a case. The PDF name is read only to derive the number.
_SENT_QUOTES_SQL = """
select sr.id::text, m.internal_date, m.provider_thread_id, a.filename
  from comms.message m
  join comms.attachment a on a.message_id = m.id
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id
                                and sr.kind = 'gmail_message'
 where m.direction = 'outbound' and m.internal_date > now() - interval '60 days'
   and a.filename ~* '^\\s*(CN|COT)\\s*-?\\s*\\d+.*\\.pdf$'
   and exists (select 1 from comms.message_participant p where p.message_id = m.id
                and p.role = 'from' and p.address_norm = 'contacto@origenlab.cl')
   and not exists (select 1 from crm.opportunity_evidence oe where oe.unlinked_at is null
                    and (oe.source_record_id = sr.id or oe.message_id = m.id))
   and not exists (select 1 from crm.opportunity o where o.origin_source_record_id = sr.id)
 order by m.internal_date desc
 limit 200
"""
#: Quotes contacto@ sent in the last 60 days on an email that IS linked to exactly one open case,
#: but whose number no case records — e.g. a second quote sent on a thread the rules had already
#: linked before they learned to record one (Arcegon 01251-26). The case and its requesting
#: institution come along so «Registrar en el caso» needs nothing typed.
_SENT_ON_CASE_SQL = """
select sr.id::text, m.internal_date, m.provider_thread_id, a.filename,
       o.id::text, o.title, o.version, org.name
  from comms.message m
  join comms.attachment a on a.message_id = m.id
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id
                                and sr.kind = 'gmail_message'
  join lateral (select oe.opportunity_id from crm.opportunity_evidence oe
                  join crm.opportunity x on x.id = oe.opportunity_id and x.closed_at is null
                 where oe.unlinked_at is null and (oe.source_record_id = sr.id or oe.message_id = m.id)
                 group by oe.opportunity_id) linked on true
  join crm.opportunity o on o.id = linked.opportunity_id
  left join crm.organization org on org.id = o.organization_id
 where m.direction = 'outbound' and m.internal_date > now() - interval '60 days'
   and a.filename ~* '^\\s*(CN|COT)\\s*-?\\s*\\d+.*\\.pdf$'
   and exists (select 1 from comms.message_participant p where p.message_id = m.id
                and p.role = 'from' and p.address_norm = 'contacto@origenlab.cl')
   and (select count(distinct oe2.opportunity_id) from crm.opportunity_evidence oe2
          join crm.opportunity y on y.id = oe2.opportunity_id and y.closed_at is null
         where oe2.unlinked_at is null and (oe2.source_record_id = sr.id or oe2.message_id = m.id)) = 1
 order by m.internal_date desc
 limit 200
"""
_QUOTE_NUMBERS_SQL = "select quote_number from crm.quote"
_FILE_TOKEN = re.compile(r"^\s*(CN|COT)\s*-?\s*(\d+)", re.IGNORECASE)
SENT_QUOTES_LIMIT = 20


def sent_quote_items(rows: list[Mapping[str, Any]], existing_numbers: set[str]) -> list[dict[str, Any]]:
    """One row per quote number, newest email first; numbers already a CRM quote are skipped.
    The file name never leaves: only the number derived from it (`mail_rules.gmail_quote_key`)."""
    from origenlab_api.v2.mail_rules import (
        canonical_quote_number,
        gmail_quote_key,
        institution_segment,
        quote_file_institution,
    )

    out: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda x: x.get("sent_at") or "", reverse=True):
        m = _FILE_TOKEN.match(r.get("filename") or "")
        key = gmail_quote_key(f"CN{m.group(2)}", r.get("sent_at")) if m else None
        if key is None:
            continue
        number = canonical_quote_number(key)
        if number in existing_numbers or number in out:
            continue
        out[number] = {"source_record_id": r["source_record_id"], "quote_number": number,
                       "sent_at": r.get("sent_at"), "thread_id": r.get("thread_id"),
                       # The institution part only (legal form / kind / «(SIGLA)»), never a person.
                       "suggested_institution": institution_segment(quote_file_institution(r.get("filename")))}
    return list(out.values())[:SENT_QUOTES_LIMIT]


def unrecorded_on_case_items(rows: list[Mapping[str, Any]], existing_numbers: set[str]) -> list[dict[str, Any]]:
    """As `sent_quote_items`, for a quote sent on an email already linked to one open case: one row
    per number not yet a CRM quote, with the case it belongs on. No file name leaves."""
    base = {r["source_record_id"]: r for r in rows}
    out = []
    for item in sent_quote_items(rows, existing_numbers):
        r = base[item["source_record_id"]]
        out.append({k: item[k] for k in ("source_record_id", "quote_number", "sent_at", "thread_id")}
                   | {"case_id": r["case_id"], "case_title": r["case_title"], "case_version": r["case_version"],
                      "organization_name": r["organization_name"]})
    return out


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
    for receipt_id, at, body, today, undone, title in cur.fetchall():
        b = _json(body)
        # No `reasons`: they quote PDF names, which can hold a person's name; the page words each
        # action from its rule instead. `trigger` is what the apply receipt records (repository).
        actions.append({
            "receipt_id": receipt_id, "at": _iso(at), "rule_id": b.get("rule_id"),
            "automatic": b.get("trigger") == "automatic", "today": bool(today),
            "case_id": b.get("case_id"), "case_title": title or b.get("case_title"),
            "quote_number": b.get("quote_number"), "undone": bool(undone),
        })
    cur.execute(_SENT_QUOTES_SQL)
    sent_rows = [{"source_record_id": sid, "sent_at": _iso(at), "thread_id": thread, "filename": name}
                 for sid, at, thread, name in cur.fetchall()]
    cur.execute(_QUOTE_NUMBERS_SQL)
    from origenlab_api.v2.mail_rules import canonical_quote_number, quote_key

    existing = {canonical_quote_number(k) for (q,) in cur.fetchall() if (k := quote_key(q, None)) is not None}
    sent_quotes = sent_quote_items(sent_rows, existing)
    cur.execute(_SENT_ON_CASE_SQL)
    on_case_rows = [{"source_record_id": sid, "sent_at": _iso(at), "thread_id": thread, "filename": name,
                     "case_id": case_id, "case_title": title, "case_version": version, "organization_name": org}
                    for sid, at, thread, name, case_id, title, version, org in cur.fetchall()]
    unrecorded_on_case = unrecorded_on_case_items(on_case_rows, existing)
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
        "sent_quotes_without_case": sent_quotes,
        "sent_quotes_unrecorded_on_case": unrecorded_on_case,
        "counts": {"cases_without_institution": int(no_institution), "institutions_por_confirmar": int(por_confirmar),
                   "sent_quotes_without_case": len(sent_quotes),
                   "sent_quotes_unrecorded_on_case": len(unrecorded_on_case)},
    }


def build_operations(facts: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    """The repository's part: the four database cards, the actions and the counts. The route adds
    the cards only the running process knows (rules timer, deployed commit)."""
    return {
        "generated_at": generated_at,
        "cards": [correo_card(facts["correo"]), clasificacion_card(facts["clasificacion"]),
                  cotizaciones_card(facts["cotizaciones"]), drive_card(facts["drive"])],
        "actions": list(facts["actions"]),
        "actions_today": sum(1 for a in facts["actions"] if a.get("today")),
        "sent_quotes_without_case": list(facts.get("sent_quotes_without_case") or []),
        "sent_quotes_unrecorded_on_case": list(facts.get("sent_quotes_unrecorded_on_case") or []),
        "counts": dict(facts["counts"]),
    }


def rules_state_for_card(state: Mapping[str, Any] | None, now_iso: str) -> dict[str, Any] | None:
    """The auto-rules timer state with the last pass's age in minutes (the card's input)."""
    if state is None or state.get("unreadable"):
        return state
    from datetime import datetime

    run = state.get("last_run")
    if run and run.get("at"):
        at = datetime.fromisoformat(str(run["at"]).replace("Z", "+00:00"))
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        run = {**run, "minutes_ago": max(0, int((now - at).total_seconds() // 60))}
    return {"enabled": bool(state.get("enabled")), "blocked": state.get("blocked"), "last_run": run}


class FactsCache:
    """The facts, shared across requests for ``ttl_seconds``: every open «Operaciones» tab polls
    each minute, and one read per half minute serves them all."""

    def __init__(self, ttl_seconds: float = 30, clock: Any = None) -> None:
        import threading
        import time

        self._ttl = ttl_seconds
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._value: Any = None
        self._at: float | None = None

    def get(self, load: Any) -> Any:
        with self._lock:
            now = self._clock()
            if self._at is None or now - self._at >= self._ttl:
                self._value, self._at = load(), now
            return self._value

    def clear(self) -> None:
        with self._lock:
            self._value, self._at = None, None
