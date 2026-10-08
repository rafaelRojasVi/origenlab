"""One Render deployment, independently supervised triage and capture processes.

If either child exits unexpectedly, stop both and fail the service so Render can
restart it. A live triage child must not hide a dead capture scheduler. SIGTERM
propagates to process groups; bounded shutdown kills descendants and frees locks.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence

from origenlab_worker.capture_queue import enabled

SHUTDOWN_SECONDS = 20


def supervise(commands: Sequence[Sequence[str]], env: Mapping[str, str], *,
              stop: threading.Event, popen=subprocess.Popen) -> int:
    children = []
    try:
        for command in commands:
            child_env = dict(env)
            if command[-1] in {"triage-worker", "triage-child"}:
                child_env = {k: v for k, v in child_env.items()
                             if not k.startswith(("ORIGENLAB_WORKER_GMAIL_", "ORIGENLAB_WORKER_DRIVE_"))
                             and k != "ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED"}
            children.append(popen(list(command), env=child_env, start_new_session=True))
        while not stop.wait(0.2):
            if any(child.poll() is not None for child in children):
                return 1  # even an unexpected clean child exit is a failed service
        return 0
    finally:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + SHUTDOWN_SECONDS
        for child in children:
            try:
                child.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=5)
            # A crashed parent may leave a command child alive in the same group.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def run_mail_worker(env: Mapping[str, str]) -> int:
    commands = [[sys.executable, "-m", "origenlab_worker.cli", "triage-child"]]
    if enabled(env):
        commands.append([sys.executable, "-m", "origenlab_worker.cli", "capture-worker"])
    stop = threading.Event()
    old_handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        old_handlers[signum] = signal.signal(signum, lambda *_: stop.set())
    try:
        return supervise(commands, env, stop=stop)
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
