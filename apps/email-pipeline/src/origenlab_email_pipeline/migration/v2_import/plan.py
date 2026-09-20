"""The deterministic Wave 1A/1B → V2 mapping.

This module turns verified artifacts into an :class:`ImportPlan`: an ordered, fully
determined set of rows, computed in memory, with no database connection and no clock read.
The same inputs always produce the same plan, byte for byte, which is what makes the apply
path idempotent and the dry run trustworthy.

What the mapping will not do
----------------------------

The artifacts are a *safety* baseline — what V1 can prove it already wrote to
(`docs/DATA.md` §7.5.2) — and not CRM identity truth. So:

* **An address is a contact point, never a person.** No `crm.person` row is ever planned.
* **A recipient is never a prospect or a lead.** No `crm.organization_relationship` row
  with `role = 'prospect'` is ever planned.
* **Nothing is invented.** No name, organization, job title or relationship is synthesised
  from an address or a domain.
* **An address with no known owner stays unresolved evidence** — an
  `evidence.assertion` with `resolution = 'unresolved'`, which is the canonical state for
  exactly this, rather than a `crm.contact_point` the operator never confirmed.
* **A historical accepted delivery is evidence of an outbound event**, never of consent,
  interest or qualification. `crm.contact_point` has no consent column by design
  (`docs/DOMAIN.md` §7 #7) and this importer adds none.
* **Promotion to `crm.*` is an operator command** (Slice 2, `docs/MIGRATION.md` §5), not an
  import step. `crm.*` therefore stays at zero rows, and :func:`build_plan` asserts it.

Supplier safety
---------------

A provider contact must not become a marketing prospect merely because campaign history
exists. That is enforced generically, never per address: every planned prior-contact row is
classified against the supplier domains recorded in the Wave 1A bundle's `supplier_master`
and the addresses in `supplier_contact_channel`, using the same
`marketing_supplier_domains.is_supplier_email_domain` the live outbound gate uses. The
classification is reported and attached to the evidence; it is not a new eligibility rule,
because `outbound_v2.eligibility` already owns that decision.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.candidate_export_gate import normalize_export_email
from origenlab_email_pipeline.marketing_supplier_domains import is_supplier_email_domain
from origenlab_email_pipeline.migration.v2_import.artifacts import ImportInputs

#: `outbound.contact_control.source` values the Slice 0 CHECK constraint accepts today
#: (`supabase/migrations/20260905230814_slice0_outbound_tables.sql`).
IMPLEMENTED_CONTACT_CONTROL_SOURCES: frozenset[str] = frozenset(
    {
        "wave1a_union",
        "wave1a_rfc2047_addendum",
        "wave1a_suppression",
        "wave1a_investigation",
        # Added by 20260920190000 so Wave 1B provenance stays separable (DATA.md §7.5.1).
        "wave1b_prior_contact",
        "wave1b_block",
        "send_accepted",
        "ndr_handler",
        "complaint_handler",
        "unsubscribe_handler",
        "operator_command",
    }
)

#: Source labels `docs/DATA.md` §7.5.1 requires for Wave 1B rows. The database does not
#: implement them yet — see `STRUCTURAL_GAPS`.
WAVE1B_PRIOR_CONTACT_SOURCE = "wave1b_prior_contact"
WAVE1B_BLOCK_SOURCE = "wave1b_block"

#: V1 suppression reason codes and the `purpose` the §7.1 truth table gives each. Every
#: observed V1 code is a delivery failure or an explicit do-not-contact, so every one maps
#: to `all`. V1 has no marketing-unsubscribe code; an unknown code is not guessed.
SUPPRESSION_PURPOSE_BY_CODE: Mapping[str, str] = {
    "bounce_no_such_user": "all",
    "bounce_other": "all",
    "bounce_access_denied": "all",
    "manual_do_not_contact": "all",
}

#: V1 `manual_contact_status` values that are hard blocks (`docs/DATA.md` §7.1).
MANUAL_HARD_BLOCK_STATUSES: frozenset[str] = frozenset({"inactive", "hold"})

#: V1 campaign recipient states that mean "selected, never attempted".
CANDIDATE_STATES: frozenset[str] = frozenset({"candidate"})


class MappingRefused(Exception):
    """The artifacts cannot be mapped deterministically. Nothing is written."""


@dataclass(frozen=True)
class StructuralGap:
    """A canonical requirement the database does not implement yet.

    A gap does not stop a dry run — the plan is still computed in full, so the operator can
    see exactly what the decision is worth. It stops the *apply* of the rows it covers.
    """

    key: str
    table: str
    requirement: str
    blocked_rows: str
    why_not_representable: str


#: Structural gaps the database does not implement. **Empty.** The two gaps this importer
#: measured on 2026-09-20 were both closed by migration
#: `20260920190000_slice0_wave1b_source_labels_and_archived_recontact_interval.sql`, which
#: added the Wave 1B source labels and made `recontact_interval_days` optional for an
#: archived campaign. `docs/DATA.md` §7.6.4 records the argument for both.
#:
#: The mechanism is kept rather than deleted: a future wave or column will find one, and a
#: gap must block an apply rather than produce a constraint violation at insert time.
STRUCTURAL_GAPS: tuple[StructuralGap, ...] = ()

#: Gaps that were found and closed, kept so the reasoning is not lost with the blocker.
CLOSED_STRUCTURAL_GAPS: tuple[StructuralGap, ...] = (
    StructuralGap(
        key="contact_control_wave1b_source",
        table="outbound.contact_control",
        requirement=(
            "docs/DATA.md §7.5.1: 'Wave 1B rows load with their own source labels "
            "(wave1b_prior_contact, wave1b_block), so provenance stays separable after "
            "the load'"
        ),
        blocked_rows="every Wave 1B prior-contact row and every Wave 1B address block",
        why_not_representable=(
            "contact_control_source_check was a closed CHECK listing only the four wave1a_* "
            "labels plus the runtime handlers. Loading Wave 1B rows under a wave1a_* label "
            "would have misattributed their provenance, which is the one thing the "
            "canonical sentence exists to prevent. Closed 2026-09-20 by adding both labels; "
            "the vocabulary stays closed, so a further wave is still a migration."
        ),
    ),
    StructuralGap(
        key="campaign_recontact_interval_days",
        table="outbound.campaign",
        requirement=(
            "docs/DATA.md §7.1: the archived V1 campaign loads as one outbound.campaign; "
            "§7.5 adds the two September campaigns"
        ),
        blocked_rows="all three V1 campaigns, and therefore every recipient and attempt",
        why_not_representable=(
            "recontact_interval_days was NOT NULL with CHECK (>= 1) and V1 has no "
            "recontact-interval concept at all — docs/DATA.md §7.1 records zero cooldown "
            "rows carried from V1 for exactly this reason, so any value would have been "
            "invented. Closed 2026-09-20 by a fourth 'archived' carve-out, beside the "
            "approval, content and audience-criteria shapes the table already makes for "
            "these same historical campaigns. A campaign that can still send is unaffected."
        ),
    ),
)


@dataclass(frozen=True)
class PlannedRow:
    """One row the import would write.

    Attributes:
        table: the qualified V2 table.
        key: the natural key the database enforces, used for idempotency. Re-running an
            import conflicts on this key and writes nothing.
        columns: column name → value, exactly as the apply path binds it.
    """

    table: str
    key: tuple[Any, ...]
    columns: dict[str, Any]


@dataclass
class TablePlan:
    """Every planned row for one table, and whether it may be applied."""

    table: str
    rows: list[PlannedRow] = field(default_factory=list)
    blocked_by: str | None = None

    @property
    def applicable(self) -> bool:
        return self.blocked_by is None

    @property
    def count(self) -> int:
        return len(self.rows)


@dataclass
class Reject:
    """One input row the mapping could not use, kept for the private operator artifact.

    ``address`` is recipient-level and therefore never enters an aggregate report or the
    console — only the `0600` reject artifact outside Git.
    """

    origin: str
    reason: str
    address: str | None = None
    detail: str | None = None


@dataclass
class ImportPlan:
    """The complete, deterministic result of mapping one set of artifacts.

    Attributes:
        anchor_dedupe_key: the `evidence.source_record` every assertion of this run hangs
            off. It is the cross-wave reconciliation report, because the assertions describe
            the *combined* baseline rather than either bundle alone. Recorded explicitly so
            the apply path never depends on row order.
    """

    tables: dict[str, TablePlan]
    counts: dict[str, int]
    classifications: dict[str, int]
    rejects: list[Reject]
    gaps: tuple[StructuralGap, ...]
    reconciliation: dict[str, Any]
    anchor_dedupe_key: str = ""

    def table(self, name: str) -> TablePlan:
        return self.tables[name]

    @property
    def applicable_tables(self) -> list[TablePlan]:
        return [t for t in self.tables.values() if t.applicable and t.rows]

    @property
    def blocked_tables(self) -> list[TablePlan]:
        return [t for t in self.tables.values() if not t.applicable and t.rows]


# --------------------------------------------------------------------------- #
# Canonicalization
# --------------------------------------------------------------------------- #


def canonical_address(raw: Any, *, origin: str) -> str | None:
    """Normalize one address to the single migration form, or return ``None``.

    The form is `candidate_export_gate.normalize_export_email` — the same one Wave 1B and
    the cross-wave reconciliation used (`docs/DATA.md` §7.5.2), so an address normalized
    here is the same string the artifacts already agreed on. It is reused rather than
    reimplemented so the importer cannot drift away from the measured unions.
    """
    if not isinstance(raw, str):
        return None
    normalized = normalize_export_email(raw)
    if not normalized:
        return None
    normalized = normalized.strip().lower()
    # The V2 CHECK constraints accept exactly this shape; anything else is a reject, never
    # a silently repaired value.
    if normalized.count("@") != 1:
        return None
    local, _, domain = normalized.partition("@")
    if not local or "." not in domain or any(c.isspace() for c in normalized):
        return None
    return normalized


def canonical_domain(raw: Any) -> str | None:
    """Normalize one domain to the `contact_control` domain shape, or return ``None``."""
    if not isinstance(raw, str):
        return None
    domain = raw.strip().lower().lstrip("@")
    if not domain or "." not in domain or any(c.isspace() for c in domain):
        return None
    if not all(part and all(c.isalnum() or c == "-" for c in part) for part in domain.split(".")):
        return None
    return domain


def dedupe_key(*parts: Any) -> str:
    """Build a stable, collision-resistant `evidence.source_record.dedupe_key`.

    The key is derived only from the parts given, never from a clock or a path, so the same
    artifact always yields the same key and a re-run conflicts instead of duplicating.
    """
    joined = "\x00".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]
    return f"{parts[0]}:{digest}"


def _text(value: Any) -> str | None:
    """Return a trimmed non-empty string, or ``None``. Never returns a blank."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


