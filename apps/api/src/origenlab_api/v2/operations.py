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
