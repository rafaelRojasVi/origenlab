"""V1-lane campaign loader — validation and runtime safety.

Pure: tests the loader without a database. Every value is invented; the repository is public.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from origenlab_api.v2.v1_lane_campaigns import (
    JSON_PATH,
    V1LaneValidationError,
    V1LaneCampaign,
    as_dict,
    load_v1_lane_campaigns,
    load_v1_lane_html,
    validate_entry,
    validate_file,
)

# ---------------------------------------------------------------------------
# 1. The committed file is valid
# ---------------------------------------------------------------------------


def test_committed_file_passes_strict_validation() -> None:
    """The committed v1_lane_campaigns.json must be strictly valid.

    A typo in the file fails CI here, not silently in production.
    """
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    campaigns = validate_file(data)
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c.key == "cyber-2026-10"
    assert c.name == "Cyber OrigenLab"
    assert c.channel == "v1"
    assert c.send_days == ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09")
    assert c.send_time == "09:30"
    assert c.promo_until == "2026-10-11"


# ---------------------------------------------------------------------------
# 2. Malformed entries are refused
# ---------------------------------------------------------------------------

GOOD = {
    "key": "test-2026",
    "name": "Test Campaign",
    "channel": "v1",
    "send_days": ["2026-10-01", "2026-10-02"],
    "send_time": "09:00",
    "promo_until": "2026-10-03",
}


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"key": ""}, "key"),
        ({"key": "UPPER_CASE"}, "key"),
        ({"key": "space key"}, "key"),
        ({"key": "-starts-with-dash"}, "key"),
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"channel": "v2"}, "channel"),
        ({"channel": None}, "channel"),
        ({"send_time": "9:30"}, "send_time"),
        ({"send_time": "09:60"}, "send_time"),
        ({"send_time": "25:00"}, "send_time"),
        ({"send_days": []}, "send_days"),
        ({"send_days": "2026-10-01"}, "send_days"),
        ({"send_days": ["2026-10-02", "2026-10-01"]}, "ascending"),  # not ascending
        ({"send_days": ["2026-10-01", "2026-10-01"]}, "ascending"),  # duplicate
        ({"promo_until": "2026-09-30"}, "promo_until"),  # before last send day
        ({"promo_until": "bad-date"}, "promo_until"),
        ({"send_days": ["not-a-date"]}, "YYYY-MM-DD"),
    ],
)
def test_malformed_entry_is_refused(override: dict, match: str) -> None:
    entry = {**GOOD, **override}
    with pytest.raises(V1LaneValidationError, match=match):
        validate_entry(entry)


def test_span_over_31_days_is_refused() -> None:
    entry = {**GOOD, "send_days": ["2026-10-01", "2026-11-10"], "promo_until": "2026-11-10"}
    with pytest.raises(V1LaneValidationError, match="31"):
        validate_entry(entry)


def test_span_exactly_31_days_is_accepted() -> None:
    entry = {**GOOD, "send_days": ["2026-10-01", "2026-11-01"], "promo_until": "2026-11-01"}
    result = validate_entry(entry)
    assert result.send_days == ("2026-10-01", "2026-11-01")


def test_promo_until_equal_to_last_send_day_is_accepted() -> None:
    entry = {**GOOD, "promo_until": GOOD["send_days"][-1]}
    result = validate_entry(entry)
    assert result.promo_until == GOOD["send_days"][-1]


# ---------------------------------------------------------------------------
# 3. Runtime: bad file returns [] + logs a warning
# ---------------------------------------------------------------------------


def test_runtime_bad_file_returns_empty_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bad = tmp_path / "v1_lane_campaigns.json"
    bad.write_text('{"campaigns": [{"key": "INVALID KEY"}]}', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_v1_lane_campaigns(bad)
    assert result == []
    assert any("v1_lane_campaigns" in r.message for r in caplog.records)


def test_runtime_missing_file_returns_empty_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    missing = tmp_path / "no_such_file.json"
    with caplog.at_level(logging.WARNING):
        result = load_v1_lane_campaigns(missing)
    assert result == []
    assert caplog.records


def test_runtime_valid_file_loads_correctly(tmp_path: Path) -> None:
    p = tmp_path / "v1_lane_campaigns.json"
    p.write_text(
        json.dumps({
            "campaigns": [
                {
                    "key": "test-2026",
                    "name": "Test",
                    "channel": "v1",
                    "send_days": ["2026-10-01"],
                    "send_time": "08:00",
                    "promo_until": "2026-10-01",
                }
            ]
        }),
        encoding="utf-8",
    )
    result = load_v1_lane_campaigns(p)
    assert len(result) == 1
    assert result[0].key == "test-2026"


# ---------------------------------------------------------------------------
# 4. as_dict serialises correctly
# ---------------------------------------------------------------------------


def test_as_dict_round_trips() -> None:
    c = V1LaneCampaign(
        key="cyber-2026-10",
        name="Cyber OrigenLab",
        channel="v1",
        send_days=("2026-10-05", "2026-10-06"),
        send_time="09:30",
        promo_until="2026-10-11",
    )
    d = as_dict(c)
    assert d == {
        "key": "cyber-2026-10",
        "name": "Cyber OrigenLab",
        "channel": "v1",
        "send_days": ["2026-10-05", "2026-10-06"],
        "send_time": "09:30",
        "promo_until": "2026-10-11",
        "clients_per_day": None,
        "total_clients": None,
        "audience_rule": None,
        "subject": None,
        "html": None,
    }


# ---------------------------------------------------------------------------
# 5. The plan per day and the audience rule
# ---------------------------------------------------------------------------


def test_committed_file_declares_the_cyber_plan_per_day() -> None:
    """How many clients go out each day: the runner's wave files, as counts only."""
    c = validate_file(json.loads(JSON_PATH.read_text(encoding="utf-8")))[0]
    assert c.clients_per_day == (1000, 1000, 1000, 1067, 528)
    assert as_dict(c)["total_clients"] == 4595
    assert c.audience_rule and len(c.audience_rule) <= 400