# --------------------------------------------------------------------------- #
# Supplier and organization evidence
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SupplierIndex:
    """Supplier domains and addresses, indexed once from the Wave 1A bundle.

    Built from `supplier_master.domain_norm` and `supplier_contact_channel.value_normalized`
    — the same primary source `marketing_supplier_domains` documents. Every `domain_norm`
    row counts regardless of `is_exclusion`, matching that module's recorded contract.
    """

    domains: frozenset[str]
    addresses: frozenset[str]

    def classify(self, address: str) -> str | None:
        """Return ``'supplier'`` when this address is a known provider contact."""
        if address in self.addresses:
            return "supplier"
        if is_supplier_email_domain(address, self.domains):
            return "supplier"
        return None


def build_supplier_index(inputs: ImportInputs) -> SupplierIndex:
    """Index the supplier evidence the Wave 1A bundle carries."""
    domains = {
        d
        for row in inputs.wave1a.get("supplier_master", [])
        if (d := canonical_domain(row.get("domain_norm")))
    }
    addresses = {
        a
        for row in inputs.wave1a.get("supplier_contact_channel", [])
        if str(row.get("channel_type", "")).lower() in {"email", "", "none"}
        and (a := canonical_address(row.get("value_normalized"), origin="supplier_contact_channel"))
    }
    return SupplierIndex(domains=frozenset(domains), addresses=frozenset(addresses))


