"""`origenlab-worker triage-worker` and `origenlab-worker triage-once` — the mail triage's entry
points (docs/OPERATIONS.md §8.11).

* `triage-worker` — the Render Background Worker `origenlab-mail-triage`: runs the Procrastinate
  worker on queue `triage` until SIGTERM. The periodic sweep defers new messages every minute.
* `triage-once [--since-days N] [--limit N] [--dry-run]` — one pass without the queue: the owner's
  backfill and smoke test. `--dry-run` lists how many messages are pending and writes nothing.
* `people-backfill [--since-days 90] [--limit N] [--sample N] [--apply]` — `people:v1` (who wrote it)
  for mail triaged before that reading existed: one people-only model call per inbound email a
  person wrote. Without `--apply` nothing is written; `--sample N` prices the run first.
* `forward-backfill [--since-days N] [--limit N] [--apply]` — reads only the forwards we sent of the
  last days for who really asked (spec 2026-10-14); never re-runs the triage, never calls a model.
  Without `--apply` it reads and counts and writes nothing.

Settings (environment; the database and Storage values are the capture's own):

* `ORIGENLAB_WORKER_DATABASE_URL`, `_DATABASE_EXPECTED_HOST`, `_DATABASE_CA_PEM`;
* `ORIGENLAB_WORKER_STORAGE_S3_*` — read-only use here: `get` of the stored `.eml`;
* `ORIGENLAB_WORKER_TRIAGE_ENABLED` — `true` to triage; anything else pauses (the worker idles);
* `ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED` — `true` to call Claude for the messages that need it;
  anything else runs the rules and the catalog match only (`model_state: off`);
* `ORIGENLAB_WORKER_ANTHROPIC_API_KEY` — required when the model is enabled;
* `ORIGENLAB_WORKER_TRIAGE_MODEL` — optional, default `claude-opus-5-5`.

`tender-child` — the tender bid preparations (`tender/job.py`), in its own supervised process that
keeps the Drive settings (ruling R4); started only when `ORIGENLAB_WORKER_BID_ENABLED` is `true`.
It serves queue `tenders` and needs the database, Storage (`crm-documents`, same key) and Anthropic
values above, plus `ORIGENLAB_WORKER_BID_READ_MODEL` (default `claude-opus-5-5`), `_BID_FILL_MODEL`
(default `claude-opus-5-5`) and `_BID_MATCH_MODEL` (default `claude-sonnet-5-5`). A model the
spend ledger has no price for is refused (`model_unpriced`): the monthly cap could not hold. With
`drive-file`'s Drive settings on and complete, the fill phase takes the spec sheets from Drive and
files the package in the case folder; without them it runs with Drive off.

Stdout: JSON lines with counts, classes and codes — never a subject, a body, an address or an
exception's text.
"""

from __future__ import annotations

import asyncio
import json
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from origenlab_worker.database import (
    TRIAGE_REQUIRED_POLICIES,
    TRIAGE_REQUIRED_PRIVILEGES,
    WorkerTarget,
    open_verified_connection,
    remote_worker_target,
    write_ca_file,
)
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.flags import ENV_BID_ENABLED, bid_enabled
from origenlab_worker.storage import S3EmlStore, StorageConfig
from origenlab_worker.triage import (
    DEFAULT_SINCE_DAYS,
    ModelSettings,
    SweepCounts,
    TriageDb,
    backfill_forwards,
    backfill_people,
    backfill_quote_refs,
    triage_one,
)
from origenlab_worker.triage_model import DEFAULT_MODEL, anthropic_reader
from origenlab_api.v2.catalog.enrichment import check_model_id

