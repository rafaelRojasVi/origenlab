"""`origenlab-worker prospects-load`: a JSON-lines file of prospect findings into evidence.

Dry run by default (counts what is valid and new); `--apply` writes, as the worker login, with the
same database settings as the other one-off commands. Prints one JSON line, like they do."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from origenlab_worker.database import WorkerTarget, open_verified_connection, remote_worker_target, write_ca_file
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.prospects.logo import fetch_logo
from origenlab_worker.prospects.store import PROSPECTS_REQUIRED_POLICIES, PROSPECTS_REQUIRED_PRIVILEGES, load_findings
from origenlab_worker.triage_cli import (
    ENV_CA_PEM,
    ENV_DATABASE_URL,
    ENV_EXPECTED_HOST,
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_OK,
    _emit,
)


def run_prospects_load(args: Any, env: Mapping[str, str], started: float, *,
                       target: WorkerTarget | None = None) -> int:
    line: dict[str, Any] = {"event": "prospects_load", "mode": "apply" if args.apply else "dry_run",
                            "exit": EXIT_OK, "error": None}
    try:
        path = Path(args.file)
        if not path.is_file():
            raise ConfigRefused("prospects_file_missing")
        if target is None:
            target = remote_worker_target(env.get(ENV_DATABASE_URL) or "", expected_host=env.get(ENV_EXPECTED_HOST),
                                          ca_file=write_ca_file(env.get(ENV_CA_PEM) or ""))
        with open_verified_connection(target, application_name="origenlab-worker-prospects",
                                      privileges=PROSPECTS_REQUIRED_PRIVILEGES,
                                      policies=PROSPECTS_REQUIRED_POLICIES) as conn:
            fetch = (lambda url: None) if args.no_logos else fetch_logo
            report = load_findings(conn, path, apply=bool(args.apply), fetch=fetch)
        line.update(report)
    except ConfigRefused as exc:
        line.update(mode="config_refused", exit=EXIT_CONFIG, error=exc.code)
    except Exception as exc:  # noqa: BLE001 — the class, never the text
        line.update(mode="failed", exit=EXIT_FAILED, error=type(exc).__name__)
    return _emit(line, started)