# --------------------------------------------------------------------------- #
# Prior contact
# --------------------------------------------------------------------------- #


def _wave1a_prior_contact(inputs: ImportInputs, rejects: list[Reject]) -> dict[str, str]:
    """Address → Wave 1A source label, for the Wave 1A safety set.

    The contacted union comes from `derived/recipient_ledger.jsonl.gz` rows flagged
    `in_contacted_union`; the three RFC 2047 recovered addresses come from the addendum
    (`docs/DATA.md` §7.4) and keep their own label so provenance stays separable.
    """
    found: dict[str, str] = {}
    for row in inputs.wave1a.get("recipient_ledger", []):
        if not row.get("in_contacted_union"):
            continue
        address = canonical_address(row.get("email_norm"), origin="wave1a recipient_ledger")
        if address is None:
            rejects.append(
                Reject(
                    origin="wave1a/recipient_ledger",
                    reason="address does not normalize to the migration form",
                    address=_text(row.get("email_norm")),
                )
            )
            continue
        found.setdefault(address, "wave1a_union")

    for row in inputs.addendum:
        address = canonical_address(
            row.get("address") or row.get("email_norm"), origin="wave1a addendum"
        )
        if address is None:
            rejects.append(
                Reject(
                    origin="wave1a/rfc2047_addendum",
                    reason="address does not normalize to the migration form",
                    address=_text(row.get("address")),
                )
            )
            continue
        # An addendum address is by definition absent from the union (§7.4); if one is
        # present anyway the union label wins, because that is the stronger evidence.
        found.setdefault(address, "wave1a_rfc2047_addendum")
    return found


def _wave1b_prior_contact(
    inputs: ImportInputs, rejects: list[Reject]
) -> dict[str, tuple[str, ...]]:
    """Address → its `source_categories`, for the Wave 1B combined delta.

    The union is read as the extractor measured it, never recomputed: `docs/DATA.md` §7.5.1
    records that the combined file is independently re-verified at extraction time, and a
    second definition here could only drift from it.
    """
    found: dict[str, tuple[str, ...]] = {}
    for row in inputs.wave1b.get("combined_prior_contact", []):
        address = canonical_address(row.get("address"), origin="wave1b combined_prior_contact")
        if address is None:
            rejects.append(
                Reject(
                    origin="wave1b/combined_prior_contact",
                    reason="address does not normalize to the migration form",
                    address=_text(row.get("address")),
                )
            )
            continue
        categories = row.get("source_categories")
        if isinstance(categories, str):
            categories = [categories]
        found[address] = tuple(sorted(str(c) for c in (categories or [])))
    return found


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #


def _classify_suppression(code: Any) -> tuple[str, bool]:
    """Map a V1 `suppression_reason_code` to `(purpose, needs_review)`.

    Fails closed: a code the §7.1 truth table does not classify loads as `all` and is
    flagged for operator review, never silently as `marketing`.
    """
    key = _text(code)
    if key is None:
        return "all", True
    purpose = SUPPRESSION_PURPOSE_BY_CODE.get(key.lower())
    if purpose is None:
        return "all", True
    return purpose, False


def _address_blocks(
    rows: Iterable[Mapping[str, Any]],
    manual_rows: Iterable[Mapping[str, Any]],
    *,
    source: str,
    origin: str,
    rejects: list[Reject],
) -> dict[str, dict[str, Any]]:
    """Plan address blocks from suppression rows plus manual hard-block statuses."""
    planned: dict[str, dict[str, Any]] = {}
    for row in rows:
        address = canonical_address(row.get("email"), origin=origin)
        if address is None:
            rejects.append(
                Reject(
                    origin=origin,
                    reason="suppressed address does not normalize to the migration form",
                    address=_text(row.get("email")),
                )
            )
            continue
        purpose, needs_review = _classify_suppression(row.get("suppression_reason_code"))
        planned[address] = {
            "purpose": purpose,
            "needs_review": needs_review,
            "reason": f"v1 {_text(row.get('suppression_reason_code')) or 'unclassified'}",
            "source": source,
            "origin_class": "suppression_table",
        }

    for row in manual_rows:
        status = (_text(row.get("status")) or "").lower()
        if status not in MANUAL_HARD_BLOCK_STATUSES:
            continue
        address = canonical_address(row.get("email_norm"), origin=f"{origin} manual")
        if address is None:
            rejects.append(
                Reject(
                    origin=f"{origin}/manual_contact_status",
                    reason="manual hard-block address does not normalize",
                    address=_text(row.get("email_norm")),
                )
            )
            continue
        # A suppression row already covers this address; the manual status adds nothing the
        # block does not already say, and must not widen or narrow it.
        planned.setdefault(
            address,
            {
                "purpose": "all",
                "needs_review": False,
                "reason": f"v1 manual_contact_status {status}",
                "source": source,
                "origin_class": "manual_status",
            },
        )
    return planned


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