ENV_DATABASE_URL = "ORIGENLAB_WORKER_DATABASE_URL"
ENV_EXPECTED_HOST = "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST"
ENV_CA_PEM = "ORIGENLAB_WORKER_DATABASE_CA_PEM"
ENV_TRIAGE_ENABLED = "ORIGENLAB_WORKER_TRIAGE_ENABLED"
ENV_TRIAGE_MODEL_ENABLED = "ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED"
ENV_ANTHROPIC_API_KEY = "ORIGENLAB_WORKER_ANTHROPIC_API_KEY"
ENV_TRIAGE_MODEL = "ORIGENLAB_WORKER_TRIAGE_MODEL"
ENV_BID_READ_MODEL = "ORIGENLAB_WORKER_BID_READ_MODEL"
ENV_BID_FILL_MODEL = "ORIGENLAB_WORKER_BID_FILL_MODEL"
ENV_BID_MATCH_MODEL = "ORIGENLAB_WORKER_BID_MATCH_MODEL"
#: The Mercado Público ticket for «En Mercado Público» (the API's variable, then V1's). Unset: the
#: discovery sweep logs `no_ticket` and the bid preparations run as before.
ENV_CHILECOMPRA_TICKET = "ORIGENLAB_V2_CHILECOMPRA_TICKET"
#: Not an `ENV_` name: V1's variable is read as a fallback, never declared on this service.
CHILECOMPRA_TICKET_FALLBACK = "CHILECOMPRA_API_TICKET"
DEFAULT_BID_READ_MODEL = "claude-opus-5-5"
DEFAULT_BID_FILL_MODEL = "claude-opus-5-5"
DEFAULT_BID_MATCH_MODEL = "claude-sonnet-5-5"
APP_NAME_BID = "origenlab-worker-bid-preparation"
APP_NAME_DISCOVERY = "origenlab-worker-tender-discovery"

EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 3
WORKER_CONCURRENCY = 2
PAUSED_HEARTBEAT_S = 3600


@dataclass(frozen=True)
class TriageConfig:
    database: WorkerTarget = field(repr=False)
    storage: StorageConfig = field(repr=False)
    model: ModelSettings = field(repr=False)
    model_name: str
    model_enabled: bool


def _flag(env: Mapping[str, str], key: str) -> bool:
    return (env.get(key) or "").strip().lower() == "true"


def triage_enabled(env: Mapping[str, str]) -> bool:
    return _flag(env, ENV_TRIAGE_ENABLED)


def triage_config_from_env(env: Mapping[str, str], *,
                           reader_factory: Callable[[str], Any] = anthropic_reader) -> TriageConfig:
    model_enabled = _flag(env, ENV_TRIAGE_MODEL_ENABLED)
    try:
        model_name = check_model_id((env.get(ENV_TRIAGE_MODEL) or DEFAULT_MODEL).strip())
    except ValueError:
        raise ConfigRefused("triage_model_invalid") from None
    reader = None
    if model_enabled:
        key = (env.get(ENV_ANTHROPIC_API_KEY) or "").strip()
        if not key:
            raise ConfigRefused("anthropic_api_key_missing")
        reader = reader_factory(key)
    storage = StorageConfig.from_env(env)
    database = remote_worker_target(
        env.get(ENV_DATABASE_URL) or "",
        expected_host=env.get(ENV_EXPECTED_HOST),
        ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""),
    )
    return TriageConfig(database=database, storage=storage, model=ModelSettings(reader, model_name),
                        model_name=model_name, model_enabled=model_enabled)


