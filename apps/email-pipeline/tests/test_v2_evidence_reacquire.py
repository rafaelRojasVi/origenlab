"""Tests for rewriting a version 1 Gmail manifest as version 2 from re-acquired labels.

The subject here is not a format conversion. Manifest version 2 exists so that a draft, a
Spam message or a Trash message is refused on the labels it really carries; an upgrade
that could invent those labels, drop a record it could not explain, or quietly accept a
message id that now points somewhere else would give the rule back its loophole. So every
test below asserts a refusal, or asserts that the records came through unchanged.
"""

from __future__ import annotations

import json

import pytest

from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    MANIFEST_VERSION,
    parse_manifest,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.reacquire import (
    ACQUISITION_VERSION,
    AcquisitionRefused,
    intake_class_for,
    load_acquisition,
    parse_acquisition,
    summarize,
    upgrade_manifest,
)


def _v1_manifest(**overrides) -> dict:
    base = {
        "manifest_version": 1,
        "provider": "gmail",
        "note": "September sweep",
        "records": [
            {
                "external_id": "18f0c1a2b3c4d5e6",
                "source_uri": "gmail://msg/18f0c1a2b3c4d5e6",
                "acquired_at": "2026-09-21T20:05:00Z",
                "payload": {
                    "subject": "Consulta de precios",
                    "from": "compras@uni.example",
                    "from_domain": "uni.example",
                    "message_date": "2026-09-21T17:30:21Z",
                    "direction": "inbound",
                    "read_only": True,
                },
                "observations": [
                    {"kind": "contact_address", "value": "compras@uni.example"},
                    {"kind": "organization_name", "value": "Universidad Ejemplo"},
                ],
            }
        ],
    }
    base.update(overrides)
    return base


def _acquisition(labels=("INBOX", "IMPORTANT"), **overrides) -> dict:
    base = {
        "acquisition_version": ACQUISITION_VERSION,
        "provider": "gmail",
        "acquired_at": "2026-09-22T00:00:00Z",
        "method": "Gmail connector messages.get, METADATA_ONLY, read-only",
        "messages": {
            "18f0c1a2b3c4d5e6": {
                "gmail_labels": list(labels),
                "sender": "compras@uni.example",
                "date": "2026-09-21T17:30:21Z",
            }
        },
    }
    base.update(overrides)
    return base


def _upgrade(manifest=None, acquisition=None) -> dict:
    return upgrade_manifest(
        manifest or _v1_manifest(), parse_acquisition(acquisition or _acquisition())
    )


# --- what a clean upgrade produces -----------------------------------------------------


def test_upgrade_produces_a_manifest_the_loader_accepts() -> None:
    upgraded = _upgrade()
    assert upgraded["manifest_version"] == MANIFEST_VERSION
    loaded = parse_manifest(upgraded)
    assert len(loaded.records) == 1
    assert loaded.observation_count == 2


def test_the_record_is_otherwise_unchanged() -> None:
    before = _v1_manifest()
    upgraded = _upgrade(before)
    original = before["records"][0]
    after = upgraded["records"][0]
    assert after["external_id"] == original["external_id"]
    assert after["source_uri"] == original["source_uri"]
    assert after["acquired_at"] == original["acquired_at"]
    assert after["observations"] == original["observations"]
    # every version 1 payload field survives, with exactly two added
    assert set(after["payload"]) - set(original["payload"]) == {"intake_class", "gmail_labels"}
    for key, value in original["payload"].items():
        assert after["payload"][key] == value


def test_the_input_manifest_is_not_mutated() -> None:
    before = _v1_manifest()
    snapshot = json.dumps(before, sort_keys=True)
    _upgrade(before)
    assert json.dumps(before, sort_keys=True) == snapshot


def test_provenance_of_the_reacquisition_is_recorded() -> None:
    upgraded = _upgrade()
    assert upgraded["reacquired"]["from_manifest_version"] == 1
    assert upgraded["reacquired"]["acquired_at"] == "2026-09-22T00:00:00Z"
    assert "read-only" in upgraded["reacquired"]["method"]


def test_the_summary_names_no_record() -> None:
    rendered = json.dumps(summarize(_upgrade()))
    assert "18f0c1a2b3c4d5e6" not in rendered
    assert "compras@uni.example" not in rendered
    assert "Consulta" not in rendered


# --- the labels decide the intake class, and can refuse the batch ----------------------


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (("INBOX",), "primary_evidence"),
        (("INBOX", "IMPORTANT", "STARRED"), "primary_evidence"),
        (("SENT",), "primary_evidence"),
        (("IMPORTANT",), "archived"),
        (("STARRED", "UNREAD"), "archived"),
    ],
)
def test_intake_class_comes_from_the_labels(labels, expected) -> None:
    assert intake_class_for(labels, where="x") == expected
    upgraded = _upgrade(acquisition=_acquisition(labels=labels))
    assert upgraded["records"][0]["payload"]["intake_class"] == expected


@pytest.mark.parametrize("label", ["DRAFT", "SPAM", "TRASH"])
def test_a_message_intake_excludes_refuses_the_whole_upgrade(label) -> None:
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(acquisition=_acquisition(labels=("INBOX", label)))
    assert "excludes by rule" in str(exc.value)


# --- the two files must describe the same messages -------------------------------------