def build_plan(inputs: ImportInputs) -> ImportPlan:
    """Map verified artifacts to a complete, deterministic :class:`ImportPlan`.

    No database is contacted, no clock is read and no file is written. The plan is a pure
    function of ``inputs``, so two runs over the same artifacts are identical.
    """
    rejects: list[Reject] = []
    tables: dict[str, TablePlan] = {
        name: TablePlan(table=name)
        for name in (
            "evidence.source_record",
            "evidence.assertion",
            "outbound.contact_control",
            "comms.mailbox",
            "outbound.campaign",
            "outbound.campaign_recipient",
            "outbound.send_attempt",
        )
    }

    suppliers = build_supplier_index(inputs)

    # -- evidence.source_record: one per verified artifact ------------------- #
    # The last identity is the cross-wave reconciliation report; it anchors the assertions
    # because they describe the combined baseline, not either bundle alone.
    anchor_key = ""
    for identity in inputs.identities:
        key = dedupe_key("migration_manifest", identity.name, identity.sha256)
        anchor_key = key
        tables["evidence.source_record"].rows.append(
            PlannedRow(
                table="evidence.source_record",
                key=(key,),
                columns={
                    "kind": "migration_manifest",
                    "dedupe_key": key,
                    "payload": json.dumps(identity.to_report(), sort_keys=True),
                    "payload_sha256": identity.sha256,
                    "review_status": "pending",
                },
            )
        )

    # -- prior contact ------------------------------------------------------- #
    wave1a_prior = _wave1a_prior_contact(inputs, rejects)
    wave1b_prior = _wave1b_prior_contact(inputs, rejects)

    cross_wave = dict.fromkeys(sorted(set(wave1a_prior) | set(wave1b_prior)))
    overlap = set(wave1a_prior) & set(wave1b_prior)

    classifications: Counter[str] = Counter()
    for address in cross_wave:
        in_1a = address in wave1a_prior
        source = wave1a_prior.get(address, WAVE1B_PRIOR_CONTACT_SOURCE)
        blocked = source not in IMPLEMENTED_CONTACT_CONTROL_SOURCES
        role = suppliers.classify(address)
        classifications["supplier_or_provider" if role else "unclassified"] += 1
        classifications["wave1a_sourced" if in_1a else "wave1b_only"] += 1

        row = PlannedRow(
            table="outbound.contact_control",
            key=("address", address, "prior_contact", "marketing"),
            columns={
                "scope": "address",
                "value_norm": address,
                "kind": "prior_contact",
                # An outreach fact is always marketing-scoped: it must never stop a
                # transactional delivery (docs/WORKFLOWS.md §1.6).
                "purpose": "marketing",
                "reason": (
                    "v1 prior contact: "
                    + (",".join(wave1b_prior.get(address, ())) or "wave1a_contacted_union")
                ),
                "source": source,
                "needs_review": False,
                "_blocked_by": None if not blocked else "contact_control_wave1b_source",
                "_classification": role or "unclassified",
            },
        )
        tables["outbound.contact_control"].rows.append(row)

        # The evidence trail: an address with no known owner stays unresolved.
        assertion_key = ("contacted_address", address)
        tables["evidence.assertion"].rows.append(
            PlannedRow(
                table="evidence.assertion",
                key=assertion_key,
                columns={
                    "kind": "contacted_address",
                    "value_norm": address,
                    "value": json.dumps(
                        {
                            "waves": sorted(
                                filter(None, ["wave1a" if in_1a else None,
                                              "wave1b" if address in wave1b_prior else None])
                            ),
                            "source_categories": list(wave1b_prior.get(address, ())),
                            "classification": role or "unclassified",
                        },
                        sort_keys=True,
                    ),
                    # Never 'promoted' or 'linked': promotion to crm.* is an operator
                    # command, and nothing here resolves an identity.
                    "resolution": "unresolved",
                },
            )
        )

    # -- blocks -------------------------------------------------------------- #
    wave1a_blocks = _address_blocks(
        inputs.wave1a.get("contact_email_suppression", []),
        inputs.wave1a.get("manual_contact_status", []),
        source="wave1a_suppression",
        origin="wave1a/contact_email_suppression",
        rejects=rejects,
    )
    wave1b_blocks = _address_blocks(
        inputs.wave1b.get("contact_email_suppression", []),
        inputs.wave1b.get("manual_contact_status", []),
        source=WAVE1B_BLOCK_SOURCE,
        origin="wave1b/contact_email_suppression",
        rejects=rejects,
    )

    for address in sorted(set(wave1a_blocks) | set(wave1b_blocks)):
        # Wave 1A is the earlier, immutable record and wins the label on the measured-zero
        # intersection (docs/DATA.md §7.5.4); the Wave 1B row is retained as the conflict.
        spec = wave1a_blocks.get(address) or wave1b_blocks[address]
        blocked = spec["source"] not in IMPLEMENTED_CONTACT_CONTROL_SOURCES
        tables["outbound.contact_control"].rows.append(
            PlannedRow(
                table="outbound.contact_control",
                key=("address", address, "block", spec["purpose"]),
                columns={
                    "scope": "address",
                    "value_norm": address,
                    "kind": "block",
                    "purpose": spec["purpose"],
                    "reason": spec["reason"],
                    "source": spec["source"],
                    "needs_review": spec["needs_review"],
                    "_blocked_by": None if not blocked else "contact_control_wave1b_source",
                    "_classification": "suppression",
                    "_origin_class": spec["origin_class"],
                },
            )
        )

    for row in inputs.wave1a.get("contact_domain_suppression", []):
        domain = canonical_domain(row.get("domain_norm"))
        if domain is None:
            rejects.append(
                Reject(
                    origin="wave1a/contact_domain_suppression",
                    reason="domain does not normalize to the contact_control domain shape",
                    address=_text(row.get("domain_norm")),
                )
            )
            continue
        tables["outbound.contact_control"].rows.append(
            PlannedRow(
                table="outbound.contact_control",
                key=("domain", domain, "block", "marketing"),
                columns={
                    "scope": "domain",
                    "value_norm": domain,
                    "kind": "block",
                    # §7.1: campaign, supplier and domain exclusions are marketing-only.
                    # V1 domain rows carry only free text, which Wave 1B does not export,
                    # so no row can claim the narrower 'all' on recorded evidence.
                    "purpose": "marketing",
                    "reason": "v1 contact_domain_suppression",
                    "source": "wave1a_suppression",
                    "needs_review": False,
                    "_blocked_by": None,
                    "_classification": "suppression",
                    "_origin_class": "suppression_table",
                },
            )
        )

    # -- organization and supplier evidence, never CRM truth ----------------- #
    _plan_organization_evidence(inputs, tables, suppliers, rejects)

    # -- campaign execution facts -------------------------------------------- #
    _plan_campaigns(inputs, tables, rejects)

    counts = _reconcile(
        inputs,
        tables,
        wave1a_prior,
        wave1b_prior,
        overlap,
        rejects,
        wave1a_blocks=wave1a_blocks,
        wave1b_blocks=wave1b_blocks,
    )

    plan = ImportPlan(
        tables=tables,
        counts=counts,
        classifications=dict(sorted(classifications.items())),
        rejects=rejects,
        gaps=STRUCTURAL_GAPS,
        reconciliation=inputs.reconciliation,
        anchor_dedupe_key=anchor_key,
    )
    _assert_no_crm_rows(plan)
    return plan


