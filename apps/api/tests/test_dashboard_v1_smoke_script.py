"""Dashboard v1 HTTP smoke script — route list and sqlite validation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = API_ROOT / "scripts" / "dashboard_v1_http_smoke.py"


def test_dashboard_v1_smoke_script_sqlite_ok() -> None:
    env = os.environ.copy()

    # The smoke contract is explicitly SQLite. Process environment must win
    # over a developer .env that may select the Postgres mirror.
    #
    # Do not set ORIGENLAB_DISABLE_DOTENV here: disabling dotenv also disables
    # the normal SQLite-path fallback and turns this into recovery-style
    # admission requiring an explicit ORIGENLAB_SQLITE_PATH.
    env["ORIGENLAB_API_BACKEND"] = "sqlite"
    env["ORIGENLAB_COMMERCIAL_OPERATIONS_WRITES_ENABLED"] = "false"
    env["ORIGENLAB_POSTGRES_URL"] = ""
    env["ORIGENLAB_POSTGRES_WRITE_URL"] = ""

    env.pop("ORIGENLAB_DISABLE_DOTENV", None)
    env.pop("ORIGENLAB_ENV", None)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--expect-backend", "sqlite"],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "GET /health" in proc.stdout
    assert '"ok": true' in proc.stdout or '"ok":true' in proc.stdout.replace(" ", "")


def test_smoke_script_does_not_include_legacy_paths() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    route_section = text.split("DASHBOARD_V1_ROUTES")[1].split(
        "FORBIDDEN_LEGACY_PREFIXES"
    )[0]
    assert '"/dashboard' not in route_section
    assert '"/classification' not in route_section
    assert "FORBIDDEN_LEGACY_PREFIXES" in text
