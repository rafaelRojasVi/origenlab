"""FastAPI Cloud default entrypoint.

The canonical application lives in `origenlab_api.main`.
FastAPI Cloud auto-discovers `main.py` from the configured application
directory (`apps/api`). Because this repo uses a `src/` package layout,
ensure `apps/api/src` is importable before re-exporting the canonical app.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent / "src"
_SRC_DIR_STR = str(_SRC_DIR)

if _SRC_DIR_STR not in sys.path:
    sys.path.insert(0, _SRC_DIR_STR)

from origenlab_api.main import app

__all__ = ["app"]
