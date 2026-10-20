"""The tender bid preparation job: claim a queued `evidence.bid_preparation` row, run its phase, and
write the result back with what the model calls cost (spec 2026-10-19 §6.4, plan Task 9).

Single flight: one sweep at a time holds the session lock `LOCK_NAME` and runs preparations one by
one, in the tender worker's own child process (ruling R4). That is what keeps the monthly cap: the
cap is checked in a short transaction before each call (`model_spend.ensure_within_cap`, against the
worst case of every attempt the call may bill), the call runs with no transaction open, and its
usage is recorded right after — no second spender can run in between. A row found `running` when
the lock is taken was left by a process that died; it is failed `interrupted`, so the case can ask
again, and — when no usage was recorded for it since its claim — charged the worst case of one call
of its phase, so a call whose usage was lost does not leak past the cap.

Two phases (`SUPPORTED_PHASES`). `read` reads every anexo into a cited summary, a checklist and the
items, then matches the items to catalog products (step `match`, plan Task 12a). `fill` (Task 12)
fills the buyer's forms and the economic offer from the approved Cotizador revision, adds the
spec sheets and the bidder's standard documents, marks each checklist line listo / falta /
firmar, and writes one zip (`paquete`), filed in the case's Drive folder when it has one. Every
model call — read, match and each form — goes through the same cap path (`_call_model`). Failure
codes are the row's `failure_code`, never a model's or an exception's text; nothing here logs a
document's text or the bidder's data.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Final
from uuid import UUID

from origenlab_api.v2.catalog.enrichment import supports_fallbacks
from origenlab_api.v2.model_spend import (
    FEATURE_LICITACIONES,
    BudgetReached,
    UnpricedModel,
    dearest_model,
    ensure_within_cap,
    record_usage,
    with_cap_lock,
    worst_case_usd,
    worst_usage,
)
from origenlab_worker.catalog_match import ProductCandidate, match_products
from origenlab_worker.drive_client import DriveError
from origenlab_worker.drive_filing import DriveTarget, FilingRefused, file_tender_package
from origenlab_worker.storage import StorageError
from origenlab_worker.tender import package
from origenlab_worker.tender.extract import (
    MAX_RENDERED_CHARS,
    AnnexSetTooLarge,
    AnnexText,
    extract_annex,
    render_for_model,
)
from origenlab_worker.tender.fill_model import MAX_TOKENS as FILL_MAX_TOKENS
from origenlab_worker.tender.fill_model import build_fill_request, validate_fill, validate_sections
from origenlab_worker.tender.forms import (
    NOT_FILLABLE,
    FormError,
    apply_values,
    count_completar,
    generated_docx,
    inspect_form,
)
from origenlab_worker.tender.match_model import build_match_request, candidate_view, validate_match
from origenlab_worker.tender.read_model import (
    CONVERT_TO_WORD,
    SYSTEM_PROMPT as READ_SYSTEM_PROMPT,
    FALLBACK_TARGETS,
    MAX_TOKENS,
    ReadFailed,
    build_read_request,
    input_tokens_estimate,
    parse_read,
    validate_read,
)
from origenlab_worker.triage_model import ModelReader

LOCK_NAME: Final = "origenlab-worker:bid-preparation"
DOCUMENTS_BUCKET: Final = "crm-documents"
SUPPORTED_PHASES: Final = ("read", "fill")
#: Preparations run per sweep; the next minute's sweep takes the rest.
SWEEP_LIMIT: Final = 5
STATUS_READY, STATUS_FAILED, STATUS_BUDGET = "ready", "failed", "budget_stopped"

#: What the tender worker's connection must hold (`database.verify_worker_connection`): read the case,
#: its notice and anexos, the catalog (the match step), and for the fill phase the offer's quote, the
#: bidder's profile and documents and the product data sheets; update the preparation; record spend.
#: UPDATE on evidence.bid_preparation is a column grant (status, result, …), which the table-level
#: probe cannot see; its RLS policy is required below and a missing grant fails the first write.
BID_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("evidence.bid_preparation", "SELECT"),
    ("crm.opportunity", "SELECT"), ("crm.opportunity_document", "SELECT"),
    ("platform.model_usage", "SELECT"), ("platform.model_usage", "INSERT"),
    ("catalog.product_document", "SELECT"), ("catalog.product", "SELECT"),
    ("catalog.product_model_alias", "SELECT"),
    ("crm.quote", "SELECT"), ("crm.quote_revision", "SELECT"), ("crm.quote_line", "SELECT"),
    ("crm.bidder_profile", "SELECT"), ("crm.bidder_document", "SELECT"),
    ("crm.opportunity_evidence", "SELECT"), ("procurement.notice", "SELECT"),
)
BID_REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset({
    ("evidence", "bid_preparation", "origenlab_worker_select"),
    ("evidence", "bid_preparation", "origenlab_worker_update"),
    ("crm", "opportunity", "origenlab_worker_select"),
    ("crm", "opportunity_document", "origenlab_worker_select"),
    ("platform", "model_usage", "origenlab_worker_select"),
    ("platform", "model_usage", "origenlab_worker_insert"),
    ("catalog", "product_document", "origenlab_worker_select"),
    ("catalog", "product", "origenlab_worker_select"),
    ("catalog", "product_model_alias", "origenlab_worker_select"),
    ("crm", "quote", "origenlab_worker_select"), ("crm", "quote_revision", "origenlab_worker_select"),
    ("crm", "quote_line", "origenlab_worker_select"),
    ("crm", "bidder_profile", "origenlab_worker_select"), ("crm", "bidder_document", "origenlab_worker_select"),
    ("crm", "opportunity_evidence", "origenlab_worker_select"), ("procurement", "notice", "origenlab_worker_select"),
})

_CLAIM = """
update evidence.bid_preparation set status = 'running', started_at = now(), updated_at = now()
 where id = (select id from evidence.bid_preparation
              where status = 'queued' and phase = any(%s)
              order by requested_at, id
              for update skip locked
              limit 1)
