"""render.yaml declares the mail-triage Background Worker exactly, and no secret as a literal."""

from __future__ import annotations

import re
from pathlib import Path

from origenlab_worker import storage, triage_cli

RENDER_YAML = Path(__file__).resolve().parents[3] / "render.yaml"
PLAIN_VALUES = {"UV_PYTHON_DOWNLOADS": "never", "PYTHON_VERSION": "3.12.13"}


def _service() -> str:
    text = RENDER_YAML.read_text(encoding="utf-8")
    start = text.index("  - type: worker\n    name: origenlab-mail-triage\n")
    end = text.find("\n  - type:", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_the_worker_runs_the_triage_queue_from_apps_worker() -> None:
    service = _service()
    for line in ("runtime: python", "rootDir: apps/worker", "buildCommand: uv sync --frozen --no-dev",
                 "startCommand: uv run --no-sync origenlab-worker triage-worker"):
        assert line in service, line


def test_the_worker_keys_are_exactly_the_names_the_triage_reads_and_none_is_a_literal() -> None:
    service = _service()
    in_code = {v for mod in (triage_cli, storage) for k, v in vars(mod).items()
               if k.startswith("ENV_") and isinstance(v, str)}
    in_yaml = set(re.findall(r"- key: (\S+)", service))
    assert in_yaml - set(PLAIN_VALUES) == in_code
    for key in in_code:
        assert re.search(rf"- key: {key}\n\s+sync: false\n", service + "\n"), key
    assert re.findall(r"- key: (\S+)\n\s+value: (\S+)", service) == list(PLAIN_VALUES.items())


def test_no_gmail_or_drive_credential_reaches_the_triage_worker() -> None:
    service = _service()
    assert "GMAIL" not in service and "DRIVE" not in service