def _emit(line: dict[str, Any], started: float) -> int:
    line["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    print(json.dumps(line, sort_keys=True), flush=True)
    return int(line["exit"])


OpenConnection = Callable[[WorkerTarget], Any]


def _open_triage_connection(target: WorkerTarget) -> Any:
    return open_verified_connection(target, application_name="origenlab-worker-triage",
                                    privileges=TRIAGE_REQUIRED_PRIVILEGES, policies=TRIAGE_REQUIRED_POLICIES)


def run_triage_once(args: Any, env: Mapping[str, str], started: float, *,
                    open_connection: OpenConnection = _open_triage_connection,
                    store_factory: Callable[[StorageConfig], Any] | None = None,
                    reader_factory: Callable[[str], Any] = anthropic_reader) -> int:
    line: dict[str, Any] = {"event": "triage_once", "mode": "dry_run" if args.dry_run else "triage",
                            "exit": EXIT_OK, "error": None}
    if not triage_enabled(env):
        line["mode"] = "paused"
        return _emit(line, started)
    try:
        config = triage_config_from_env(env, reader_factory=reader_factory)
        line["model"] = config.model_name if config.model_enabled else "off"
        store = (store_factory or (lambda s: S3EmlStore(s.client())))(config.storage)
        since = max(1, min(int(args.since_days), 3650))
        limit = max(1, min(int(args.limit), 5000))
        counts = SweepCounts()
        with open_connection(config.database) as conn:
            db = TriageDb(conn)
            pending = db.pending(since_days=since, limit=limit)
            counts.found = len(pending)
            if not args.dry_run:
                for sid in pending:
                    counts.add(triage_one(db, store, sid, config.model))
        line.update(found=counts.found, outcomes=counts.outcomes, classes=counts.classes,
                    model_states=counts.model)
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit(line, started)


def run_forward_backfill(args: Any, env: Mapping[str, str], started: float, *,
                         open_connection: OpenConnection = _open_triage_connection,
                         store_factory: Callable[[StorageConfig], Any] | None = None) -> int:
    return _run_backfill("forward_backfill", "pending_forwards", backfill_forwards, args, env, started,
                         open_connection=open_connection, store_factory=store_factory)


def run_quote_refs_backfill(args: Any, env: Mapping[str, str], started: float, *,
                            open_connection: OpenConnection = _open_triage_connection,
                            store_factory: Callable[[StorageConfig], Any] | None = None) -> int:
    """Past emails read for the quote numbers their body names (`quote_refs:v1`); dry run by default."""
    return _run_backfill("quote_refs_backfill", "pending_quote_refs", backfill_quote_refs, args, env, started,
                         open_connection=open_connection, store_factory=store_factory)


PEOPLE_BACKFILL_DAYS = 90


def run_people_backfill(args: Any, env: Mapping[str, str], started: float, *,
                        open_connection: OpenConnection = _open_triage_connection,
                        store_factory: Callable[[StorageConfig], Any] | None = None,
                        reader_factory: Callable[[str], Any] = anthropic_reader) -> int:
    """`people:v1` for the last days' mail triaged before it existed (`triage.backfill_people`).

    Plain run: counts only, no model, nothing written. `--sample N`: the model reads the first N
    pending emails and the line prices them (`sample_usd`) and the whole list (`estimated_usd_all`,
    `_price_people_run`; `limit_reached` when `--limit` cut the list); written only with `--apply`.
    A model outage mid-run ends `failed` with the counts so far. `--apply` alone: every pending
    email. Resumable — rerun it after a stop. Needs `ORIGENLAB_WORKER_ANTHROPIC_API_KEY` whenever
    the model runs; `ORIGENLAB_WORKER_TRIAGE_MODEL` picks the model (default as the triage)."""
    sample = None if args.sample is None else max(1, min(int(args.sample), 1000))
    mode = "apply" if args.apply else ("sample" if sample is not None else "dry_run")
    line: dict[str, Any] = {"event": "people_backfill", "mode": mode, "exit": EXIT_OK, "error": None}
    try:
        try:
            model = check_model_id((env.get(ENV_TRIAGE_MODEL) or DEFAULT_MODEL).strip())
        except ValueError:
            raise ConfigRefused("triage_model_invalid") from None
        storage = StorageConfig.from_env(env)
        database = remote_worker_target(env.get(ENV_DATABASE_URL) or "", expected_host=env.get(ENV_EXPECTED_HOST),
                                        ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""))
        reader = None
        if args.apply or sample is not None:
            key = (env.get(ENV_ANTHROPIC_API_KEY) or "").strip()
            if not key:
                raise ConfigRefused("anthropic_api_key_missing")
            reader = reader_factory(key)
        store = (store_factory or (lambda s: S3EmlStore(s.client())))(storage)
        since = max(1, min(int(args.since_days), 3650))
        limit = max(1, min(int(args.limit), 5000))
        line.update(model=model, since_days=since)
        with open_connection(database) as conn:
            db = TriageDb(conn)
            pending = db.pending_people(since_days=since, limit=limit)
            # `limit_reached`: the list was cut at `--limit` — more mail is pending than `found`, the
            # estimate covers only `found`, and `--apply` needs another pass.
            line["limit_reached"] = len(pending) >= limit
            counts = backfill_people(db, store, reader, pending, apply=bool(args.apply), sample=sample, model=model)
        line.update(counts)
        _price_people_run(line, counts, model)
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(getattr(exc, "counts", None) or {})
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit(line, started)


