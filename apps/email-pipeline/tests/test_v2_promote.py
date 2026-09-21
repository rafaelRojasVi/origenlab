"""Tests for the evidence → CRM promotion.

The policy lives in pure functions, so almost all of it is tested without a database. What
these assert is not "the code runs" but "the code refuses to do the things it must never
do", because every one of those refusals is a statement about a real person or a real
organization.
"""

from __future__ import annotations

import pytest

from origenlab_email_pipeline.migration.v2_promote.plan import (
    AssertionRow,
    build_plan,
    deterministic_id,
)
from origenlab_email_pipeline.migration.v2_promote.rules import (
    PUBLIC_MAIL_DOMAINS,
    ROLE_LOCAL_PARTS,
    UNKNOWN_ORGANIZATION_KIND,
    PromotionRefused,
    decide_address,
    looks_like_person_name,
    organization_similarity_key,
    split_address,
)


# --------------------------------------------------------------------------- addresses


@pytest.mark.parametrize("local", ["ventas", "contacto", "compras", "info"])
def test_the_four_named_role_addresses_are_shared_mailboxes_never_persons(local: str) -> None:
    decision = decide_address(f"{local}@laboratorio-ejemplo.cl")
    assert decision.usage == "shared_mailbox"
    assert decision.is_role_mailbox is True
    assert decision.looks_personal is False


def test_a_role_address_is_never_attached_to_an_organization_by_its_domain() -> None:
    # The decision carries no organization at all; DOMAIN.md 2.2 forbids using the domain
    # as an identity key, and the reason says so.
    decision = decide_address("ventas@laboratorio-ejemplo.cl")
    assert "identity key" in decision.reason


def test_a_personal_shaped_address_does_not_become_a_person() -> None:
    decision = decide_address("juan.perez@laboratorio-ejemplo.cl")
    assert decision.usage == "unattributed"
    assert decision.looks_personal is True
    assert "not created" in decision.reason or "is not created" in decision.reason


def test_a_named_personal_address_on_a_public_domain_implies_no_organization() -> None:
    decision = decide_address("juan.perez@gmail.com")
    assert decision.usage == "unattributed"
    assert decision.is_public_domain is True


def test_an_unrecognised_local_part_stays_unknown() -> None:
    decision = decide_address("xq7z@laboratorio-ejemplo.cl")
    assert decision.usage == "unattributed"
    assert decision.is_role_mailbox is False
    assert decision.looks_personal is False
    assert "unknown remains unknown" in decision.reason


def test_usage_is_only_ever_shared_mailbox_or_unattributed() -> None:
    # `personal` and `work` both require a person_id, which promotion never has.
    samples = [
        "ventas@x.cl",
        "juan.perez@x.cl",
        "jperez@x.cl",
        "info@gmail.com",
        "abc123@x.cl",
    ]
    assert {decide_address(s).usage for s in samples} <= {"shared_mailbox", "unattributed"}


@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b@c", "@domain.cl", "local@"])
def test_a_malformed_address_is_refused_rather_than_parsed_harder(bad: str) -> None:
    with pytest.raises(PromotionRefused):
        split_address(bad)


def test_role_and_public_domain_sets_are_lowercase_and_non_overlapping() -> None:
    assert all(p == p.lower() for p in ROLE_LOCAL_PARTS)
    assert all(d == d.lower() for d in PUBLIC_MAIL_DOMAINS)
    assert not (ROLE_LOCAL_PARTS & PUBLIC_MAIL_DOMAINS)


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        ("juan.perez", True),
        ("j.perez", True),
        ("juan_perez", True),
        ("juan-perez", True),
        ("maria.jose.soto", True),
        ("jperez", False),
        ("juan.perez2", False),
        ("ventas", False),
    ],
)
def test_person_name_shape_detection(local: str, expected: bool) -> None:
    assert looks_like_person_name(local) is expected


# ---------------------------------------------------------------------- organizations


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Laboratorio Andes Ltda", "laboratorio andes"),
        ("Clínica Santa María", "Clinica Santa Maria"),
        ("Hospital  del  Salvador", "hospital del salvador"),
        ("Equipos SpA", "EQUIPOS"),
    ],
)
def test_similar_names_fold_to_the_same_key(a: str, b: str) -> None:
    assert organization_similarity_key(a) == organization_similarity_key(b)


