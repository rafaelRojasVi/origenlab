"""`origenlab-worker gmail-sync [--init] [--dry-run]` — the Render cron entry point (spec §3).

Environment — Render secret values, never in the repository:

* `ORIGENLAB_WORKER_DATABASE_URL` — `origenlab_worker.<project-ref>` over the Supavisor session
  route (port 5432; 6543 is refused), no query string;
* `ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST` — the pooler host the URL must name;
* `ORIGENLAB_WORKER_DATABASE_CA_PEM` — the Supabase CA certificate (PEM text, not a path);
* `ORIGENLAB_WORKER_GMAIL_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` — the Internal OAuth
  client and contacto@'s `gmail.readonly` consent;
* `ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT` / `_REGION` / `_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY`;
* `ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED` — `true` to capture; anything else pauses (exit 0).

Exit codes: 0 done or nothing to do, 1 failed, 2 Gmail authorization, 3 configuration. Stdout: one
JSON line per run — never a subject, a body, an address, a Gmail id or an exception's text.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any

from origenlab_worker.database import WorkerTarget, open_worker_db, remote_worker_target, write_ca_file
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.gmail_client import GmailCredentials, GmailReader
from origenlab_worker.gmail_sync import EXIT_CONFIG, EXIT_FAILED, EXIT_OK, RunCounts, RunReport, run_gmail_sync
from origenlab_worker.storage import S3EmlStore, StorageConfig

ENV_DATABASE_URL = "ORIGENLAB_WORKER_DATABASE_URL"
ENV_EXPECTED_HOST = "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST"
ENV_CA_PEM = "ORIGENLAB_WORKER_DATABASE_CA_PEM"
ENV_CLIENT_ID = "ORIGENLAB_WORKER_GMAIL_CLIENT_ID"
ENV_CLIENT_SECRET = "ORIGENLAB_WORKER_GMAIL_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN"
ENV_ENABLED = "ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED"


@dataclass(frozen=True)
class WorkerConfig:
    # Each of the three holds a secret (a DSN password, an OAuth secret, an S3 key): none is repr'd.
    database: WorkerTarget = field(repr=False)
    gmail: GmailCredentials = field(repr=False)
    storage: StorageConfig = field(repr=False)
    enabled: bool


def config_from_env(env: Mapping[str, str], *, need_storage: bool = True) -> WorkerConfig:
    """The cron's settings. The owner scripts that never touch Storage (`payload_parity.py`,
    `shadow_reconcile.py`) pass `need_storage=False`: the S3 secret is then neither required nor
    read, and `config.storage` is None."""
    gmail = GmailCredentials(
        client_id=(env.get(ENV_CLIENT_ID) or "").strip(),
        client_secret=(env.get(ENV_CLIENT_SECRET) or "").strip(),
        refresh_token=(env.get(ENV_REFRESH_TOKEN) or "").strip(),
    )
    if not (gmail.client_id and gmail.client_secret and gmail.refresh_token):
        raise ConfigRefused("gmail_credentials_missing")
    storage = StorageConfig.from_env(env) if need_storage else None
    database = remote_worker_target(
        env.get(ENV_DATABASE_URL) or "",
        expected_host=env.get(ENV_EXPECTED_HOST),
        ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""),
    )
    return WorkerConfig(
        database=database, gmail=gmail, storage=storage,
        enabled=(env.get(ENV_ENABLED) or "").strip().lower() == "true",
    )


@contextmanager
def open_components(config: WorkerConfig) -> Iterator[tuple[Any, Any, Any]]:
    with open_worker_db(config.database) as db:
        if config.storage is None:  # only the owner scripts build a config without storage
            raise ConfigRefused("storage_not_configured")
        yield db, GmailReader(config.gmail), S3EmlStore(config.storage.client())


Components = Callable[[WorkerConfig], AbstractContextManager[tuple[Any, Any, Any]]]


class _UsageError(Exception):
    """A command line argparse refused. argparse would print usage and exit 2 — the Gmail-auth code."""


class _Terminated(BaseException):
    """Render's SIGTERM, raised in the running code so every `finally` (unlock, close) still runs."""


def _on_sigterm(_signum: int, _frame: Any) -> None:
    raise _Terminated


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # noqa: ARG002 — usage text is never printed
        raise _UsageError


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="origenlab-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("gmail-sync", help="capture new contacto@ mail into the V2 database")
    sync.add_argument("--init", action="store_true", help="owner, once: authorize and take the baseline")
    sync.add_argument("--dry-run", action="store_true", help="read Gmail and the database; write nothing")
    return parser


def _emit(report: RunReport, started: float) -> int:
    line = report.as_log()
    line["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    # ru_maxrss is KB on Linux (Render); integer division floors it, so a tiny run reports 0.
    line["max_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(json.dumps(line, sort_keys=True), flush=True)
    return report.exit_code


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    components: Components = open_components,
) -> int:
    started = time.monotonic()
    try:
        args = _parser().parse_args(argv)
    except _UsageError:
        return _emit(RunReport("usage", EXIT_CONFIG, RunCounts(), error="usage"), started)
    env = os.environ if environ is None else environ
    previous = None
    try:
        previous = signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:  # not the main thread (tests): no handler, nothing else changes
        pass
    try:
        config = config_from_env(env)
        if not config.enabled and not args.init:
            # Paused: no Supavisor session every ten minutes for a run that would do nothing.
            report = RunReport("paused", EXIT_OK, RunCounts())
        else:
            with components(config) as (db, gmail, store):
                report = run_gmail_sync(db=db, gmail=gmail, store=store, enabled=config.enabled,
                                        init=args.init, dry_run=args.dry_run)
    except ConfigRefused as exc:
        report = RunReport("config_refused", EXIT_CONFIG, RunCounts(), error=exc.code)
    except _Terminated:
        report = RunReport("failed", EXIT_FAILED, RunCounts(), error="terminated")
    except KeyboardInterrupt:
        report = RunReport("failed", EXIT_FAILED, RunCounts(), error="interrupted")
    except Exception as exc:  # noqa: BLE001 — connection or TLS failure: the class, never the text
        report = RunReport("failed", EXIT_FAILED, RunCounts(), error=type(exc).__name__)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    return _emit(report, started)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