returning id
"""
_START = """
update evidence.bid_preparation
   set status = 'running', started_at = coalesce(started_at, now()), updated_at = now()
 where id = %s and status in ('queued', 'running') and phase = any(%s)
returning phase, opportunity_id::text, annex_document_ids::text[], based_on_preparation_id::text,
          quote_revision_id::text
"""
_FINISH = """
update evidence.bid_preparation
   set status = %s, failure_code = %s, result = %s, models = %s, finished_at = now(), updated_at = now()
 where id = %s and status = 'running'
"""
_SAVE_OUTPUTS = """
update evidence.bid_preparation set outputs = %s, updated_at = now() where id = %s and status = 'running'
"""
# Each row failed here, and whether any usage was recorded for it since its last claim.
_INTERRUPTED = """
update evidence.bid_preparation p
   set status = 'failed', failure_code = 'interrupted', finished_at = now(), updated_at = now()
 where status = 'running'
returning p.id, p.phase,
          exists (select 1 from platform.model_usage u
                   where u.bid_preparation_id = p.id and u.created_at >= p.started_at) as covered
"""
_ANNEXES = """
select id::text, original_filename, content_type, storage_path, sha256
  from crm.opportunity_document
 where opportunity_id = %s and kind = 'tender_annex' and id = any(%s::uuid[])
"""
_READ_ROW = """
select opportunity_id::text, phase, status, result from evidence.bid_preparation where id = %s
"""
_REVISION = """
select r.status, r.quote_currency, r.price_decimals, r.tax_rate, r.tax_base, r.tax_total, r.grand_total,
       r.approved_at, r.created_at, q.quote_number, q.opportunity_id::text as opportunity_id
  from crm.quote_revision r join crm.quote q on q.id = r.quote_id
 where r.id = %s
"""
_LINES = """
select line_kind, description, quantity, unit_price_qc, line_total_qc, product_id::text as product_id
  from crm.quote_line where quote_revision_id = %s order by line_no
"""
_BIDDER = """
select bidder_kind, legal_name, rut, giro, address, comuna, region, phone, email, legal_rep_name, legal_rep_rut,
       bank_name, bank_account_type, bank_account_number, chileproveedores_status, chileproveedores_checked_on,
       is_emt, is_local_supplier
  from crm.bidder_profile order by created_at desc, id desc limit 1
"""
_BIDDER_DOCUMENTS = """
select id::text as id, kind, title, storage_path, sha256, content_type, original_filename, byte_size
  from crm.bidder_document where retired_at is null order by added_at desc, id desc
"""
_CASE = """
select o.title, n.codigo_externo as code, n.title as notice_title, n.buyer_name, n.closes_at, org.name as institution
  from crm.opportunity o
  left join lateral (
        select n2.* from crm.opportunity_evidence e join procurement.notice n2 on n2.id = e.notice_id
         where e.opportunity_id = o.id and e.relation = 'origin' and e.unlinked_at is null
         order by e.linked_at desc limit 1) n on true
  left join crm.organization org on org.id = o.organization_id
 where o.id = %s
"""
_DATASHEETS = """
select distinct on (d.product_id) d.product_id::text as product_id, d.drive_file_id, d.mime_type, d.title,
       p.model_number, coalesce(p.name_es, p.name) as name
  from catalog.product_document d join catalog.product p on p.id = d.product_id
 where d.product_id = any(%s::uuid[]) and d.kind = 'datasheet' and d.status = 'active'
 order by d.product_id, d.as_of desc nulls last, d.version desc, d.created_at desc
"""
#: A form the worker cannot fill becomes this checklist code and the R13 line «Falta: convertir a
#: Word (.docx): <file>»; a form the model could not answer stays `no_se_pudo_llenar`.
FALTA_CONVERT, FALTA_NOT_FILLED, FALTA_MISSING, FALTA_COMPLETAR = (
    "convertir_a_word", "no_se_pudo_llenar", "sin_documento", "completar")
_FORM_EXTENSIONS: Final = {"docx": "docx", "xlsx": "xlsx", "pdf_fillable": "pdf"}
#: The bidder's bank details go to a form only when the form asks about payment.
BANK_FIELDS: Final = ("bank_name", "bank_account_type", "bank_account_number")
_BANK_WORDS = re.compile(r"banco|bancari|cuenta|transferencia|dep[oó]sito", re.IGNORECASE)
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BidModels:
    read: str
    fill: str
    match: str


class PreparationFailed(Exception):
    def __init__(self, code: str, status: str = STATUS_FAILED, result: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.result = result or {}


class ConnectionLost(Exception):
    """The worker's session died after a model call. Its usage was recorded and its row failed on a
    fresh connection (when one could be opened); the single-flight lock went with the session, so the
    sweep must stop."""


#: The largest prompt one call may carry, in `input_tokens_estimate`'s tokens (two characters each,
#: plus its 1000): the anexos at the render limit, the system prompt and room for the case and the
#: instructions around them.
WORST_INPUT_TOKENS: Final = (MAX_RENDERED_CHARS + len(READ_SYSTEM_PROMPT)) // 2 + 1000 + 10_000


def _interrupted_call(phase: str, models: BidModels) -> tuple[str, int, tuple[str, ...]]:
    """(model, max_tokens, every attempt it may bill) of the dearest single call of `phase`."""
    model, max_tokens = (models.fill, FILL_MAX_TOKENS) if phase == "fill" else (models.read, MAX_TOKENS)
    return model, max_tokens, call_attempts({"fallbacks": supports_fallbacks(model)}, model)


def interrupted_worst_usd(phase: str, models: BidModels) -> Decimal:
    """What the recovery charges a row that died with no usage recorded since its claim: the worst
    case of one call of its phase, every attempt it may bill included."""
    model, max_tokens, attempts = _interrupted_call(phase, models)
    return worst_case_usd(model, input_tokens_estimate=WORST_INPUT_TOKENS, max_tokens=max_tokens,
                          fallback_models=attempts[1:])


def call_attempts(request: dict[str, Any], model: str) -> tuple[str, ...]:
    """The attempts one call may bill: the requested model's and, when the request carries
    server-side fallbacks, one fallback attempt priced at the dearest permitted target."""
    return (model, dearest_model(FALLBACK_TARGETS)) if request.get("fallbacks") else (model,)


def call_worst_case(request: dict[str, Any], model: str) -> Decimal:
    """What one call may bill: every attempt's worst case, summed — each is billed (claude-api skill)."""
    attempts = call_attempts(request, model)
    return worst_case_usd(attempts[0], input_tokens_estimate=input_tokens_estimate(request),
                          max_tokens=int(request.get("max_tokens") or MAX_TOKENS), fallback_models=attempts[1:])