def _plan_organization_evidence(
    inputs: ImportInputs,
    tables: dict[str, TablePlan],
    suppliers: SupplierIndex,
    rejects: list[Reject],
) -> None:
    """Plan organization-name and supplier-candidate assertions.

    `institution_name` on a V1 campaign recipient is a *recorded* observation, not an
    invention — so it becomes an `evidence.assertion` of kind `organization_name`, left
    unresolved. It never becomes a `crm.organization`: that is an operator promotion.
    """
    seen: set[str] = set()
    for wave, key in (("wave1a", "outbound_campaign_recipient"), ("wave1b", "outbound_campaign_recipient")):
        source = inputs.wave1a if wave == "wave1a" else inputs.wave1b
        for row in source.get(key, []):
            name = _text(row.get("institution_name"))
            if name is None:
                continue
            value_norm = " ".join(name.split()).lower()
            if value_norm in seen:
                continue
            seen.add(value_norm)
            tables["evidence.assertion"].rows.append(
                PlannedRow(
                    table="evidence.assertion",
                    key=("organization_name", value_norm),
                    columns={
                        "kind": "organization_name",
                        "value_norm": value_norm,
                        "value": json.dumps({"observed_name": name}, sort_keys=True),
                        "resolution": "unresolved",
                    },
                )
            )

    for row in inputs.wave1a.get("supplier_master", []):
        trade_name = _text(row.get("trade_name"))
        domain = canonical_domain(row.get("domain_norm"))
        if trade_name is None and domain is None:
            continue
        value_norm = (domain or " ".join(trade_name.split()).lower())  # type: ignore[union-attr]
        tables["evidence.assertion"].rows.append(
            PlannedRow(
                table="evidence.assertion",
                key=("supplier_candidate", value_norm),
                columns={
                    "kind": "supplier_candidate",
                    "value_norm": value_norm,
                    "value": json.dumps(
                        {"trade_name": trade_name, "domain_norm": domain}, sort_keys=True
                    ),
                    # Pending evidence, never automatic CRM truth (docs/DATA.md §7.2, §8).
                    "resolution": "unresolved",
                },
            )
        )


def _campaign_status(v1_status: Any) -> str:
    """Map a V1 campaign status to the V2 vocabulary.

    Every V1 campaign loads as `archived`: the V2 `campaign` table explicitly carves
    `archived` out of the approval, content and audience-criteria shapes precisely because
    these historical campaigns predate all three (`docs/DATA.md` §7.1, and the comments in
    `20260908120000_slice0_outbound_campaign_content_criteria_exclusions.sql`). Loading one
    as `active` would require approval and content facts that do not exist.
    """
    return "archived"


