"""Ensure ``origenlab_api`` resolves to apps/api, not email-pipeline legacy mirror."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


def test_origenlab_api_main_resolves_under_apps_api_src() -> None:
    main = importlib.import_module("origenlab_api.main")
    path = Path(main.__file__).resolve()
    posix = path.as_posix()
    assert "/apps/api/src/origenlab_api/" in posix, f"unexpected main module path: {path}"
    assert "/apps/email-pipeline/src/origenlab_api/" not in posix


def test_origenlab_api_package_root_under_apps_api() -> None:
    pkg = importlib.import_module("origenlab_api")
    root = Path(pkg.__file__).resolve().parent
    assert root.name == "origenlab_api"
    assert root.parent.name == "src"
    assert root.parent.parent.name == "api"


def test_fastapi_cloud_entrypoint_imports_src_layout_without_pythonpath() -> None:
    api_root = Path(__file__).resolve().parents[1]
    script = """
import importlib
import json
import sys
from pathlib import Path

api_root = Path.cwd().resolve()
src = api_root / "src"
sys.path = [
    entry
    for entry in sys.path
    if Path(entry or ".").resolve() != src
]
for name in list(sys.modules):
    if name == "origenlab_api" or name.startswith("origenlab_api."):
        del sys.modules[name]

entrypoint = importlib.import_module("main")
canonical = sys.modules["origenlab_api.main"]
print(json.dumps({
    "app_title": entrypoint.app.title,
    "src_first": Path(sys.path[0]).resolve() == src,
    "canonical_main": Path(canonical.__file__).resolve().as_posix(),
}))
"""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=api_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["app_title"] == "OrigenLab API"
    assert payload["src_first"] is True
    assert Path(payload["canonical_main"]) == api_root / "src" / "origenlab_api" / "main.py"