def _price_people_run(line: dict[str, Any], counts: dict[str, int], model: str) -> None:
    """`sample_usd` (what the model calls cost) and `estimated_usd_all`: the cost per email looked
    at — skipped ones count as free — times `found`, so mail the run will skip is not priced."""
    from decimal import Decimal

    from origenlab_api.v2.model_spend import UnpricedModel, cost_usd

    if not counts["model_calls"]:
        return
    try:
        spent = cost_usd(model, input_tokens=counts["input_tokens"], output_tokens=counts["output_tokens"])
    except UnpricedModel:
        line["sample_usd"] = "unpriced"
        return
    line["sample_usd"] = str(spent)
    looked_at = counts["read"] + counts["skipped"]
    if looked_at:
        line["estimated_usd_all"] = str((spent / looked_at * counts["found"]).quantize(Decimal("0.0001")))


def _run_backfill(event: str, pending: str, backfill: Callable[..., dict[str, int]], args: Any,
                  env: Mapping[str, str], started: float, *, open_connection: OpenConnection,
                  store_factory: Callable[[StorageConfig], Any] | None) -> int:
    line: dict[str, Any] = {"event": event, "mode": "apply" if args.apply else "dry_run",
                            "exit": EXIT_OK, "error": None}
    try:
        storage = StorageConfig.from_env(env)
        database = remote_worker_target(env.get(ENV_DATABASE_URL) or "", expected_host=env.get(ENV_EXPECTED_HOST),
                                        ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""))
        store = (store_factory or (lambda s: S3EmlStore(s.client())))(storage)
        since = max(1, min(int(args.since_days), 3650))
        limit = max(1, min(int(args.limit), 2000))
        with open_connection(database) as conn:
            db = TriageDb(conn)
            line.update(backfill(db, store, getattr(db, pending)(since_days=since, limit=limit),
                                 apply=bool(args.apply)))
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit(line, started)


def run_memory_backfill(args: Any, env: Mapping[str, str], started: float, *,
                        open_connection: OpenConnection = _open_triage_connection,
                        store_factory: Callable[[StorageConfig], Any] | None = None,
                        reader_factory: Callable[[str], Any] = anthropic_reader) -> int:
    """Past HUMAN_CLASSES emails matched to a case that have no memory yet — write their first memory.

    Requires a valid `ORIGENLAB_WORKER_ANTHROPIC_API_KEY` (the memory writer calls Claude).
    Dry run by default; pass `--apply` to write.  The model defaults to
    `ORIGENLAB_WORKER_MEMORY_MODEL` (default `claude-haiku-5-5`).
    """
    from origenlab_worker.memory import backfill_memory, memory_model as _model_name

    line: dict[str, Any] = {"event": "memory_backfill", "mode": "apply" if args.apply else "dry_run",
                            "exit": EXIT_OK, "error": None}
    try:
        storage = StorageConfig.from_env(env)
        database = remote_worker_target(env.get(ENV_DATABASE_URL) or "", expected_host=env.get(ENV_EXPECTED_HOST),
                                        ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""))
        key = (env.get(ENV_ANTHROPIC_API_KEY) or "").strip()
        if not key:
            raise ConfigRefused("anthropic_api_key_missing")
        reader = reader_factory(key)
        store = (store_factory or (lambda s: S3EmlStore(s.client())))(storage)
        model = _model_name()
        since = max(1, min(int(args.since_days), 3650))
        limit = max(1, min(int(args.limit), 2000))
        line["model"] = model
        with open_connection(database) as conn:
            db = TriageDb(conn)
            pending = db.pending_memory_backfill(since_days=since, limit=limit)
            line["found"] = len(pending)
            if args.apply:
                counts = backfill_memory(db, store, reader, pending, apply=True, model=model)
                line.update(counts)
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit(line, started)


# ── the tender child ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BidConfig:
    database: WorkerTarget = field(repr=False)
    storage: StorageConfig = field(repr=False)
    models: Any
    reader: Any = field(repr=False)
    #: The case archive's Drive consent and folders (`drive-file`'s settings, kept by this child —
    #: ruling R4): spec sheets come from Drive and the package is filed there. None: Drive is off.
    drive: Any = field(default=None, repr=False)
    drive_target: Any = None
    #: The Mercado Público ticket, or None: discovery is skipped.
    ticket: str | None = field(default=None, repr=False)


