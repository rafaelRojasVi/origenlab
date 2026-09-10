"""build_audience_preview: reviewable, deterministic, writes nothing."""

from __future__ import annotations

from datetime import datetime, timezone

from origenlab_email_pipeline.outbound_v2 import (
    AudienceCriteria,
    CampaignPolicy,
    ContactControlIndex,
    RecipientCandidate,
    RecontactOverride,
    build_audience_preview,
)
from origenlab_email_pipeline.outbound_v2.reasons import (
    REASON_ALREADY_IN_AUDIENCE,
    REASON_BLOCK,
    REASON_POLICY_NOISE_ADDRESS,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
CRITERIA = AudienceCriteria(source_lane="lead_master", sent_folders=("Sent", "[Gmail]/Sent Mail"))


def _policy(**kwargs) -> CampaignPolicy:
    base = dict(max_sends=100, recontact_interval_days=180)
    base.update(kwargs)
    return CampaignPolicy(**base)


def _preview(candidates, controls=None, policy=None, overrides=None):
    return build_audience_preview(
        candidates=candidates,
        criteria=CRITERIA,
        controls=controls or ContactControlIndex(),
        policy=policy or _policy(),
        now=NOW,
        overrides=overrides,
    )


def test_preview_reports_counts_and_carries_the_criteria_fingerprint() -> None:
    preview = _preview(
        [
            RecipientCandidate(address_norm="a@uni.example"),
            RecipientCandidate(address_norm="noreply@uni.example"),
        ]
    )
    assert preview.eligible_count == 1
    assert preview.excluded_count == 1
    assert preview.counts_by_reason == {REASON_POLICY_NOISE_ADDRESS: 1}
    assert preview.criteria_fingerprint == CRITERIA.fingerprint()


def test_rows_keep_the_caller_ranking() -> None:
    addresses = ["c@uni.example", "a@uni.example", "b@uni.example"]
    preview = _preview([RecipientCandidate(address_norm=a) for a in addresses])
    assert [r.candidate.address_norm for r in preview.rows] == addresses


def test_a_repeated_address_is_dropped_not_excluded_twice() -> None:
    preview = _preview(
        [
            RecipientCandidate(address_norm="a@uni.example"),
            RecipientCandidate(address_norm="A@Uni.Example"),
        ]
    )
    assert len(preview.rows) == 1
    assert preview.duplicate_addresses_dropped == 1
    assert preview.eligible_count == 1


def test_one_destination_per_person_the_highest_ranked_wins() -> None:
    preview = _preview(
        [
            RecipientCandidate(address_norm="primera@uni.example", person_id="p-1"),
            RecipientCandidate(address_norm="segunda@uni.example", person_id="p-1"),
            RecipientCandidate(address_norm="otra@uni.example", person_id="p-2"),
        ]
    )
    verdicts = {r.candidate.address_norm: r.verdict for r in preview.rows}
    assert verdicts["primera@uni.example"].eligible is True
    assert verdicts["segunda@uni.example"].reasons == (REASON_ALREADY_IN_AUDIENCE,)
    assert verdicts["otra@uni.example"].eligible is True
    assert preview.eligible_count == 2


def test_an_excluded_row_never_takes_the_persons_slot() -> None:
    controls = ContactControlIndex(blocked_addresses=frozenset({"primera@uni.example"}))
    preview = _preview(
        [
            RecipientCandidate(address_norm="primera@uni.example", person_id="p-1"),
            RecipientCandidate(address_norm="segunda@uni.example", person_id="p-1"),
        ],
        controls=controls,
    )
    verdicts = {r.candidate.address_norm: r.verdict for r in preview.rows}
    assert verdicts["primera@uni.example"].reasons == (REASON_BLOCK,)
    assert verdicts["segunda@uni.example"].eligible is True


def test_candidates_without_a_person_never_share_a_slot() -> None:
    preview = _preview(
        [
            RecipientCandidate(address_norm="a@uni.example"),
            RecipientCandidate(address_norm="b@uni.example"),
        ]
    )
    assert preview.eligible_count == 2


def test_budget_overflow_is_reported_not_silently_truncated() -> None:
    preview = _preview(
        [RecipientCandidate(address_norm=f"a{i}@uni.example") for i in range(4)],
        policy=_policy(max_sends=3),
    )
    assert preview.eligible_count == 4
    assert len(preview.rows) == 4
    assert preview.exceeds_max_sends is True


def test_counts_by_reason_counts_a_row_once_per_reason() -> None:
    controls = ContactControlIndex(blocked_addresses=frozenset({"noreply@uni.example"}))
    preview = _preview([RecipientCandidate(address_norm="noreply@uni.example")], controls=controls)
    assert preview.counts_by_reason == {REASON_BLOCK: 1, REASON_POLICY_NOISE_ADDRESS: 1}
    assert preview.excluded_count == 1


def test_overrides_are_applied_by_address() -> None:
    controls = ContactControlIndex(prior_contact_addresses=frozenset({"a@uni.example"}))
    overrides = {"a@uni.example": RecontactOverride(operator_id="op-1", reason="pidió info")}
    preview = _preview([RecipientCandidate(address_norm="a@uni.example")], controls=controls)
    assert preview.eligible_count == 0
    with_override = _preview(
        [RecipientCandidate(address_norm="a@uni.example")],
        controls=controls,
        overrides=overrides,
    )
    assert with_override.eligible_count == 1


def test_the_same_inputs_produce_the_same_preview() -> None:
    candidates = [
        RecipientCandidate(address_norm="a@uni.example", person_id="p-1"),
        RecipientCandidate(address_norm="noreply@uni.example"),
    ]
    assert _preview(candidates) == _preview(candidates)
