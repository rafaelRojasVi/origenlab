"""The daily prospect search: top up to 25 unreviewed prospects from the ANID pool.

Entry point: `run_search(conn, run_id, model, *, dry_run, csv_path)`.

  * Opens an autocommit connection (passed in as `conn`), never a long transaction.
  * Reads how many unreviewed visible prospects are already in the queue; derives `need`.
  * Downloads (or reads from `csv_path`) the ANID CSV and filters the pool.
  * Drops candidates already present (same project code or same folded person + institution).
  * For each ranked candidate (up to `need`):
    1. Enrich via Europe PMC (free, polite).
    2. Score via Claude (cap check → call → record usage).
    3. Write the finding through store.py + store.store_finding.
    4. Increment the run row's counts via UPDATE … status=running.
    5. Fit < FIT_THRESHOLD → stored but hidden (hidden_reason='no_calza'), still counts.
  * Updates the run row to status='done' or 'failed' when finished.

Required privileges beyond store.py's PROSPECTS_REQUIRED_PRIVILEGES:
  evidence.prospect_search_run: SELECT, INSERT, UPDATE (status, counts, …)
  platform.model_usage: SELECT, INSERT
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import requests

from origenlab_worker.prospects.anid import AnidCandidate, download_csv, filter_new, parse_csv
from origenlab_worker.prospects.enrich import search_epmc
from origenlab_worker.prospects.finding import ProspectFinding, dedupe_key, fold, parse_finding
from origenlab_worker.prospects.fit import (
    BudgetReached,
    FIT_THRESHOLD,
    FitResult,
    build_candidate_context,
    call_fit,
)
from origenlab_worker.prospects.logo import fetch_logo
from origenlab_worker.prospects.store import PROSPECTS_REQUIRED_PRIVILEGES, store_finding

_log = logging.getLogger(__name__)

QUEUE_TARGET = 25   # keep this many unreviewed visible prospects in the queue

SEARCH_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    *PROSPECTS_REQUIRED_PRIVILEGES,
    ("evidence.prospect_search_run", "SELECT"),
    ("evidence.prospect_search_run", "INSERT"),
    ("evidence.prospect_search_run", "UPDATE"),
    ("platform.model_usage", "SELECT"),
    ("platform.model_usage", "INSERT"),
    ("crm.prospect_review", "SELECT"),
)

# ── SQL ──────────────────────────────────────────────────────────────────────────────────────
_UNREVIEWED_COUNT = """
select count(*) from evidence.source_record s
  left join lateral (
      select verdict from crm.prospect_review
       where source_record_id = s.id
       order by reviewed_at desc, id desc limit 1) r on true
 where s.kind = 'prospect_finding'
   and not s.is_quarantined
   and s.superseded_by_source_record_id is null
   and (r.verdict is null or r.verdict in ('reabrir'))
   and (s.payload->>'hidden_reason') is null
"""

_EXISTING_CODES = """
select payload->>'external_id' from evidence.source_record
 where kind = 'prospect_finding' and not is_quarantined
"""

_EXISTING_PERSON_KEYS = """
select payload->>'person_name', payload->>'organization_name'
  from evidence.source_record
 where kind = 'prospect_finding' and not is_quarantined
"""

_CLAIM_RUN = """
update evidence.prospect_search_run
   set status = 'running', started_at = now(), updated_at = now()
 where id = %s and status = 'queued'
returning id
"""

_UPDATE_COUNTS = """
update evidence.prospect_search_run
   set counts = %s::jsonb, updated_at = now()
 where id = %s
"""

_FINISH_RUN = """
update evidence.prospect_search_run
   set status = %s, finished_at = now(), counts = %s::jsonb, error = %s, updated_at = now()
 where id = %s