def _bid_drive(env: Mapping[str, str]) -> tuple[Any, Any]:
    """(Drive credentials, target) when `drive-file` is switched on and fully set, else (None, None):
    a tender is prepared without Drive rather than refused."""
    from origenlab_worker import cli
    from origenlab_worker.drive_client import DriveCredentials
    from origenlab_worker.drive_filing import DriveTarget

    if (env.get(cli.ENV_DRIVE_ENABLED) or "").strip().lower() != "true":
        return None, None
    credentials = DriveCredentials(client_id=(env.get(cli.ENV_DRIVE_CLIENT_ID) or "").strip(),
                                   client_secret=(env.get(cli.ENV_DRIVE_CLIENT_SECRET) or "").strip(),
                                   refresh_token=(env.get(cli.ENV_DRIVE_REFRESH_TOKEN) or "").strip())
    casos = (env.get(cli.ENV_DRIVE_CASOS) or "").strip()
    protected = frozenset(p.strip() for p in (env.get(cli.ENV_DRIVE_PROTECTED) or "").split(",") if p.strip())
    if not (credentials.client_id and credentials.client_secret and credentials.refresh_token) or not casos \
            or casos in protected:
        return None, None
    principal = (env.get(cli.ENV_DRIVE_PRINCIPAL) or cli.DEFAULT_DRIVE_PRINCIPAL).strip().lower()
    return credentials, DriveTarget(principal=principal, casos_folder_id=casos, protected_folder_ids=protected)


def _bid_model(env: Mapping[str, str], key: str, default: str) -> str:
    from origenlab_api.v2.model_spend import UnpricedModel, worst_case_usd

    try:
        model = check_model_id((env.get(key) or default).strip())
    except ValueError:
        raise ConfigRefused("model_invalid") from None
    try:
        worst_case_usd(model, input_tokens_estimate=0, max_tokens=0)
    except UnpricedModel:
        raise ConfigRefused("model_unpriced") from None
    return model


def bid_config_from_env(env: Mapping[str, str], *,
                        reader_factory: Callable[[str], Any] | None = None) -> BidConfig:
    from origenlab_worker.tender.job import BidModels
    from origenlab_worker.tender.read_model import streaming_reader

    models = BidModels(read=_bid_model(env, ENV_BID_READ_MODEL, DEFAULT_BID_READ_MODEL),
                       fill=_bid_model(env, ENV_BID_FILL_MODEL, DEFAULT_BID_FILL_MODEL),
                       match=_bid_model(env, ENV_BID_MATCH_MODEL, DEFAULT_BID_MATCH_MODEL))
    key = (env.get(ENV_ANTHROPIC_API_KEY) or "").strip()
    if not key:
        raise ConfigRefused("anthropic_api_key_missing")
    storage = StorageConfig.from_env(env)
    database = remote_worker_target(
        env.get(ENV_DATABASE_URL) or "",
        expected_host=env.get(ENV_EXPECTED_HOST),
        ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""),
    )
    drive, drive_target = _bid_drive(env)
    ticket = ((env.get(ENV_CHILECOMPRA_TICKET) or "").strip()
              or (env.get(CHILECOMPRA_TICKET_FALLBACK) or "").strip() or None)
    return BidConfig(database=database, storage=storage, models=models,
                     reader=(reader_factory or streaming_reader)(key), drive=drive, drive_target=drive_target,
                     ticket=ticket)


def bid_sweep_for(config: BidConfig) -> Callable[[], dict[str, Any]]:
    """One sweep on a fresh verified connection: what the `bid_preparation_sweep` task runs."""
    from datetime import UTC, datetime

    from origenlab_worker.tender.job import (
        BID_REQUIRED_POLICIES,
        BID_REQUIRED_PRIVILEGES,
        DOCUMENTS_BUCKET,
        run_bid_sweep,
    )

    def connect() -> Any:
        # A remote target carries TCP keepalives (`database.KEEPALIVES`): the session stays up
        # through a model call of several minutes.
        return open_verified_connection(config.database, application_name=APP_NAME_BID,
                                        privileges=BID_REQUIRED_PRIVILEGES, policies=BID_REQUIRED_POLICIES)

    def sweep() -> dict[str, Any]:
        from origenlab_worker.drive_client import DriveRest

        with connect() as conn:
            store = S3EmlStore(config.storage.client(), bucket=DOCUMENTS_BUCKET)
            drive = DriveRest(config.drive) if config.drive is not None else None
            return run_bid_sweep(conn, store, config.reader, config.models, now=lambda: datetime.now(UTC),
                                 reconnect=connect, drive=drive, drive_target=config.drive_target)

    return sweep