def test_distinct_organizations_do_not_fold_together() -> None:
    assert organization_similarity_key("Hospital del Salvador") != organization_similarity_key(
        "Hospital del Carmen"
    )


@pytest.mark.parametrize("bad", ["", "   ", "---", "..."])
def test_a_name_that_folds_to_nothing_is_refused(bad: str) -> None:
    with pytest.raises(PromotionRefused):
        organization_similarity_key(bad)


# ------------------------------------------------------------------------------ plan


def _assertion(kind: str, value_norm: str, observed: str | None = None) -> AssertionRow:
    return AssertionRow(
        id=deterministic_id("test-assertion", f"{kind}:{value_norm}"),
        kind=kind,
        value_norm=value_norm,
        value={"observed_name": observed} if observed else None,
        resolution="unresolved",
        source_record_id=None,
    )


def test_the_plan_never_contains_a_person() -> None:
    plan = build_plan(
        [
            _assertion("contacted_address", "juan.perez@x.cl"),
            _assertion("contacted_address", "ventas@x.cl"),
        ]
    )
    assert len(plan.contact_points) == 2
    assert not hasattr(plan, "persons")
    assert all(cp.usage in {"shared_mailbox", "unattributed"} for cp in plan.contact_points)


def test_similar_organization_names_go_to_review_together_and_are_not_promoted() -> None:
    plan = build_plan(
        [
            _assertion("organization_name", "laboratorio andes ltda", "Laboratorio Andes Ltda"),
            _assertion("organization_name", "laboratorio andes", "Laboratorio Andes"),
        ]
    )
    assert plan.organizations == []
    assert len(plan.reviews) == 2
    assert all("never merged automatically" in r.note for r in plan.reviews)


def test_a_unique_organization_name_is_promoted_with_an_unknown_kind() -> None:
    plan = build_plan(
        [_assertion("organization_name", "hospital del salvador", "Hospital del Salvador")]
    )
    assert len(plan.organizations) == 1
    org = plan.organizations[0]
    assert org.kind == UNKNOWN_ORGANIZATION_KIND
    # The organization carries the name a human typed, not the normalized index value.
    assert org.name == "Hospital del Salvador"


def test_supplier_candidates_are_left_unresolved() -> None:
    plan = build_plan([_assertion("supplier_candidate", "proveedor-ejemplo.cl")])
    assert plan.organizations == []
    assert plan.contact_points == []
    assert plan.counts["supplier_candidates_left_unresolved"] == 1


def test_an_already_resolved_assertion_is_never_re_decided() -> None:
    resolved = AssertionRow(
        id=deterministic_id("test-assertion", "already"),
        kind="contacted_address",
        value_norm="ventas@x.cl",
        value=None,
        resolution="ambiguous",
        source_record_id=None,
    )
    plan = build_plan([resolved])
    assert plan.contact_points == []
    assert plan.reviews == []
    assert plan.counts["assertions_already_resolved"] == 1


def test_the_plan_is_deterministic_regardless_of_input_order() -> None:
    rows = [
        _assertion("contacted_address", "b@x.cl"),
        _assertion("contacted_address", "a@x.cl"),
        _assertion("organization_name", "beta", "Beta"),
        _assertion("organization_name", "alfa", "Alfa"),
    ]
    first = build_plan(rows)
    second = build_plan(list(reversed(rows)))
    assert [cp.contact_point_id for cp in first.contact_points] == [
        cp.contact_point_id for cp in second.contact_points
    ]
    assert [o.organization_id for o in first.organizations] == [
        o.organization_id for o in second.organizations
    ]


def test_identifiers_are_stable_across_processes() -> None:
    # The same evidence must mint the same identifier every time, or the hosted replay can
    # only be reconciled by count and never row by row.
    assert deterministic_id("contact_point", "ventas@x.cl") == deterministic_id(
        "contact_point", "ventas@x.cl"
    )
    assert deterministic_id("contact_point", "ventas@x.cl") != deterministic_id(
        "organization", "ventas@x.cl"
    )


def test_an_unclassifiable_address_is_routed_to_review_not_dropped() -> None:
    plan = build_plan([_assertion("contacted_address", "not-an-address")])
    assert plan.contact_points == []
    assert len(plan.reviews) == 1
    assert "cannot be classified" in plan.reviews[0].note
