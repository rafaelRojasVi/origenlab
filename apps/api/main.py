"""FastAPI Cloud default entrypoint.

The canonical application lives in `origenlab_api.main`.
FastAPI Cloud auto-discovers `main.py` from the configured application
directory (`apps/api`). Because this repo uses a `src/` package layout and
the API depends on the sibling `apps/email-pipeline` local package, ensure
both source roots are importable before re-exporting the canonical app.
"""

from __future__ import annotations

import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent
_REPO_APPS_DIR = _API_DIR.parent

_SRC_DIRS = (
    _API_DIR / "src",
    _REPO_APPS_DIR / "email-pipeline" / "src",
)

for src_dir in reversed(_SRC_DIRS):
    src_dir_str = str(src_dir)
    if src_dir_str not in sys.path:
        sys.path.insert(0, src_dir_str)

from origenlab_api.main import app

__all__ = ["app"]
