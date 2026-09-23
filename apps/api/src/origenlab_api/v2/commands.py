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
| `attribute_sender_organization` | *one* asserted name is the sender's institution, and this address is its mailbox |

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
CONFIRM_PERSON_FROM_EVIDENCE = "confirm_person_from_evidence"
ATTRIBUTE_SENDER_ORGANIZATION = "attribute_sender_organization"

COMMAND_NAMES: tuple[str, ...] = (
    KEEP_EVIDENCE_PENDING,
    CONFIRM_ORGANIZATION,
    CREATE_ORGANIZATION,
    ATTACH_CONTACT_ADDRESS,
    CONFIRM_PERSON_FROM_EVIDENCE,
    ATTRIBUTE_SENDER_ORGANIZATION,
)

#: How `attribute_sender_organization` gets its organization. Spelled out by the operator
#: rather than inferred from which fields happen to be present: a request that forgot
#: `organization_id` must be refused, never silently reinterpreted as "create a new one".
ORGANIZATION_TARGETS: tuple[str, ...] = ("existing", "new")

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

#: The `usage` values an address can take when it is attached to an organization and no
#: person exists. `docs/DOMAIN.md` §2.5: `work` needs a person and no command here creates
#: one, `personal` forbids the organization, and `unattributed` forbids it too — so those
#: three are unreachable, and saying so in the type is more honest than accepting them and
#: failing on a CHECK constraint.
#:
#: The two that remain say opposite things about who is on the other end, and the operator
#: chooses between them:
#:
#: * `shared_mailbox` — a desk two or more people read. `ventas@`, `secretaria@`,
#:   `produccion@`.
#: * `individual_owner_unknown` — one individual owns this address and nobody has recorded
#:   who. The institution is recorded because it is known; the person is absent because they
#:   are not.
#:
#: There is deliberately **no default**. A default here would be a claim about a real human
#: made by this module, and the one it used to make — `shared_mailbox` for everything — was
#: false for every named address that ever went through it.
ATTACHABLE_USAGE: tuple[str, ...] = ("shared_mailbox", "individual_owner_unknown")

#: Mailbox names that name a desk rather than a person.
#:
#: A closed, reviewed list rather than a pattern, for the reason
#: `apps/email-pipeline/.../v2_promote/rules.py` states at its own copy: a heuristic like
#: "contains no dot, so it is a role address" misclassifies `jrojas@` and `ventas.equipos@`
#: in opposite directions. This list is a **superset** of the dashboard's
#: (`apps/dashboard/src/lib/evidenceReview.ts`) on purpose. The dashboard asks for an
#: override wherever it does not recognise a local part; this boundary refuses wherever it
#: does not. A superset here means the boundary never refuses a request the surface said was
#: ready — the drift, when the two lists drift, costs an operator one unnecessary sentence
#: rather than a decision they cannot record.
ROLE_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        # commercial
        "ventas", "contacto", "compras", "info", "informacion", "comercial", "sales",
        "contact", "cotizaciones", "cotizacion", "presupuestos", "licitaciones",
        "adquisiciones", "abastecimiento", "proveedores", "postventa", "repuestos",
        # administrative
        "administracion", "admin", "gerencia", "secretaria", "recepcion", "oficina",
        "office", "contabilidad", "finanzas", "facturacion", "pagos", "tesoreria",
        "cobranzas", "cobranza", "direcciontecnica",
        # operational
        "logistica", "bodega", "despacho", "distribucion", "importaciones", "operaciones",
        "produccion", "mantencion", "mantenimiento", "servicio", "servicioalcliente",
        "atencionclientes", "soporte", "support", "calidad", "proyectos", "ingenieria",
        "laboratorio", "molecular",
        # generic
        "hello", "hola", "consultas", "reclamos", "webmaster", "postmaster", "noreply",
        "no-reply", "donotreply", "mailer", "mail", "correo",
    }
)


