"""freeze_campaign: returns one transaction's intent, or refuses entirely."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from origenlab_email_pipeline.outbound_v2 import (
    AUDIENCE_CRITERIA_VERSION,
    AudienceCriteria,
    CampaignContent,
    CampaignDraft,
    CampaignPolicy,
    ContactControlIndex,
    FreezeRefused,
    RecipientCandidate,
    RecontactOverride,
    build_audience_preview,
    freeze_campaign,
)
from origenlab_email_pipeline.outbound_v2.reasons import (
    REASON_BLOCK,
    REASON_POLICY_NOISE_ADDRESS,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
CRITERIA = AudienceCriteria(source_lane="lead_master")
CONTENT = CampaignContent(subject="Equipamiento de laboratorio", body_text="Hola")
CAMPAIGN = CampaignDraft(campaign_id="camp-1", status="draft", version=3, max_sends=100)


def _preview(candidates=None, controls=None, policy=None, criteria=CRITERIA, overrides=None):
    return build_audience_preview(
        candidates=candidates
        if candidates is not None
        else [RecipientCandidate(address_norm="a@uni.example")],
        criteria=criteria,
        controls=controls or ContactControlIndex(),
        policy=policy or CampaignPolicy(max_sends=100, recontact_interval_days=180),
        now=NOW,
        overrides=overrides,
    )


def _freeze(preview=None, campaign=CAMPAIGN, content=CONTENT, criteria=CRITERIA):
    return freeze_campaign(
        campaign=campaign,
        preview=preview if preview is not None else _preview(),
        content=content,
        criteria=criteria,
        operator_id="op-1",
        now=NOW,
    )


# ── content fingerprint ─────────────────────────────────────────────────────────────────


def test_content_sha256_is_recomputable_from_the_frozen_row() -> None:
    expected = hashlib.sha256("Equipamiento de laboratorio\0Hola\0".encode("utf-8")).hexdigest()
    assert CONTENT.sha256() == expected
    assert _freeze().content_sha256 == expected


def test_a_different_body_is_a_different_fingerprint() -> None:
    other = CampaignContent(subject=CONTENT.subject, body_text="Hola de nuevo")
    assert other.sha256() != CONTENT.sha256()


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(subject="   ", body_text="Hola"),
        dict(subject="Asunto", body_text=""),
        dict(subject="Asunto", body_text="Hola", body_html="  "),
    ],
)
def test_blank_content_is_rejected_at_construction(kwargs) -> None:
    with pytest.raises(ValueError):
        CampaignContent(**kwargs)


# ── the plan ────────────────────────────────────────────────────────────────────────────


def test_the_plan_freezes_status_content_and_criteria_together() -> None:
    plan = _freeze()
    updates = plan.campaign_updates
    assert updates["status"] == "audience_frozen"
    assert updates["subject"] == CONTENT.subject
    assert updates["content_sha256"] == CONTENT.sha256()
    assert updates["content_frozen_at"] == NOW
    assert updates["audience_criteria"] == CRITERIA.to_json()
    assert updates["audience_criteria_version"] == AUDIENCE_CRITERIA_VERSION
    assert updates["audience_frozen_at"] == NOW


def test_the_plan_carries_the_optimistic_concurrency_guard() -> None:
    plan = _freeze()
    assert plan.expected_version == 3
    assert plan.campaign_updates["version"] == 4


def test_rows_carry_state_and_every_reason_sorted_and_deduplicated() -> None:
    controls = ContactControlIndex(blocked_addresses=frozenset({"noreply@uni.example"}))
    plan = _freeze(
        preview=_preview(
            [
                RecipientCandidate(address_norm="a@uni.example"),
                RecipientCandidate(address_norm="noreply@uni.example"),
            ],
            controls=controls,
        )
    )
    rows = {r.address_norm: r for r in plan.recipient_rows}
    assert rows["a@uni.example"].state == "snapshotted"
    assert rows["a@uni.example"].exclusion_reasons == ()
    excluded = rows["noreply@uni.example"]
    assert excluded.state == "excluded"
    assert excluded.exclusion_reasons == tuple(sorted({REASON_BLOCK, REASON_POLICY_NOISE_ADDRESS}))
    assert plan.eligible_count == 1
    assert plan.excluded_count == 1


def test_addresses_are_normalized_into_the_rows() -> None:
    plan = _freeze(preview=_preview([RecipientCandidate(address_norm="A@Uni.Example")]))
    assert plan.recipient_rows[0].address_norm == "a@uni.example"


def test_an_unusable_address_refuses_the_whole_freeze() -> None:
    """A frozen row count is always fully explained by the preview it came from."""
    preview = _preview(
        [
            RecipientCandidate(address_norm="a@uni.example"),
            RecipientCandidate(address_norm="not-an-email", source_ref="lead:4711"),
        ]
    )
    assert len(preview.rows) == 2
    with pytest.raises(FreezeRefused, match="unusable address"):
        _freeze(preview=preview)


def test_the_refusal_names_the_candidates_it_could_not_freeze() -> None:
    preview = _preview(
        [
            RecipientCandidate(address_norm="not-an-email", source_ref="lead:4711"),
            RecipientCandidate(address_norm="also bad", source_ref="lead:4712"),
        ]
    )
    with pytest.raises(FreezeRefused) as excinfo:
        _freeze(preview=preview)
    message = str(excinfo.value)
    assert "2" in message
    assert "lead:4711" in message
    assert "lead:4712" in message


def test_an_override_writes_the_triple_together() -> None:
    controls = ContactControlIndex(prior_contact_addresses=frozenset({"a@uni.example"}))
    preview = _preview(
        [RecipientCandidate(address_norm="a@uni.example")],
        controls=controls,
        overrides={"a@uni.example": RecontactOverride(operator_id="op-1", reason="pidió info")},
    )
    row = _freeze(preview=preview).recipient_rows[0]
    assert row.state == "snapshotted"
    assert row.recontact_override_by_operator_id == "op-1"
    assert row.recontact_override_reason == "pidió info"
    assert row.recontact_override_at == NOW


def test_a_row_without_an_override_carries_no_part_of_the_triple() -> None:
    row = _freeze().recipient_rows[0]
    assert (
        row.recontact_override_by_operator_id,
        row.recontact_override_reason,
        row.recontact_override_at,
    ) == (None, None, None)


# ── refusals ────────────────────────────────────────────────────────────────────────────


def test_a_campaign_that_is_not_draft_is_refused() -> None:
    frozen = CampaignDraft(campaign_id="camp-1", status="audience_frozen", version=4, max_sends=100)
    with pytest.raises(FreezeRefused, match="not 'draft'"):
        _freeze(campaign=frozen)


def test_a_preview_built_from_other_criteria_is_refused() -> None:
    stale = _preview(criteria=AudienceCriteria(source_lane="contact_master_archive"))
    with pytest.raises(FreezeRefused, match="different audience criteria"):
        _freeze(preview=stale)


def test_an_empty_preview_is_refused() -> None:
    with pytest.raises(FreezeRefused, match="empty"):
        _freeze(preview=_preview([]))


def test_exceeding_max_sends_is_refused_rather_than_truncated() -> None:
    small = CampaignDraft(campaign_id="camp-1", status="draft", version=1, max_sends=2)
    preview = _preview(
        [RecipientCandidate(address_norm=f"a{i}@uni.example") for i in range(3)],
        policy=CampaignPolicy(max_sends=2, recontact_interval_days=180),
    )
    with pytest.raises(FreezeRefused, match="exceed max_sends"):
        _freeze(preview=preview, campaign=small)


def test_a_refusal_writes_nothing_and_leaves_the_draft_untouched() -> None:
    before = CAMPAIGN
    with pytest.raises(FreezeRefused):
        _freeze(preview=_preview([]))
    assert before == CampaignDraft(campaign_id="camp-1", status="draft", version=3, max_sends=100)


# ── criteria ────────────────────────────────────────────────────────────────────────────


def test_criteria_fingerprint_is_stable_and_order_independent() -> None:
    a = AudienceCriteria(source_lane="lead_master", sent_folders=("Sent",), limit=50)
    b = AudienceCriteria(limit=50, sent_folders=("Sent",), source_lane="lead_master")
    assert a.fingerprint() == b.fingerprint()


def test_criteria_reject_an_unsupported_version() -> None:
    with pytest.raises(ValueError, match="version"):
        AudienceCriteria(source_lane="lead_master", version=2)


def test_criteria_require_a_source_lane() -> None:
    with pytest.raises(ValueError, match="source_lane"):
        AudienceCriteria(source_lane="  ")
