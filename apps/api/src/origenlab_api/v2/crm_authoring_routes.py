"""CRM freeform authoring command boundary — `POST /v2/commands/<crm-command>`.

Mounted only when `ORIGENLAB_V2_CRM_AUTHORING_ENABLED=true` **and** a V2 DSN is set
(default off).  All 28 routes require:
- An active operator with role `sales` or `admin` (viewer → 403 `role_may_not_decide`).
- An `Idempotency-Key` header (absent → 400).
- A JSON body whose shape is enforced by Pydantic with `extra="forbid"`.
- A non-blank `note` ≤ 2000 chars (except `add-note`/`revise-note` which use `body` ≤ 8000).

Admin-only routes (sales → 403 `role_may_not_archive`):
  archive-person, restore-person, merge-people,
  archive-organization, restore-organization.

archive-note: the note's author **or** an admin.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import (
    CommandRefused,
    request_digest,
    require_idempotency_key,
)
from origenlab_api.v2.identity import OperatorIdentity

crm_authoring_router = APIRouter(prefix="/v2/commands", tags=["v2-crm-authoring"])

_NOTE = Field(min_length=1, max_length=2000)
_NOTE_OPT = Field(default=None, min_length=1, max_length=2000)
_BODY = Field(min_length=1, max_length=8000)
_NAME = Field(default=None, min_length=1, max_length=500)
_UUID = Field(min_length=36, max_length=36)


def get_crm_authoring_repository(request: Request) -> V2CrmAuthoringRepository:
    repo = getattr(request.app.state, "crm_authoring_repository", None)
    if repo is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="CRM authoring is not configured")
    return repo


CrmAuthoringRepo = Annotated[V2CrmAuthoringRepository, Depends(get_crm_authoring_repository)]


def _archive_admin(operator: Deciding) -> OperatorIdentity:
    """An admin operator for archive/restore/merge commands."""
    if operator.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"code": "role_may_not_archive", "message": "only an admin may archive or merge"},
        )
    return operator


ArchiveAdmin = Annotated[OperatorIdentity, Depends(_archive_admin)]


def _run(
    *,
    command_name: str,
    body: Any,
    fields: dict[str, Any],
    repo: V2CrmAuthoringRepository,
    operator: OperatorIdentity,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Same six-step pattern as command_routes._run, with pre-extracted fields."""
    try:
        key = require_idempotency_key(idempotency_key)
        digest = request_digest(command_name, body)
        return repo.execute(
            command_name=command_name,
            operator=operator,
            fields=fields,
            idempotency_key=key,
            digest=digest,
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


# ──────────────────────────────────────────────────────── Pydantic body models ──

class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: Annotated[str, _NOTE]

    @field_validator("note")
    @classmethod
    def _note_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("note must not be blank")
        return v


class CreatePersonBody(_Base):
    display_name: Annotated[str, Field(min_length=1, max_length=500)]
    given_name: Annotated[str | None, _NAME] = None
    family_name: Annotated[str | None, _NAME] = None
    title: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    email: Annotated[str | None, Field(default=None, min_length=1, max_length=500)] = None
    phone: Annotated[str | None, Field(default=None, min_length=1, max_length=30)] = None
    organization_id: Annotated[str | None, Field(default=None)] = None
    role_title: Annotated[str | None, _NAME] = None


class UpdatePersonBody(_Base):
    person_id: str
    expected_version: int
    display_name: Annotated[str | None, Field(default=None, min_length=1, max_length=500)] = None
    given_name: Annotated[str | None, _NAME] = None
    family_name: Annotated[str | None, _NAME] = None
    title: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None


class ArchivePersonBody(_Base):
    person_id: str
    expected_version: int


class RestorePersonBody(_Base):
    person_id: str
    expected_version: int


class MergePeopleBody(_Base):
    loser_person_id: str
    winner_person_id: str
    expected_loser_version: int
    expected_winner_version: int
    expected_preview_sha256: Annotated[str, Field(min_length=64, max_length=64)]
    confirmed: Annotated[bool, Field()] = False

    @field_validator("confirmed")
    @classmethod
    def _must_confirm(cls, v: bool) -> bool:
        if not v:
            raise ValueError("confirmed must be true")
        return v


class AddContactPointBody(_Base):
    person_id: Annotated[str | None, Field(default=None)] = None
    organization_id: Annotated[str | None, Field(default=None)] = None
    expected_version: int
    kind: Annotated[str, Field(pattern="^(email|phone)$")]
    value: Annotated[str, Field(min_length=1, max_length=500)]
    usage: Annotated[str, Field(min_length=1, max_length=50)]


class UpdateContactPointBody(_Base):
    contact_point_id: str
    expected_version: int
    usage: Annotated[str | None, Field(default=None, min_length=1, max_length=50)] = None
    value_display: Annotated[str | None, Field(default=None, min_length=1, max_length=500)] = None


class DeactivateContactPointBody(_Base):
    contact_point_id: str
    expected_version: int


class LinkPersonOrganizationBody(_Base):
    person_id: str
    expected_version: int
    organization_id: str
    role_title: Annotated[str | None, _NAME] = None
    unit_label: Annotated[str | None, _NAME] = None
    valid_from: Annotated[str | None, Field(default=None)] = None


class UnlinkPersonOrganizationBody(_Base):
    person_id: str
    expected_version: int
    affiliation_id: str
    valid_to: Annotated[str | None, Field(default=None)] = None


class RegisterOrganizationBody(_Base):
    name: Annotated[str, Field(min_length=1, max_length=500)]
    legal_name: Annotated[str | None, Field(default=None, min_length=1, max_length=500)] = None
    kind: Annotated[str, Field(min_length=1, max_length=40)]
    classification: Annotated[str | None, Field(default=None)] = None
    domain: Annotated[str | None, Field(default=None, min_length=1, max_length=253)] = None
    product_lines: Annotated[list[str] | None, Field(default=None)] = None


class UpdateOrganizationBody(_Base):
    organization_id: str
    expected_version: int
    name: Annotated[str | None, Field(default=None, min_length=1, max_length=500)] = None
    legal_name: Annotated[str | None, Field(default=None, min_length=1, max_length=500)] = None
    kind: Annotated[str | None, Field(default=None, min_length=1, max_length=40)] = None


class ArchiveOrganizationBody(_Base):
    organization_id: str
    expected_version: int


class RestoreOrganizationBody(_Base):
    organization_id: str
    expected_version: int


class AddOrganizationIdentifierBody(_Base):
    organization_id: str
    expected_version: int
    scheme: Annotated[str, Field(min_length=1, max_length=50)]
    value: Annotated[str, Field(min_length=1, max_length=500)]


class RemoveOrganizationIdentifierBody(_Base):
    organization_id: str
    expected_version: int
    identifier_id: str


class AddOrganizationDomainBody(_Base):
    organization_id: str
    expected_version: int
    domain: Annotated[str, Field(min_length=1, max_length=253)]
    scope: Annotated[str, Field(default="shared", pattern="^(exclusive|shared)$")] = "shared"


class RemoveOrganizationDomainBody(_Base):
    organization_id: str
    expected_version: int
    domain_id: str


class RestoreOrganizationDomainBody(_Base):
    """Bring a soft-removed domain back on the organization it already belongs to."""

    organization_id: str
    expected_version: int
    domain_id: str
    scope: Annotated[str | None, Field(default=None, pattern="^(exclusive|shared)$")] = None


class AddOrganizationClassificationBody(_Base):
    organization_id: str
    expected_version: int
    role: Annotated[str, Field(min_length=1, max_length=50)]
    valid_from: Annotated[str | None, Field(default=None)] = None


class RemoveOrganizationClassificationBody(_Base):
    organization_id: str
    expected_version: int
    relationship_id: str
    valid_to: Annotated[str | None, Field(default=None)] = None


class LinkOrganizationProductLineBody(_Base):
    organization_id: str
    expected_version: int
    line_id: Annotated[str, Field(min_length=1, max_length=50)]


class UnlinkOrganizationProductLineBody(_Base):
    organization_id: str
    expected_version: int
    link_id: str


class _NewOrgBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=500)]
    kind: Annotated[str, Field(min_length=1, max_length=40)]


