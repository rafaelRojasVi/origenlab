"""CRM workspace read routes — ``/v2/workspace/*``.

GET only, same operator identity (``viewer`` or above) as ``/v2/cockpit``. The Drive links come
from the archive ledgers named by ``ORIGENLAB_V2_DRIVE_ARCHIVE_LEDGERS``, loaded once at startup;
without it every route still answers from the CRM and says ``drive_configured: false``.

**Not in the dashboard-proxy allowlist.** Local review through the Vite dev proxy only; adding
these paths to ``apps/dashboard-proxy`` is a separate, deliberate decision.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from origenlab_api.v2.audience_freeze import SEND_BLOCKERS, FreezeCriteria
from origenlab_api.v2.campaign_blocks import redact_for_viewer
from origenlab_api.v2.campaign_calendar import campaign_lines
from origenlab_api.v2.campaign_history import TOTALS, RecipientQuery
from origenlab_api.v2.campaign_planning import PLANNING_TIME_ZONE
from origenlab_api.v2.campaign_test_send import SENDER_ADDRESS, TEST_SENDS_PER_DAY, TEST_SENDS_PER_HOUR
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.case_documents import mail_storage_key
from origenlab_api.v2.case_mail import MAX_EML_BYTES, read_message, valid_message_id
from origenlab_api.v2.catalog.storage import StorageUnavailable
from origenlab_api.v2.marketing_analytics import UnknownFigure
from origenlab_api.v2.contact_redaction import ContactRedactingRoute, sees_contact_addresses
from origenlab_api.v2.crm_merge_preview import merge_preview
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.equipment_interests import interest_index, supplier_directory
from origenlab_api.v2.equipment_taxonomy import load_taxonomy
from origenlab_api.v2.fx_rates import FxRates, FxUnavailable
from origenlab_api.v2.marketing_audience import BASES, AudienceFilter, apply_filter, compose
from origenlab_api.v2.org_web_suggestions import load_org_suggestions, suggestion_for
from origenlab_api.v2.v1_lane_campaigns import as_dict as v1_campaign_as_dict
from origenlab_api.v2.v1_lane_campaigns import load_v1_lane_campaigns, load_v1_lane_html
from origenlab_api.v2.unsubscribe_replies import (
    ACCEPTED_FORMS,
    BAJA_GRAMMAR_VERSION,
    REVIEW_REASON_LABEL,
    UNSUBSCRIBE_POLICY_VERSION,
)

workspace_router = APIRouter(prefix="/v2/workspace", tags=["workspace"], route_class=ContactRedactingRoute)


def _repo(request: Request) -> CrmWorkspaceRepository:
    repo = getattr(request.app.state, "crm_workspace", None)
    if repo is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="the CRM workspace is not configured")
    return repo


Repo = Annotated[CrmWorkspaceRepository, Depends(_repo)]


@workspace_router.get("/overview")
def get_overview(_: Operator, repo: Repo) -> Any:
    """Entity counts with their provenance: imported, partial, not imported, or no write path."""
    return repo.overview()


@workspace_router.get("/history")
def get_history(_: Operator, repo: Repo, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> Any:
    """«Historial»: who decided what, on which case or institution, and when — plain Spanish only."""
    return repo.history(limit)


@workspace_router.get("/pipeline")
def get_pipeline(_: Operator, repo: Repo, request: Request) -> Any:
    """One card per opportunity: institution, contact evidence, quotes, revisions, Drive, Gmail.

    Also the link of the Drive «Casos» folder, for «Hoy»'s header (None when not configured).
    """
    body = repo.pipeline()
    body["drive_casos_url"] = getattr(request.app.state, "drive_casos_url", None)
    return body


@workspace_router.get("/providers")
def get_providers(_: Operator, repo: Repo, request: Request) -> Any:
    """The six catalogue brands first (the curated directory), then the machine candidates.

    A candidate is never promoted here: a brand name in its domain is shown as a hint only.
    Lines are real CRM organizations linked to the six taxonomy product lines.
    """
    body = repo.providers()
    body["directory"] = supplier_directory(load_taxonomy(), body["on_cases"], body["candidates"])
    body["authoring"] = {
        "enabled": bool(getattr(request.app.state, "crm_authoring_enabled", False)),
    }
    return body


@workspace_router.get("/people/merge-preview")
def get_merge_preview(
    loser: Annotated[UUID, Query()],
    winner: Annotated[UUID, Query()],
    _: Operator,
    repo: Repo,
) -> Any:
    """What a merge of loser → winner would move, without writing anything.

    Returns the same preview shape the merge-people command verifies.  The
    ``preview_sha256`` must be echoed back in the command body as
    ``expected_preview_sha256``.

    NOTE: This route must be registered before /people/{person_id} so FastAPI
    does not treat the literal string "merge-preview" as a person_id UUID.
    """
    from contextlib import contextmanager

    @contextmanager
    def _cur():  # type: ignore[no-untyped-def]
        with repo._read() as c:  # noqa: SLF001
            yield c

    # Check for same-person before touching the DB (also lets fake repos skip _read).
    if loser == winner:
        raise HTTPException(status_code=422, detail={"code": "same_person"})

    try:
        with _cur() as cur:
            preview = merge_preview(cur, str(loser), str(winner))
    except ValueError as exc:
        code = getattr(exc, "code", str(exc))
        if code == "person_not_found":
            raise HTTPException(status_code=404, detail={"code": code}) from exc
        raise HTTPException(status_code=422, detail={"code": code}) from exc
    return preview


@workspace_router.get("/people/{person_id}")
def get_person_authoring(person_id: UUID, operator: Operator, repo: Repo, request: Request) -> Any:
    """Full authoring view of one person: contact points, affiliations, notes, references, other
    names, provenance and the «¿Es …?» guesses about them (people first)."""
    people = getattr(request.app.state, "people_rules_repository", None)
    body = repo.person_authoring(str(person_id), guesses=people.guesses_for_person if people is not None else None)
    if body is None:
        raise HTTPException(status_code=404, detail="no such person")
    enabled = bool(getattr(request.app.state, "crm_authoring_enabled", False))
    body["authoring"] = {
        "enabled": enabled,
        "may_author": enabled and getattr(operator, "role", None) in ("sales", "admin"),
        "may_archive": enabled and getattr(operator, "role", None) == "admin",
    }
    return body


@workspace_router.get("/organizations/{organization_id}/authoring")
def get_organization_authoring(
    organization_id: UUID, operator: Operator, repo: Repo, request: Request
) -> Any:
    """Full authoring view of one organization: identifiers, domains, product lines, notes."""
    body = repo.organization_authoring(str(organization_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such organization")
    enabled = bool(getattr(request.app.state, "crm_authoring_enabled", False))
    body["authoring"] = {
        "enabled": enabled,
        "may_author": enabled and getattr(operator, "role", None) in ("sales", "admin"),
        "may_archive": enabled and getattr(operator, "role", None) == "admin",
    }
    # Read per request, like the V1-lane declaration: a bad file is a warning and null, never a 500.
    body["web_suggestions"] = suggestion_for(
        load_org_suggestions(getattr(request.app.state, "org_suggestions_file", None)), str(organization_id)
    )
    return body


@workspace_router.get("/opportunities/{opportunity_id}/notes")
def get_opportunity_notes(opportunity_id: UUID, _: Operator, repo: Repo) -> Any:
    """The notes on one case, for the case drawer's «Registrar seguimiento». A read only:
    writing one is `add-note` with `subject_kind = 'opportunity'` (CRM authoring)."""
    body = repo.opportunity_notes(str(opportunity_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such case")
    return body


@workspace_router.get("/opportunities/{opportunity_id}/threads")
def get_opportunity_threads(opportunity_id: UUID, _: Operator, repo: Repo, request: Request) -> Any:
    """The case's emails as Gmail cadenas, newest activity first, for the drawer's «Correos del
    caso». A read only; senders by name or domain, never the full address — except the address a
    «¿Es …?» guess asks about (`person_guesses`), masked for a viewer like every other read."""
    people = getattr(request.app.state, "people_rules_repository", None)
    body = repo.opportunity_threads(str(opportunity_id),
                                    person_guesses=people.guesses_for_case if people is not None else None)
    if body is None:
        raise HTTPException(status_code=404, detail="no such case")
    return body


@workspace_router.get("/lookup")
def get_lookup(_: Operator, repo: Repo, q: Annotated[str, Query(min_length=1, max_length=200)]) -> Any:
    """«¿Ya lo tenemos?»: where a correo, a name or a domain already is in OrigenLab — CRM people
    and institutions, their addresses, the mail we exchanged, Bajas and prospects. A read only;
    addresses masked for a role that does not read them, like every workspace read."""
    return repo.lookup(q)


@workspace_router.get("/opportunities/{opportunity_id}/messages/{message_id}")
def get_opportunity_message(opportunity_id: UUID, message_id: str, operator: Operator, repo: Repo,
                            request: Request) -> Any:
    """One email of the case, as plain text, for the dashboard's reader (`case_mail`). Only for the
    roles that read contact addresses: a body carries them. 404 when the email is not on one of
    the case's threads; 409 when only a staged record exists (open it in Gmail)."""
    if not sees_contact_addresses(operator.role):
        raise HTTPException(status_code=403, detail="reading an email is for sales and admin")
    if not valid_message_id(message_id):
        raise HTTPException(status_code=404, detail="no such email on this case")
    source = repo.opportunity_message_source(str(opportunity_id), message_id)
    if source is None:
        raise HTTPException(status_code=404, detail="no such email on this case")
    not_stored = HTTPException(status_code=409, detail={"code": "message_not_stored",
                                                        "message": "this email's original is not stored"})
    if not source.get("eml_storage_path"):
        raise not_stored
    mail = getattr(request.app.state, "mail_storage", None)
    if mail is None:
        raise HTTPException(status_code=503, detail="mail storage is unavailable")
    try:
        raw = mail.get(mail_storage_key(source["eml_storage_path"]), MAX_EML_BYTES)
    except (KeyError, ValueError):
        raise not_stored from None
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="mail storage is unavailable") from None
    return {"message_id": message_id, **read_message(raw)}


