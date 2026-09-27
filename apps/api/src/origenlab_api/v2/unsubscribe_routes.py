"""W10 unsubscribe routes — preview and apply staged «BAJA» replies.

* ``POST /v2/unsubscribe/preview`` — mounted with the V2 database. A read: the plan for a batch
  of already-fetched reply records and its ``input_sha256``. Nothing is written.
* ``POST /v2/commands/apply-unsubscribe-replies`` — mounted **only** with
  ``ORIGENLAB_V2_UNSUBSCRIBE_APPLY_ENABLED=true``. Needs an ``Idempotency-Key`` and the
  ``expected_input_sha256`` and ``expected_plan_sha256`` the preview answered; a batch or an
  outcome that changed since is refused.
* ``POST /v2/commands/resolve-unsubscribe-review`` — mounted with the apply command, same
  switch. Confirms one request held for review as a permanent suppression; idempotent.

Both require an active ``sales`` or ``admin`` operator from the verified identity (a viewer is
403, an unknown caller 401): the batch carries message bodies and sender addresses. Neither is
in the ``apps/dashboard-proxy`` allowlist — the dashboard shows the suppression state read-only
and has no action here. Any database failure answers without detail and writes nothing: the
transaction is rolled back before the answer leaves.
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.unsubscribe_commands import (
    ApplyUnsubscribeBody,
    PreviewUnsubscribeBody,
    ResolveUnsubscribeReviewBody,
    V2UnsubscribeRepository,
)
from origenlab_api.v2.unsubscribe_replies import APPLY_UNSUBSCRIBE_REPLIES, RESOLVE_UNSUBSCRIBE_REVIEW

unsubscribe_preview_router = APIRouter(prefix="/v2/unsubscribe", tags=["v2-unsubscribe"])
unsubscribe_apply_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])

#: SQLSTATEs `outbound.add_contact_control` and the permanence guard raise when they refuse.
_REFUSAL_SQLSTATES = frozenset({"22023", "42501", "P0001"})


def get_unsubscribe_repository(request: Request) -> V2UnsubscribeRepository:
    repo = getattr(request.app.state, "unsubscribe_repository", None)
    if repo is None:  # pragma: no cover - the routers are not mounted without one
        raise HTTPException(status_code=503, detail="unsubscribe processing is not configured")
    return repo


def _database_failure(exc: psycopg.Error) -> HTTPException:
    """Fail closed, and say no more than which kind of failure it was."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate in _REFUSAL_SQLSTATES:
        return HTTPException(status_code=409, detail={
            "code": "suppression_refused_by_database",
            "message": f"the database refused the unsubscribe (SQLSTATE {sqlstate}); nothing was written",
        })
    return HTTPException(status_code=503, detail={
        "code": "database_error", "message": "the database failed; nothing was written",
    })


@unsubscribe_preview_router.post("/preview")
def preview_unsubscribe_replies(
    body: PreviewUnsubscribeBody,
    _: Deciding,
    repo: V2UnsubscribeRepository = Depends(get_unsubscribe_repository),
) -> dict[str, Any]:
    """What applying these replies would do, one outcome per record. Writes nothing."""
    try:
        return repo.preview(body.records)
    except psycopg.Error as exc:
        raise _database_failure(exc) from exc


@unsubscribe_apply_router.post("/apply-unsubscribe-replies")
def apply_unsubscribe_replies(
    body: ApplyUnsubscribeBody,
    operator: Deciding,
    repo: V2UnsubscribeRepository = Depends(get_unsubscribe_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Record each clear «BAJA» of a previewed batch as a permanent marketing suppression."""
    try:
        key = require_idempotency_key(idempotency_key)
        return repo.execute(
            command_name=APPLY_UNSUBSCRIBE_REPLIES,
            operator=operator,
            fields=body.model_dump(mode="json"),
            idempotency_key=key,
            digest=request_digest(APPLY_UNSUBSCRIBE_REPLIES, body),
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
    except psycopg.Error as exc:
        raise _database_failure(exc) from exc


@unsubscribe_apply_router.post("/resolve-unsubscribe-review")
def resolve_unsubscribe_review(
    body: ResolveUnsubscribeReviewBody,
    operator: Deciding,
    repo: V2UnsubscribeRepository = Depends(get_unsubscribe_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Confirm one «BAJA» held for review as a permanent marketing suppression."""
    try:
        key = require_idempotency_key(idempotency_key)
        return repo.execute(
            command_name=RESOLVE_UNSUBSCRIBE_REVIEW,
            operator=operator,
            fields=body.model_dump(mode="json"),
            idempotency_key=key,
            digest=request_digest(RESOLVE_UNSUBSCRIBE_REVIEW, body),
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
    except psycopg.Error as exc:
        raise _database_failure(exc) from exc
