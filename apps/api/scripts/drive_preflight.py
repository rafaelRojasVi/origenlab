#!/usr/bin/env python3
"""Operator CLI: verify the configured CRM-Q1 Drive workspace before activation.

Runs the read-only preflight boundary (credentials load, root folder is a
writable/non-trashed destination whose storage model matches the auth mode,
template is readable/non-trashed/copyable) against the current
ORIGENLAB_DRIVE_* environment. Never creates, modifies, or deletes anything
in Drive. Output is restricted to a redacted step/category pair -- never a
token, credential path, file content, or provider response.

Usage:
    cd apps/api
    uv run python scripts/drive_preflight.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from origenlab_api.drive.preflight import DrivePreflightResult, run_drive_preflight
from origenlab_api.settings import build_settings


def main() -> int:
    settings = build_settings()
    result = run_drive_preflight(settings)

    if result.ok:
        print("ok: drive configuration preflight passed")
        return 0

    print(f"error: drive preflight failed at step={result.step} category={result.category}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