class ConfirmSupplierCandidateBody(_Base):
    assertion_id: str
    organization_id: Annotated[str | None, Field(default=None)] = None
    new_organization: Annotated[_NewOrgBody | None, Field(default=None)] = None
    classification: Annotated[str, Field(pattern="^(supplier|manufacturer)$")]
    product_lines: Annotated[list[str] | None, Field(default=None)] = None


class RejectSupplierCandidateBody(_Base):
    assertion_id: str


class AddNoteBody(BaseModel):
    """add-note: uses 'body' (≤ 8000) instead of 'note'."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject_kind: Annotated[str, Field(pattern="^(person|organization|opportunity)$")]
    subject_id: str
    body: Annotated[str, _BODY]

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("body must not be blank")
        return v


class ReviseNoteBody(BaseModel):
    """revise-note: uses 'body' (≤ 8000) instead of 'note'."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note_id: str
    expected_version: int
    body: Annotated[str, _BODY]

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("body must not be blank")
        return v


class ArchiveNoteBody(_Base):
    note_id: str
    expected_version: int


# ──────────────────────────────────────────────────────────────────── routes ──

@crm_authoring_router.post("/create-person")
def create_person(
    body: CreatePersonBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="create-person", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/update-person")
def update_person(
    body: UpdatePersonBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="update-person", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/archive-person")
def archive_person(
    body: ArchivePersonBody,
    operator: ArchiveAdmin,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="archive-person", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/restore-person")
def restore_person(
    body: RestorePersonBody,
    operator: ArchiveAdmin,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="restore-person", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/merge-people")
def merge_people(
    body: MergePeopleBody,
    operator: ArchiveAdmin,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="merge-people", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/add-contact-point")
def add_contact_point(
    body: AddContactPointBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="add-contact-point", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/update-contact-point")
def update_contact_point(
    body: UpdateContactPointBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="update-contact-point", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/deactivate-contact-point")
def deactivate_contact_point(
    body: DeactivateContactPointBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="deactivate-contact-point", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/link-person-organization")
def link_person_organization(
    body: LinkPersonOrganizationBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="link-person-organization", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/unlink-person-organization")
def unlink_person_organization(
    body: UnlinkPersonOrganizationBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="unlink-person-organization", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/register-organization")
def register_organization(
    body: RegisterOrganizationBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="register-organization", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/update-organization")
def update_organization(
    body: UpdateOrganizationBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="update-organization", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/archive-organization")
def archive_organization(
    body: ArchiveOrganizationBody,
    operator: ArchiveAdmin,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="archive-organization", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/restore-organization")
def restore_organization(
    body: RestoreOrganizationBody,
    operator: ArchiveAdmin,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="restore-organization", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/add-organization-identifier")
def add_organization_identifier(
    body: AddOrganizationIdentifierBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="add-organization-identifier", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/remove-organization-identifier")
def remove_organization_identifier(
    body: RemoveOrganizationIdentifierBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="remove-organization-identifier", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/add-organization-domain")
def add_organization_domain(
    body: AddOrganizationDomainBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="add-organization-domain", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/remove-organization-domain")
def remove_organization_domain(
    body: RemoveOrganizationDomainBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="remove-organization-domain", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/restore-organization-domain")
def restore_organization_domain(
    body: RestoreOrganizationDomainBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="restore-organization-domain", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/add-organization-classification")
def add_organization_classification(
    body: AddOrganizationClassificationBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="add-organization-classification", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/remove-organization-classification")
def remove_organization_classification(
    body: RemoveOrganizationClassificationBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="remove-organization-classification", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/link-organization-product-line")
def link_organization_product_line(
    body: LinkOrganizationProductLineBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="link-organization-product-line", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/unlink-organization-product-line")
def unlink_organization_product_line(
    body: UnlinkOrganizationProductLineBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="unlink-organization-product-line", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/confirm-supplier-candidate")
def confirm_supplier_candidate(
    body: ConfirmSupplierCandidateBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="confirm-supplier-candidate", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/reject-supplier-candidate")
def reject_supplier_candidate(
    body: RejectSupplierCandidateBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="reject-supplier-candidate", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/add-note")
def add_note(
    body: AddNoteBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="add-note", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/revise-note")
def revise_note(
    body: ReviseNoteBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    return _run(command_name="revise-note", body=body,
                fields=body.model_dump(), repo=repo,
                operator=operator, idempotency_key=idempotency_key)


@crm_authoring_router.post("/archive-note")
def archive_note(
    request: Request,
    body: ArchiveNoteBody,
    operator: Deciding,
    repo: CrmAuthoringRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """archive-note: the note's author or an admin may archive."""
    # Author-or-admin check: we need to peek at who authored the note.
    # We do not query the DB here (that is the handler's job, inside a transaction);
    # instead we let the handler enforce the rule and raise CommandRefused 403 if needed,
    # or rely on the route setting the operator's id in fields.
    fields = body.model_dump()
    fields["_requesting_operator_id"] = operator.operator_id
    fields["_requesting_operator_role"] = operator.role
    return _run(command_name="archive-note", body=body,
                fields=fields, repo=repo,
                operator=operator, idempotency_key=idempotency_key)
