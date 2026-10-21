"""`origenlab-worker` — the console script. The long-running commands (the supervisor and its three
children) and the tender child's discovery run go straight to their own module; every other command
is `cli.main`.

Why: `cli` imports every command's code — Gmail capture, Drive filing, the quote reader and, through
them, the V1 email pipeline. A process that lives all day and runs one of them kept all of it
resident: on Render's 512 MB plan the supervisor and its children together outgrew the instance
once the tender child joined (2026-10-21). The one-shot commands (`gmail-sync`, `drive-file`,
`quote-read`, …) still load `cli`; the capture child runs them as short subprocesses that exit.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping, Sequence

#: Commands that run until SIGTERM, each importing only what it runs, and the tender child's
#: one-shot discovery run (`triage_cli.discovery_in_subprocess`).
LIGHT = frozenset({"mail-worker", "triage-worker", "capture-worker", "triage-child", "tender-child",
                   "tender-discovery"})


def _run(command: str, env: Mapping[str, str], started: float) -> int:
    if command == "tender-discovery":
        from origenlab_worker.triage_cli import run_tender_discovery_once
        return run_tender_discovery_once(env)
    if command == "triage-child":  # pragma: no cover - supervised until SIGTERM
        from origenlab_worker.triage_cli import run_triage_worker
        return run_triage_worker(env, started)
    if command == "tender-child":
        from origenlab_worker.triage_cli import run_tender_worker
        return run_tender_worker(env, started)
    from origenlab_worker.errors import ConfigRefused
    try:
        if command == "capture-worker":
            from origenlab_worker.capture_queue import run_capture_worker
            return run_capture_worker(env)
        from origenlab_worker.mail_worker import run_mail_worker
        return run_mail_worker(env)
    except ConfigRefused as exc:
        return _emit_line({"event": "mail_worker", "exit": 3, "error": exc.code}, started)
    except Exception as exc:
        return _emit_line({"event": "mail_worker", "exit": 1, "error": type(exc).__name__}, started)


def _emit_line(line: dict, started: float) -> int:
    """`cli._emit_line`'s twin (same JSON line), so the failure path imports nothing heavy."""
    line["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    print(json.dumps(line, sort_keys=True), flush=True)
    return int(line["exit"])


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 1 and args[0] in LIGHT:
        return _run(args[0], os.environ if environ is None else environ, time.monotonic())
    from origenlab_worker.cli import main as cli_main
    return cli_main(argv, environ)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
