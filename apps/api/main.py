"""FastAPI Cloud default entrypoint.

The canonical application lives in `origenlab_api.main`.
FastAPI Cloud auto-discovers `main.py` from the configured application
directory (`apps/api`), so this shim exposes the same app without changing
runtime behavior.
"""

from origenlab_api.main import app

__all__ = ["app"]