def discovery_for(config: BidConfig, *, urlopen_fn: Any = None,
                  sleep: Callable[[float], None] | None = None) -> Callable[[], dict[str, Any]]:
    """One «En Mercado Público» run on a fresh verified connection: what `tender_discovery` runs.
    A failure is reported by its class only (an HTTP error's text could carry the ticket)."""
    import time
    from datetime import UTC, datetime

    from origenlab_api.v2.tenders.mercado_publico import _fetch_with_backoff
    from origenlab_email_pipeline.chilecompra_api import _extract_listado, build_licitaciones_url

    from origenlab_worker.tender.discovery import (
        DISCOVERY_REQUIRED_POLICIES,
        DISCOVERY_REQUIRED_PRIVILEGES,
        run_discovery,
    )

    pause = sleep or time.sleep

    def fetch_list() -> dict[str, Any]:
        assert config.ticket is not None  # noqa: S101 - checked by run()
        url = build_licitaciones_url(ticket=config.ticket, estado="activas")
        return _fetch_with_backoff(url, ticket=config.ticket, timeout=120.0, urlopen_fn=urlopen_fn, sleep=pause)

    def fetch_detail(code: str) -> dict[str, Any] | None:
        assert config.ticket is not None  # noqa: S101
        try:
            payload = _fetch_with_backoff(build_licitaciones_url(ticket=config.ticket, codigo=code),
                                          ticket=config.ticket, timeout=30.0, urlopen_fn=urlopen_fn, sleep=pause)
        except Exception:  # noqa: BLE001 — one missing detail waits for the next run
            return None
        listado = _extract_listado(payload)
        return listado[0] if listado else None

    def run() -> dict[str, Any]:
        if config.ticket is None:
            return {"outcome": "no_ticket"}
        try:
            with open_verified_connection(config.database, application_name=APP_NAME_DISCOVERY,
                                          privileges=DISCOVERY_REQUIRED_PRIVILEGES,
                                          policies=DISCOVERY_REQUIRED_POLICIES) as conn:
                counts = run_discovery(conn, fetch_list, fetch_detail, now=lambda: datetime.now(UTC), sleep=pause)
        except Exception as exc:  # noqa: BLE001 — the class, never the text
            return {"outcome": "failed", "error": type(exc).__name__}
        return {"outcome": "ran", **counts}

    return run


#: A discovery run that has not finished in 14 minutes is stopped before the next one is due.
DISCOVERY_TIMEOUT_S = 14 * 60


def run_tender_discovery_once(env: Mapping[str, str]) -> int:
    """`tender-discovery`: one «En Mercado Público» run, printed as one JSON line of counts. Run by
    the tender child in a process of its own (`discovery_in_subprocess`) so the ~4,800-tender list
    and the catalog vocabulary are freed when it exits instead of staying in the child all day."""
    try:
        counts = discovery_for(bid_config_from_env(env))()
    except ConfigRefused as exc:
        counts = {"outcome": "config_refused", "error": exc.code}
    print(json.dumps(counts, sort_keys=True, default=str), flush=True)
    return EXIT_OK