def _plan_campaigns(
    inputs: ImportInputs,
    tables: dict[str, TablePlan],
    rejects: list[Reject],
) -> None:
    """Plan campaigns, their frozen audience and their send attempts."""
    senders: dict[str, str | None] = {}
    for wave in ("wave1a", "wave1b"):
        source = inputs.wave1a if wave == "wave1a" else inputs.wave1b
        for row in source.get("outbound_campaign", []):
            sender = canonical_address(row.get("sender_email"), origin=f"{wave} campaign sender")
            if sender is None:
                rejects.append(
                    Reject(
                        origin=f"{wave}/outbound_campaign",
                        reason="campaign sender_email does not normalize",
                        address=_text(row.get("sender_email")),
                    )
                )
                continue
            senders.setdefault(sender, _text(row.get("sender_name")))
            campaign_id = _text(row.get("campaign_id"))
            if campaign_id is None:
                rejects.append(
                    Reject(origin=f"{wave}/outbound_campaign", reason="campaign has no campaign_id")
                )
                continue
            tables["outbound.campaign"].rows.append(
                PlannedRow(
                    table="outbound.campaign",
                    key=(campaign_id,),
                    columns={
                        "v1_campaign_id": campaign_id,
                        "name": _text(row.get("name")) or campaign_id,
                        "status": _campaign_status(row.get("status")),
                        "sender_address_norm": sender,
                        "max_sends": int(row.get("target_attempt_count") or 1),
                        # recontact_interval_days has no V1 source — see STRUCTURAL_GAPS.
                        "recontact_interval_days": None,
                        "policy_include_suppliers": False,
                    },
                )
            )

    for sender, display in sorted(senders.items()):
        tables["comms.mailbox"].rows.append(
            PlannedRow(
                table="comms.mailbox",
                key=(sender,),
                columns={
                    "address_norm": sender,
                    "display_name": display,
                    "provider": "gmail",
                    # A migrated historical mailbox is not a production sender and holds no
                    # authorization: both send flags stay false and nothing is authorized.
                    "is_production_sender": False,
                    "authorization_state": "unauthorized",
                },
            )
        )

    state_map = {
        "sent": "sent",
        "bounced": "bounced",
        "replied": "replied",
        "candidate": "snapshotted",
        "selected": "snapshotted",
        "inactive": "excluded",
        "blocked": "excluded",
    }
    for wave in ("wave1a", "wave1b"):
        source = inputs.wave1a if wave == "wave1a" else inputs.wave1b
        for row in source.get("outbound_campaign_recipient", []):
            address = canonical_address(row.get("email_norm") or row.get("email"), origin=wave)
            campaign_id = _text(row.get("campaign_id"))
            if address is None or campaign_id is None:
                rejects.append(
                    Reject(
                        origin=f"{wave}/outbound_campaign_recipient",
                        reason="recipient has no normalizable address or no campaign",
                        address=_text(row.get("email_norm") or row.get("email")),
                    )
                )
                continue
            v1_state = (_text(row.get("state")) or "").lower()
            state = state_map.get(v1_state)
            if state is None:
                rejects.append(
                    Reject(
                        origin=f"{wave}/outbound_campaign_recipient",
                        reason=f"unmapped V1 recipient state {v1_state!r}",
                        address=address,
                    )
                )
                continue
            exclusion_reasons: list[str] = []
            if state == "excluded":
                exclusion_reasons = [
                    "manual_inactive" if v1_state == "inactive" else "block"
                ]
            tables["outbound.campaign_recipient"].rows.append(
                PlannedRow(
                    table="outbound.campaign_recipient",
                    key=(campaign_id, address),
                    columns={
                        "v1_campaign_id": campaign_id,
                        "address_norm": address,
                        "state": state,
                        "exclusion_reasons": exclusion_reasons,
                        # Identity stays unresolved: no contact_point, person or
                        # organization is created or linked by an import.
                        "contact_point_id": None,
                        "person_id": None,
                        "organization_id": None,
                        "_v1_state": v1_state,
                        "_candidate_only": v1_state in CANDIDATE_STATES,
                    },
                )
            )

        for row in source.get("outbound_send_attempt", []):
            address = canonical_address(row.get("email_norm"), origin=wave)
            campaign_id = _text(row.get("campaign_id"))
            if address is None or campaign_id is None:
                rejects.append(
                    Reject(
                        origin=f"{wave}/outbound_send_attempt",
                        reason="attempt has no normalizable address or no campaign",
                        address=_text(row.get("email_norm")),
                    )
                )
                continue
            result = (_text(row.get("result")) or "").lower()
            attempted_at = _text(row.get("attempted_at"))
            if result == "accepted":
                # send_attempt_accepted_shape requires accepted_at. An accepted attempt V1
                # cannot date is evidence we cannot place in time, and inventing an instant
                # would be worse than losing it — the Wave 1B extractor refuses an undateable
                # Sent row for the same reason (docs/DATA.md §7.5.1). Reject, visibly.
                if attempted_at is None:
                    rejects.append(
                        Reject(
                            origin=f"{wave}/outbound_send_attempt",
                            reason="accepted attempt carries no attempted_at and cannot be dated",
                            address=address,
                            detail=_text(row.get("id")),
                        )
                    )
                    continue
                submission, delivery = "accepted", "pending"
            elif result in {"failed", "rejected", "error"}:
                submission, delivery = "rejected", "n/a"
            else:
                rejects.append(
                    Reject(
                        origin=f"{wave}/outbound_send_attempt",
                        reason=f"unmapped V1 attempt result {result!r}",
                        address=address,
                    )
                )
                continue
            tables["outbound.send_attempt"].rows.append(
                PlannedRow(
                    table="outbound.send_attempt",
                    key=(campaign_id, address, _text(row.get("id")) or ""),
                    columns={
                        "v1_campaign_id": campaign_id,
                        "v1_attempt_id": _text(row.get("id")),
                        "address_norm": address,
                        "purpose": "marketing",
                        "submission_state": submission,
                        "delivery_state": delivery,
                        # V1 never minted an RFC 822 id (docs/DATA.md §7.1).
                        "rfc822_message_id": None,
                        "error_class": "permanent" if submission == "rejected" else None,
                        "accepted_at": attempted_at if submission == "accepted" else None,
                    },
                )
            )