def test_a_message_the_acquisition_never_looked_at_refuses() -> None:
    acquisition = _acquisition()
    acquisition["messages"] = {}
    with pytest.raises(AcquisitionRefused):
        parse_acquisition(acquisition)

    manifest = _v1_manifest()
    manifest["records"].append(
        {
            "external_id": "deadbeefdeadbeef",
            "payload": {},
            "observations": [{"kind": "contact_address", "value": "a@b.example"}],
        }
    )
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(manifest)
    assert "missing 1 of the manifest's 2 messages" in str(exc.value)


def test_evidence_the_manifest_does_not_hold_is_never_added() -> None:
    acquisition = _acquisition()
    acquisition["messages"]["ffffffffffffffff"] = {"gmail_labels": ["INBOX"]}
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(acquisition=acquisition)
    assert "never adds evidence" in str(exc.value)


def test_a_sender_that_changed_refuses() -> None:
    acquisition = _acquisition()
    acquisition["messages"]["18f0c1a2b3c4d5e6"]["sender"] = "someone.else@uni.example"
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(acquisition=acquisition)
    assert "different sender" in str(exc.value)


def test_a_date_that_changed_refuses() -> None:
    acquisition = _acquisition()
    acquisition["messages"]["18f0c1a2b3c4d5e6"]["date"] = "2020-01-01T00:00:00Z"
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(acquisition=acquisition)
    assert "no longer identifies the same message" in str(exc.value)


def test_a_duplicated_external_id_refuses() -> None:
    manifest = _v1_manifest()
    manifest["records"].append(dict(manifest["records"][0]))
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(manifest)
    assert "appears twice" in str(exc.value)


# --- the shapes of both files ----------------------------------------------------------


def test_only_version_1_is_upgraded() -> None:
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(_v1_manifest(manifest_version=2))
    assert "manifest_version 1 file" in str(exc.value)


def test_a_v1_payload_that_already_declares_the_v2_fields_refuses() -> None:
    manifest = _v1_manifest()
    manifest["records"][0]["payload"]["intake_class"] = "primary_evidence"
    with pytest.raises(AcquisitionRefused) as exc:
        _upgrade(manifest)
    assert "inconsistent with itself" in str(exc.value)


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        ({"acquisition_version": 99}, "acquisition_version must be 1"),
        ({"provider": "drive"}, "provider must be 'gmail'"),
        ({"acquired_at": ""}, "acquired_at is required"),
        ({"method": ""}, "method is required"),
        ({"messages": []}, "messages must be an object"),
    ],
)
def test_an_unusable_acquisition_file_refuses(mutation, needle) -> None:
    with pytest.raises(AcquisitionRefused) as exc:
        parse_acquisition(_acquisition(**mutation))
    assert needle in str(exc.value)


def test_a_message_with_no_labels_refuses() -> None:
    with pytest.raises(AcquisitionRefused) as exc:
        parse_acquisition(_acquisition(labels=()))
    assert "non-empty list" in str(exc.value)


def test_load_acquisition_refuses_a_missing_file(tmp_path) -> None:
    with pytest.raises(AcquisitionRefused) as exc:
        load_acquisition(tmp_path / "nope.json")
    assert "no such acquisition file" in str(exc.value)


def test_load_acquisition_reads_a_real_file(tmp_path) -> None:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(_acquisition()), encoding="utf-8")
    acquisition = load_acquisition(path)
    assert acquisition.provider == "gmail"
    assert acquisition.messages["18f0c1a2b3c4d5e6"].gmail_labels == ("INBOX", "IMPORTANT")


# --- the command-line boundary ---------------------------------------------------------


def _cli():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts/migration/reacquire_gmail_manifest_v2.py"
    spec = importlib.util.spec_from_file_location("reacquire_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_inputs(tmp_path):
    manifest = tmp_path / "v1.json"
    manifest.write_text(json.dumps(_v1_manifest()), encoding="utf-8")
    acquisition = tmp_path / "labels.json"
    acquisition.write_text(json.dumps(_acquisition()), encoding="utf-8")
    return manifest, acquisition


def test_cli_refuses_to_write_inside_the_working_tree(tmp_path, capsys) -> None:
    cli = _cli()
    manifest, acquisition = _write_inputs(tmp_path)
    rc = cli.main(
        [
            "--manifest", str(manifest),
            "--acquisition", str(acquisition),
            "--out", str(cli.REPO_ROOT / "supabase" / "leaked.json"),
        ]
    )
    assert rc == 2
    assert "inside the working tree" in capsys.readouterr().err
    assert not (cli.REPO_ROOT / "supabase" / "leaked.json").exists()


def test_cli_writes_a_private_file_and_refuses_to_clobber_it(tmp_path, capsys) -> None:
    cli = _cli()
    manifest, acquisition = _write_inputs(tmp_path)
    out = tmp_path / "out" / "v2.json"
    argv = [
        "--manifest", str(manifest),
        "--acquisition", str(acquisition),
        "--out", str(out),
    ]
    assert cli.main(argv) == 0
    capsys.readouterr()
    assert json.loads(out.read_text())["manifest_version"] == MANIFEST_VERSION
    assert oct(out.stat().st_mode)[-3:] == "600"

    assert cli.main(argv) == 2
    assert "re-run with --force" in capsys.readouterr().err
    assert cli.main(argv + ["--force"]) == 0
    assert not list(out.parent.glob("*.partial"))