def _sent_and_maybe_billed(exc: BaseException) -> bool:
    """False only when the API answered with an error status before generating anything (nothing is
    billed); a stream cut after it started, a timeout or a lost connection may have been billed."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the SDK is a worker dependency
        return True
    return not isinstance(exc, anthropic.APIStatusError)


_CONNECTION_ERRORS: tuple[type[BaseException], ...]
try:
    import psycopg as _psycopg

    _CONNECTION_ERRORS = (_psycopg.OperationalError, _psycopg.InterfaceError)
except ImportError:  # pragma: no cover
    _CONNECTION_ERRORS = ()


def _rows(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


@dataclass
class _Fill:
    """One fill's state: what it wrote, and which checklist line each output answers."""

    prep_id: UUID
    case_id: str
    code: str
    outputs: list[dict[str, Any]] = field(default_factory=list)
    links: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    documents: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    falta: dict[int, str] = field(default_factory=dict)
    #: R13 lines to add: (anexo id, file name).
    convert: list[tuple[str, str]] = field(default_factory=list)
    forms: int = 0

    def link(self, index: int | None, output: dict[str, Any]) -> None:
        if index is not None and output not in self.links.setdefault(index, []):
            self.links[index].append(output)


class BidPreparer:
    """Runs preparations on one verified autocommit worker connection.

    The remote connection has TCP keepalives on (`database.KEEPALIVES`), so it survives a model call
    of several minutes. If it is gone anyway when the usage must be recorded, `reconnect` (a context
    manager factory for a fresh verified connection) records it there and fails the row
    `connection_lost`, and :class:`ConnectionLost` stops the sweep (fail closed).

    `drive` (a `DriveRest`) and `drive_target` are the case archive's Drive settings, kept by the
    tender child (ruling R4); without them the fill adds no spec sheet and files nothing in Drive.
    """

    def __init__(self, conn: Any, store: Any, reader: ModelReader, models: BidModels, *,
                 now: Callable[[], datetime],
                 reconnect: Callable[[], AbstractContextManager[Any]] | None = None,
                 drive: Any = None, drive_target: DriveTarget | None = None) -> None:
        self._conn = conn
        self._store = store
        self._reader = reader
        self._models = models
        self._now = now
        self._reconnect = reconnect
        self._drive = drive if drive_target is not None else None
        self._drive_target = drive_target

    # ── the queue ───────────────────────────────────────────────────────────────────────────

    def claim(self) -> UUID | None:
        """The oldest queued preparation of a supported phase, now `running`; None if there is none
        (a row another session holds locked is skipped, not waited for)."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_CLAIM, (list(SUPPORTED_PHASES),))
            row = cur.fetchone()
        return row[0] if row else None

    def recover_interrupted(self) -> int:
        """Fail every `running` row. Call only while holding `LOCK_NAME`: then none is really running.

        A row with no usage recorded since its last claim may have died in a paid call whose usage
        was never written (the session and the reconnect both failed, or the process was killed): it
        is charged the worst case of one call of its phase (`interrupted_worst_usd`, step = phase),
        so the monthly cap does not leak. A row with usage since its claim is not charged again."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_INTERRUPTED)
            rows = cur.fetchall()
            for prep_id, phase, covered in rows:
                if covered:
                    continue
                model, max_tokens, attempts = _interrupted_call(phase, self._models)
                usage = SimpleNamespace(iterations=[
                    worst_usage(m, input_tokens_estimate=WORST_INPUT_TOKENS, max_tokens=max_tokens)
                    for m in attempts])
                record_usage(cur, feature=FEATURE_LICITACIONES, step=phase, model=model, usage=usage,
                             bid_preparation_id=prep_id)
            return len(rows)

    def run(self, prep_id: UUID) -> str:
        """Run one preparation to its final status (`ready`, `failed` or `budget_stopped`)."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_START, (prep_id, list(SUPPORTED_PHASES)))
            row = cur.fetchone()
        if row is None:
            with self._conn.cursor() as cur:
                cur.execute("select status from evidence.bid_preparation where id = %s", (prep_id,))
                found = cur.fetchone()
            return found[0] if found else "missing"
        phase, opportunity_id, annex_ids, based_on, revision_id = row
        models: dict[str, str] = {}
        try:
            if phase == "fill":
                result = self._fill(prep_id, opportunity_id, annex_ids, based_on, revision_id, models)
            else:
                result = self._read(prep_id, opportunity_id, annex_ids, models)
        except PreparationFailed as exc:
            self._finish(prep_id, exc.status, exc.code, exc.result, models)
            return exc.status
        except ConnectionLost:
            raise
        except Exception:  # noqa: BLE001 — the row must never stay `running`; the class is not stored
            self._finish(prep_id, STATUS_FAILED, "internal_error", {}, models)
            raise
        self._finish(prep_id, STATUS_READY, None, result, models)
        return STATUS_READY

    def _finish(self, prep_id: UUID, status: str, code: str | None, result: dict[str, Any],
                models: dict[str, str], *, conn: Any = None) -> None:
        from psycopg.types.json import Jsonb

        conn = conn or self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_FINISH, (status, code, Jsonb(result), Jsonb(models), prep_id))

    def _record(self, prep_id: UUID, models: dict[str, str], *, step: str, **usage: Any) -> None:
        """Record one call's usage; on a dead session, on a fresh one, then stop (`ConnectionLost`)."""
        def write(conn: Any) -> None:
            with conn.transaction(), conn.cursor() as cur:
                record_usage(cur, feature=FEATURE_LICITACIONES, step=step, bid_preparation_id=prep_id, **usage)

        try:
            write(self._conn)
            return
        except _CONNECTION_ERRORS:
            if self._reconnect is None:
                raise ConnectionLost("usage_not_recorded") from None
        # If reconnect() itself fails, its error propagates: the usage stays unrecorded, the row is
        # left `running` (the next sweep fails it `interrupted` and charges it a worst-case call), and
        # the sweep stops — fail closed.
        with self._reconnect() as fresh:
            write(fresh)
            self._finish(prep_id, STATUS_FAILED, "connection_lost", {}, models, conn=fresh)
        raise ConnectionLost("connection_lost")

    def _call_model(self, prep_id: UUID, models: dict[str, str], step: str, request: dict[str, Any],
                    model: str) -> Any:
        """One paid call, the same way for every step (read, match, each form): the worst case of
        every attempt it may bill is checked against the cap in a short transaction under the cap
        lock, the call runs with no transaction open, and its usage is recorded right after — also
        when it failed after it was sent. Raises :class:`PreparationFailed` (`model_unpriced`,
        `budget_reached` with status `budget_stopped`, `request_failed`) or :class:`ConnectionLost`."""
        attempts = call_attempts(request, model)
        try:
            worst = call_worst_case(request, model)
        except UnpricedModel:
            raise PreparationFailed("model_unpriced") from None
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                with_cap_lock(cur, FEATURE_LICITACIONES)
                ensure_within_cap(cur, FEATURE_LICITACIONES, self._now(), worst)
        except BudgetReached:
            raise PreparationFailed("budget_reached", STATUS_BUDGET) from None

        try:
            message = self._reader(request)  # no transaction is open during the call
        except Exception as exc:  # noqa: BLE001 — network, 4xx/5xx, timeout: the code, never the text
            if _sent_and_maybe_billed(exc):
                # What it really cost is unknown: the requested model may have declined mid-stream and
                # the fallback started before the break, so every attempt is recorded at its worst
                # case — the same total the cap check assumed (`call_worst_case`).
                estimate = input_tokens_estimate(request)
                limit = int(request.get("max_tokens") or MAX_TOKENS)
                conservative = SimpleNamespace(iterations=[
                    worst_usage(m, input_tokens_estimate=estimate, max_tokens=limit) for m in attempts])
                self._record(prep_id, models, step=step, model=model, usage=conservative)
            raise PreparationFailed("request_failed") from None
        served = getattr(message, "model", None)
        served = served if isinstance(served, str) and served else model
        models[step] = served
        # Recorded before the answer is judged: a refused or truncated call may still be billed.
        self._record(prep_id, models, step=step, model=model, usage=getattr(message, "usage", None),
                     served_model=served)
        return message

    # ── the read phase ──────────────────────────────────────────────────────────────────────

    def _annex_rows(self, opportunity_id: str, annex_ids: list[str]) -> dict[str, tuple[Any, ...]]:
        with self._conn.cursor() as cur:
            cur.execute(_ANNEXES, (opportunity_id, annex_ids))
            return {r[0]: r for r in cur.fetchall()}

    def _annex_data(self, row: tuple[Any, ...]) -> bytes:
        _, _filename, _content_type, path, sha256 = row
        try:
            data = self._store.get(path)
        except StorageError:
            raise PreparationFailed("annex_unavailable") from None
        if hashlib.sha256(data).hexdigest() != sha256:
            raise PreparationFailed("annex_hash_mismatch")
        return data

    def _annexes(self, opportunity_id: str, annex_ids: list[str]) -> list[AnnexText]:
        rows = self._annex_rows(opportunity_id, annex_ids)
        annexes: list[AnnexText] = []
        for doc_id in annex_ids:  # the order the preparation recorded
            row = rows.get(doc_id)
            if row is None:
                raise PreparationFailed("annex_missing")
            annexes.append(extract_annex(doc_id, row[1], row[2], self._annex_data(row)))
        return annexes

    def _case(self, opportunity_id: str) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute("select title from crm.opportunity where id = %s", (opportunity_id,))
            row = cur.fetchone()
        return {"titulo": row[0] if row else None}

    def _read(self, prep_id: UUID, opportunity_id: str, annex_ids: list[str],
              models: dict[str, str]) -> dict[str, Any]:
        annexes = self._annexes(opportunity_id, annex_ids)
        readable = [a for a in annexes if a.pages]
        if not readable:
            raise PreparationFailed("no_readable_annex")
        try:
            rendered = render_for_model(readable)
        except AnnexSetTooLarge:
            raise PreparationFailed("annex_set_too_large") from None
        model = self._models.read
        request = build_read_request(rendered, self._case(opportunity_id), model=model)
        message = self._call_model(prep_id, models, "read", request, model)
        try:
            answer = parse_read(message)
        except ReadFailed as exc:
            extra = {"refusal_category": exc.category} if exc.code == "model_refused" else None
            raise PreparationFailed(exc.code, result=extra) from None
        result = validate_read(answer, annexes)
        result["annexes"] = [{"document_id": a.document_id, "filename": a.filename, "outcome": a.outcome}
                             for a in annexes]
        result["match"] = self._match(prep_id, result["items"], models)
        return result

    def _match(self, prep_id: UUID, items: list[dict[str, Any]], models: dict[str, str]) -> dict[str, Any]:
        """Step 12a: each item's catalog candidates (`catalog_match.match_products`, ruling R14), then
        one cheap call picks a product or none. Sets `product_id`, `match_reason` and `candidatos` on
        every item. The read is already paid for and stands on its own, so a match that cannot run
        (cap reached, call failed, answer unusable) leaves the items unmatched and says why."""
        candidates: dict[int, list[ProductCandidate]] = {}
        with self._conn.cursor() as cur:
            for position, item in enumerate(items, start=1):
                found = match_products(cur, item["descripcion"], "\n".join(item["requisitos"]))
                candidates[position] = found
                item.update(product_id=None, match_reason=None, candidatos=[candidate_view(c) for c in found])
        if not any(candidates.values()):
            return {"estado": "sin_candidatos"}
        shown = [{**item, "linea": position} for position, item in enumerate(items, start=1)]
        model = self._models.match
        request = build_match_request(shown, candidates, model=model)
        try:
            message = self._call_model(prep_id, models, "match", request, model)
            answer = parse_read(message)
        except PreparationFailed as exc:
            return {"estado": "tope_alcanzado"} if exc.status == STATUS_BUDGET else {"estado": "fallo", "codigo": exc.code}
        except ReadFailed as exc:
            return {"estado": "fallo", "codigo": exc.code}
        for position, (product_id, reason) in validate_match(answer, candidates).items():
            items[position - 1].update(product_id=product_id, match_reason=reason)
        return {"estado": "hecho"}

    # ── the fill phase ──────────────────────────────────────────────────────────────────────

    def _one(self, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(sql, args)
            rows = _rows(cur)
        return rows[0] if rows else None

    def _all(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql, args)
            return _rows(cur)

    def _offer_revision(self, opportunity_id: str, revision_id: str | None) -> dict[str, Any]:
        """The offer's revision, checked again here (Task 1 carry: same case; ruling R9: approved or
        sent; the money printed is CLP)."""
        if revision_id is None:
            raise PreparationFailed("offer_quote_missing")
        revision = self._one(_REVISION, (revision_id,))
        if revision is None or revision["opportunity_id"] != opportunity_id:
            raise PreparationFailed("offer_mismatch")
        if revision["status"] not in ("approved", "sent"):
            raise PreparationFailed("offer_not_approved")
        if revision["quote_currency"] != "CLP":
            raise PreparationFailed("offer_currency_not_clp")
        revision["lines"] = self._all(_LINES, (revision_id,))
        return revision

    def _fill_context(self, opportunity_id: str, based_on: str | None) -> dict[str, Any]:
        read = self._one(_READ_ROW, (based_on,)) if based_on else None
        if (read is None or read["opportunity_id"] != opportunity_id or read["phase"] != "read"
                or read["status"] != "ready"):
            raise PreparationFailed("read_not_ready")
        bidder = self._one(_BIDDER, ())
        if bidder is None:
            raise PreparationFailed("bidder_profile_missing")
        case = self._one(_CASE, (opportunity_id,)) or {}
        return {"read": read["result"] if isinstance(read["result"], dict) else {}, "bidder": bidder, "case": case}

    def _store_output(self, run: _Fill, *, file_key: str, filename: str, ext: str, kind: str, data: bytes,
                      completar_count: int = 0, **extra: Any) -> dict[str, Any]:
        """Write one output at `opportunities/<case>/bid/<prep>/<file_key>.<ext>` (the only prefix the
        API's file link signs) and record it on the row at once, so a stop keeps it."""
        from psycopg.types.json import Jsonb

        if not data:
            raise PreparationFailed("internal_error")  # never an empty output
        path = f"opportunities/{run.case_id}/bid/{run.prep_id}/{file_key}.{ext}"
        try:
            self._store.put_if_absent(path, data, content_type=package.CONTENT_TYPES.get(ext, "application/octet-stream"))
        except StorageError:
            raise PreparationFailed("output_store_failed") from None
        output = {"file_key": file_key, "filename": filename, "kind": kind, "completar_count": completar_count,
                  "storage_path": path, "byte_size": len(data), **extra}
        run.outputs.append(output)
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_SAVE_OUTPUTS, (Jsonb(run.outputs), run.prep_id))
        return output

    def _fill(self, prep_id: UUID, opportunity_id: str, annex_ids: list[str], based_on: str | None,
              revision_id: str | None, models: dict[str, str]) -> dict[str, Any]:
        ctx = self._fill_context(opportunity_id, based_on)
        revision = self._offer_revision(opportunity_id, revision_id)
        offer = package.offer_payload(revision)
        case = ctx["case"]
        code = case.get("code") or f"caso-{opportunity_id[:8]}"
        run = _Fill(prep_id=prep_id, case_id=opportunity_id, code=code)
        checklist = [dict(e) for e in ctx["read"].get("checklist") or [] if isinstance(e, dict)]
        annexes = self._annex_rows(opportunity_id, annex_ids)
        bidder = _bidder_for_model(ctx["bidder"])
        tender = {"codigo": code, "titulo": case.get("notice_title") or case.get("title"),
                  "comprador": case.get("buyer_name") or case.get("institution"),
                  "cierre": case["closes_at"].isoformat() if case.get("closes_at") else None}
        result: dict[str, Any] = {"read_preparation_id": based_on, "offer": offer}

        # Only the first offer-like line takes the offer; any other is filled as an ordinary form.
        offer_index = next((i for i, e in enumerate(checklist) if not e.get("falta") and package.is_offer_entry(e)),
                           None)
        stopped = False
        try:
            self._bidder_documents(run, checklist)
            result["fichas"] = self._datasheets(run, checklist, revision["lines"])
            # A form already written (the offer form) is not filled twice when another line names it.
            done: dict[str, list[dict[str, Any]]] = self._offer(run, checklist, offer_index, annexes, offer, bidder,
                                                                tender, ctx["bidder"], models)
            for index, entry in enumerate(checklist):
                form_id = entry.get("archivo_formulario_id")
                if not form_id or entry.get("falta") or index == offer_index:
                    continue
                if form_id in done:
                    for output in done[form_id]:
                        run.link(index, output)
                    if not done[form_id]:
                        run.falta.setdefault(index, run.falta.get(_first(checklist, form_id), FALTA_MISSING))
                    continue
                row = annexes.get(form_id)
                if row is None:
                    run.falta[index] = FALTA_MISSING
                    done[form_id] = []
                    continue
                output = self._form(run, index, entry, row, offer, bidder, tender, "formulario", models)
                done[form_id] = [output] if output else []
        except PreparationFailed as exc:
            if exc.status != STATUS_BUDGET:
                exc.result = {**result, "checklist": self._estados(run, checklist)}
                raise
            stopped = True
        result["checklist"] = self._estados(run, checklist)
        if stopped:
            raise PreparationFailed("budget_reached", STATUS_BUDGET, result=result)
        self._package(run, checklist, result)
        return result

    # The pieces of a fill, each writing its outputs as it goes.

    def _bidder_documents(self, run: _Fill, checklist: list[dict[str, Any]]) -> None:
        """The bidder's standard documents where a line asks for them: the newest live one of each
        kind, copied once into the preparation (an unreadable one is skipped: the line stays falta)."""
        newest: dict[str, dict[str, Any]] = {}
        for doc in self._all(_BIDDER_DOCUMENTS, ()):
            newest.setdefault(doc["kind"], doc)
        copied: dict[str, dict[str, Any] | None] = {}
        for index, entry in enumerate(checklist):
            if entry.get("falta"):
                continue
            for kind in sorted(package.bidder_kinds_for(entry)):
                doc = newest.get(kind)
                if doc is None:
                    continue
                if doc["id"] not in copied:
                    copied[doc["id"]] = self._copy_bidder_document(run, doc, len(copied) + 1)
                if copied[doc["id"]] is not None:
                    run.link(index, copied[doc["id"]])
                    run.documents.setdefault(index, []).append({"id": doc["id"], "kind": doc["kind"]})

    def _copy_bidder_document(self, run: _Fill, doc: dict[str, Any], n: int) -> dict[str, Any] | None:
        ext = package.EXTENSIONS.get(doc["content_type"])
        try:
            data = self._store.get(doc["storage_path"])
        except StorageError:
            return None
        if ext is None or hashlib.sha256(data).hexdigest() != doc["sha256"]:
            return None
        filename = package.safe_filename(doc["original_filename"], doc["title"])
        if not filename.lower().endswith(f".{ext}"):
            filename = package.safe_filename(f"{doc['title']}.{ext}", f"documento.{ext}")
        return self._store_output(run, file_key=f"oferente_{n:02d}", filename=filename, ext=ext,
                                  kind="documento_oferente",
                                  data=data, bidder_document_id=doc["id"])

    def _datasheets(self, run: _Fill, checklist: list[dict[str, Any]],
                    lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One spec sheet per product of the offer that has an active data sheet, downloaded from
        Drive (bounded); each product's outcome is listed. Without Drive there is none to add."""
        product_ids = list(dict.fromkeys(line["product_id"] for line in lines if line.get("product_id")))
        if not product_ids:
            return []
        sheets = {r["product_id"]: r for r in self._all(_DATASHEETS, (product_ids,))}
        target = next((i for i, e in enumerate(checklist) if not e.get("falta") and package.is_datasheet_entry(e)), None)
        fichas: list[dict[str, Any]] = []
        for product_id in product_ids:
            sheet = sheets.get(product_id)
            if sheet is None:
                fichas.append({"product_id": product_id, "estado": "sin_ficha"})
                continue
            if self._drive is None:
                fichas.append({"product_id": product_id, "estado": "sin_drive"})
                continue
            estado, data, ext = self._download_sheet(sheet)
            if data is None:
                fichas.append({"product_id": product_id, "estado": estado})
                continue
            label = sheet.get("model_number") or sheet.get("title") or sheet.get("name") or "producto"
            filename = package.safe_filename(f"Ficha técnica {label}.{ext}", f"Ficha técnica.{ext}")
            output = self._store_output(run, file_key=f"ficha_{len(fichas) + 1:02d}", filename=filename, ext=ext,
                                        kind="ficha_tecnica", data=data, product_id=product_id)
            run.link(target, output)
            fichas.append({"product_id": product_id, "estado": "adjunta", "file_key": output["file_key"]})
        return fichas

    def _download_sheet(self, sheet: dict[str, Any]) -> tuple[str, bytes | None, str]:
        try:
            meta = self._drive.get(sheet["drive_file_id"])
            if not meta or meta.get("trashed"):
                return "no_disponible", None, ""
            ext = package.EXTENSIONS.get(meta.get("mimeType") or sheet["mime_type"])
            if ext is None:
                return "formato_no_soportado", None, ""
            size = meta.get("size")
            if size is None or not str(size).isdigit():
                return "no_disponible", None, ""  # never download a body of unknown size
            if int(size) > package.MAX_ATTACHMENT_BYTES:
                return "demasiado_grande", None, ""
            data = self._drive.download(sheet["drive_file_id"])
        except DriveError:
            return "no_disponible", None, ""
        if not data:
            return "no_disponible", None, ""
        if len(data) > package.MAX_ATTACHMENT_BYTES:
            return "demasiado_grande", None, ""
        return "adjunta", data, ext

    def _offer(self, run: _Fill, checklist: list[dict[str, Any]], index: int | None,
               annexes: dict[str, tuple[Any, ...]],
               offer: dict[str, Any], bidder: dict[str, Any], tender: dict[str, Any], profile: dict[str, Any],
               models: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
        """The economic offer: the buyer's own offer form filled from the approved revision, or —
        when the bases bring none, or it cannot be filled — a generated «Oferta económica <code>».
        Returns {offer form id: its output} when the buyer's form was the one written."""
        output = None
        written: dict[str, list[dict[str, Any]]] = {}
        if index is not None:
            row = annexes.get(checklist[index].get("archivo_formulario_id") or "")
            if row is not None:
                output = self._form(run, index, checklist[index], row, offer, bidder, tender, "oferta_economica",
                                    models)
                if output is not None:
                    written[row[0]] = [output]
        if output is None:
            data = package.offer_docx(run.code, offer, profile)
            output = self._store_output(run, file_key="oferta_economica", ext="docx",
                                        filename=package.safe_filename(f"Oferta económica {run.code}.docx",
                                                                       "Oferta económica.docx"),
                                        kind="oferta_economica", data=data)
            if index is not None:
                run.falta.pop(index, None)
        run.link(index, output)
        return written

    def _form(self, run: _Fill, index: int, entry: dict[str, Any], row: tuple[Any, ...], offer: dict[str, Any],
              bidder: dict[str, Any], tender: dict[str, Any], kind: str,
              models: dict[str, str]) -> dict[str, Any] | None:
        """One buyer's form: its fields filled by the model, or — a flat form with text — a generated
        .docx with its content filled. A form that cannot be either is ruling R13's «Falta: convertir
        a Word (.docx)», never an empty output. Raises `PreparationFailed` from the model call (the
        cap stops the fill; a failed request fails it)."""
        doc_id, filename = row[0], row[1]
        data = self._annex_data(row)
        inspection = inspect_form(filename, data)
        fillable = inspection.kind != NOT_FILLABLE
        if not fillable and inspection.reason != "sin_campos":
            return self._not_fillable(run, index, doc_id, filename)
        annex = extract_annex(doc_id, filename, row[2], data)
        form_text = "\n\n".join(page.text for page in annex.pages)
        if not fillable and not form_text.strip():
            return self._not_fillable(run, index, doc_id, filename)
        model = self._models.fill
        request = build_fill_request(inspection.kind if fillable else "plano", inspection.slots, form_text,
                                     bidder_for_form(bidder, form_text, inspection.slots), tender, offer, model=model)
        del annex, form_text
        message = self._call_model(run.prep_id, models, "fill", request, model)
        try:
            answer = parse_read(message)
        except ReadFailed:
            run.falta[index] = FALTA_NOT_FILLED
            return None
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        try:
            if fillable:
                written = apply_values(inspection.kind, data, validate_fill(answer, inspection.slots))
                ext, count_kind = _FORM_EXTENSIONS[inspection.kind], inspection.kind
            else:
                sections = validate_sections(answer)
                if not sections:
                    run.falta[index] = FALTA_NOT_FILLED
                    return None
                written = generated_docx(str(entry.get("documento") or stem), sections, filename)
                ext, count_kind = "docx", "docx"
                kind = kind if kind == "oferta_economica" else "formulario_generado"
        except FormError:
            return self._not_fillable(run, index, doc_id, filename)
        del data
        run.forms += 1
        file_key = "oferta_economica" if kind == "oferta_economica" else f"formulario_{run.forms:02d}"
        output = self._store_output(run, file_key=file_key, filename=package.safe_filename(f"{stem}.{ext}", f"{file_key}.{ext}"),
                                    ext=ext, kind=kind, data=written, completar_count=count_completar(written, count_kind),
                                    source_document_id=doc_id)
        run.link(index, output)
        return output

    def _not_fillable(self, run: _Fill, index: int, doc_id: str, filename: str) -> None:
        run.falta[index] = FALTA_CONVERT
        if all(doc_id != known for known, _ in run.convert):
            run.convert.append((doc_id, filename))
        return None

    def _estados(self, run: _Fill, checklist: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each line with its `estado`, the `archivos` (file keys) that answer it and, when it is
        `falta`, why; then ruling R13's line for every form that could not be filled."""
        out: list[dict[str, Any]] = []
        for index, entry in enumerate(checklist):
            outputs = run.links.get(index, [])
            line = {**entry, "archivos": [o["file_key"] for o in outputs]}
            if entry.get("falta"):  # the read's own R13 line for an anexo without text
                line["estado"] = "falta"
            else:
                line["estado"] = package.checklist_status(entry, outputs, run.documents.get(index, []))
                if line["estado"] == "falta":
                    line["falta"] = run.falta.get(index) or (FALTA_COMPLETAR if outputs else FALTA_MISSING)
            out.append(line)
        # One «Falta» line per anexo: the read's own (whatever its reason) is enough.
        flagged = {e.get("archivo_formulario_id") for e in checklist if e.get("falta")}
        for doc_id, filename in run.convert:
            if doc_id in flagged:
                continue
            out.append({"documento": f"{CONVERT_TO_WORD}: {package.safe_filename(filename, 'formulario')}",
                        "tipo": "otro", "requiere_firma": False, "archivo_formulario_id": doc_id,
                        "fuente": {"archivo_id": doc_id, "ubicacion": ""}, "sin_cita": False,
                        "falta": FALTA_CONVERT, "estado": "falta", "archivos": []})
        return out

    def _package(self, run: _Fill, checklist: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """The zip of every output, in the checklist's order (then whatever answers no line), bounded
        by `package.MAX_PACKAGE_BYTES`; each member is read back from Storage only while it is
        written, and the zip is spooled to disk. Then Drive, when the case has a folder."""
        ordered: list[dict[str, Any]] = []
        for index in range(len(checklist)):
            for output in run.links.get(index, []):
                if output not in ordered:
                    ordered.append(output)
        ordered += [o for o in run.outputs if o not in ordered]
        if not ordered:
            result["drive"] = "sin_paquete"
            return
        if sum(int(o["byte_size"]) for o in ordered) > package.MAX_PACKAGE_BYTES:
            raise PreparationFailed("package_too_large", result=result)

        def reader(output: dict[str, Any]) -> Callable[[], bytes]:
            def read() -> bytes:
                try:
                    return self._store.get(output["storage_path"])
                except StorageError:
                    raise PreparationFailed("output_store_failed", result=result) from None
            return read

        with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as spool:
            try:
                package.write_package(spool, [(o["filename"], reader(o)) for o in ordered], run.code)
            except package.PackageTooLarge:
                raise PreparationFailed("package_too_large", result=result) from None
            spool.seek(0)
            data = spool.read()
        filename = package.package_filename(run.code)
        paquete = self._store_output(run, file_key="paquete", filename=filename, ext="zip", kind="paquete", data=data,
                                     completar_count=sum(int(o.get("completar_count") or 0) for o in ordered))
        result["drive"] = self._file_in_drive(run, paquete, filename, data)

    def _file_in_drive(self, run: _Fill, paquete: dict[str, Any], filename: str, data: bytes) -> str:
        """`archivado` (its id recorded on the package), `sin_carpeta` (the case has no Drive folder),
        `apagado` (no Drive settings) or `error`: Drive never fails the preparation. The Drive client's
        multipart upload takes the body as bytes, so the zip (≤ `package.MAX_PACKAGE_BYTES`) is held
        once more while it is sent."""
        from psycopg.types.json import Jsonb

        if self._drive is None or self._drive_target is None:
            return "apagado"
        try:
            file_id = file_tender_package(self._conn, self._drive, self._drive_target, opportunity_id=run.case_id,
                                          preparation_id=str(run.prep_id), name=filename, data=data)
        except Exception as exc:  # noqa: BLE001 — Drive never fails a preparation; the class only, never the text
            _log.warning("tender_drive_error %s", exc.code if isinstance(exc, FilingRefused) else type(exc).__name__)
            return "error"
        if file_id is None:
            return "sin_carpeta"
        paquete["drive_file_id"] = file_id
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_SAVE_OUTPUTS, (Jsonb(run.outputs), run.prep_id))
        return "archivado"


def _first(checklist: list[dict[str, Any]], form_id: str) -> int:
    return next(i for i, e in enumerate(checklist) if e.get("archivo_formulario_id") == form_id)


def bidder_for_form(bidder: dict[str, Any], form_text: str, slots: list[Any]) -> dict[str, Any]:
    """The bidder as one form's call sees it: the bank details only when the form's text or a field
    label asks about payment (banco, cuenta, transferencia, depósito)."""
    asks = _BANK_WORDS.search(form_text) or any(_BANK_WORDS.search(getattr(s, "label", "")) for s in slots)
    return dict(bidder) if asks else {k: v for k, v in bidder.items() if k not in BANK_FIELDS}


def _bidder_for_model(profile: dict[str, Any]) -> dict[str, Any]:
    """The bidder as the forms print it: RUTs with dots (ruling R15), dates as text, blanks left out."""
    out: dict[str, Any] = {}
    for key, value in profile.items():
        if value is None or value == "":
            continue
        if key in ("rut", "legal_rep_rut"):
            value = package._rut(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        out[key] = value
    return out


def run_bid_sweep(conn: Any, store: Any, reader: ModelReader, models: BidModels, *,
                  now: Callable[[], datetime], limit: int = SWEEP_LIMIT,
                  reconnect: Callable[[], AbstractContextManager[Any]] | None = None,
                  drive: Any = None, drive_target: DriveTarget | None = None) -> dict[str, Any]:
    """One sweep: take the single-flight lock, fail rows a dead process left `running`, then claim
    and run queued preparations one at a time. Counts only — never a case, a file or a text: an
    unexpected error is reported by its class (a database error's text may quote row values), and
    the sweep stops; a lost connection stops it too (the lock went with the session)."""
    with conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))", (LOCK_NAME,))
        if not cur.fetchone()[0]:
            return {"outcome": "locked"}
    counts: dict[str, Any] = {"outcome": "ran", "interrupted": 0, "claimed": 0, "statuses": {}}
    try:
        preparer = BidPreparer(conn, store, reader, models, now=now, reconnect=reconnect, drive=drive,
                               drive_target=drive_target)
        counts["interrupted"] = preparer.recover_interrupted()
        for _ in range(max(1, limit)):
            prep = preparer.claim()
            if prep is None:
                break
            counts["claimed"] += 1
            try:
                status = preparer.run(prep)
            except ConnectionLost:
                counts["outcome"] = "connection_lost"
                return counts
            except Exception:  # noqa: BLE001 — the row is already failed; the next one still runs
                status = "error"
            counts["statuses"][status] = counts["statuses"].get(status, 0) + 1
        return counts
    except Exception as exc:  # noqa: BLE001 — the class only; never the text
        counts.update(outcome="error", error=type(exc).__name__)
        return counts
    finally:
        with suppress(Exception), conn.cursor() as cur:
            cur.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK_NAME,))


__all__ = ["BID_REQUIRED_POLICIES", "BID_REQUIRED_PRIVILEGES", "DOCUMENTS_BUCKET", "LOCK_NAME", "SUPPORTED_PHASES",
           "BidModels", "BidPreparer", "ConnectionLost", "PreparationFailed", "WORST_INPUT_TOKENS", "call_worst_case",
           "interrupted_worst_usd", "run_bid_sweep"]