"""


def _unreviewed_count(cur: Any) -> int:
    cur.execute(_UNREVIEWED_COUNT)
    return int(cur.fetchone()[0])


def _load_existing(cur: Any) -> tuple[frozenset[str], frozenset[str]]:
    cur.execute(_EXISTING_CODES)
    codes = frozenset(r[0] for r in cur.fetchall() if r[0])
    cur.execute(_EXISTING_PERSON_KEYS)
    person_keys: set[str] = set()
    for person_name, org_name in cur.fetchall():
        key = f"{fold(person_name or '')}|{fold(org_name or '')}"
        person_keys.add(key)
    return codes, frozenset(person_keys)


def _candidate_to_finding_dict(c: AnidCandidate, enrich: dict[str, Any] | None,
                                fit: FitResult | None, hidden: bool) -> dict[str, Any]:
    sources: list[list[str]] = [
        [f"Adjudicación ANID {c.agno_fallo}",
         "https://github.com/ANID-GITHUB/Historico-de-Proyectos-Adjudicados"]
    ]
    if enrich and enrich.get("source_url"):
        sources.append(["Correo publicado en paper", enrich["source_url"]])

    raw: dict[str, Any] = {
        "source": c.programa.lower(),
        "external_id": c.codigo,
        "person_name": c.person_name or None,
        "role": None,
        "organization_name": c.institucion,
        "unit": None,
        "email": enrich.get("email") if enrich else None,
        "email_source_url": enrich.get("source_url") if enrich else None,
        "email_source_year": enrich.get("source_year") if enrich else None,
        "orcid": enrich.get("orcid") if enrich else None,
        "phone": None,
        "website": c.website,
        "linkedin_person": None,
        "linkedin_org": None,
        "scholar": None,
        "fund_label": c.fund_label or None,
        "project_title": c.nombre_proyecto or None,
        "amount_label": c.amount_label or None,
        "awarded_on": None,
        "why_now": fit.why_now if fit else f"Proyecto adjudicado por {c.fund_label or 'ANID'}.",
        "equipment": fit.equipment if fit else [],
        "sources": sources,
        "confidence": "alta" if c.prog_rank >= 3 else "media",
        "score": fit.fit if fit else 0,
        "salutation_sex": c.sexo or None,
        "program": c.programa.lower(),
        "keywords": c.palabras_clave[:20],
        "hidden_reason": "no_calza" if hidden else None,
    }
    return raw


def run_search(conn: Any, run_id: str, model: str, *,
               dry_run: bool = False, csv_path: str | None = None,
               _fetch_logo: Callable = fetch_logo,
               _epmc_session: Any = None,
               _csv_session: Any = None) -> dict[str, Any]:
    """Execute one prospect search run.

    `conn` is a verified worker connection (autocommit); `run_id` is the UUID of the
    `evidence.prospect_search_run` row to update. In dry_run mode no writes occur.

    Returns a counts dict: added, skipped, no_email, low_fit, pool_left, error (if any).
    """
    counts: dict[str, Any] = {
        "added": 0, "skipped": 0, "no_email": 0, "low_fit": 0, "pool_left": 0
    }

    # Claim the run row
    if not dry_run:
        with conn.transaction() if not getattr(conn, "_autocommit", True) else _noop_tx(conn) as cur:
            cur.execute(_CLAIM_RUN, (run_id,))
            if cur.fetchone() is None:
                return {**counts, "error": "run_not_found_or_already_claimed"}

    # Read existing state (read-only tx)
    with conn.cursor() as cur:
        cur.execute("set transaction read only")
        unreviewed = _unreviewed_count(cur)
        existing_codes, existing_person_keys = _load_existing(cur)
        conn.rollback()

    need = max(0, QUEUE_TARGET - unreviewed)
    if need == 0:
        if not dry_run:
            _finish(conn, run_id, "done", {**counts, "pool_left": 0}, None)
        return {**counts, "pool_left": 0}

    # Download / parse CSV
    if csv_path:
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            csv_content = fh.read()
    else:
        session = _csv_session or requests.Session()
        csv_content = download_csv(session)

    candidates = parse_csv(csv_content)
    pool = filter_new(candidates, existing_codes, existing_person_keys)
    counts["pool_left"] = max(0, len(pool) - need)

    epmc_session = _epmc_session or requests.Session()

    added = 0
    for candidate in pool:
        if added >= need:
            break

        # Enrich: Europe PMC
        enrich_result: dict[str, Any] | None = None
        try:
            enrich_result = search_epmc(epmc_session, candidate.name_variants, candidate.institucion)
        except Exception:  # noqa: BLE001
            enrich_result = None

        if not enrich_result:
            counts["no_email"] = counts.get("no_email", 0) + 1

        # Fit: Claude
        fit_result: FitResult | None = None
        hidden = False
        if not dry_run:
            try:
                context = build_candidate_context(
                    nombre_proyecto=candidate.nombre_proyecto,
                    disciplina=candidate.disciplina,
                    area_ocde=candidate.area_ocde,
                    palabras_clave=candidate.palabras_clave,
                    monto_label=candidate.amount_label,
                    fund_label=candidate.fund_label,
                )
                fit_result = call_fit(conn, context, model, search_run_id=run_id)
                if fit_result.fit < FIT_THRESHOLD:
                    hidden = True
                    counts["low_fit"] = counts.get("low_fit", 0) + 1
            except BudgetReached:
                counts["error"] = "budget"
                _finish(conn, run_id, "failed", counts, "budget")
                return counts
            except Exception as exc:  # noqa: BLE001
                _log.error("prospect fit error for %s: %s", candidate.codigo, type(exc).__name__)
                # No fit: store with score=0, hidden
                hidden = True
                counts["low_fit"] = counts.get("low_fit", 0) + 1

        # Build and write finding
        raw = _candidate_to_finding_dict(candidate, enrich_result, fit_result, hidden)
        try:
            finding = parse_finding(raw)
        except Exception as exc:  # noqa: BLE001
            _log.warning("prospect finding parse error %s: %s", candidate.codigo, exc)
            counts["skipped"] = counts.get("skipped", 0) + 1
            continue

        if not dry_run:
            logo: tuple[str, bytes] | None = None
            if candidate.website:
                try:
                    logo = _fetch_logo(candidate.website)
                except Exception:  # noqa: BLE001
                    logo = None
            with conn.transaction() if not getattr(conn, "_autocommit", True) else _simple_tx(conn) as cur:
                result = store_finding(cur, finding, logo)
            if result == "inserted":
                # Only visible (non-hidden) findings count toward the queue target;
                # hidden ones are stored but must not consume a slot meant for a visible prospect.
                if not hidden:
                    added += 1
                counts["added"] = counts.get("added", 0) + 1
            else:
                counts["skipped"] = counts.get("skipped", 0) + 1
                continue
        else:
            added += 1
            counts["added"] = added

        # Update counts periodically (every 5 added)
        if not dry_run and added % 5 == 0:
            with conn.cursor() as cur:
                cur.execute(_UPDATE_COUNTS, (json.dumps(counts), run_id))

    counts["pool_left"] = max(0, len(pool) - added - counts.get("low_fit", 0) - counts.get("skipped", 0))
    if not dry_run:
        _finish(conn, run_id, "done", counts, None)
    return counts


def _finish(conn: Any, run_id: str, status: str, counts: dict[str, Any], error: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(_FINISH_RUN, (status, json.dumps(counts), error, run_id))


class _noop_tx:
    """Context manager that yields the connection's cursor for an autocommit connection."""
    def __init__(self, conn: Any) -> None:
        self._conn = conn
    def __enter__(self) -> Any:
        return self._conn.cursor().__enter__()
    def __exit__(self, *args: Any) -> None:
        pass


class _simple_tx:
    """Context manager that yields a cursor inside a BEGIN/COMMIT."""
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._cur: Any = None
    def __enter__(self) -> Any:
        self._cur = self._conn.cursor()
        return self._cur.__enter__()
    def __exit__(self, exc_type: Any, *args: Any) -> None:
        if self._cur is not None:
            self._cur.__exit__(exc_type, *args)
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()


__all__ = ["QUEUE_TARGET", "SEARCH_REQUIRED_PRIVILEGES", "run_search"]
