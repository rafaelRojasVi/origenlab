"""Request bodies for quote drafts and the quote commands. Shape validation only: no pricing here.

Money and quantities carry the precision of the columns they land in (`crm.quote_line` numeric(18,6),
`margin_pct` numeric(9,6), an FX rate numeric(18,8)), so a saved draft reads back exactly as sent and a
value the column cannot hold is a 422 here, never a 500 at the insert.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ItemType = Literal["equipment", "accessory", "consumable", "spare_part", "service", "other"]
Currency = Literal["CLP", "EUR", "USD"]
_Text1000 = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
_Reason = Annotated[str, StringConstraints(min_length=3, max_length=500)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftItemLine(_Model):
    product_id: UUID | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    quantity: Decimal = Field(gt=0, le=Decimal("100000"), max_digits=18, decimal_places=6)
    item_type: ItemType | None = None  # None: the product's kind, else "other"
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    cost_currency: Currency | None = None
    supplier_organization_id: UUID | None = None
    cost_observation_id: UUID | None = None
    margin_mode: Literal["markup", "margin", "none"] = "markup"
    margin_pct: Decimal | None = Field(default=None, ge=0, le=Decimal("10"),
                                       decimal_places=6)  # None + markup: default by item type
    typed_unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    is_principal: bool = False
    line_note_es: Annotated[str | None, StringConstraints(min_length=1, max_length=1000)] = None

    @model_validator(mode="after")
    def _consistent(self) -> "DraftItemLine":
        if (self.unit_cost is None) != (self.cost_currency is None):
            raise ValueError("unit_cost and cost_currency go together")
        if self.margin_mode == "none" and self.typed_unit_price is None:
            raise ValueError("margin_mode none requires typed_unit_price")
        if self.margin_mode == "margin" and (self.margin_pct is None or self.margin_pct >= 1):
            raise ValueError("margin_mode margin requires margin_pct below 1")
        return self


class DraftExpenseLine(_Model):
    label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    currency: Currency = "CLP"


class DraftFxOverride(_Model):
    currency: Literal["EUR", "USD"]
    clp_per_unit: Decimal = Field(gt=0, max_digits=18, decimal_places=8)
    reason: _Reason


class DraftTerm(_Model):
    label_es: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    text_es: _Text1000


_OptText = Annotated[str | None, StringConstraints(max_length=1000)]


class DraftTerms(_Model):
    delivery_terms: _OptText = None
    validity_days: int | None = Field(default=None, ge=1, le=365)
    payment_terms: _OptText = None
    warranty_terms_es: _OptText = None
    delivery_place_es: _OptText = None
    installation_terms_es: _OptText = None
    client_notes_es: _OptText = None
    terms_extra: list[DraftTerm] = Field(default_factory=list, max_length=12)


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DraftRecipient(_Model):
    """Whom the quote goes to: exactly one of a current participant of the case, a person, a
    contact point, or an email address (with, optionally, the name the address was picked by).

    Anything but a participant becomes one when the draft is saved (`commands._recipient`): a
    `quote_recipient` participant of the case, unless that person or address already is one.
    """
    participant_id: UUID | None = None
    person_id: UUID | None = None
    contact_point_id: UUID | None = None
    email: Annotated[str | None, StringConstraints(min_length=3, max_length=320)] = None
    name: Annotated[str | None, StringConstraints(min_length=1, max_length=200)] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "DraftRecipient":
        picked = [self.participant_id, self.person_id, self.contact_point_id, self.email]
        if sum(v is not None for v in picked) != 1:
            raise ValueError("recipient takes exactly one of participant_id, person_id, contact_point_id, email")
        if self.name is not None and self.email is None:
            raise ValueError("recipient name goes only with an email address")
        if self.email is not None:
            self.email = self.email.lower()
            if not _EMAIL.match(self.email):
                raise ValueError("recipient email is not an email address")
        return self


class QuoteDraft(_Model):
    items: list[DraftItemLine] = Field(default_factory=list, max_length=60)
    manual_expenses: list[DraftExpenseLine] = Field(default_factory=list, max_length=20)
    expense_method: Literal["profile", "dhl_calc", "manual"] = "profile"
    allocation_mode: Literal["principal", "proportional_cost"] = "principal"
    principal_rule: Literal[
        "highest_cost_imported_equipment", "first_eur_equipment_over_threshold", "manual"
    ] = "highest_cost_imported_equipment"
    fx_overrides: list[DraftFxOverride] = Field(default_factory=list, max_length=2)
    terms: DraftTerms = Field(default_factory=DraftTerms)
    recipient_participant_id: UUID | None = None  # the older form of recipient={participant_id}
    recipient: DraftRecipient | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "QuoteDraft":
        if self.recipient is not None and self.recipient_participant_id is not None:
            raise ValueError("give recipient or recipient_participant_id, not both")
        principals = sum(1 for item in self.items if item.is_principal)
        if principals > 1:
            raise ValueError("at most one item may be principal")
        if principals and self.principal_rule != "manual":
            raise ValueError("is_principal is only allowed when principal_rule is manual")
        currencies = [o.currency for o in self.fx_overrides]
        if len(set(currencies)) != len(currencies):
            raise ValueError("fx_overrides currencies must be unique")
        return self


def _email(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.lower()
    if not _EMAIL.match(value):
        raise ValueError("email is not an email address")
    return value


#: The kinds the dashboard offers for a new institution (plan R2).
NewOrganizationKind = Literal["company", "university", "public_institution", "hospital", "laboratory", "other"]


class NewClientOrganization(_Model):
    """An institution the CRM does not hold yet: registered as `register-organization` registers one."""
    name: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    kind: NewOrganizationKind = "company"
    email: Annotated[str | None, StringConstraints(min_length=3, max_length=320)] = None

    @model_validator(mode="after")
    def _valid_email(self) -> "NewClientOrganization":
        self.email = _email(self.email)
        return self


class QuoteClient(_Model):
    """Whom a new quote is for when no case is named: exactly one of an institution, a person, an
    email contact point, an address (with, optionally, the name it goes by) or a new institution."""
    organization_id: UUID | None = None
    person_id: UUID | None = None
    contact_point_id: UUID | None = None
    email: Annotated[str | None, StringConstraints(min_length=3, max_length=320)] = None
    name: Annotated[str | None, StringConstraints(min_length=1, max_length=200)] = None
    new_organization: NewClientOrganization | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "QuoteClient":
        picked = [self.organization_id, self.person_id, self.contact_point_id, self.email, self.new_organization]
        if sum(v is not None for v in picked) != 1:
            raise ValueError("client takes exactly one of organization_id, person_id, contact_point_id, email, "
                             "new_organization")
        if self.name is not None and self.email is None:
            raise ValueError("client name goes only with an email address")
        self.email = _email(self.email)
        return self


class CreateQuoteBody(_Model):
    """A quote on a case: the case named (`opportunity_id`), or found from the client (`client`) —
    with `opportunity_id` then the client's open case chosen, or `new_case` for a new one."""
    note: Annotated[str | None, StringConstraints(min_length=1, max_length=500)] = None
    opportunity_id: UUID | None = None
    client: QuoteClient | None = None
    new_case: bool = False

    @model_validator(mode="after")
    def _a_case_or_a_client(self) -> "CreateQuoteBody":
        if self.opportunity_id is None and self.client is None:
            raise ValueError("give opportunity_id or client")
        if self.new_case and self.client is None:
            raise ValueError("new_case goes only with a client")
        if self.new_case and self.opportunity_id is not None:
            raise ValueError("give opportunity_id or new_case, not both")
        return self


class SaveQuoteDraftBody(_Model):
    revision_id: UUID
    expected_version: int = Field(ge=1)
    draft: QuoteDraft


class PreviewQuoteBody(_Model):
    revision_id: UUID | None = None
    draft: QuoteDraft


class ApproveQuoteBody(_Model):
    revision_id: UUID
    expected_version: int = Field(ge=1)


class VoidQuoteBody(_Model):
    revision_id: UUID
    expected_version: int = Field(ge=1)
    reason: _Reason


class NewQuoteRevisionBody(_Model):
    quote_id: UUID


QUOTE_BODIES = {
    "create-quote": CreateQuoteBody,
    "save-quote-draft": SaveQuoteDraftBody,
    "approve-quote-revision": ApproveQuoteBody,
    "void-quote-revision": VoidQuoteBody,
    "create-quote-revision": NewQuoteRevisionBody,
}