def _reconcile(
    inputs: ImportInputs,
    tables: dict[str, TablePlan],
    wave1a_prior: Mapping[str, str],
    wave1b_prior: Mapping[str, tuple[str, ...]],
    overlap: set[str],
    rejects: list[Reject],
    *,
    wave1a_blocks: Mapping[str, Mapping[str, Any]],
    wave1b_blocks: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    """Count every output class and check it against the measured source totals.

    Raises:
        MappingRefused: a planned total disagrees with the count the reconciliation report
            measured. The importer fails closed rather than loading a safety set of the
            wrong size.
    """
    controls = tables["outbound.contact_control"].rows
    prior = [r for r in controls if r.columns["kind"] == "prior_contact"]
    address_blocks = [
        r for r in controls if r.columns["kind"] == "block" and r.columns["scope"] == "address"
    ]
    domain_blocks = [
        r for r in controls if r.columns["kind"] == "block" and r.columns["scope"] == "domain"
    ]

    recipients = tables["outbound.campaign_recipient"].rows
    attempts = tables["outbound.send_attempt"].rows

    # §7.5.4: the measured suppression union counts the suppression *tables*; the manual
    # hard blocks the loader folds in are a separate class. Both figures are reported, and
    # only the table figure is reconciled against the measured union.
    suppression_table_blocks = [
        r for r in address_blocks if r.columns["_origin_class"] == "suppression_table"
    ]
    manual_hard_blocks = [
        r for r in address_blocks if r.columns["_origin_class"] == "manual_status"
    ]

    # Per-wave address-block *inputs*, before the cross-wave union. A suppression row and
    # a manual hard-block status are two different V1 control classes (§7.5.4); they are
    # counted apart here so no figure downstream can silently call one the other.
    def by_class(blocks: Mapping[str, Mapping[str, Any]], origin_class: str) -> int:
        return len([a for a, spec in blocks.items() if spec["origin_class"] == origin_class])

    counts = {
        "input_prior_contact_union": len(prior),
        "input_suppression_union": len(suppression_table_blocks),
        "input_domain_suppression_union": len(domain_blocks),
        "address_blocks_total": len(address_blocks),
        "manual_hard_blocks_folded_in": len(manual_hard_blocks),
        "wave1a_suppression_rows": by_class(wave1a_blocks, "suppression_table"),
        "wave1a_manual_hard_block_statuses": by_class(wave1a_blocks, "manual_status"),
        "wave1a_address_block_inputs": len(wave1a_blocks),
        "wave1b_suppression_rows": by_class(wave1b_blocks, "suppression_table"),
        "wave1b_manual_hard_block_statuses": by_class(wave1b_blocks, "manual_status"),
        "wave1b_address_block_inputs": len(wave1b_blocks),
        "wave1a_prior_contact": len(wave1a_prior),
        "wave1b_prior_contact": len(wave1b_prior),
        "cross_wave_overlap": len(overlap),
        "existing_matched_contact_points": 0,
        "new_unresolved_contact_points": len(prior),
        "existing_matched_organizations": 0,
        "unresolved_organization_identities": len(
            [r for r in tables["evidence.assertion"].rows if r.columns["kind"] == "organization_name"]
        ),
        "supplier_provider_contacts": len(
            [r for r in prior if r.columns["_classification"] == "supplier"]
        ),
        "prospect_or_customer_classified_contacts": 0,
        "unclassified_contacts": len(
            [r for r in prior if r.columns["_classification"] == "unclassified"]
        ),
        "campaigns": tables["outbound.campaign"].count,
        "campaign_memberships": len(recipients),
        "accepted_deliveries": len(
            [r for r in attempts if r.columns["submission_state"] == "accepted"]
        ),
        "failed_attempts": len(
            [r for r in attempts if r.columns["submission_state"] == "rejected"]
        ),
        "candidate_only_memberships": len([r for r in recipients if r.columns["_candidate_only"]]),
        "suppressions": len(address_blocks) + len(domain_blocks),
        "existing_organization_relationships": 0,
        "manual_review_records": len([r for r in address_blocks if r.columns["needs_review"]]),
        "conflicts": len(overlap),
        "rejects": len(rejects),
        "source_records": tables["evidence.source_record"].count,
        "assertions": tables["evidence.assertion"].count,
        "crm_rows": 0,
    }

    # Every reconciled class is checked against the independently measured report. A
    # mismatch is never a warning: a safety set of the wrong size is the one outcome this
    # importer exists to prevent.
    report = inputs.reconciliation
    measured: dict[str, Any] = {
        "cross_wave_safety_union": report.get("counts", {}).get("cross_wave_safety_union"),
        "cross_wave_intersection": report.get("counts", {}).get("cross_wave_intersection"),
        "address_suppression_union": report.get("address_suppression", {}).get(
            "deduplicated_union"
        ),
        "domain_suppression_union": report.get("domain_suppression", {}).get(
            "deduplicated_union"
        ),
    }
    for planned_key, measured_key in (
        ("input_prior_contact_union", "cross_wave_safety_union"),
        ("cross_wave_overlap", "cross_wave_intersection"),
        ("input_suppression_union", "address_suppression_union"),
        ("input_domain_suppression_union", "domain_suppression_union"),
    ):
        expected = measured[measured_key]
        if expected is None:
            raise MappingRefused(
                f"the verified reconciliation report carries no {measured_key}; the importer "
                "cannot confirm the size of the set it would load"
            )
        actual = counts[planned_key]
        if actual != int(expected):
            raise MappingRefused(
                f"{planned_key} is {actual}, but the verified reconciliation report measured "
                f"{measured_key} = {int(expected)}. The importer fails closed rather than "
                "loading a safety set of the wrong size."
            )
    return counts


def _assert_no_crm_rows(plan: ImportPlan) -> None:
    """Prove the invariant that gives this slice its safety: `crm.*` is never written.

    Raises:
        MappingRefused: any planned row targets a `crm.` table.
    """
    offenders = sorted(name for name in plan.tables if name.startswith("crm."))
    if offenders:
        raise MappingRefused(
            "the import plan targets " + ", ".join(offenders) + "; promotion to crm.* is an "
            "operator command (docs/MIGRATION.md §5 slice 2), never an import step"
        )


__all__ = [
    "IMPLEMENTED_CONTACT_CONTROL_SOURCES",
    "STRUCTURAL_GAPS",
    "ImportPlan",
    "MappingRefused",
    "PlannedRow",
    "Reject",
    "StructuralGap",
    "SupplierIndex",
    "TablePlan",
    "build_plan",
    "build_supplier_index",
    "canonical_address",
    "canonical_domain",
    "dedupe_key",
]