def address_names_a_desk(value_norm: str) -> bool:
    """Whether this address's local part is a recognised role mailbox.

    `contacto+equipos@` and `ventas.sur@` are the same desk under a suffix, so the base
    before an explicit separator decides — and only when the separator is explicit, because
    `pmorales@` must not read as a role. Anything unrecognised is **not** a desk: unknown
    stays unknown, which is the direction that refuses rather than the one that asserts.
    """
    local = value_norm.partition("@")[0]
    if local in ROLE_LOCAL_PARTS:
        return True
    base = re.split(r"[+._-]", local, maxsplit=1)[0]
    return base != local and base in ROLE_LOCAL_PARTS

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


def as_uuid(value: str, what: str) -> str:
    """A UUID, or a refusal that names the field rather than the exception.

    Public because the commercial-case commands parse the same way. One parser means one
    answer to "what does this boundary do with a malformed id", in every command.
    """
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise CommandRefused(422, "malformed_identifier", f"{what} is not a UUID") from None


_uuid = as_uuid


class DecisionBody(BaseModel):
    """What every durable decision says, whatever it is about.

    `note` is required and non-blank on purpose. A durable decision whose reason is "" is a
    decision nobody can audit later, and this boundary exists precisely because the machine
    could not justify itself.

    `extra="forbid"` is load-bearing rather than tidy. It is why no command in this package
    can be handed a price, an amount, a currency, a consent flag or a campaign id by a caller
    who guessed a field name: an unknown key is a 422, not a value that is quietly dropped
    and quietly assumed to have been honoured.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("note")
    @classmethod
    def _note_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note must not be blank")
        return value


class _CommandBody(DecisionBody):
    """What every evidence-review command says: the record it is about, and why."""

    source_record_id: str


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


#: Why the override is a *note* and not a boolean.
#:
#: `shared_mailbox` on `jperez@` is a claim this boundary cannot check and the evidence
#: argues against. An operator may still be right — they may know three people read that
#: address — and refusing them outright would make the queue unusable for a real case. So the
#: claim is allowed and made expensive: it costs a second sentence, written at the moment of
#: the decision, that says how they know. A checkbox would cost nothing and would therefore
#: be clicked; a sentence ends up in the event payload where a later reader can weigh it.
_OVERRIDE = Annotated[str, Field(min_length=1, max_length=2000)]


class AttachContactAddressBody(_CommandBody):
    """This asserted address is a channel that organization operates.

    `usage` must be spelled out, with **no default**, because the two reachable values make
    opposite claims about a human being: `shared_mailbox` says two or more people read this
    desk, `individual_owner_unknown` says one person owns it and nobody has recorded who.
    The boundary cannot tell which is true from an address, and a default would have it guess
    on every record — which is exactly how a named sender's mailbox became "a desk".

    `shared_mailbox_override_note` is required when `usage` is `shared_mailbox` and the
    address does not carry a recognised role local part. That check needs the address, which
    lives in the assertion, so it runs in the transaction that reads it.
    """

    assertion_id: str
    organization_id: str
    usage: Literal["shared_mailbox", "individual_owner_unknown"]
    shared_mailbox_override_note: _OVERRIDE | None = None


class ConfirmPersonFromEvidenceBody(_CommandBody):
    """A human confirms who owns one address asserted by this evidence."""

    assertion_id: str
    display_name: Annotated[str, Field(min_length=1, max_length=400)]
    given_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    family_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    organization_id: str | None = None



class AttributeSenderOrganizationBody(_CommandBody):
    """One institution named in this message is the sender's, and this address is its mailbox.

    **Why this exists as its own command rather than as two.** Attributing a sender took
    `create_organization` (or `confirm_organization`) and then `attach_contact_address`: two
    transactions, two receipts, two chances to stop halfway. The half-done state is not
    hypothetical — it is an organization nobody can reach and an address nobody has placed,
    and the operator who created it has no single thing to undo. Here the institution and the
    address move together or neither moves.

    **Why it names two assertions and not a record.** A message that names a supplier and an
    end customer asserts two institutions, and exactly one of them is the sender's. The
    operator says which — `organization_assertion_id` — and the other assertion is left
    `unresolved`, so the record stays in the queue with its remaining question intact. This
    is the whole point: choosing is not the same as discarding.

    **Why the address is an assertion too.** `address_assertion_id` is the sender address the
    message itself asserted, not a string in this request. An operator cannot attribute an
    address the evidence never contained, and cannot attribute the *other* party's address by
    pairing it with this record.

    `target` is explicit. `"existing"` needs the organization's id and the version the
    operator was shown; `"new"` needs neither and takes its name from the assertion, exactly
    as `create_organization` does. Sending the fields of one target under the other is
    refused rather than ignored, because a request that says two things is a request nobody
    read carefully.
    """

    organization_assertion_id: str
    address_assertion_id: str
    target: Literal["existing", "new"]
    organization_id: str | None = None
    organization_version: Annotated[int, Field(ge=1)] | None = None
    kind: Literal["unknown", "institution"] = "unknown"
    name_display: Annotated[str, Field(min_length=1, max_length=400)] | None = None
    usage: Literal["shared_mailbox", "individual_owner_unknown"]
    shared_mailbox_override_note: _OVERRIDE | None = None


BODY_BY_COMMAND: dict[str, type[_CommandBody]] = {
    KEEP_EVIDENCE_PENDING: KeepEvidencePendingBody,
    CONFIRM_ORGANIZATION: ConfirmOrganizationBody,
    CREATE_ORGANIZATION: CreateOrganizationBody,
    ATTACH_CONTACT_ADDRESS: AttachContactAddressBody,
    CONFIRM_PERSON_FROM_EVIDENCE: ConfirmPersonFromEvidenceBody,
    ATTRIBUTE_SENDER_ORGANIZATION: AttributeSenderOrganizationBody,
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


def request_digest(command_name: str, body: BaseModel) -> str:
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


def validated_usage(usage: str, override_note: str | None) -> dict[str, Any]:
    """The half of the usage rule that needs no database: the note belongs to one claim only.

    An override attached to `individual_owner_unknown` is refused rather than dropped. It
    means the operator wrote a justification for a claim they did not make — most likely they
    changed the relationship after writing it — and a request that says two things is a
    request nobody read carefully.

    The other half — whether *this address* needed the override at all — is deliberately not
    here. It depends on the assertion's value, which is a row, and checking a row anywhere
    but inside the command's own transaction is a race dressed up as validation.
    """
    note = (override_note or "").strip() or None
    if note is not None and usage != "shared_mailbox":
        raise CommandRefused(
            422,
            "override_note_is_only_for_shared_mailbox",
            "shared_mailbox_override_note justifies calling a named address a shared "
            f"mailbox; usage '{usage}' makes no such claim, so there is nothing to justify",
        )
    return {"usage": usage, "shared_mailbox_override_note": note}


def require_override_for_a_named_address(
    *, value_norm: str, usage: str, override_note: str | None
) -> None:
    """Refuse `shared_mailbox` on an address that does not look like a desk, unless told why.

    This is the rule the whole change exists for. `shared_mailbox` was the only value the
    schema allowed, so it was what every attribution wrote — including on `jperez@`, where it
    asserts that a named individual's mailbox is a desk shared by several people. Nobody
    decided that; the vocabulary decided it for them, and the CRM then read it back as its own
    belief about a real person.

    So the claim now has to be made on purpose. An unrecognised local part is not evidence
    that the address belongs to a person — it is only the absence of evidence that it does
    not — which is why this refuses rather than rewrites: `individual_owner_unknown` is
    right here and the operator is the one who says so.
    """
    if usage != "shared_mailbox" or address_names_a_desk(value_norm):
        return
    if (override_note or "").strip():
        return
    raise CommandRefused(
        422,
        "named_address_is_not_a_shared_mailbox_by_default",
        f"'{value_norm}' does not carry a recognised role local part, so calling it a "
        "shared mailbox asserts that several people read it. Choose "
        "'individual_owner_unknown', which records the institution without claiming who "
        "owns the address, or say in shared_mailbox_override_note how you know it is a desk",
    )


def validated(command_name: str, body: _CommandBody) -> dict[str, Any]:
    """Everything that can be checked without the database, checked in one place.

    What is deliberately *not* here: whether the assertion exists, whether it belongs to the
    record, whether it is still unresolved, whether the organization exists. Those are facts
    about rows, they can change between the check and the write, and checking them anywhere
    but inside the command's own transaction would be a race dressed up as validation.
    """
    if isinstance(body, AttributeSenderOrganizationBody):
        return validated_attribution(body)

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
        fields.update(validated_usage(body.usage, body.shared_mailbox_override_note))
    elif isinstance(body, ConfirmPersonFromEvidenceBody):
        fields["assertion_id"] = _uuid(body.assertion_id, "assertion_id")
        fields["display_name"] = body.display_name.strip()
        fields["given_name"] = body.given_name.strip() if body.given_name else None
        fields["family_name"] = body.family_name.strip() if body.family_name else None
        fields["organization_id"] = (
            _uuid(body.organization_id, "organization_id")
            if body.organization_id is not None
            else None
        )
    return fields


def _refuse_fields_of_the_other_target(body: AttributeSenderOrganizationBody) -> None:
    """A request may carry the fields of one target, and only of the one it named.

    Ignoring the surplus would be worse than refusing it: an operator who filled in an
    organization and then chose `"new"` gets a new organization and never learns that the one
    they picked was discarded. A request that contradicts itself is answered, not tidied.
    """
    if body.target == "existing":
        if body.organization_id is None or body.organization_version is None:
            raise CommandRefused(
                422,
                "existing_organization_requires_id_and_version",
                "target 'existing' needs organization_id and the organization_version you "
                "were shown",
            )
        if body.name_display is not None:
            raise CommandRefused(
                422,
                "name_display_is_only_for_a_new_organization",
                "target 'existing' does not name an organization; it identifies one by id",
            )
    else:
        if body.organization_id is not None or body.organization_version is not None:
            raise CommandRefused(
                422,
                "new_organization_takes_no_organization_id",
                "target 'new' creates an organization from the assertion; it cannot also "
                "point at an existing one",
            )


def validated_attribution(body: AttributeSenderOrganizationBody) -> dict[str, Any]:
    """Everything about an attribution that can be decided without the database.

    The one check worth naming: the two assertion ids must differ. They are read as different
    kinds a moment later, so an identical pair would be refused anyway — but it would be
    refused by `assertion_wrong_kind`, which says nothing about what the operator actually
    got wrong.
    """
    _refuse_fields_of_the_other_target(body)

    fields: dict[str, Any] = {
        "source_record_id": _uuid(body.source_record_id, "source_record_id"),
        "note": body.note.strip(),
        "organization_assertion_id": _uuid(
            body.organization_assertion_id, "organization_assertion_id"
        ),
        "address_assertion_id": _uuid(body.address_assertion_id, "address_assertion_id"),
        "target": body.target,
        **validated_usage(body.usage, body.shared_mailbox_override_note),
    }
    if fields["organization_assertion_id"] == fields["address_assertion_id"]:
        raise CommandRefused(
            422,
            "one_assertion_cannot_be_both",
            "the institution and the address must be two different assertions of this message",
        )
    if body.target == "existing":
        fields["organization_id"] = _uuid(body.organization_id or "", "organization_id")
        fields["organization_version"] = int(body.organization_version or 0)
    else:
        fields["kind"] = body.kind
        fields["name_display"] = body.name_display.strip() if body.name_display else None
    return fields
