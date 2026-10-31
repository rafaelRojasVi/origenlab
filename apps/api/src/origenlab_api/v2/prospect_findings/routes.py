"""«Prospectos» — the prospect finding routes (spec 2026-10-23 §9).

| Route | Reads |
|---|---|
| `GET /v2/prospect-findings?view=&limit=&offset=` | one view of the classified findings and the counters |
| `GET /v2/prospect-findings/{id}` | one finding's full profile and its verdict history |
| `GET /v2/prospect-findings/{id}/logo` | the organization's logo, stored by the worker (never hot-linked) |
| `GET /v2/prospect-findings/{id}/email-preview` | a preview of the intro email (no Gmail fetch) |
| `GET /v2/prospect-findings/search-status` | last run summary, running flag, unreviewed count |
| `POST /v2/commands/review-prospect` | «Más tarde», «Descartar» or «Deshacer» (`reabrir`) |
| `POST /v2/commands/contact-prospect` | «Contactar»: the promotion into the CRM (`commands.py`) |
| `POST /v2/commands/edit-prospect` | «Editar»: a person's correction to the card (`crm.prospect_edit`) |
| `POST /v2/commands/request-prospect-search` | «Buscar ahora»: enqueue a manual prospect search run |
| `POST /v2/commands/prepare-prospect-email` | «Preparar correo»: create a Gmail draft, never send it |

Any signed-in operator reads; the commands are behind `Deciding` (a `viewer` is 403), an
`Idempotency-Key` and one `CommandTransaction`. A blocked finding is never listed and its page is 404. Not to be
confused with `GET /v2/prospects`, the lead-stage opportunities.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.prospect_findings.commands import (
    CONTACT_PROSPECT,
    EDIT_PROSPECT,
    PREPARE_PROSPECT_EMAIL,
    REVIEW_PROSPECT,
    ContactProspectBody,
    EditProspectBody,
    PrepareProspectEmailBody,
    ReviewProspectBody,
    V2ProspectCommandRepository,
    prepare_contact_prospect,
    prepare_edit_prospect,
    prepare_prepare_prospect_email,
    prepare_review_prospect,
)
from origenlab_api.v2.prospect_findings.reads import PAGE, ProspectFindingReadRepository

# Contact addresses are masked for an operator without the role (OPERATIONS §2), like every /v2 read.
prospect_read_router = APIRouter(prefix="/v2/prospect-findings", tags=["v2-prospects"], route_class=ContactRedactingRoute)
prospect_command_router = APIRouter(prefix="/v2/commands", tags=["v2-prospects"])

_NOT_FOUND = {"code": "prospect_not_found", "message": "Ese prospecto no existe."}


def get_prospect_read_repository(request: Request) -> ProspectFindingReadRepository:
    repo = getattr(request.app.state, "v2_prospect_read_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 read boundary is not configured")
    return repo


ReadRepo = Annotated[ProspectFindingReadRepository, Depends(get_prospect_read_repository)]


def _finding_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from None


@prospect_read_router.get("")
def list_prospect_findings(
    _: Operator, repo: ReadRepo,
    view: Literal["nuevos", "conocidos", "mas_tarde", "contactados", "descartados"] = "nuevos",
    limit: Annotated[int, Query(ge=1, le=200)] = PAGE, offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return repo.list(view, limit=limit, offset=offset)


@prospect_read_router.get("/{finding_id}")
def get_prospect_finding(finding_id: str, _: Operator, repo: ReadRepo) -> dict[str, Any]:
    page = repo.one(_finding_id(finding_id))
    if page is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return page


@prospect_read_router.get("/search-status")
def get_search_status(_: Operator, repo: ReadRepo) -> dict[str, Any]:
    """Last run summary, running/queued flag, and unreviewed count — for the Prospectos header."""
    return repo.search_status()


@prospect_read_router.get("/{finding_id}/logo")
def get_prospect_logo(finding_id: str, _: Operator, repo: ReadRepo) -> Response:
    logo = repo.logo(_finding_id(finding_id))
    if logo is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return Response(content=logo[1], media_type=logo[0],
                    headers={"Cache-Control": "private, max-age=604800, immutable",  # a finding's logo never changes
                             "X-Content-Type-Options": "nosniff"})


def get_prospect_command_repository(request: Request) -> V2ProspectCommandRepository:
    repo = getattr(request.app.state, "v2_prospect_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo


CommandRepo = Annotated[V2ProspectCommandRepository, Depends(get_prospect_command_repository)]


def _run(name: str, prepare: Any, body: Any, operator: Any, repo: V2ProspectCommandRepository,
         idempotency_key: str | None) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = prepare(body)
        return repo.execute(command_name=name, operator=operator, fields=fields, idempotency_key=key,
                            digest=request_digest(name, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@prospect_command_router.post("/review-prospect")
def review_prospect_route(body: ReviewProspectBody, operator: Deciding, repo: CommandRepo,
                          idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _run(REVIEW_PROSPECT, prepare_review_prospect, body, operator, repo, idempotency_key)


@prospect_command_router.post("/contact-prospect")
def contact_prospect_route(body: ContactProspectBody, operator: Deciding, repo: CommandRepo,
                           idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _run(CONTACT_PROSPECT, prepare_contact_prospect, body, operator, repo, idempotency_key)


@prospect_command_router.post("/edit-prospect")
def edit_prospect_route(body: EditProspectBody, operator: Deciding, repo: CommandRepo,
                        idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _run(EDIT_PROSPECT, prepare_edit_prospect, body, operator, repo, idempotency_key)


@prospect_command_router.post("/request-prospect-search")
def request_prospect_search_route(operator: Deciding, repo: CommandRepo) -> dict[str, Any]:
    """«Buscar ahora»: enqueue a manual prospect search run.

    Returns ``{"queued": true, "run_id": "<uuid>"}`` when a new run was enqueued, or
    ``{"queued": false, "reason": "already_running"}`` when one is already active.
    Idempotency-Key is not required here — the partial unique index is the dedup guard.
    """
    _INSERT_MANUAL = """
        insert into evidence.prospect_search_run
               (trigger, requested_by_operator_id, status)
        values ('manual', %s::uuid, 'queued')
        on conflict do nothing
        returning id::text
    """
    try:
        with repo._write() as cur:  # noqa: SLF001 — intentional: same pattern as execute()
            cur.execute(_INSERT_MANUAL, (operator.operator_id,))
            row = cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": type(exc).__name__}) from exc
    if row is None:
        return {"queued": False, "reason": "already_running"}
    return {"queued": True, "run_id": row[0]}


@prospect_read_router.get("/{finding_id}/email-preview")
def get_prospect_email_preview(finding_id: str, _: Operator, repo: ReadRepo) -> dict[str, Any]:
    """Preview the intro email for a prospect without calling Gmail.

    Returns `subject`, `text`, `html` and the person fields used to build them.
    The signature placeholder shows ``(firma de Gmail de contacto@)`` instead of the real signature.
    """
    from origenlab_api.v2.prospect_findings.commands import apply_edits
    from origenlab_api.v2.prospect_findings.intro_email import build_html, build_subject, build_text

    fid = _finding_id(finding_id)
    with repo._read() as cur:  # noqa: SLF001
        rows = repo._rows(cur,
            """
            select s.payload - 'logo' as payload,
                   jsonb_agg(pe.changes order by pe.edited_at, pe.id) filter (where pe.id is not null) as edits
              from evidence.source_record s
              left join crm.prospect_edit pe on pe.source_record_id = s.id
             where s.id = %s::uuid and s.kind = 'prospect_finding'
               and not s.is_quarantined and s.superseded_by_source_record_id is null
             group by s.id, s.payload
            """,
            (fid,),
        )
    if not rows:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    row = rows[0]
    payload = apply_edits(row["payload"] or {}, row["edits"] or [])
    if payload.get("hidden_reason"):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    # Preview uses a placeholder instead of the real Gmail signature (no network fetch)
    _PREVIEW_SIG = "(firma de Gmail de contacto@)"
    subject = build_subject(payload.get("fund_label"))
    text = build_text(
        person_name=payload.get("person_name"),
        salutation_sex=payload.get("salutation_sex"),
        project_title=payload.get("project_title"),
        fund_label=payload.get("fund_label"),
        equipment=list(payload.get("equipment") or []),
        email_source_note=payload.get("email_source_note"),
        email_source_url=payload.get("email_source_url"),
        email_source_year=payload.get("email_source_year"),
        gmail_signature_html=None,
    ).replace("OrigenLab · Valdivia, Chile\ncontacto@origenlab.cl · origenlab.cl", _PREVIEW_SIG)
    html = build_html(
        person_name=payload.get("person_name"),
        salutation_sex=payload.get("salutation_sex"),
        project_title=payload.get("project_title"),
        fund_label=payload.get("fund_label"),
        equipment=list(payload.get("equipment") or []),
        email_source_note=payload.get("email_source_note"),
        email_source_url=payload.get("email_source_url"),
        email_source_year=payload.get("email_source_year"),
        gmail_signature_html=f"<p>{_PREVIEW_SIG}</p>",
    )
    return {
        "finding_id": fid,
        "to": payload.get("email"),
        "subject": subject,
        "text": text,
        "html": html,
        "person_name": payload.get("person_name"),
        "fund_label": payload.get("fund_label"),
    }


@prospect_command_router.post("/prepare-prospect-email")
def prepare_prospect_email_route(body: PrepareProspectEmailBody, operator: Deciding, repo: CommandRepo,
                                  idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """«Preparar correo»: create a Gmail draft for a prospect, never send it.

    Appends a `source_record.prospect_email_drafted` event on success. The finding's
    `email_draft` read field will reflect the new draft after the next list/detail request.
    """
    try:
        key = require_idempotency_key(idempotency_key)
        prepare_prepare_prospect_email(body)  # validate — raises CommandRefused on bad input
        digest = request_digest(PREPARE_PROSPECT_EMAIL, body)
        return repo.prepare_email(operator=operator, body=body, idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
