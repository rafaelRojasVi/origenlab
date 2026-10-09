"""`origenlab-worker gmail-sync [--init] [--dry-run]` — the Render cron entry point (spec §3) —
and `origenlab-worker drive-file [--dry-run]`, which files recorded quote PDFs in Drive right after
(`drive_filing.py`), plus the owner's one-off `drive-ledger-import <archive_links.jsonl>...`.

Environment — Render secret values, never in the repository:

* `ORIGENLAB_WORKER_DATABASE_URL` — `origenlab_worker.<project-ref>` over the Supavisor session
  route (port 5432; 6543 is refused), no query string;
* `ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST` — the pooler host the URL must name;
* `ORIGENLAB_WORKER_DATABASE_CA_PEM` — the Supabase CA certificate (PEM text, not a path);
* `ORIGENLAB_WORKER_GMAIL_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` — the Internal OAuth
  client and contacto@'s `gmail.readonly` consent;
* `ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT` / `_REGION` / `_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY`;
* `ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED` — `true` to capture; anything else pauses (exit 0).

`drive-file` needs the database and Storage values above (not Gmail's), and:

* `ORIGENLAB_WORKER_DRIVE_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` — contacto@'s consent
  to the `drive` scope (the case archive's, `apps/api/scripts/authorize_drive_user.py`);
* `ORIGENLAB_WORKER_DRIVE_CASOS_FOLDER_ID` — `Cotizaciones/Casos`;
* `ORIGENLAB_WORKER_DRIVE_PROTECTED_FOLDER_IDS` — optional, comma-separated: the legacy
  `Pendientes` and `Enviadas`, under which nothing is ever written;
* `ORIGENLAB_WORKER_DRIVE_EXPECTED_PRINCIPAL` — optional, default `contacto@origenlab.cl`;
* `ORIGENLAB_WORKER_DRIVE_FILING_ENABLED` — `true` to file; anything else pauses (exit 0).

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
from origenlab_worker.drive_client import DriveAuthError, DriveCredentials, DriveError, DriveRest
from origenlab_worker.drive_filing import LOCK_NAME as DRIVE_LOCK_NAME
from origenlab_worker.drive_filing import DriveFiler, DriveTarget, FilingCounts, import_ledger
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.gmail_client import GmailCredentials, GmailReader
from origenlab_worker.gmail_sync import EXIT_CONFIG, EXIT_FAILED, EXIT_OK, RunCounts, RunReport, run_gmail_sync

#: `drive-file`: the Drive consent is revoked or wrong — the Gmail-auth code's twin.
EXIT_DRIVE_AUTH = 2
from origenlab_worker.storage import S3EmlStore, StorageConfig
from origenlab_worker.triage import DEFAULT_SINCE_DAYS
from origenlab_worker.triage_cli import run_triage_once, run_triage_worker

ENV_DATABASE_URL = "ORIGENLAB_WORKER_DATABASE_URL"
ENV_EXPECTED_HOST = "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST"
ENV_CA_PEM = "ORIGENLAB_WORKER_DATABASE_CA_PEM"
ENV_CLIENT_ID = "ORIGENLAB_WORKER_GMAIL_CLIENT_ID"
ENV_CLIENT_SECRET = "ORIGENLAB_WORKER_GMAIL_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN"
ENV_ENABLED = "ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED"
ENV_DRIVE_CLIENT_ID = "ORIGENLAB_WORKER_DRIVE_CLIENT_ID"
ENV_DRIVE_CLIENT_SECRET = "ORIGENLAB_WORKER_DRIVE_CLIENT_SECRET"
ENV_DRIVE_REFRESH_TOKEN = "ORIGENLAB_WORKER_DRIVE_REFRESH_TOKEN"
ENV_DRIVE_CASOS = "ORIGENLAB_WORKER_DRIVE_CASOS_FOLDER_ID"
ENV_DRIVE_PROTECTED = "ORIGENLAB_WORKER_DRIVE_PROTECTED_FOLDER_IDS"
ENV_DRIVE_PRINCIPAL = "ORIGENLAB_WORKER_DRIVE_EXPECTED_PRINCIPAL"
ENV_DRIVE_ENABLED = "ORIGENLAB_WORKER_DRIVE_FILING_ENABLED"
DEFAULT_DRIVE_PRINCIPAL = "contacto@origenlab.cl"


@dataclass(frozen=True)
class WorkerConfig:
    # Each of the three holds a secret (a DSN password, an OAuth secret, an S3 key): none is repr'd.
    database: WorkerTarget = field(repr=False)
    gmail: GmailCredentials = field(repr=False)
    storage: StorageConfig | None = field(repr=False)
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


@dataclass(frozen=True)
class DriveConfig:
    database: WorkerTarget = field(repr=False)
    storage: StorageConfig | None = field(repr=False)
    drive: DriveCredentials | None = field(repr=False)
    target: DriveTarget | None
    enabled: bool


def drive_config_from_env(env: Mapping[str, str], *, need_drive: bool = True) -> DriveConfig:
    """`drive-file`'s settings (`need_drive`), or `drive-ledger-import`'s: the database only."""
    enabled = (env.get(ENV_DRIVE_ENABLED) or "").strip().lower() == "true"
    drive = target = storage = None
    if need_drive:
        drive = DriveCredentials(
            client_id=(env.get(ENV_DRIVE_CLIENT_ID) or "").strip(),
            client_secret=(env.get(ENV_DRIVE_CLIENT_SECRET) or "").strip(),
            refresh_token=(env.get(ENV_DRIVE_REFRESH_TOKEN) or "").strip(),
        )
        if not (drive.client_id and drive.client_secret and drive.refresh_token):
            raise ConfigRefused("drive_credentials_missing")
        casos = (env.get(ENV_DRIVE_CASOS) or "").strip()
        if not casos:
            raise ConfigRefused("drive_casos_folder_missing")
        protected = frozenset(p.strip() for p in (env.get(ENV_DRIVE_PROTECTED) or "").split(",") if p.strip())
        if casos in protected:
            raise ConfigRefused("drive_casos_folder_protected")
        principal = (env.get(ENV_DRIVE_PRINCIPAL) or DEFAULT_DRIVE_PRINCIPAL).strip().lower()
        target = DriveTarget(principal=principal, casos_folder_id=casos, protected_folder_ids=protected)
        storage = StorageConfig.from_env(env)
    database = remote_worker_target(
        env.get(ENV_DATABASE_URL) or "",
        expected_host=env.get(ENV_EXPECTED_HOST),
        ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""),
    )
    return DriveConfig(database=database, storage=storage, drive=drive, target=target, enabled=enabled)


@contextmanager
def open_drive_components(config: DriveConfig) -> Iterator[tuple[Any, Any, Any]]:
    with open_worker_db(config.database) as db:
        if config.storage is None or config.drive is None:
            raise ConfigRefused("drive_not_configured")
        yield db, DriveRest(config.drive), S3EmlStore(config.storage.client())


DriveComponents = Callable[[DriveConfig], AbstractContextManager[tuple[Any, Any, Any]]]


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
    drive = commands.add_parser("drive-file", help="file recorded quote PDFs in their case's Drive folder")
    drive.add_argument("--dry-run", action="store_true", help="read and resolve; write nothing")
    drive.add_argument("--limit", type=int, default=20, help="at most this many documents per run")
    ledger = commands.add_parser("drive-ledger-import", help="owner, once: record the laptop archive's links")
    ledger.add_argument("ledgers", nargs="+", help="archive_links.jsonl files")
    ledger.add_argument("--dry-run", action="store_true", help="count; write nothing")
    commands.add_parser("triage-worker", help="compatible entrypoint for the consolidated mail worker")
    commands.add_parser("triage-child", help=argparse.SUPPRESS)
    commands.add_parser("mail-worker", help="supervise triage and gated periodic capture in one release")
    commands.add_parser("capture-worker", help="internal isolated periodic capture queue")
    once = commands.add_parser("triage-once", help="triage pending messages once, without the queue")
    once.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS, help="look back this many days")
    once.add_argument("--limit", type=int, default=200, help="at most this many messages")
    once.add_argument("--dry-run", action="store_true", help="count pending messages; write nothing")
    return parser


def _emit_line(line: dict[str, Any], started: float) -> int:
    line["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    print(json.dumps(line, sort_keys=True), flush=True)
    return int(line["exit"])


def run_drive_file(args: Any, env: Mapping[str, str], components: DriveComponents, started: float) -> int:
    line: dict[str, Any] = {"event": "drive_file", "mode": "dry_run" if args.dry_run else "file",
                            "exit": EXIT_OK, "error": None}
    if (env.get(ENV_DRIVE_ENABLED) or "").strip().lower() != "true":
        # Paused: no secret is read and no session opened for a run that would do nothing.
        line["mode"] = "paused"
        return _emit_line(line, started)
    try:
        config = drive_config_from_env(env)
        with components(config) as (db, drive, store):
            conn = db.connection
            with conn.cursor() as cur:
                cur.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))", (DRIVE_LOCK_NAME,))
                if not cur.fetchone()[0]:
                    line["mode"] = "locked"
                    return _emit_line(line, started)
            if config.target is None:  # pragma: no cover - drive_config_from_env sets it
                raise ConfigRefused("drive_not_configured")
            counts: FilingCounts = DriveFiler(conn, drive, store, config.target).run(
                limit=max(1, min(int(args.limit), 200)), dry_run=args.dry_run)
            # A refused document is reported by code and retried next run; it does not fail the
            # cron (it would fail every ten minutes until a person looks at that one case).
            line.update(counts.as_log())
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except DriveAuthError as exc:
        line.update(mode="failed", exit=EXIT_DRIVE_AUTH, error=exc.kind)
    except DriveError as exc:
        line.update(mode="failed", exit=EXIT_FAILED, error=exc.kind)
    except _Terminated:
        line.update(mode="failed", exit=EXIT_FAILED, error="terminated")
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit_line(line, started)


def run_drive_ledger_import(args: Any, env: Mapping[str, str], components: Callable[..., Any],
                            started: float) -> int:
    line: dict[str, Any] = {"event": "drive_ledger_import", "mode": "dry_run" if args.dry_run else "import",
                            "exit": EXIT_OK, "error": None}
    try:
        rows: list[dict[str, Any]] = []
        for path in args.ledgers:
            with open(path, encoding="utf-8") as fh:
                rows += [json.loads(x) for x in fh if x.strip()]
        config = drive_config_from_env(env, need_drive=False)
        with components(config.database) as db:
            line.update(import_ledger(db.connection, rows, dry_run=args.dry_run))
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except (OSError, ValueError) as exc:
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit_line(line, started)


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
    drive_components: DriveComponents = open_drive_components,
    db_components: Callable[..., Any] = open_worker_db,
) -> int:
    started = time.monotonic()
    try:
        args = _parser().parse_args(argv)
    except _UsageError:
        return _emit(RunReport("usage", EXIT_CONFIG, RunCounts(), error="usage"), started)
    env = os.environ if environ is None else environ
    if args.command in {"mail-worker", "triage-worker", "capture-worker"}:
        try:
            if args.command in {"mail-worker", "triage-worker"}:
                from origenlab_worker.mail_worker import run_mail_worker
                return run_mail_worker(env)
            from origenlab_worker.capture_queue import run_capture_worker
            return run_capture_worker(env)
        except ConfigRefused as exc:
            return _emit_line({"event": "mail_worker", "exit": EXIT_CONFIG, "error": exc.code}, started)
        except Exception as exc:
            return _emit_line({"event": "mail_worker", "exit": EXIT_FAILED, "error": type(exc).__name__}, started)
    if args.command == "drive-file":
        return run_drive_file(args, env, drive_components, started)
    if args.command == "drive-ledger-import":
        return run_drive_ledger_import(args, env, db_components, started)
    if args.command == "triage-once":
        return run_triage_once(args, env, started)
    if args.command == "triage-child":  # pragma: no cover - supervised until SIGTERM
        return run_triage_worker(env, started)
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
