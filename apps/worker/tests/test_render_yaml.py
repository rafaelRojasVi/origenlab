"""render.yaml declares the Gmail capture's cron service exactly, and no worker value as a literal.
Static text checks, like apps/email-pipeline/tests/test_sync_institution_prospects_to_cloud.py."""

from __future__ import annotations

import re
from pathlib import Path

from origenlab_worker import cli, storage

RENDER_YAML = Path(__file__).resolve().parents[3] / "render.yaml"
SECRET_KEYS = (
    "ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED",
    "ORIGENLAB_WORKER_DATABASE_URL",
    "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST",
    "ORIGENLAB_WORKER_DATABASE_CA_PEM",
    "ORIGENLAB_WORKER_GMAIL_CLIENT_ID",
    "ORIGENLAB_WORKER_GMAIL_CLIENT_SECRET",
    "ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN",
    "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT",
    "ORIGENLAB_WORKER_STORAGE_S3_REGION",
    "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID",
    "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY",
)


#: The only plain values: not secrets, set in the file on purpose.
PLAIN_VALUES = {"UV_PYTHON_DOWNLOADS": "never"}


def _service() -> str:
    text = RENDER_YAML.read_text(encoding="utf-8")
    start = text.index("  - type: cron\n    name: origenlab-gmail-sync\n")
    end = text.find("\n  - type:", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_the_cron_service_runs_the_worker_every_ten_minutes() -> None:
    service = _service()
    for line in ('schedule: "*/10 * * * *"', "runtime: python", "rootDir: apps/worker",
                 "buildCommand: uv sync --frozen --no-dev",
                 "startCommand: uv run --no-sync origenlab-worker gmail-sync"):
        assert line in service, line
    assert "--init" not in service and "--dry-run" not in service


def test_every_worker_variable_is_set_on_render_never_in_the_file() -> None:
    service = _service()
    for key in SECRET_KEYS:
        assert re.search(rf"- key: {key}\n\s+sync: false\n", service + "\n"), key
    assert re.findall(r"- key: (\S+)\n\s+value: (\S+)", service) == list(PLAIN_VALUES.items())
    assert service.count("value:") == len(PLAIN_VALUES)  # no other literal, secret or not


def test_a_change_to_the_imported_v1_code_rebuilds_the_cron() -> None:
    service = _service()
    match = re.search(r"buildFilter:\n\s+paths:\n((?:\s+- \S+\n)+)", service + "\n")
    assert match is not None
    assert set(re.findall(r"- (\S+)", match.group(1))) == {"apps/worker/**", "apps/email-pipeline/**", "render.yaml"}


def test_the_cron_keys_are_exactly_the_names_the_code_reads() -> None:
    in_code = {v for mod in (cli, storage) for k, v in vars(mod).items()
               if k.startswith("ENV_") and isinstance(v, str)}
    in_yaml = set(re.findall(r"- key: (\S+)", _service()))
    assert in_yaml - set(PLAIN_VALUES) == in_code == set(SECRET_KEYS)
    assert set(PLAIN_VALUES) <= in_yaml
