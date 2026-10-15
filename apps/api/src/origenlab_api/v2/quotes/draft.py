"""Request bodies for quote drafts and the quote commands. Shape validation only: no pricing here."""
from __future__ import annotations

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
    quantity: Decimal = Field(gt=0, le=Decimal("100000"))
    item_type: ItemType | None = None  # None: the product's kind, else "other"
    unit_cost: Decimal | None = Field(default=None, ge=0)
    cost_currency: Currency | None = None
    supplier_organization_id: UUID | None = None
    cost_observation_id: UUID | None = None
    margin_mode: Literal["markup", "margin", "none"] = "markup"
    margin_pct: Decimal | None = Field(default=None, ge=0, le=Decimal("10"))  # None + markup: default by item type
    typed_unit_price: Decimal | None = Field(default=None, ge=0)
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
    amount: Decimal = Field(ge=0)
    currency: Currency = "CLP"


class DraftFxOverride(_Model):
    currency: Literal["EUR", "USD"]
    clp_per_unit: Decimal = Field(gt=0)
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
    recipient_participant_id: UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "QuoteDraft":
        principals = sum(1 for item in self.items if item.is_principal)
        if principals > 1:
            raise ValueError("at most one item may be principal")
        if principals and self.principal_rule != "manual":
            raise ValueError("is_principal is only allowed when principal_rule is manual")
        currencies = [o.currency for o in self.fx_overrides]
        if len(set(currencies)) != len(currencies):
            raise ValueError("fx_overrides currencies must be unique")
        return self


class CreateQuoteBody(_Model):
    note: str | None = None
    opportunity_id: UUID


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