def discovery_in_subprocess(env: Mapping[str, str], *, run: Callable[..., Any] = subprocess.run,
                            timeout_s: float = DISCOVERY_TIMEOUT_S) -> Callable[[], dict[str, Any]]:
    """`QueueContext.discovery` for the tender child: `tender-discovery` in a short-lived process.
    Its stderr is dropped (a traceback could carry the ticket); a run that prints no counts is
    reported by its exit code only."""

    def run_once() -> dict[str, Any]:
        try:
            done = run([sys.executable, "-m", "origenlab_worker.entry", "tender-discovery"], env=dict(env),
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {"outcome": "failed", "error": "TimeoutExpired"}
        lines = [line for line in (done.stdout or "").splitlines() if line.startswith("{")]
        try:
            counts = json.loads(lines[-1])
        except (IndexError, ValueError):
            counts = None
        if not isinstance(counts, dict):
            return {"outcome": "failed", "error": f"exit_{done.returncode}"}
        return counts

    return run_once


def _serve_tenders(config: BidConfig, *, discovery: Callable[[], dict[str, Any]],
                   prospect_search: Callable[[], dict[str, Any]] | None = None) -> None:  # pragma: no cover
    """Run the tender queue until SIGTERM: one preparation at a time, and the discovery sweep."""
    from origenlab_worker import task_queue

    task_queue.CONTEXT.bid_sweep = bid_sweep_for(config)
    task_queue.CONTEXT.discovery = discovery
    task_queue.CONTEXT.prospect_search = prospect_search

    async def serve() -> None:
        with task_queue.tender_app.replace_connector(task_queue.connector_for(config.database)) as app:
            async with app.open_async():
                # One preparation at a time: the monthly cap relies on a single spender.
                await app.run_worker_async(queues=[task_queue.TENDER_QUEUE], concurrency=1, name="tenders",
                                           install_signal_handlers=True, delete_jobs="successful")

    asyncio.run(serve())


def run_tender_worker(env: Mapping[str, str], started: float) -> int:
    """The tender child. A setting it refuses (no key, an unpriced model) or a failure while it runs
    is logged — the failure by its class only — and the child idles: the supervisor stops every
    child when one exits, and a tender failure must not take triage and capture down with it."""
    line: dict[str, Any] = {"event": "tender_worker", "mode": "start", "exit": EXIT_OK, "error": None}
    try:
        if not bid_enabled(env):  # the supervisor starts this child only when tenders are on
            raise ConfigRefused("bid_disabled")
        config = bid_config_from_env(env)
    except ConfigRefused as exc:
        line.update(mode="config_refused", error=exc.code)
        _emit(line, started)
        _idle_until_terminated("tender_worker")
        return EXIT_OK
    line["models"] = {"read": config.models.read, "fill": config.models.fill, "match": config.models.match}
    _emit(dict(line), started)
    try:
        from origenlab_worker.flags import prospects_enabled
        ps = prospect_search_in_subprocess(env) if prospects_enabled(env) else None
        _serve_tenders(config, discovery=discovery_in_subprocess(env), prospect_search=ps)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", error=type(exc).__name__)
        _emit(line, started)
        _idle_until_terminated("tender_worker")
        return EXIT_OK
    line["mode"] = "stopped"  # pragma: no cover - SIGTERM ends the queue
    return _emit(line, started)  # pragma: no cover


def _idle_until_terminated(event: str = "triage_worker") -> None:
    """Paused on Render: a Background Worker that exits is restarted in a loop, so it idles."""
    stop = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    except ValueError:  # pragma: no cover - not the main thread
        pass
    while not stop.wait(PAUSED_HEARTBEAT_S):
        print(json.dumps({"event": event, "mode": "paused"}), flush=True)


def run_triage_worker(env: Mapping[str, str], started: float) -> int:  # pragma: no cover - needs a database
    from origenlab_worker import task_queue

    line: dict[str, Any] = {"event": "triage_worker", "mode": "start", "exit": EXIT_OK, "error": None}
    if not triage_enabled(env):
        line["mode"] = "paused"
        _emit(line, started)
        _idle_until_terminated()
        return EXIT_OK
    try:
        config = triage_config_from_env(env)
        task_queue.CONTEXT.target = config.database
        task_queue.CONTEXT.store_factory = lambda: S3EmlStore(config.storage.client())
        task_queue.CONTEXT.model = config.model
        revision = env.get("RENDER_GIT_COMMIT", "")
        task_queue.CONTEXT.revision = revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None
        line["model"] = config.model_name if config.model_enabled else "off"
        _emit(dict(line), started)

        async def serve() -> None:
            # replace_connector, not with_connector: the periodic sweep defers through the task's
            # own app, so a copy would leave every sweep in the module's in-memory connector.
            with task_queue.app.replace_connector(task_queue.connector_for(config.database)) as app:
                async with app.open_async():
                    await app.run_worker_async(queues=[task_queue.QUEUE], concurrency=WORKER_CONCURRENCY,
                                               install_signal_handlers=True, delete_jobs="successful")

        asyncio.run(serve())
        line["mode"] = "stopped"
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit(line, started)


PROSPECT_SEARCH_TIMEOUT_S = 20 * 60  # 20 minutes


def prospect_search_in_subprocess(env: Mapping[str, str], *,
                                   run: Callable[..., Any] = subprocess.run,
                                   timeout_s: float = PROSPECT_SEARCH_TIMEOUT_S,
                                   ) -> Callable[[], dict[str, Any]]:
    """`QueueContext.prospect_search` for the tender child: `prospect-search` in a short-lived
    process. Its stderr is dropped (a traceback could carry a project title); a run that prints
    no counts is reported by its exit code only."""

    def run_once() -> dict[str, Any]:
        try:
            done = run([sys.executable, "-m", "origenlab_worker.entry", "prospect-search"],
                       env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {"outcome": "failed", "error": "TimeoutExpired"}
        lines = [line for line in (done.stdout or "").splitlines() if line.startswith("{")]
        try:
            counts = json.loads(lines[-1])
        except (IndexError, ValueError):
            counts = None
        if not isinstance(counts, dict):
            return {"outcome": "failed", "error": f"exit_{done.returncode}"}
        return counts

    return run_once


def run_prospect_search_once(env: Mapping[str, str]) -> int:
    """`prospect-search`: claim and execute one queued prospect search run. Prints one JSON line
    of counts. Run by the tender child in a short-lived subprocess so its memory is freed."""
    from origenlab_worker.database import open_verified_connection
    from origenlab_worker.flags import prospects_enabled
    from origenlab_worker.prospects.daily import SEARCH_REQUIRED_PRIVILEGES, run_search
    from origenlab_worker.prospects.fit import model_from_env

    if not prospects_enabled(env):
        counts: dict[str, Any] = {"outcome": "skipped", "reason": "feature_off"}
        print(json.dumps(counts, sort_keys=True), flush=True)
        return EXIT_OK

    try:
        model = model_from_env(dict(env))
    except (ValueError, Exception) as exc:
        print(json.dumps({"outcome": "failed", "error": type(exc).__name__}, sort_keys=True), flush=True)
        return EXIT_OK

    # Build target from same env vars as the rest of the worker
    try:
        from origenlab_worker.triage_cli import bid_config_from_env
        config = bid_config_from_env(env)
        target = config.database
    except Exception as exc:
        print(json.dumps({"outcome": "config_refused", "error": type(exc).__name__}, sort_keys=True), flush=True)
        return EXIT_OK

    try:
        with open_verified_connection(target, application_name="origenlab-worker-prospect",
                                      privileges=SEARCH_REQUIRED_PRIVILEGES,
                                      policies=frozenset()) as conn:
            # Find the oldest queued run
            with conn.cursor() as cur:
                cur.execute(
                    "select id::text from evidence.prospect_search_run "
                    "where status = 'queued' order by requested_at, id limit 1 for update skip locked"
                )
                row = cur.fetchone()
            if row is None:
                print(json.dumps({"outcome": "no_run"}, sort_keys=True), flush=True)
                return EXIT_OK
            run_id = row[0]
            counts = run_search(conn, run_id, model)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"outcome": "failed", "error": type(exc).__name__}, sort_keys=True), flush=True)
        return EXIT_OK

    print(json.dumps({**counts, "outcome": "done"}, sort_keys=True, default=str), flush=True)
    return EXIT_OK


__all__ = ["DEFAULT_SINCE_DAYS", "ENV_ANTHROPIC_API_KEY", "ENV_BID_ENABLED", "ENV_BID_FILL_MODEL", "ENV_BID_MATCH_MODEL",
           "ENV_BID_READ_MODEL", "ENV_TRIAGE_ENABLED", "ENV_TRIAGE_MODEL", "ENV_TRIAGE_MODEL_ENABLED", "BidConfig",
           "TriageConfig", "bid_config_from_env", "bid_enabled", "bid_sweep_for", "discovery_in_subprocess",
           "prospect_search_in_subprocess", "run_prospect_search_once",
           "run_tender_discovery_once", "run_tender_worker", "run_triage_once",
           "run_triage_worker", "triage_config_from_env", "triage_enabled"]
