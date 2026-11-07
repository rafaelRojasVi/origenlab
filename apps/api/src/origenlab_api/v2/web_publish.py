"""POST /v2/web/publish-model-sheet — «Publicar web»: an operator's model sheet goes live.

Validates the sheet against the public site's own JSON Schema
(`apps/web/src/content/modelos.schema.json`, the single copy), commits it as
`apps/web/src/content/modelos/<model_id>.json` through the GitHub Contents API and dispatches the
`web-deploy.yml` workflow with `apply=true`. The human approval of the `production` environment
stays in GitHub; this route deploys nothing by itself.

A command without a database: it writes no row. The audit trail is the GitHub commit (committer
`OrigenLab panel`, message names the model) plus one log line with the operator and the model.
Same role gate as the CRM authoring writers: an active `sales` or `admin` operator.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from jsonschema import Draft7Validator
from pydantic import BaseModel, ConfigDict, Field

from origenlab_api.v2._github import (
    GithubError,
    GithubPublishConfig,
    github_dispatch_workflow,
    github_file_sha,
    github_put_file,
)
from origenlab_api.v2.command_routes import Deciding

logger = logging.getLogger(__name__)

SCHEMA_RELATIVE = Path("apps/web/src/content/modelos.schema.json")
CONTENT_DIR = "apps/web/src/content/modelos"
RATE_WINDOW_SECONDS = 60.0
MAX_ERRORS = 20


def find_schema(start: Path | None = None) -> Path:
    """Walk up from `start` (default: this module) to the repo root that holds the site's schema."""
    here = (start or Path(__file__)).resolve()
    for parent in (here, *here.parents):
        candidate = parent / SCHEMA_RELATIVE
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        f"{SCHEMA_RELATIVE} not found above {here}: «Publicar web» validates against the site's "
        "own schema, so the API needs apps/web/src/content/ next to it (the Dockerfile copies it)."
    )


SCHEMA_PATH = find_schema()
MODEL_SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
Draft7Validator.check_schema(MODEL_SCHEMA)
_VALIDATOR = Draft7Validator(MODEL_SCHEMA)

_clock = time.monotonic
_last_publish: dict[str, float] = {}
_lock = threading.Lock()


def reset_rate_guard() -> None:
    with _lock:
        _last_publish.clear()


class PublishModelSheetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$", max_length=60)]
    sheet: dict[str, Any]


def _schema_errors(sheet: dict[str, Any]) -> list[dict[str, str]]:
    errors = sorted(_VALIDATOR.iter_errors(sheet), key=lambda e: [str(p) for p in e.absolute_path])
    return [
        {"path": "/".join(str(p) for p in e.absolute_path), "message": e.message[:300]}
        for e in errors[:MAX_ERRORS]
    ]


def _claim(model_id: str) -> None:
    """One publish per model per 60 s. The slot is taken before the write so a double click cannot race."""
    now = _clock()
    with _lock:
        last = _last_publish.get(model_id)
        if last is not None and now - last < RATE_WINDOW_SECONDS:
            wait = int(RATE_WINDOW_SECONDS - (now - last)) + 1
            raise HTTPException(
                status_code=429,
                detail={"code": "publish_rate_limited",
                        "message": f"this model was published a moment ago; wait {wait} s"},
                headers={"Retry-After": str(wait)},
            )
        _last_publish[model_id] = now


def _release(model_id: str) -> None:
    with _lock:
        _last_publish.pop(model_id, None)


def _config(request: Request) -> GithubPublishConfig:
    config = getattr(request.app.state, "web_publish_config", None)
    if config is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="web publishing is not configured")
    return config


web_publish_router = APIRouter(prefix="/v2/web", tags=["v2-web-publish"])


@web_publish_router.post("/publish-model-sheet")
def publish_model_sheet(
    body: PublishModelSheetRequest,
    operator: Deciding,
    request: Request,
) -> dict[str, Any]:
    config = _config(request)
    errors = _schema_errors(body.sheet)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"code": "sheet_invalid", "message": "the sheet does not match the site schema", "errors": errors},
        )
    if body.sheet.get("slug") != body.model_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "slug_mismatch", "message": "sheet.slug must equal model_id", "errors": []},
        )
    path = f"{CONTENT_DIR}/{body.model_id}.json"
    content = (json.dumps(body.sheet, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    message = f"web: ficha de {body.model_id} publicada desde el panel"

    _claim(body.model_id)
    commit_sha: str | None = None
    try:
        try:
            sha = github_file_sha(config, path)
            commit_sha = github_put_file(config, path, content, message, sha)
            run_url = github_dispatch_workflow(config)
        except GithubError as exc:
            logger.warning(
                "web publish failed: operator=%s model_id=%s code=%s status=%s commit=%s",
                operator.operator_id, body.model_id, exc.code, exc.status, commit_sha,
            )
            detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
            if commit_sha:
                detail["commit_sha"] = commit_sha
            raise HTTPException(status_code=502, detail=detail) from None
    except BaseException:
        _release(body.model_id)
        raise
    logger.info(
        "web publish: operator=%s role=%s model_id=%s commit=%s",
        operator.operator_id, operator.role, body.model_id, commit_sha,
    )
    return {"outcome": "dispatched", "run_url": run_url, "commit_sha": commit_sha}
