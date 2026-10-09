"""The nine commercial-case commands — their requests, their refusals, their vocabulary.

A *caso comercial* is `crm.opportunity` and nothing else ([`docs/DOMAIN.md`](../../../../../docs/DOMAIN.md)
§3.6): no case table, no case identifier, no second lifecycle. These commands are how a human
runs one, and the shape of each request is the shape of a sentence an operator is willing to
sign.

| Command | What durable fact it records |
|---|---|
| `open_commercial_case` | a named operator opened a case, and this document is the reason it exists |
| `link_case_evidence` | this document is why the case believes something — including *against* it |
| `add_case_organization` | this institution is on this case, in this part |
| `set_case_organization_role` | an institution's part on this case is confirmed, or it is this other part now |
| `record_case_interest` | this is what the case is seeking |
| `advance_case_stage` | the case moved, and the rules for moving it were met |
| `record_case_won` | the case was won, against this sent, current quote revision |
| `resolve_current_revision` | of this quote's current revisions, this one is the current one |
| `record_case_quotation` | this quote, printing this number, was already sent on this case, by this message |

**Nine, and deliberately only nine.** There is no command that closes an evidence link, no
command that withdraws an interest, no command that creates a person or a participant, no
command that opens a `crm.organization_relationship`, no command that drafts, prices or sends a
quote. `record_case_won` *names* a quote revision that already exists; it creates none.
`resolve_current_revision` supersedes revisions that exist; it creates none either.
`record_case_quotation` records a quote that was **already sent** from Gmail — the printed
number, the document, the message — through the same writer the historical import and the
email → cases rules use (`case_quotation.py`); it carries no amount and drafts nothing. Every
absence is a column that exists and stays unwritten until the act that writes it has been
designed — the same rule the schema migration stated about event types: a capability nothing
exercises is a promise, not a contract.

**What none of them can reach, structurally.**

* **`outbound.*`.** No command here names a campaign, a recipient, a contact control or a
  send, and none of these request bodies has a field that could carry one. Being a live case
  is not a reason to mail an institution (§3.6.4), and consent has exactly one authority —
  `outbound.contact_control` — which this boundary never opens.
* **Money.** An interest carries a quantity and never an amount, a currency or a margin
  (§3.6.2). `extra="forbid"` on every body means a request that tries to smuggle one is a
  422 rather than a silently discarded field.
* **A commercial role for the machine.** Every row these commands write is `confirmation =
  'confirmed'` with the deciding operator named, because every one of them *is* an operator
  act. The vocabulary for `machine_proposed` is not absent by policy: there is no field in
  any of these requests that could ask for it, and the database refuses any role but
  `mentioned` from a machine regardless.

**Every command carries four things by construction** — the case it is about, the version of
that case the operator was shown, a non-blank reason, and an idempotency key. A request
missing any of them is refused before a database connection is opened.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from origenlab_api.v2.commands import CommandRefused, DecisionBody, as_uuid

#: The closed command vocabulary, in the order an operator meets them.
OPEN_COMMERCIAL_CASE = "open_commercial_case"
LINK_CASE_EVIDENCE = "link_case_evidence"
ADD_CASE_ORGANIZATION = "add_case_organization"
SET_CASE_ORGANIZATION_ROLE = "set_case_organization_role"
RECORD_CASE_INTEREST = "record_case_interest"
ADVANCE_CASE_STAGE = "advance_case_stage"
#: Also the step name the email → cases rules use (R5, `mail_rules.py`): one writer of a win,
#: whether a person presses «Marcar ganada» or a rule reads a purchase order.
RECORD_CASE_WON = "record_case_won"
RESOLVE_CURRENT_REVISION = "resolve_current_revision"
RECORD_CASE_QUOTATION = "record_case_quotation"

CASE_COMMAND_NAMES: tuple[str, ...] = (
    OPEN_COMMERCIAL_CASE,
    LINK_CASE_EVIDENCE,
    ADD_CASE_ORGANIZATION,
    SET_CASE_ORGANIZATION_ROLE,
    RECORD_CASE_INTEREST,
    ADVANCE_CASE_STAGE,
    RECORD_CASE_WON,
    RESOLVE_CURRENT_REVISION,
    RECORD_CASE_QUOTATION,
)

#: The one stage a case is won from (`STAGE_TRANSITIONS`: only `negotiating` leads to `won`).
WON_FROM_STAGE = "negotiating"

#: `crm.opportunity_organization.role`, exactly as the CHECK constraint spells it
#: (`DOMAIN.md` §3.6.1). `mentioned` is honest silence — named in the evidence, part not
#: decided — and it is the only value a machine may ever propose. It stays available to an
#: operator too: "this institution is on the case and I have not worked out why" is a true
#: sentence and a recordable one.
CASE_ORGANIZATION_ROLES: tuple[str, ...] = (
    "requesting_institution",
    "end_user_institution",
    "purchasing_agent",
    "funder",
    "supplier",
    "manufacturer",
    "mentioned",
)

#: The role whose presence is what `qualified` and a quote actually mean (§3.6.5).
REQUESTING_INSTITUTION = "requesting_institution"

#: Relationships to OrigenLab that make an institution a supplier of ours. Naming one of
#: these as the *requesting* institution of a case needs the justified exception (§3.6.1).
SUPPLIER_RELATIONSHIP_ROLES: tuple[str, ...] = ("supplier", "manufacturer")

#: `crm.opportunity_evidence.relation`. `contradicts` is deliberately first-class: the
#: reading *against* a case is how one is later closed honestly, and this is the only durable
#: place it can live (§3.6.3).
EVIDENCE_RELATIONS: tuple[str, ...] = (
    "origin",
    "supports_requesting_institution",
    "supports_interest",
    "supports_participant",
    "mentions",
    "contradicts",
)

#: The four typed subjects an evidence link may have. No `subject_type` / `subject_id` pair
#: exists anywhere: a link points at exactly one of these columns, and the request says which
#: by filling exactly one field.
EVIDENCE_SUBJECT_FIELDS: tuple[str, ...] = (
    "source_record_id",
    "assertion_id",
    "message_id",
    "notice_id",
)

#: `crm.opportunity.stage`, and the stage machine of `docs/WORKFLOWS.md` §1.1 as data.
#: Duplicated from `crm.opportunity_stage_transition_allowed` on purpose, not by accident:
#: the command refuses an illegal move with a sentence naming the rule, and the database
#: refuses it again where no code path can miss it. A database-backed test drives every
#: transition through both and asserts they agree, so the duplication cannot rot quietly.
CASE_STAGES: tuple[str, ...] = (
    "lead", "qualifying", "qualified", "quoting", "negotiating", "won", "lost", "abandoned",
)
TERMINAL_STAGES: tuple[str, ...] = ("won", "lost", "abandoned")
OPENING_STAGE = "lead"

STAGE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "lead": ("qualifying", "abandoned", "lost"),
    "qualifying": ("qualified", "lead", "abandoned", "lost"),
    "qualified": ("quoting", "qualifying", "abandoned", "lost"),
    "quoting": ("negotiating", "qualified", "abandoned", "lost"),
    "negotiating": ("won", "lost", "quoting", "abandoned"),
    "won": (),
    "lost": (),
    "abandoned": (),
}

#: Stages that mean *an operator decided who is asking* (§3.6.5). A case may sit at `lead` or
#: `qualifying` with no requesting institution for as long as it takes; from `qualified` on,
#: it may not.
STAGES_REQUIRING_A_REQUESTING_INSTITUTION: tuple[str, ...] = (
    "qualified", "quoting", "negotiating", "won",
)

#: Stages that end a case and therefore need a motive.
STAGES_REQUIRING_A_CLOSE_REASON: tuple[str, ...] = ("lost", "abandoned")


class _CaseBody(DecisionBody):
    """What every command about an existing case says: which case, and which version of it.

    `opportunity_version` is the version the operator was shown. Every case command is a
    compare-and-set against it, so two operators working the same case do not both win — the
    loser is told the case moved and asked to re-read it, which is the honest answer.
    """

    opportunity_id: str
    opportunity_version: Annotated[int, Field(ge=1)]


class OpenCommercialCaseBody(DecisionBody):
    """Open a case, and say which document is the reason it exists.

    **Why the origin record is required.** [`WORKFLOWS.md`](../../../../../docs/WORKFLOWS.md)
    §W2 step 2 refuses a case that has neither an organization nor a participant — *a case is
    never an empty shell* — and neither is available here: a case from an inbound message
    routinely has no institution yet (§3.6.5, and forcing one would mean guessing it), and no
    command in this system creates a participant. The rule survives in the form the evidence
    can actually satisfy: a case is opened **from a document**, and that document becomes the
    case's `origin` evidence link in the same transaction. A case with nothing behind it is
    therefore not refused by a check — it is unrequestable.

    There is no `stage` field. A case opens at `lead` and moves from there; the opening stage
    is not an operator's choice, and a trigger on `crm.opportunity` says so too.

    There is no `organization_id` field either. Naming the requesting institution is
    `add_case_organization`, which is a different sentence with different consequences — it
    can require the supplier exception, and opening a case never should.
    """

    title: Annotated[str, Field(min_length=1, max_length=400)]
    origin_source_record_id: str

    @model_validator(mode="after")
    def _title_is_not_whitespace(self) -> OpenCommercialCaseBody:
        if not self.title.strip():
            raise ValueError("title must not be blank")
        return self


class LinkCaseEvidenceBody(_CaseBody):
    """Attach an existing document to a case as the reason it believes something.

    Exactly one subject, named by its own typed field. `relation` is the reading, and
    `contradicts` is as legitimate as `origin`: a case that has collected the evidence
    against itself is a case that can be closed honestly rather than quietly.

    The subject must already exist. This command creates no evidence, no message and no
    notice — it records that an operator read one and decided what it meant for this case.
    """

    relation: Literal[
        "origin",
        "supports_requesting_institution",
        "supports_interest",
        "supports_participant",
        "mentions",
        "contradicts",
    ]
    source_record_id: str | None = None
    assertion_id: str | None = None
    message_id: str | None = None
    notice_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> LinkCaseEvidenceBody:
        named = [f for f in EVIDENCE_SUBJECT_FIELDS if getattr(self, f) is not None]
        if len(named) != 1:
            raise ValueError(
                "name exactly one subject among "
                f"{', '.join(EVIDENCE_SUBJECT_FIELDS)} (named: {len(named)})"
            )
        return self


class AddCaseOrganizationBody(_CaseBody):
    """Put an institution on a case, in a named part.

    `role` has no default. What an institution is *to this deal* is the reading the whole
    table exists to hold, and a default would have this module make it — the mistake
    §3.6.1 was written to prevent. An operator who does not yet know says `mentioned`, which
    is a value, not a shrug.

    **`requesting_institution` does two things at once**, and that is why the command exists
    rather than an INSERT: it opens the row *and* sets `crm.opportunity.organization_id`,
    which §3.6.1 defines as exactly that institution. Either both move or neither does.

    `supplier_exception_reason` is the justified override of §3.6.1. It is required — and
    only accepted — when the institution holds a current `supplier` or `manufacturer`
    relationship with OrigenLab and is being made the requesting institution of this case.
    Whether it is needed depends on `crm.organization_relationship`, which is a row, so that
    half of the rule runs inside the command's transaction and not here.
    """

    organization_id: str
    organization_version: Annotated[int, Field(ge=1)]
    role: Literal[
        "requesting_institution",
        "end_user_institution",
        "purchasing_agent",
        "funder",
        "supplier",
        "manufacturer",
        "mentioned",
    ]
    supplier_exception_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class SetCaseOrganizationRoleBody(_CaseBody):
    """Confirm what an institution is to this case, or say it is something else now.

    One command, because from the operator's chair it is one sentence — *no, they are the
    end user, not the buyer* — and because the two halves of saying it must not be separable:
    closing the old reading and opening the new one are the same act.

    **Nothing is ever rewritten.** `role` is not in `origenlab_api`'s UPDATE grant on
    `crm.opportunity_organization`, so a part cannot be edited in place even by this code.
    Changing it closes the current row (`valid_to`) and opens a new one; both stay readable
    forever, which is what §3.6.1 means by *rows are never deleted*.

    The one in-place move is confirming a proposal that the machine made and the operator
    agrees with: a `machine_proposed` `mentioned` row becomes `confirmed`, on the same row,
    because the reading did not change — only who vouches for it.

    `opportunity_organization_id` names the **current** row, not the institution: an
    institution may legitimately hold several parts on one case at once (§3.6.1), so naming
    the organization would be ambiguous exactly when it matters.
    """

    opportunity_organization_id: str
    role: Literal[
        "requesting_institution",
        "end_user_institution",
        "purchasing_agent",
        "funder",
        "supplier",
        "manufacturer",
        "mentioned",
    ]
    supplier_exception_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class RecordCaseInterestBody(_CaseBody):
    """Record what the case is seeking.

    At least one of `product_id`, `manufacturer_organization_id` and `model_text`. An
    interest may begin as a model string read off a message and gain a catalogue product
    later, on the same row (§3.6.2).

    **There is no money field, and there is no room for one.** An interest is never a
    commitment and never a price; amounts exist on `crm.quote_revision` and `crm.quote_line`
    alone. `extra="forbid"` turns a request carrying `unit_price` or `currency` into a 422
    rather than a field that is dropped while the operator believes it was recorded.

    Naming a manufacturer here creates nothing for that manufacturer: no relationship, no
    prospect, no permission, no case role. If they belong on the case as an institution, that
    is `add_case_organization`, said out loud.
    """

    product_id: str | None = None
    manufacturer_organization_id: str | None = None
    model_text: Annotated[str, Field(min_length=1, max_length=400)] | None = None
    description: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    quantity: Decimal | None = None
    quantity_unit: Annotated[str, Field(min_length=1, max_length=40)] | None = None

    @model_validator(mode="after")
    def _has_a_subject_and_a_sane_quantity(self) -> RecordCaseInterestBody:
        named = [
            v
            for v in (self.product_id, self.manufacturer_organization_id, self.model_text)
            if v is not None
        ]
        if not named:
            raise ValueError(
                "an interest needs at least one of product_id, "
                "manufacturer_organization_id or model_text"
            )
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        if self.quantity_unit is not None and self.quantity is None:
            raise ValueError("quantity_unit describes a quantity; name the quantity too")
        return self


class AdvanceCaseStageBody(_CaseBody):
    """Move the case, when the rules for moving it are met.

    `won` is not reachable through this command and is refused with its own code.
    `crm.opportunity` may only be `won` together with `won_quote_id` and `won_revision_no`
    (a shipped CHECK), and this request has no field that names a quote revision — so
    accepting `won` here could only ever end in a constraint violation. `record_case_won` is
    the command that names one; saying so up front, by name, is more useful than a 500.

    `close_reason` is required for `lost` and `abandoned`, and refused for anything else.
    §3.4 and [`WORKFLOWS.md`](../../../../../docs/WORKFLOWS.md) §1.1: *abandoned requires an
    operator and a motive*, and no timer, cron job, worker, classifier or import may write a
    stage at all — this boundary is the only writer, and it always has a named operator.
    """

    stage: Literal[
        "lead", "qualifying", "qualified", "quoting", "negotiating", "won", "lost", "abandoned",
    ]
    close_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class RecordCaseWonBody(_CaseBody):
    """The case was won, against one quote revision the operator names.

    `quote_id` and `revision_no` name the revision the way an operator reads it — «CN 1234,
    r2» — and the command refuses any revision that is not on this case, not `sent`, or
    already superseded: a case is won against what the customer actually accepted, never a
    draft and never a version that was replaced. The case must be at `negotiating`, the one
    stage `won` follows; from `quoting` that is an `advance_case_stage` first, said out loud.

    There is no amount, no currency and no purchase-order field. The value of the win is the
    revision's, already recorded on `crm.quote_revision`; nothing here can restate it.
    """

    quote_id: str
    revision_no: Annotated[int, Field(ge=1)]


class ResolveCurrentRevisionBody(_CaseBody):
    """Of this quote's current revisions, this one is the current one.

    A case card says «Hay más de una revisión vigente» when a quote on it has two or more
    revisions that are neither void nor superseded — the historical import records a
    supersession only when the owner named one. `quote_id` + `revision_no` name the revision the
    operator chose; every *other* current revision of that quote is then superseded by it, in
    one transaction. Nothing is voided and nothing is deleted: a superseded revision stays
    `sent` forever (`WORKFLOWS.md` §1.2), and supersession is recorded once.
    """

    quote_id: str
    revision_no: Annotated[int, Field(ge=1)]


class RecordCaseQuotationBody(_CaseBody):
    """A quote already sent on this case: its printed number, its document, the message.

    `source_record_id` is a Gmail message already linked to this case — the evidence that the
    quote was sent — and `document_sha256` is one document that message carries. `quote_number`
    is the number printed on that document, as the operator reads it; it becomes a
    `printed_historical` quote, never a minted one. A number that is already a quote on another
    case is refused: deciding which case a shared printed number belongs to is a different act.

    `supersedes_revision_no` makes it «Nueva revisión»: a new revision of this case's quote with
    that number, replacing the named revision (which stays `sent`, superseded). Without it, a
    document under a number the case already has is a second current revision, and the card
    asks the operator to choose.

    No amount, no currency, no line: the document was never parsed for them, and `extra="forbid"`
    refuses a caller who guesses a field name.
    """

    quote_number: Annotated[str, Field(min_length=1, max_length=32)]
    source_record_id: str
    document_sha256: Annotated[str, Field(min_length=64, max_length=64)]
    supersedes_revision_no: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _shapes(self) -> RecordCaseQuotationBody:
        from origenlab_api.v2.quote_import_commands import QUOTE_NUMBER_SHAPE

        if not QUOTE_NUMBER_SHAPE.match(self.quote_number.strip()):
            raise ValueError("quote_number does not have the shape crm.quote accepts")
        if not re.fullmatch(r"[0-9a-f]{64}", self.document_sha256.strip().lower()):
            raise ValueError("document_sha256 must be a SHA-256 hex digest")
        return self


CASE_BODY_BY_COMMAND: dict[str, type[DecisionBody]] = {
    OPEN_COMMERCIAL_CASE: OpenCommercialCaseBody,
    LINK_CASE_EVIDENCE: LinkCaseEvidenceBody,
    ADD_CASE_ORGANIZATION: AddCaseOrganizationBody,
    SET_CASE_ORGANIZATION_ROLE: SetCaseOrganizationRoleBody,
    RECORD_CASE_INTEREST: RecordCaseInterestBody,
    ADVANCE_CASE_STAGE: AdvanceCaseStageBody,
    RECORD_CASE_WON: RecordCaseWonBody,
    RESOLVE_CURRENT_REVISION: ResolveCurrentRevisionBody,
    RECORD_CASE_QUOTATION: RecordCaseQuotationBody,
}


def stage_transition_allowed(from_stage: str, to_stage: str) -> bool:
    """`WORKFLOWS.md` §1.1. Staying put is not a transition; a terminal stage allows nothing."""
    return from_stage == to_stage or to_stage in STAGE_TRANSITIONS.get(from_stage, ())


def _exception_reason(raw: str | None) -> str | None:
    reason = (raw or "").strip() or None
    return reason


def _validated_supplier_exception(role: str, raw: str | None) -> str | None:
    """The half of the exception rule that needs no database: it belongs to one role only.

    An exception attached to `manufacturer` or `mentioned` is refused rather than dropped.
    `opportunity_organization_exception_requesting_only` would refuse it too, but a request
    that says two things deserves a sentence about what it said, not a constraint name.
    """
    reason = _exception_reason(raw)
    if reason is not None and role != REQUESTING_INSTITUTION:
        raise CommandRefused(
            422,
            "supplier_exception_is_only_for_a_requesting_institution",
            "the supplier exception justifies making an institution OrigenLab buys from the "
            f"one asking on this case; role '{role}' makes no such claim, so there is "
            "nothing to justify",
        )
    return reason


def validated_case(command_name: str, body: DecisionBody) -> dict[str, Any]:
    """Everything that can be checked without the database, checked in one place.

    What is deliberately *not* here: whether the case exists, whether it is still open,
    whether the institution holds a supplier relationship, whether the evidence subject is
    real, whether another institution already holds the requesting part. Those are facts
    about rows, they can change between the check and the write, and checking them anywhere
    but inside the command's own transaction would be a race dressed up as validation.
    """
    fields: dict[str, Any] = {"note": body.note.strip()}

    if isinstance(body, OpenCommercialCaseBody):
        fields["title"] = body.title.strip()
        fields["origin_source_record_id"] = as_uuid(
            body.origin_source_record_id, "origin_source_record_id"
        )
        return fields

    assert isinstance(body, _CaseBody)  # noqa: S101 - every other command is about a case
    fields["opportunity_id"] = as_uuid(body.opportunity_id, "opportunity_id")
    fields["opportunity_version"] = int(body.opportunity_version)

    if isinstance(body, LinkCaseEvidenceBody):
        fields["relation"] = body.relation
        for name in EVIDENCE_SUBJECT_FIELDS:
            raw = getattr(body, name)
            fields[name] = as_uuid(raw, name) if raw is not None else None
    elif isinstance(body, AddCaseOrganizationBody):
        fields["organization_id"] = as_uuid(body.organization_id, "organization_id")
        fields["organization_version"] = int(body.organization_version)
        fields["role"] = body.role
        fields["supplier_exception_reason"] = _validated_supplier_exception(
            body.role, body.supplier_exception_reason
        )
    elif isinstance(body, SetCaseOrganizationRoleBody):
        fields["opportunity_organization_id"] = as_uuid(
            body.opportunity_organization_id, "opportunity_organization_id"
        )
        fields["role"] = body.role
        fields["supplier_exception_reason"] = _validated_supplier_exception(
            body.role, body.supplier_exception_reason
        )
    elif isinstance(body, RecordCaseInterestBody):
        fields["product_id"] = (
            as_uuid(body.product_id, "product_id") if body.product_id else None
        )
        fields["manufacturer_organization_id"] = (
            as_uuid(body.manufacturer_organization_id, "manufacturer_organization_id")
            if body.manufacturer_organization_id
            else None
        )
        fields["model_text"] = body.model_text.strip() if body.model_text else None
        fields["description"] = body.description.strip() if body.description else None
        fields["quantity"] = body.quantity
        fields["quantity_unit"] = body.quantity_unit.strip() if body.quantity_unit else None
    elif isinstance(body, AdvanceCaseStageBody):
        fields.update(validated_stage(body))
    elif isinstance(body, (RecordCaseWonBody, ResolveCurrentRevisionBody)):
        fields["quote_id"] = as_uuid(body.quote_id, "quote_id")
        fields["revision_no"] = int(body.revision_no)
    elif isinstance(body, RecordCaseQuotationBody):
        fields["quote_number"] = body.quote_number.strip()
        fields["source_record_id"] = as_uuid(body.source_record_id, "source_record_id")
        fields["document_sha256"] = body.document_sha256.strip().lower()
        fields["supersedes_revision_no"] = (
            int(body.supersedes_revision_no) if body.supersedes_revision_no is not None else None
        )
    return fields


def validated_stage(body: AdvanceCaseStageBody) -> dict[str, Any]:
    """The stage rules that do not need the current stage: the target, and its motive.

    `won` is refused here rather than three layers down. This request cannot name the quote
    revision a win is recorded against, and an operator who asks for it here has
    misunderstood something a constraint violation would not explain.
    """
    if body.stage == "won":
        raise CommandRefused(
            422,
            "won_requires_a_quote",
            "a case is won against a specific quote revision, which this command cannot name; "
            "record the win with record-case-won, naming the sent, current revision",
        )
    reason = (body.close_reason or "").strip() or None
    needs_reason = body.stage in STAGES_REQUIRING_A_CLOSE_REASON
    if needs_reason and reason is None:
        raise CommandRefused(
            422,
            "closing_a_case_needs_a_motive",
            f"stage '{body.stage}' ends this case; close_reason must say why "
            "(DOMAIN.md §3.4)",
        )
    if not needs_reason and reason is not None:
        raise CommandRefused(
            422,
            "close_reason_is_only_for_a_closing_stage",
            f"stage '{body.stage}' does not end the case, so there is nothing for "
            "close_reason to explain",
        )
    return {"stage": body.stage, "close_reason": reason}
