"""render.yaml declares the mail-triage Background Worker exactly, and no secret as a literal."""

from __future__ import annotations

import re
from pathlib import Path

from origenlab_worker import storage, triage_cli, cli, capture_queue

RENDER_YAML = Path(__file__).resolve().parents[3] / "render.yaml"
PLAIN_VALUES = {"UV_PYTHON_DOWNLOADS": "never", "MALLOC_ARENA_MAX": "2", "PYTHON_VERSION": "3.12.13"}


def _service() -> str:
    text = RENDER_YAML.read_text(encoding="utf-8")
    start = text.index("  - type: worker\n    name: origenlab-mail-triage\n")
    end = text.find("\n  - type:", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_the_worker_runs_the_triage_queue_from_apps_worker() -> None:
    service = _service()
    for line in ("runtime: python", "rootDir: apps/worker", "buildCommand: uv sync --frozen --no-dev",
                 "startCommand: .venv/bin/origenlab-worker mail-worker"):
        assert line in service, line


def test_the_worker_keys_are_exactly_the_names_the_triage_reads_and_none_is_a_literal() -> None:
    service = _service()
    in_code = {v for mod in (triage_cli, storage, cli, capture_queue) for k, v in vars(mod).items()
               if k.startswith("ENV_") and isinstance(v, str)}
    in_yaml = set(re.findall(r"- key: (\S+)", service))
    assert in_yaml - set(PLAIN_VALUES) == in_code
    for key in in_code:
        assert re.search(rf"- key: {key}\n\s+sync: false\n", service + "\n"), key
    assert re.findall(r"- key: (\S+)\n\s+value: (\S+)", service) == list(PLAIN_VALUES.items())


def test_scheduler_is_explicitly_gated_and_legacy_cron_is_retained_for_cutover():
    service = _service()
    assert "ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED" in service
    assert "value: true" not in service
    assert "name: origenlab-gmail-sync" in RENDER_YAML.read_text()


def test_the_four_tender_settings_are_declared_on_the_worker() -> None:
    service = _service()
    for key in ("ORIGENLAB_WORKER_BID_ENABLED", "ORIGENLAB_WORKER_BID_READ_MODEL",
                "ORIGENLAB_WORKER_BID_FILL_MODEL", "ORIGENLAB_WORKER_BID_MATCH_MODEL"):
        assert re.search(rf"- key: {key}\n\s+sync: false\n", service + "\n"), key


def test_the_mercado_publico_ticket_is_declared_on_the_worker_as_a_secret() -> None:
    assert re.search(r"- key: ORIGENLAB_V2_CHILECOMPRA_TICKET\n\s+sync: false\n", _service() + "\n")