def test_an_entry_without_a_plan_is_still_valid() -> None:
    c = validate_entry(GOOD)
    assert c.clients_per_day is None and c.audience_rule is None


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"clients_per_day": [1000]}, "clients_per_day"),  # one per send day
        ({"clients_per_day": [1000, 0]}, "clients_per_day"),
        ({"clients_per_day": [1000, -5]}, "clients_per_day"),
        ({"clients_per_day": [1000, True]}, "clients_per_day"),
        ({"clients_per_day": [1000, 1.5]}, "clients_per_day"),
        ({"clients_per_day": [1000, 5001]}, "clients_per_day"),
        ({"clients_per_day": "1000"}, "clients_per_day"),
        ({"audience_rule": "   "}, "audience_rule"),
        ({"audience_rule": "x" * 401}, "audience_rule"),
    ],
)
def test_a_malformed_plan_is_refused(override: dict, match: str) -> None:
    with pytest.raises(V1LaneValidationError, match=match):
        validate_entry({**GOOD, **override})


# ---------------------------------------------------------------------------
# 6. The email, from a content directory outside the repository
# ---------------------------------------------------------------------------


def test_html_is_read_from_the_content_directory(tmp_path: Path) -> None:
    (tmp_path / "v1-lane-test-2026.html").write_text("<p>Hola</p>", encoding="utf-8")
    assert load_v1_lane_html("test-2026", str(tmp_path)) == "<p>Hola</p>"


def test_no_content_directory_or_no_file_means_no_html(tmp_path: Path) -> None:
    assert load_v1_lane_html("test-2026", None) is None
    assert load_v1_lane_html("test-2026", "") is None
    assert load_v1_lane_html("test-2026", str(tmp_path)) is None


def test_an_oversized_or_unreadable_html_is_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "v1-lane-big.html").write_text("x" * (256 * 1024 + 1), encoding="utf-8")
    (tmp_path / "v1-lane-bin.html").write_bytes(b"\xff\xfe\x00bad")
    with caplog.at_level(logging.WARNING):
        assert load_v1_lane_html("big", str(tmp_path)) is None
        assert load_v1_lane_html("bin", str(tmp_path)) is None
    assert "v1_lane_campaigns" in caplog.text


def test_the_committed_cyber_entry_carries_its_subject() -> None:
    c = validate_file(json.loads(JSON_PATH.read_text(encoding="utf-8")))[0]
    assert c.subject == "Cyber OrigenLab · 5% a 10% en productos seleccionados para laboratorio"
    assert as_dict(c)["subject"] == c.subject


@pytest.mark.parametrize("bad", ["   ", "x" * 301, 5])
def test_a_blank_or_long_subject_is_refused(bad) -> None:
    with pytest.raises(V1LaneValidationError, match="subject"):
        validate_entry({**GOOD, "subject": bad})
