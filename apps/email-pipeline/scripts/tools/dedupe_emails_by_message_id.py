#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (break-glass compatibility): forwards to canonical Gmail dedupe.
# Default is dry-run; DELETE requires --apply --ack-sqlite-backup.
# -----------------------------------------------------------------------------
"""Compatibility wrapper for the guarded canonical Gmail dedupe tool.

This legacy path used to delete rows immediately. It now forwards to
``scripts/maintenance/dedupe_canonical_gmail_messages.py``, whose default is
dry-run and whose writes require ``--apply --ack-sqlite-backup``.
"""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MAINTENANCE_SCRIPT = _ROOT / "scripts" / "maintenance" / "dedupe_canonical_gmail_messages.py"


def _load_maintenance_main():
    spec = importlib.util.spec_from_file_location(
        "origenlab_maintenance_dedupe_canonical_gmail_messages",
        _MAINTENANCE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load maintenance dedupe script: {_MAINTENANCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[call-arg]
    return module.main


def main(argv: Sequence[str] | None = None) -> int:
    print(
        "Compatibility wrapper: forwarding to "
        "scripts/maintenance/dedupe_canonical_gmail_messages.py "
        "(dry-run unless --apply --ack-sqlite-backup are supplied).",
        file=sys.stderr,
    )
    return _load_maintenance_main()(argv)



if __name__ == "__main__":
    raise SystemExit(main())
