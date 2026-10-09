"""`origenlab-worker triage-worker` and `origenlab-worker triage-once` — the mail triage's entry
points (docs/OPERATIONS.md §8.11).

* `triage-worker` — the Render Background Worker `origenlab-mail-triage`: runs the Procrastinate
  worker on queue `triage` until SIGTERM. The periodic sweep defers new messages every minute.
* `triage-once [--since-days N] [--limit N] [--dry-run]` — one pass without the queue: the owner's
  backfill and smoke test. `--dry-run` lists how many messages are pending and writes nothing.

Settings (environment; the database and Storage values are the capture's own):

* `ORIGENLAB_WORKER_DATABASE_URL`, `_DATABASE_EXPECTED_HOST`, `_DATABASE_CA_PEM`;
* `ORIGENLAB_WORKER_STORAGE_S3_*` — read-only use here: `get` of the stored `.eml`;
* `ORIGENLAB_WORKER_TRIAGE_ENABLED` — `true` to triage; anything else pauses (the worker idles);
* `ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED` — `true` to call Claude for the messages that need it;
  anything else runs the rules and the catalog match only (`model_state: off`);
* `ORIGENLAB_WORKER_ANTHROPIC_API_KEY` — required when the model is enabled;
* `ORIGENLAB_WORKER_TRIAGE_MODEL` — optional, default `claude-opus-5-5`.

Stdout: JSON lines with counts, classes and codes — never a subject, a body, an address or an
exception's text.
"""

from __future__ import annotations

import asyncio
import json
import re
import signal
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
from origenlab_worker.storage import S3EmlStore, StorageConfig
from origenlab_worker.triage import DEFAULT_SINCE_DAYS, ModelSettings, SweepCounts, TriageDb, triage_one
from origenlab_worker.triage_model import DEFAULT_MODEL, anthropic_reader
from origenlab_api.v2.catalog.enrichment import check_model_id

ENV_DATABASE_URL = "ORIGENLAB_WORKER_DATABASE_URL"
ENV_EXPECTED_HOST = "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST"
ENV_CA_PEM = "ORIGENLAB_WORKER_DATABASE_CA_PEM"
ENV_TRIAGE_ENABLED = "ORIGENLAB_WORKER_TRIAGE_ENABLED"
ENV_TRIAGE_MODEL_ENABLED = "ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED"
ENV_ANTHROPIC_API_KEY = "ORIGENLAB_WORKER_ANTHROPIC_API_KEY"
ENV_TRIAGE_MODEL = "ORIGENLAB_WORKER_TRIAGE_MODEL"

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


def _idle_until_terminated() -> None:
    """Paused on Render: a Background Worker that exits is restarted in a loop, so it idles."""
    stop = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    except ValueError:  # pragma: no cover - not the main thread
        pass
    while not stop.wait(PAUSED_HEARTBEAT_S):
        print(json.dumps({"event": "triage_worker", "mode": "paused"}), flush=True)


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


__all__ = ["DEFAULT_SINCE_DAYS", "ENV_ANTHROPIC_API_KEY", "ENV_TRIAGE_ENABLED", "ENV_TRIAGE_MODEL",
           "ENV_TRIAGE_MODEL_ENABLED", "TriageConfig", "run_triage_once", "run_triage_worker", "triage_config_from_env",
           "triage_enabled"]
