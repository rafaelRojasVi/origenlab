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

from fastapi import APIRouter, HTTPException, Path as PathParam, Request
from fastapi.concurrency import run_in_threadpool
from jsonschema import Draft7Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from origenlab_api.v2._github import (
    GithubError,
    GithubPublishConfig,
    github_dispatch_workflow,
    github_file_sha,
    github_latest_deploy_run,
    github_put_file,
    github_read_file,
)
from origenlab_api.v2.command_routes import Deciding

logger = logging.getLogger(__name__)

SCHEMA_RELATIVE = Path("apps/web/src/content/modelos.schema.json")
CONTENT_DIR = "apps/web/src/content/modelos"
RATE_WINDOW_SECONDS = 60.0
STATUS_CACHE_SECONDS = 10.0
MAX_ERRORS = 20
# A real sheet is a few KB. The cap is applied to the raw body, streamed, before any parsing.
MAX_BODY_BYTES = 256 * 1024
# The schema leaves these arrays unbounded and is shared byte-identical with the site, so the cap lives here.
ARRAY_CAPS = {"specs": 50, "accessories": 50, "related": 50, "faqs": 50}


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
_status_lock = threading.Lock()
_status_cache: tuple[float, dict[str, Any]] | None = None


def reset_rate_guard() -> None:
    with _lock:
        _last_publish.clear()


def reset_status_cache() -> None:
    global _status_cache
    with _status_lock:
        _status_cache = None


class PublishModelSheetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$", max_length=60)]
    sheet: dict[str, Any]


def _schema_errors(sheet: dict[str, Any]) -> list[dict[str, str]]:
    errors = sorted(_VALIDATOR.iter_errors(sheet), key=lambda e: [str(p) for p in e.absolute_path])
    found = [
        {"path": "/".join(str(p) for p in e.absolute_path), "message": e.message[:300]}
        for e in errors
    ]
    for name, cap in ARRAY_CAPS.items():
        value = sheet.get(name)
        if isinstance(value, list) and len(value) > cap:
            found.append({"path": name, "message": f"at most {cap} items are allowed ({len(value)} given)"})
    found.sort(key=lambda e: e["path"])
    return found[:MAX_ERRORS]


def _claim(model_id: str) -> None:
    """One publish per model per 60 s. The slot is taken before the write so a double click cannot race."""
    now = _clock()
    with _lock:
        for stale in [k for k, t in _last_publish.items() if now - t >= RATE_WINDOW_SECONDS]:
            del _last_publish[stale]
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


ModelId = Annotated[str, PathParam(pattern=r"^[a-z0-9-]+$", max_length=60)]

web_publish_router = APIRouter(prefix="/v2/web", tags=["v2-web-publish"])


async def _read_bounded_body(request: Request) -> bytes:
    """The raw body, streamed and capped; Content-Length alone is never trusted."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_big = int(declared) > MAX_BODY_BYTES
        except ValueError:
            raise HTTPException(
                status_code=400, detail={"code": "bad_content_length", "message": "invalid Content-Length"},
            ) from None
        if too_big:
            raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail={"code": "sheet_too_large", "message": f"the request body may not exceed {MAX_BODY_BYTES // 1024} KB"},
    )


def _parse_body(raw: bytes) -> PublishModelSheetRequest:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise HTTPException(
            status_code=422, detail={"code": "body_invalid", "message": "the body is not valid JSON"},
        ) from None
    try:
        return PublishModelSheetRequest.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_url=False, include_input=False, include_context=False),
        ) from None


@web_publish_router.post(
    "/publish-model-sheet",
    openapi_extra={"requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PublishModelSheetRequest.model_json_schema()}},
    }},
)
async def publish_model_sheet(operator: Deciding, request: Request) -> dict[str, Any]:
    # The role gate (`Deciding`) has already run: a viewer is refused before any byte is read.
    config = _config(request)
    body = _parse_body(await _read_bounded_body(request))
    return await run_in_threadpool(_publish, config, operator, body)


def _publish(config: GithubPublishConfig, operator: Any, body: PublishModelSheetRequest) -> dict[str, Any]:
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


@web_publish_router.get("/model-sheet/{model_id}")
def read_model_sheet(model_id: ModelId, operator: Deciding, request: Request) -> dict[str, Any]:
    """The published sheet, so the editor prefills from it instead of overwriting it with blanks.

    Validated leniently: an invalid sheet is still returned, with `valid: false` and the errors, so
    the editor can show what is wrong.
    """
    config = _config(request)
    try:
        record = github_read_file(config, f"{CONTENT_DIR}/{model_id}.json")
    except GithubError as exc:
        logger.warning("web sheet read failed: model_id=%s code=%s status=%s", model_id, exc.code, exc.status)
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from None
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "sheet_not_found", "message": "no published sheet"})
    try:
        sheet = json.loads(record["content"].decode("utf-8"))
    except ValueError:  # includes UnicodeDecodeError
        sheet = None
    if not isinstance(sheet, dict):
        raise HTTPException(
            status_code=502,
            detail={"code": "read_failed", "message": "the published file is not a JSON object"},
        )
    errors = _schema_errors(sheet)
    return {
        "model_id": model_id, "sheet": sheet, "sha": record["sha"], "html_url": record["html_url"],
        "valid": not errors, "errors": errors,
    }


def _map_run(run: dict[str, Any] | None) -> dict[str, Any]:
    if run is None:
        return {"status": "unknown", "run_url": None, "updated_at": None, "head_sha": None}
    gh_status, conclusion = run.get("status"), run.get("conclusion")
    if gh_status in ("queued", "requested", "pending"):
        status = "queued"
    elif gh_status == "in_progress":
        status = "running"
    elif gh_status == "waiting":  # the `production` environment is awaiting its approval
        status = "waiting"
    elif gh_status == "completed" and conclusion:
        status = "success" if conclusion == "success" else "failure"
    else:
        status = "unknown"
    return {
        "status": status, "run_url": run.get("html_url"),
        "updated_at": run.get("updated_at"), "head_sha": run.get("head_sha"),
    }


@web_publish_router.get("/publish-status/{model_id}")
def get_publish_status(model_id: ModelId, operator: Deciding, request: Request) -> dict[str, Any]:
    """State of the latest deploy run. `model_id` is accepted for symmetry with the other routes and
    for future filtering; today the latest `web-deploy.yml` dispatch is returned whichever model it
    was for (a dispatch carries no model). One GitHub call per 10 s per process, however many
    editors poll; failures are not cached."""
    global _status_cache
    config = _config(request)
    with _status_lock:
        now = _clock()
        if _status_cache is not None and now - _status_cache[0] < STATUS_CACHE_SECONDS:
            return _status_cache[1]
        try:
            answer = _map_run(github_latest_deploy_run(config))
        except GithubError as exc:
            logger.warning("web publish status failed: code=%s status=%s", exc.code, exc.status)
            raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from None
        _status_cache = (now, answer)
        return answer
