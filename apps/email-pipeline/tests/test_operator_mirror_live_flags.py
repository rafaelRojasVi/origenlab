"""Operator mirror live passthrough defaults."""

from __future__ import annotations

from origenlab_email_pipeline.operator_cli.mirror import build_live_dashboard_passthrough


def test_live_dashboard_passthrough_does_not_reopen_equipment_csv_by_default() -> None:
    passthrough = build_live_dashboard_passthrough(
        updated_by="rafael",
        reason="live mirror",
        passthrough=[],
    )

    assert "--include-warm-cases" in passthrough
    assert "--include-commercial-deals" in passthrough
    assert "--include-operator-snapshots" in passthrough
    assert "--include-equipment-opportunities" not in passthrough


def test_legacy_equipment_csv_backfill_can_still_be_requested_explicitly() -> None:
    passthrough = build_live_dashboard_passthrough(
        updated_by="rafael",
        reason="legacy equipment backfill",
        passthrough=["--include-equipment-opportunities"],
    )

    assert "--include-equipment-opportunities" in passthrough
    assert passthrough.count("--include-equipment-opportunities") == 1
