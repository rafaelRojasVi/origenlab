"""The four evidence-review commands — their requests, their refusals, their digest.

This module is the *shape* of the V2 human-review command boundary: what an operator must
say out loud before a durable row moves, and which of those requests are refused before a
database connection is ever opened. The transactional half lives in
`command_repository.py`; keeping the two apart means the rules below can be proven without a
database, and are.

**Four commands, and deliberately only four.** `docs/WORKFLOWS.md` §W1 gives evidence review
three outcomes — promote an assertion, quarantine the record, or leave it alone — and the
first of those is the only one with any reach into `crm.*`. These four are the smallest set
that lets an operator finish a Gmail record honestly:

| Command | What durable fact it records |
|---|---|
| `keep_evidence_pending` | a named operator read this record and judged the evidence insufficient |
| `confirm_organization` | an asserted name *is* this one existing organization |
| `create_organization` | an asserted name is an organization nobody has recorded yet |
| `attach_contact_address` | an asserted address is a mailbox this organization operates |

**What is absent is the point.** There is no merge, no person creation, no affiliation, no
prospect, no marketing permission, no campaign, no quote and no send. There is no command
that takes a *domain* as its subject, because `docs/DOMAIN.md` §2.2 makes a domain a routing
hint and never an identity — an organization may not be born from one. There is no
similarity threshold anywhere in this file: `confirm_organization` accepts an organization
only when the asserted name folds to exactly its name, and every other candidate the operator
might have meant is their problem to look at, not this module's to guess.

**Every command carries four things by construction** — the evidence record it is about, the
operator who decided (from the verified identity, never the body), a non-blank reason, and an
idempotency key. A request missing any of them is refused before it reaches Postgres.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The closed command vocabulary, in the order an operator meets them.
KEEP_EVIDENCE_PENDING = "keep_evidence_pending"
CONFIRM_ORGANIZATION = "confirm_organization"
CREATE_ORGANIZATION = "create_organization"
ATTACH_CONTACT_ADDRESS = "attach_contact_address"

COMMAND_NAMES: tuple[str, ...] = (
    KEEP_EVIDENCE_PENDING,
    CONFIRM_ORGANIZATION,
    CREATE_ORGANIZATION,
    ATTACH_CONTACT_ADDRESS,
)

#: Roles that may record a durable commercial decision. `viewer` may read the queue and may
#: not decide it — `docs/OPERATIONS.md` §2. This is layer C of the three-layer authorization
#: in `docs/ARCHITECTURE.md` §6.1; the grants and RLS underneath do not know about roles.
WRITER_ROLES: tuple[str, ...] = ("sales", "admin")

#: `crm.organization.kind` is a lower_snake_case token whose closed list is still an open
#: domain decision (see the CHECK in the Slice 0 identity migration). Until that list exists
#: this boundary accepts the two values the evidence can actually justify: `unknown`, which
#: is what an organization named in an email really is, and `institution`, which an operator
#: may assert when the name itself says so. Guessing a richer kind from a name would be the
#: inference this whole path exists to avoid.
ORGANIZATION_KINDS: tuple[str, ...] = ("unknown", "institution")

#: The only `usage` an address can take when it is attached to an organization and no person
#: exists. `docs/DOMAIN.md` §2.5: `shared_mailbox ⇒ person NULL`, `work ⇒ person NOT NULL`.
#: Since no command here creates a person, `work` is unreachable and saying so in the type is
#: more honest than accepting it and failing on a CHECK constraint.
ATTACHABLE_USAGE: tuple[str, ...] = ("shared_mailbox",)

_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CommandRefused(Exception):
    """A command that will not run, with the HTTP status the boundary should answer.

    Refusals carry a machine-readable `code` as well as a sentence, because the dashboard
    has to tell "you may not do this" apart from "somebody else already did this" without
    parsing prose.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise CommandRefused(422, "malformed_identifier", f"{what} is not a UUID") from None