@workspace_router.get("/cases/{opportunity_id}/memory")
def get_case_memory(opportunity_id: UUID, _: Operator, repo: Repo) -> Any:
    """The latest machine memory for one case: a running summary, facts, open questions and next
    step the triage worker writes after processing each HUMAN_CLASSES email on the case's thread.
    Also returns the institution memory for the case's primary organisation when one exists.

    A read only; nothing is written here. Returns 404 when the case does not exist; returns 200
    with ``case_memory: null`` when the case exists but no memory has been written yet (the switch
    is off or no emails have been triaged yet).
    """
    body = repo.opportunity_memory(str(opportunity_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such case")
    return body


@workspace_router.get("/quote-readings")
def get_quote_readings(_: Operator, repo: Repo) -> Any:
    """The quote PDFs the quote reader refused (Datos → «Acciones automáticas»): quotation, revision,
    reason code and when. A read only; a PDF read later drops off the list."""
    return repo.quote_readings()


@workspace_router.get("/opportunities/{opportunity_id}/mail-documents")
def get_opportunity_mail_documents(opportunity_id: UUID, _: Operator, repo: Repo) -> Any:
    """The Gmail messages linked to one case and the documents they carry, for the case drawer's
    «Registrar cotización». A read only: recording one is `record-case-quotation`."""
    body = repo.opportunity_mail_documents(str(opportunity_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such case")
    return body


@workspace_router.get("/opportunities/{opportunity_id}/quote-candidates")
def get_opportunity_quote_candidates(opportunity_id: UUID, operator: Operator, repo: Repo) -> Any:
    """Read-only proposal requiring sales/admin: exact CN plus an external participant.

    A candidate may contain a PDF filename and Gmail link from a different commercial
    case, so viewers must not receive it. This never merges or records anything.
    """
    if getattr(operator, "role", None) not in ("sales", "admin"):
        raise HTTPException(status_code=403, detail="sales or admin required")
    body = repo.opportunity_quote_candidates(str(opportunity_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such case")
    return body


@workspace_router.get("/opportunities/{opportunity_id}/purchase-order-candidates")
def get_opportunity_purchase_order_candidates(
    opportunity_id: UUID, operator: Operator, repo: Repo,
) -> Any:
    """Propose verified purchase-order files from another Gmail thread; never mark won.

    This reveals an email subject, document filename and Gmail link, so restrict to
    sales/admin. Operator must read the original PO PDF to verify its printed
    quotation reference and line amounts before attaching anything to a case.
    """
    if getattr(operator, "role", None) not in ("sales", "admin"):
        raise HTTPException(status_code=403, detail="sales or admin required")
    body = repo.opportunity_purchase_order_candidates(str(opportunity_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such case")
    return body


@workspace_router.get("/person-suggestions")
def get_person_suggestions(_: Operator, repo: Repo) -> Any:
    """People the quote emails name and the CRM does not hold yet. A read: nothing is created."""
    return repo.person_suggestions()


@workspace_router.get("/equipment-interests")
def get_equipment_interests(_: Operator, repo: Repo) -> Any:
    """Evidenced equipment interest per line, institution and destination, for the CRM cards.

    The same derivation as the Marketing audience (`compose`), unfiltered and without the
    sending-eligibility summary: this is who has shown interest in what, with source and date.
    """
    taxonomy = load_taxonomy()
    return interest_index(taxonomy, compose(taxonomy, repo.marketing_audience_inputs()))


@workspace_router.get("/marketing")
def get_marketing(operator: Operator, repo: Repo, request: Request) -> Any:
    body = repo.marketing()
    taxonomy = load_taxonomy()
    for c in body["campaigns"]:
        c["equipment_lines"] = campaign_lines(taxonomy, c)
        c.pop("audience_criteria", None)
    # Whether this API records drafts at all: the dashboard must never imply a save it cannot do.
    body["authoring"] = {
        "drafts_enabled": bool(getattr(request.app.state, "campaign_drafts_enabled", False)),
        "freeze_enabled": bool(getattr(request.app.state, "audience_freeze_enabled", False)),
        "recontact_review_enabled": bool(getattr(request.app.state, "recontact_review_enabled", False)),
        "planning_enabled": bool(getattr(request.app.state, "campaign_planning_enabled", False)),
        "unsubscribe_apply_enabled": bool(getattr(request.app.state, "unsubscribe_apply_enabled", False)),
        "campaign_blocks_enabled": bool(getattr(request.app.state, "campaign_blocks_enabled", False)),
    }
    body["time_zone"] = PLANNING_TIME_ZONE
    # V1-lane declarations: campaigns being sent through the old systemd lane that are not yet
    # visible in the V2 database. Loaded from a checked-in JSON file; a bad file returns [].
    # The email preview comes from outside the repository (it carries contact addresses).
    content_dir = getattr(request.app.state, "v1_lane_content_dir", None)
    # Once its sends are imported (v1_sent_campaign_import) the CRM row is the campaign: the
    # plan is kept for the calendar (waves not sent yet) and marked, so the list shows it once.
    imported = {c.get("v1_campaign_key") for c in body["campaigns"]} - {None}
    body["v1_lane_campaigns"] = [
        {**v1_campaign_as_dict(c, load_v1_lane_html(c.key, content_dir)), "imported": c.key in imported}
        for c in load_v1_lane_campaigns()
    ]
    body["test_send"] = {
        "enabled": bool(getattr(request.app.state, "campaign_test_send_enabled", False)),
        "per_hour": TEST_SENDS_PER_HOUR,
        "per_day": TEST_SENDS_PER_DAY,
        "sender": SENDER_ADDRESS,
    }
    return _holds_for(operator, body)


def _holds_for(operator: Any, body: dict[str, Any]) -> dict[str, Any]:
    """A viewer sees whether a campaign is held and since when, never the reason or who decided."""
    if getattr(operator, "role", None) != "viewer":
        return body
    if "holds" in body:
        body["holds"] = redact_for_viewer(body["holds"])
    for c in body.get("campaigns", []):
        if "hold" in c:
            c["hold"] = redact_for_viewer(c["hold"])
    if "hold" in body:
        body["hold"] = redact_for_viewer(body["hold"])
    return body


@workspace_router.get("/marketing/campaign-blocks")
def get_marketing_campaign_blocks(operator: Operator, repo: Repo, request: Request) -> Any:
    """Every active campaign safety block, the latest lifted ones and each target's version.

    A read. Always mounted with the workspace: a block is shown whether or not the block
    commands are enabled. A viewer sees status only (no reason, no operator).
    """
    body = repo.campaign_blocks()
    body["commands_enabled"] = bool(getattr(request.app.state, "campaign_blocks_enabled", False))
    body["may_decide"] = getattr(operator, "role", None) == "admin" and body["commands_enabled"]
    if getattr(operator, "role", None) == "viewer":
        return redact_for_viewer(body)
    return body


@workspace_router.get("/marketing/analytics")
def get_marketing_analytics(_: Operator, repo: Repo) -> Any:
    """Reach, still-writable, bounces, bajas, open cases, per-campaign results and CRM coverage.

    A read, computed from the CRM's facts (`marketing_analytics`); `null` means not measurable.
    """
    return repo.marketing_analytics()


@workspace_router.get("/marketing/analytics/people")
def get_marketing_analytics_people(
    _: Operator,
    repo: Repo,
    metric: str | None = None,
    campaign_id: UUID | None = None,
    segment: str | None = None,
    column: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """The people one figure counts: `?metric=` (with `campaign_id`, or an overview metric), or a
    coverage `?segment=` (optionally `column=`). The route class masks addresses for a viewer."""
    try:
        return repo.marketing_people(metric=metric, campaign_id=None if campaign_id is None else str(campaign_id),
                                     segment=segment, column=column, limit=limit, offset=offset)
    except UnknownFigure as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@workspace_router.get("/marketing/taxonomy")
def get_marketing_taxonomy(_: Operator) -> Any:
    """Families, brands, models and verified product images, from the public website catalogue."""
    return load_taxonomy().public()


@workspace_router.get("/marketing/campaigns/{campaign_id}")
def get_marketing_campaign(campaign_id: UUID, operator: Operator, repo: Repo) -> Any:
    row = repo.campaign(str(campaign_id))
    if row is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    return _holds_for(operator, row)


@workspace_router.get("/marketing/campaigns/{campaign_id}/archive")
def get_marketing_campaign_archive(campaign_id: UUID, _: Operator, repo: Repo) -> Any:
    """The frozen content a campaign was (or will be) sent with, and its real send record.

    `html` is present only when the content is frozen and its fingerprint recomputes; an
    imported campaign whose HTML was never archived says so (`html_state`). A read.
    """
    body = repo.campaign_archive(str(campaign_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    return body


@workspace_router.get("/marketing/campaigns/{campaign_id}/freeze-preview")
def get_marketing_freeze_preview(
    campaign_id: UUID,
    _: Operator,
    repo: Repo,
    request: Request,
    family_id: str | None = None,
    brand_id: str | None = None,
    model_id: str | None = None,
    organization_id: UUID | None = None,
    basis: Annotated[list[str] | None, Query()] = None,
    recorded: Literal["crm", "evidence"] | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    scope: Literal["persons", "institutions", "both"] = "both",
) -> Any:
    """The snapshot `freeze-campaign-audience` would write now, and what would stop it.

    A read: nothing is written. `preview_sha256` is what the confirmation screen sends back.
    """
    try:
        criteria = FreezeCriteria(
            family_id=family_id, brand_id=brand_id, model_id=model_id, organization_id=organization_id,
            bases=tuple(basis or ()), recorded=recorded, q=q, scope=scope,
        )
        preview = repo.freeze_preview(
            str(campaign_id), criteria,
            recontact_review=bool(getattr(request.app.state, "recontact_review_enabled", False)),
        )
    except (ValidationError, CommandRefused) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if preview is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    preview.pop("body_text", None)
    preview["freeze_enabled"] = bool(getattr(request.app.state, "audience_freeze_enabled", False))
    return preview


@workspace_router.get("/marketing/campaigns/{campaign_id}/recipients")
def get_marketing_frozen_recipients(campaign_id: UUID, _: Operator, repo: Repo) -> Any:
    """A frozen campaign's recipient snapshot. Empty for a draft."""
    body = repo.frozen_recipients(str(campaign_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    body["send_blockers"] = list(SEND_BLOCKERS)
    return body


@workspace_router.get("/marketing/campaigns/{campaign_id}/history/recipients")
def get_marketing_campaign_recipients(
    campaign_id: UUID,
    operator: Operator,
    repo: Repo,
    total: Literal[tuple(TOTALS)] = "audience",  # type: ignore[valid-type]
    reason: Annotated[str | None, Query(max_length=40)] = None,
    identity: Literal["crm_person", "historical_address"] | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=RecipientQuery.MAX_PAGE_SIZE)] = 50,
) -> Any:
    """One page of a campaign's recorded recipients, filtered by the same predicate as the total
    the operator clicked. A `viewer` searches names only; the address is masked in the answer."""
    try:
        query = RecipientQuery(total=total, reason=reason, identity=identity, q=q, page=page, page_size=page_size)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    body = repo.campaign_recipients(str(campaign_id), query, operator.role)
    if body is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    return body


@workspace_router.get("/marketing/campaigns/{campaign_id}/history/replies")
def get_marketing_campaign_replies(campaign_id: UUID, operator: Operator, repo: Repo) -> Any:
    """Replies and «BAJA» stored with lineage to this campaign. Gmail is never called."""
    body = repo.campaign_replies(str(campaign_id), operator.role)
    if body is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    return body


@workspace_router.get("/marketing/campaigns/{campaign_id}/history/audit")
def get_marketing_campaign_audit(campaign_id: UUID, _: Operator, repo: Repo) -> Any:
    """The campaign's recorded history: its row, its origin, its domain events. A read."""
    body = repo.campaign_audit(str(campaign_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    return body


#: Shown wherever the suppression state is: nothing reads a mailbox on its own.
GMAIL_SYNC_NOTICE = (
    "Las respuestas de Gmail no se sincronizan automáticamente. Una BAJA queda registrada sólo cuando "
    "se aplica un lote de respuestas ya descargadas con un script del servidor: no hay botón para eso aquí."
)


@workspace_router.get("/marketing/suppressions")
def get_marketing_suppressions(_: Operator, repo: Repo, request: Request) -> Any:
    """Marketing unsubscribes (W10), read-only: which addresses, since when, and what they refuse.

    Addresses are masked for a viewer by the route class. No message body, subject or header is
    ever part of this answer; the evidence stays in the database.
    """
    body = repo.suppressions()
    body["gmail_sync"] = {"automatic": False, "label": GMAIL_SYNC_NOTICE}
    body["grammar"] = {
        "version": BAJA_GRAMMAR_VERSION,
        "accepted": list(ACCEPTED_FORMS),
        "rule": "Sólo una respuesta cuyo texto propio es exactamente «BAJA» o «REMOVER» (la palabra de las "
                "plantillas V1), en mayúsculas o minúsculas, con o sin un punto final. Cualquier otra "
                "redacción queda para revisión humana.",
    }
    body["sender_policy"] = {
        "version": UNSUBSCRIBE_POLICY_VERSION,
        "rule": "Se suprime una dirección ya conocida, o la del destinatario exacto del correo enviado al que "
                "responde. Si no se puede comprobar, la BAJA queda en revisión y esa dirección exacta queda "
                "bloqueada para marketing hasta confirmarla. Nunca se crea una persona, contacto ni institución.",
    }
    for p in body.get("pending_reviews", []):
        p["review_reason_label"] = REVIEW_REASON_LABEL.get(p.get("review_reason") or "", p.get("review_reason"))
    body["apply_enabled"] = bool(getattr(request.app.state, "unsubscribe_apply_enabled", False))
    body["permanent"] = True
    body["resubscribe_supported"] = False
    return body


@workspace_router.get("/marketing/audience")
def get_marketing_audience(
    operator: Operator,
    repo: Repo,
    family_id: str | None = None,
    brand_id: str | None = None,
    model_id: str | None = None,
    organization_id: UUID | None = None,
    basis: Annotated[list[str] | None, Query()] = None,
    recorded: Literal["crm", "evidence"] | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> Any:
    """People and institutions with evidenced equipment interest, and who may be written to.

    Relevance and sending eligibility are separate fields; no score is computed.
    """
    taxonomy = load_taxonomy()
    for value, known, name in (
        (family_id, taxonomy.families, "family_id"),
        (brand_id, taxonomy.brands, "brand_id"),
        (model_id, taxonomy.models, "model_id"),
    ):
        if value is not None and value not in known:
            raise HTTPException(status_code=422, detail=f"unknown {name}")
    bases = tuple(basis or ())
    if any(b not in BASES for b in bases):
        raise HTTPException(status_code=422, detail="unknown basis")
    composed = compose(taxonomy, repo.marketing_audience_inputs())
    flt = AudienceFilter(
        family_id=family_id, brand_id=brand_id, model_id=model_id,
        organization_id=str(organization_id) if organization_id else None,
        bases=bases, recorded=recorded, q=q,
    )
    holds = repo.campaign_blocks()
    # A preview is not a freeze, but an operator building an audience while every campaign is
    # blocked must see it here, not first at the freeze.
    campaign_holds = {"all_campaigns": holds["all_campaigns"], "legacy": holds["legacy"], "effect": holds["effect"]}
    if getattr(operator, "role", None) == "viewer":
        campaign_holds = redact_for_viewer(campaign_holds)
    return {**apply_filter(composed, flt), "coverage": composed["coverage"], "campaign_holds": campaign_holds}


@workspace_router.get("/drive")
def get_drive(_: Operator, repo: Repo) -> Any:
    return repo.drive_archive()


@workspace_router.get("/review")
def get_review(_: Operator, repo: Repo) -> Any:
    return repo.review()


@workspace_router.get("/mail-sync")
def get_mail_sync(_: Operator, repo: Repo) -> Any:
    """Gmail capture status for the dashboard banner (Phase 4a): state and last sync time."""
    return repo.mail_sync()


@workspace_router.get("/operations")
def get_operations(request: Request, _: Operator, repo: Repo) -> Any:
    """«Operaciones»: every part of the system with its state (spec 2026-10-13). Read-only, any
    signed-in operator; no body, address or subject. The rules timer and the deployed commit are
    this process's, so the route adds those two cards."""
    from origenlab_api.services.health_service import deployed_version
    from origenlab_api.v2.operations import reglas_card, respaldo_card, rules_state_for_card, version_card

    body = dict(repo.operations())  # a copy: the repository shares its result for 30 s
    auto = getattr(request.app.state, "auto_mail_rules", None)
    try:
        state = auto.state() if auto is not None else None
    except Exception:  # noqa: BLE001 - one card says it, the page stays up
        state = {"unreadable": True}
    body["cards"] = [*body["cards"],
                     reglas_card(rules_state_for_card(state, body["generated_at"]), int(body.get("actions_today") or 0)),
                     version_card(deployed_version()), respaldo_card()]
    return body


@workspace_router.get("/mail-quote-numbers")
def get_mail_quote_numbers(_: Operator, repo: Repo) -> Any:
    """Quote numbers already seen in the captured Gmail (the 500 newest), for the Resumen's
    quote-number box: number, first sighting, message count. No client data."""
    return repo.mail_quote_numbers()


#: The process's exchange-rate cache, unless the app supplies its own (`build_fx_rates`; tests).
_FX_RATES = FxRates()


def build_fx_rates(connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> FxRates:
    """The exchange-rate cache over the V2 database: when every source fails and no figures are in
    memory it answers the newest stored `catalog.fx_rate` observation (the pricing lookup's
    precedence rule), and each mindicador.cl fetch writes its USD and EUR through to that table.

    Both use the API's own pooled connection as `origenlab_api`: a read-only transaction for the
    lookup, and `FxCacheWriter`'s own short write transaction (`on conflict do nothing`).
    """
    from origenlab_api.v2.catalog.fx import FxCacheWriter
    from origenlab_api.v2.catalog.reads import V2CatalogReads

    reads = V2CatalogReads(connect, dsn, statement_timeout_ms=statement_timeout_ms)
    return FxRates(store_reader=reads.stored_fx,
                   store_writer=FxCacheWriter(connect, dsn, statement_timeout_ms))


@workspace_router.get("/fx")
def get_fx(_: Operator, request: Request) -> Any:
    """Dólar observado, euro and UF in pesos, from the Banco Central (display only, cached 1 h)."""
    rates: FxRates = getattr(request.app.state, "fx_rates", None) or _FX_RATES
    try:
        return rates.current()
    except FxUnavailable:
        raise HTTPException(status_code=503, detail="tipo de cambio no disponible") from None