class _CommandBody(BaseModel):
    """What every command says, whatever it decides.

    `note` is required and non-blank on purpose. A durable decision whose reason is "" is a
    decision nobody can audit later, and this queue exists precisely because the machine
    could not justify itself.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_record_id: str
    note: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("note")
    @classmethod
    def _note_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note must not be blank")
        return value


class KeepEvidencePendingBody(_CommandBody):
    """Leave the record exactly where it is, and say who looked and why.

    This writes no `crm.*` row and does not move `review_status`, which stays `'pending'`
    because the record genuinely still is. The durable part is the event.
    """


class ConfirmOrganizationBody(_CommandBody):
    """This asserted name is *that* organization — the one whose id is in this request.

    `organization_version` is the version the operator was shown. If the row has moved since,
    the command is refused rather than applied to a record nobody reviewed: the conflict is
    the answer, not an obstacle to route around.
    """

    assertion_id: str
    organization_id: str
    organization_version: Annotated[int, Field(ge=1)]


class CreateOrganizationBody(_CommandBody):
    """This asserted name is an organization nobody has recorded yet.

    The name is **not** in this request. It is read from the assertion, so an organization
    can only ever be created with a name the evidence itself contains — which is what makes
    "no organization from a domain hint" a structural property rather than a promise.

    `name_display` is the one concession: an operator may supply the capitalisation a
    lower-cased `value_norm` lost. It is accepted only when it folds to exactly the asserted
    value, so it can restore letter case and nothing else.
    """

    assertion_id: str
    kind: Literal["unknown", "institution"] = "unknown"
    name_display: Annotated[str, Field(min_length=1, max_length=400)] | None = None


class AttachContactAddressBody(_CommandBody):
    """This asserted address is a mailbox that organization operates.

    `usage` must be spelled out. The value is forced — no person exists, so `shared_mailbox`
    is the only shape the schema allows — but making the operator write it means they are
    asserting "this is a desk", which is a claim about the world and not a default the
    boundary picked for them. The dashboard warns when an address does not look like one; it
    does not decide, because a local part is a spelling, not evidence.
    """

    assertion_id: str
    organization_id: str
    usage: Literal["shared_mailbox"]


BODY_BY_COMMAND: dict[str, type[_CommandBody]] = {
    KEEP_EVIDENCE_PENDING: KeepEvidencePendingBody,
    CONFIRM_ORGANIZATION: ConfirmOrganizationBody,
    CREATE_ORGANIZATION: CreateOrganizationBody,
    ATTACH_CONTACT_ADDRESS: AttachContactAddressBody,
}


def require_writer(role: str) -> None:
    """Refuse a reader that tried to decide.

    403, not 401: the operator is who they say they are, and that is exactly why the answer
    is "not you" rather than "who are you".
    """
    if role not in WRITER_ROLES:
        raise CommandRefused(
            403,
            "role_may_not_decide",
            f"role '{role}' may read the review queue but may not record a decision; "
            f"one of {', '.join(WRITER_ROLES)} is required",
        )


def require_idempotency_key(raw: str | None) -> str:
    """Every command needs a key, and the key has to fit the column that stores it.

    `platform.command_receipt.idempotency_key` is 1–200 characters. Refusing a longer one
    here turns a constraint violation into a 400 that says what to fix.
    """
    key = (raw or "").strip()
    if not key:
        raise CommandRefused(
            400, "idempotency_key_required", "the Idempotency-Key header is required"
        )
    if len(key) > 200:
        raise CommandRefused(
            400, "idempotency_key_too_long", "the Idempotency-Key header must be at most 200 characters"
        )
    return key


def request_digest(command_name: str, body: _CommandBody) -> str:
    """A stable SHA-256 over the command and its request.

    Canonical JSON — sorted keys, no incidental whitespace — so the same request digests the
    same way twice and a *different* request under a reused key is detectable. The digest
    never includes the operator: the receipt is already keyed by operator, and folding it in
    again would only make the value harder to reason about.
    """
    payload = {"command": command_name, "body": body.model_dump(mode="json")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_organization_name(raw: str) -> str:
    """Fold a name the way the evidence folded it: trimmed, whitespace-collapsed, lower-cased.

    This is a *comparison* key, not a similarity score. Two names either fold to the same
    string or they do not; nothing here measures how close they came.
    """
    return re.sub(r"\s+", " ", raw.strip()).lower()


def normalize_email(raw: str) -> str:
    """The `crm.contact_point` email normal form: lower-cased, no tag stripping.

    Tags are not stripped on purpose (`docs/DOMAIN.md` §2.5): `ventas+equipos@` is a
    different reachable channel from `ventas@`, and folding them would merge two mailboxes
    on a guess.
    """
    return raw.strip().lower()


def validate_email_shape(value_norm: str) -> str:
    if not _EMAIL_SHAPE.match(value_norm):
        raise CommandRefused(
            422,
            "not_an_email_address",
            "the assertion's value is not an email address, so it cannot become a contact point",
        )
    return value_norm


def validated(command_name: str, body: _CommandBody) -> dict[str, Any]:
    """Everything that can be checked without the database, checked in one place.

    What is deliberately *not* here: whether the assertion exists, whether it belongs to the
    record, whether it is still unresolved, whether the organization exists. Those are facts
    about rows, they can change between the check and the write, and checking them anywhere
    but inside the command's own transaction would be a race dressed up as validation.
    """
    fields: dict[str, Any] = {
        "source_record_id": _uuid(body.source_record_id, "source_record_id"),
        "note": body.note.strip(),
    }
    if isinstance(body, KeepEvidencePendingBody):
        return fields

    fields["assertion_id"] = _uuid(body.assertion_id, "assertion_id")  # type: ignore[union-attr]

    if isinstance(body, ConfirmOrganizationBody):
        fields["organization_id"] = _uuid(body.organization_id, "organization_id")
        fields["organization_version"] = int(body.organization_version)
    elif isinstance(body, CreateOrganizationBody):
        fields["kind"] = body.kind
        fields["name_display"] = body.name_display.strip() if body.name_display else None
    elif isinstance(body, AttachContactAddressBody):
        fields["organization_id"] = _uuid(body.organization_id, "organization_id")
        fields["usage"] = body.usage
    return fields
