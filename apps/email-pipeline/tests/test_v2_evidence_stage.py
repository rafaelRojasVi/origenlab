"""Tests for the conservative Gmail/Drive evidence staging path.

What these assert is not "the code runs" but "the code refuses". Every refusal below is a
statement about a real person, a real institution or a real mailbox: a staged observation
that quietly became a contact, an address that arrived as a header list and was stored as
one identity, or a manifest that reached a hosted database would each be a different kind
of harm, and the boundary is what prevents them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (  # noqa: I001
    MANIFEST_VERSION,
    PROVIDER_ASSERTION_KINDS,
    PROVIDER_SOURCE_KIND,
    ManifestRefused,
    load_manifest,
    parse_manifest,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.report import (
    build_report,
    render_console,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    GMAIL_STAGEABLE_INTAKE_CLASSES,
)


def _manifest(**overrides) -> dict:
    base = {
        "manifest_version": MANIFEST_VERSION,
        "provider": "gmail",
        "note": "September inbox sweep",
        "records": [
            {
                "external_id": "18f0c1a2b3",
                "source_uri": "gmail://msg/18f0c1a2b3",
                "acquired_at": "2026-09-21T10:00:00Z",
                "payload": {
                    "subject": "Consulta de precios",
                    "intake_class": "primary_evidence",
                    "gmail_labels": ["INBOX", "IMPORTANT"],
                },
                "observations": [
                    {"kind": "contact_address", "value": "Compras@Uni.Example"},
                    {"kind": "organization_name", "value": "  Universidad   Ejemplo "},
                ],
            }
        ],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------------- the manifest


def test_a_valid_gmail_manifest_parses_and_folds_its_values() -> None:
    manifest = parse_manifest(_manifest())
    assert manifest.source_kind == "gmail_message"
    record = manifest.records[0]
    assert record.dedupe_key == "gmail_message:18f0c1a2b3"
    # Folding is what lets a staged observation deduplicate against one already recorded.
    assert [o.value_norm for o in record.observations] == [
        "compras@uni.example",
        "universidad ejemplo",
    ]
    # The value as written is kept beside the folded one; nothing is silently lost.
    assert record.observations[0].value == "Compras@Uni.Example"


def test_the_two_providers_map_to_the_two_new_source_kinds() -> None:
    assert PROVIDER_SOURCE_KIND == {"gmail": "gmail_message", "drive": "drive_file"}


def test_a_gmail_record_may_not_assert_a_document_reference() -> None:
    bad = _manifest()
    bad["records"][0]["observations"] = [
        {"kind": "document_reference", "value": "cotizacion.pdf"}
    ]
    with pytest.raises(ManifestRefused, match="contact_address, organization_name"):
        parse_manifest(bad)


def test_a_drive_record_may_not_assert_a_contact_address() -> None:
    bad = _manifest(provider="drive")
    with pytest.raises(ManifestRefused, match="document_reference, organization_name"):
        parse_manifest(bad)


def test_neither_provider_may_assert_a_contacted_address() -> None:
    # `contacted_address` is a claim about our own outbound history. Only the send ledger
    # may make it; a message in the inbox proving somebody wrote to *us* is not evidence
    # that we wrote to them, and treating it as such would corrupt the suppression baseline.
    for provider in ("gmail", "drive"):
        assert "contacted_address" not in PROVIDER_ASSERTION_KINDS[provider]
        assert "affiliation" not in PROVIDER_ASSERTION_KINDS[provider]


def test_an_address_list_in_one_observation_is_refused() -> None:
    # A `To:` header pasted whole would be stored as a single identity, and every later
    # query would treat that string as one person's address.
    bad = _manifest()
    bad["records"][0]["observations"] = [
        {"kind": "contact_address", "value": "a@uni.example, b@uni.example"}
    ]
    with pytest.raises(ManifestRefused, match="one address, not a header list"):
        parse_manifest(bad)


@pytest.mark.parametrize(
    "value", ["not-an-address", "@uni.example", "compras@", "a@b@c", "  "]
)
def test_a_value_that_is_not_one_address_is_refused(value: str) -> None:
    bad = _manifest()
    bad["records"][0]["observations"] = [{"kind": "contact_address", "value": value}]
    with pytest.raises(ManifestRefused):
        parse_manifest(bad)


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ManifestRefused, match="provider must be one of"):
        parse_manifest(_manifest(provider="outlook"))


def test_a_manifest_of_another_version_is_refused_rather_than_read_leniently() -> None:
    with pytest.raises(ManifestRefused, match="manifest_version"):
        parse_manifest(_manifest(manifest_version=MANIFEST_VERSION + 1))


def test_a_record_with_no_observation_stages_nothing_and_is_refused() -> None:
    bad = _manifest()
    bad["records"][0]["observations"] = []
    with pytest.raises(ManifestRefused, match="stages nothing"):
        parse_manifest(bad)


def test_a_duplicate_external_id_in_one_manifest_is_refused() -> None:
    bad = _manifest()
    bad["records"] = [bad["records"][0], dict(bad["records"][0])]
    with pytest.raises(ManifestRefused, match="appears twice"):
        parse_manifest(bad)


def test_the_same_observation_twice_in_one_record_is_refused() -> None:
    bad = _manifest()
    bad["records"][0]["observations"] = [
        {"kind": "contact_address", "value": "compras@uni.example"},
        {"kind": "contact_address", "value": "COMPRAS@UNI.EXAMPLE"},
    ]
    with pytest.raises(ManifestRefused, match="asserted twice"):
        parse_manifest(bad)


def test_the_manifest_carries_no_resolution_vocabulary_at_all() -> None:
    # There is deliberately no way to express "this is confirmed", "this is person X" or
    # "merge these" in a manifest. Staging proposes; only promotion decides.
    manifest = parse_manifest(_manifest())
    observation = manifest.records[0].observations[0]
    for forbidden in ("resolution", "resolved_kind", "resolved_id", "confirmation"):
        assert not hasattr(observation, forbidden)


def test_an_empty_record_list_is_refused() -> None:
    with pytest.raises(ManifestRefused, match="nothing to stage"):
        parse_manifest(_manifest(records=[]))


def test_a_missing_file_is_refused_without_a_traceback(tmp_path: Path) -> None:
    with pytest.raises(ManifestRefused, match="no such manifest file"):
        load_manifest(tmp_path / "absent.json")


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestRefused, match="unreadable manifest"):
        load_manifest(path)


def test_a_real_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "gmail.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.provider == "gmail"
    assert manifest.observation_count == 2


# --------------------------------------------------------------------- offline boundary


def test_the_package_imports_no_google_client() -> None:
    """The staging path is offline by construction, not by habit.

    A Google client appearing anywhere in this package would mean the tool could acquire
    evidence as a side effect of staging it, and the manifest would stop being the thing a
    human reviews before anything reaches the database.
    """
    import origenlab_email_pipeline.migration.v2_evidence_stage as package

    root = Path(package.__file__).parent
    for module in sorted(root.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        for forbidden in ("googleapiclient", "google.oauth2", "google_auth", "import requests"):
            assert forbidden not in source, f"{module.name} imports {forbidden}"


def test_staging_declares_only_the_two_evidence_tables() -> None:
    from origenlab_email_pipeline.migration.v2_evidence_stage.apply import WRITABLE_TABLES

    assert WRITABLE_TABLES == {"evidence.source_record", "evidence.assertion"}
    assert not any(table.startswith("crm.") for table in WRITABLE_TABLES)
    assert not any(table.startswith("outbound.") for table in WRITABLE_TABLES)


def test_the_apply_path_writes_no_crm_or_outbound_sql() -> None:
    import inspect

    from origenlab_email_pipeline.migration.v2_evidence_stage import apply

    source = inspect.getsource(apply).lower()
    assert "insert into crm." not in source
    assert "insert into outbound." not in source
    assert "update crm." not in source
    # The only `crm.` reads are the before/after row count that proves nothing changed.
    assert "resolution = 'unresolved'" not in source or "'unresolved'" in source


def test_the_target_guard_is_the_importers_own_and_has_no_override() -> None:
    import inspect

    from origenlab_email_pipeline.migration.v2_evidence_stage import apply
    from origenlab_email_pipeline.migration.v2_import import target as import_target

    assert apply.neutralized_libpq_environment is import_target.neutralized_libpq_environment
    source = inspect.getsource(apply)
    for widening in ("--authorize", "allow_hosted", "force_remote", "skip_target_check"):
        assert widening not in source


# ---------------------------------------------------------------------------- reporting


def test_the_report_carries_no_address_document_name_or_organization_name() -> None:
    """A report is pasted into commit messages, terminals and CI logs.

    Every value in a staging manifest is somebody's real address or a real document name.
    The report carries counts and kinds; the manifest on disk is where values live.
    """
    manifest = parse_manifest(_manifest())
    report = build_report(manifest, mode="dry-run", target="127.0.0.1:54332/origenlab_dev", applied=None)
    rendered = render_console(report) + json.dumps(report)
    for secret in ("compras@uni.example", "Compras@Uni.Example", "Universidad Ejemplo"):
        assert secret.lower() not in rendered.lower()
    assert report["manifest"]["observations_by_kind"] == {
        "contact_address": 1,
        "organization_name": 1,
    }


def test_the_report_states_plainly_what_staging_does_not_do() -> None:
    report = build_report(
        parse_manifest(_manifest()), mode="dry-run", target="t", applied=None
    )
    assert report["writes_crm"] is False
    assert report["writes_outbound"] is False
    assert report["resolves_anything"] is False


# ----------------------------------------------------------- the CLI, without a database


def test_a_dry_run_opens_no_connection(tmp_path: Path, capsys, monkeypatch) -> None:
    """The default mode must be provably connection-free.

    The database URL is not even required, and a poisoned one is never parsed: if a dry run
    ever grew a connection, this test fails rather than a hosted database being touched.
    """
    import importlib.util

    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "migration" / "stage_gmail_drive_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("stage_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def _explode(*args, **kwargs):  # pragma: no cover - the point is that it is not called
        raise AssertionError("a dry run opened a database connection")

    monkeypatch.setattr(module, "apply_staging", _explode)
    monkeypatch.setattr(module, "assert_local_target", _explode)

    path = tmp_path / "gmail.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    assert module.main(["--manifest", str(path)]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "Nothing was written" in out


def test_apply_without_a_database_url_is_refused(tmp_path: Path, capsys) -> None:
    import importlib.util

    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "migration" / "stage_gmail_drive_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("stage_cli2", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    path = tmp_path / "gmail.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    assert module.main(["--manifest", str(path), "--apply"]) == 2
    assert "needs --database-url" in capsys.readouterr().err


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.example.supabase.co:5432/postgres",
        "postgresql://u:p@localhost:54332/origenlab_dev",
        "postgresql://u:p@127.0.0.1:54332/db?host=203.0.113.10",
    ],
)
def test_apply_against_anything_but_a_literal_loopback_is_refused(
    tmp_path: Path, capsys, dsn: str
) -> None:
    import importlib.util

    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "migration" / "stage_gmail_drive_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("stage_cli3", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    path = tmp_path / "gmail.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    assert module.main(["--manifest", str(path), "--database-url", dsn, "--apply"]) == 3
    assert "refused" in capsys.readouterr().err


# ------------------------------------------------------- database-backed proofs (opt-in)

from protected_databases import assert_not_protected  # noqa: E402

_TEST_DSN = assert_not_protected(
    os.environ.get("ORIGENLAB_V2_TEST_DSN", "").strip(),
    variable="ORIGENLAB_V2_TEST_DSN",
)
_needs_db = pytest.mark.skipif(not _TEST_DSN, reason="ORIGENLAB_V2_TEST_DSN is not set")


@_needs_db
def test_staging_is_idempotent_and_never_touches_crm() -> None:
    """Stage twice, into a real database, and prove both invariants hold.

    The rows are removed again afterwards so the development database keeps the exact
    contents `docs/STATUS.md` §2.7.5 reconciles.
    """
    import psycopg

    from origenlab_email_pipeline.migration.v2_evidence_stage.apply import apply_staging
    from origenlab_email_pipeline.migration.v2_import.target import assert_local_target

    target = assert_local_target(_TEST_DSN)
    manifest = parse_manifest(
        _manifest(
            records=[
                {
                    "external_id": "pytest-stage-18f0c1",
                    "source_uri": "gmail://msg/pytest-stage-18f0c1",
                    "payload": {
                        "subject": "pytest",
                        "intake_class": "primary_evidence",
                        "gmail_labels": ["INBOX"],
                    },
                    "observations": [
                        {"kind": "contact_address", "value": "pytest-stage@uni.example"},
                        {"kind": "organization_name", "value": "Pytest Stage Institute"},
                    ],
                }
            ]
        )
    )
    try:
        first = apply_staging(manifest, target)
        assert first.source_records_created == 1
        assert first.assertions_created == 2
        assert first.crm_rows_before == first.crm_rows_after

        second = apply_staging(manifest, target)
        assert second.source_records_created == 0
        assert second.assertions_created == 0
        assert second.source_records_already_present == 1
        assert second.assertions_already_present == 2
        assert second.crm_rows_before == second.crm_rows_after

        with psycopg.connect(_TEST_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "select kind, review_status from evidence.source_record where dedupe_key = %s",
                ("gmail_message:pytest-stage-18f0c1",),
            )
            kind, review_status = cur.fetchone()
            assert kind == "gmail_message"
            # Staged, never believed.
            assert review_status == "pending"
            cur.execute(
                "select distinct a.resolution from evidence.assertion a "
                "join evidence.source_record sr on sr.id = a.source_record_id "
                "where sr.dedupe_key = %s",
                ("gmail_message:pytest-stage-18f0c1",),
            )
            assert [r[0] for r in cur.fetchall()] == ["unresolved"]
    finally:
        with psycopg.connect(_TEST_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute(
                "delete from evidence.assertion where source_record_id in "
                "(select id from evidence.source_record where dedupe_key = %s)",
                ("gmail_message:pytest-stage-18f0c1",),
            )
            cur.execute(
                "delete from evidence.source_record where dedupe_key = %s",
                ("gmail_message:pytest-stage-18f0c1",),
            )


@_needs_db
def test_a_record_restaged_with_a_different_uri_is_refused_not_overwritten() -> None:
    import psycopg

    from origenlab_email_pipeline.migration.v2_evidence_stage.apply import (
        ApplyRefused,
        apply_staging,
    )
    from origenlab_email_pipeline.migration.v2_import.target import assert_local_target

    target = assert_local_target(_TEST_DSN)

    def build(uri: str):
        return parse_manifest(
            _manifest(
                records=[
                    {
                        "external_id": "pytest-stage-drift",
                        "source_uri": uri,
                        "payload": {
                            "intake_class": "archived",
                            "gmail_labels": ["IMPORTANT"],
                        },
                        "observations": [
                            {"kind": "contact_address", "value": "drift@uni.example"}
                        ],
                    }
                ]
            )
        )

    try:
        apply_staging(build("gmail://msg/a"), target)
        with pytest.raises(ApplyRefused, match="already staged with source_uri"):
            apply_staging(build("gmail://msg/b"), target)
    finally:
        with psycopg.connect(_TEST_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute(
                "delete from evidence.assertion where source_record_id in "
                "(select id from evidence.source_record where dedupe_key = %s)",
                ("gmail_message:pytest-stage-drift",),
            )
            cur.execute(
                "delete from evidence.source_record where dedupe_key = %s",
                ("gmail_message:pytest-stage-drift",),
            )


# ------------------------------------------- what intake excludes by rule, the loader refuses


def _gmail_record(*, intake_class="archived", labels=None, address="alguien@uni.example"):
    return {
        "external_id": "18f0c1a2b3",
        "source_uri": "gmail://msg/18f0c1a2b3",
        "payload": {
            "subject": "asunto",
            "intake_class": intake_class,
            "gmail_labels": ["IMPORTANT"] if labels is None else labels,
        },
        "observations": [{"kind": "contact_address", "value": address}],
    }


@pytest.mark.parametrize(
    ("label", "why"),
    [
        ("DRAFT", "metadata_only"),
        ("[Gmail]/Borradores", "metadata_only"),
        ("SPAM", "excluded_spam"),
        ("[Gmail]/Spam", "excluded_spam"),
        ("TRASH", "excluded_trash"),
        ("[Gmail]/Papelera", "excluded_trash"),
    ],
)
def test_a_draft_spam_or_trash_label_is_refused_however_the_manifest_labels_it(
    label: str, why: str
) -> None:
    """The labels win over the declaration — a hopeful intake_class cannot smuggle one in."""
    raw = _manifest(records=[_gmail_record(intake_class="archived", labels=[label])])
    with pytest.raises(ManifestRefused, match=why):
        parse_manifest(raw)


def test_an_excluded_label_beside_a_permitted_one_is_still_refused() -> None:
    raw = _manifest(records=[_gmail_record(labels=["IMPORTANT", "UNREAD", "TRASH"])])
    with pytest.raises(ManifestRefused, match="excluded_trash"):
        parse_manifest(raw)


@pytest.mark.parametrize("declared", ["excluded_spam", "excluded_trash", "metadata_only", None, ""])
def test_only_a_stageable_intake_class_may_be_declared(declared) -> None:
    raw = _manifest(records=[_gmail_record(intake_class=declared)])
    with pytest.raises(ManifestRefused, match="intake_class"):
        parse_manifest(raw)


def test_gmail_labels_are_required_evidence_not_an_optional_field() -> None:
    record = _gmail_record()
    del record["payload"]["gmail_labels"]
    with pytest.raises(ManifestRefused, match="gmail_labels"):
        parse_manifest(_manifest(records=[record]))


@pytest.mark.parametrize(
    "address",
    [
        "mailer-daemon@googlemail.com",
        "MAILER-DAEMON@mx0a-0033e401.pphosted.example",
        "Postmaster@uni.example",
        "mail-daemon@host.example",
    ],
)
def test_a_bounce_is_refused_because_a_postmaster_is_not_a_correspondent(address: str) -> None:
    raw = _manifest(records=[_gmail_record(address=address)])
    with pytest.raises(ManifestRefused, match="mail-delivery subsystem"):
        parse_manifest(raw)


def test_both_stageable_classes_are_accepted() -> None:
    for intake_class in ("primary_evidence", "archived"):
        manifest = parse_manifest(_manifest(records=[_gmail_record(intake_class=intake_class)]))
        assert manifest.records[0].payload["intake_class"] == intake_class


def test_the_stageable_classes_are_the_inventory_vocabulary_not_a_second_copy() -> None:
    """If the inventory ever stops calling `archived` a replay candidate, so does staging."""
    from origenlab_email_pipeline.qa.mailbox_intake_inventory import REPLAY_CANDIDATE_CLASSES

    assert REPLAY_CANDIDATE_CLASSES <= GMAIL_STAGEABLE_INTAKE_CLASSES


def test_a_drive_record_is_unaffected_by_the_gmail_intake_rules() -> None:
    manifest = parse_manifest(
        {
            "manifest_version": MANIFEST_VERSION,
            "provider": "drive",
            "records": [
                {
                    "external_id": "1AbC",
                    "payload": {},
                    "observations": [
                        {"kind": "document_reference", "value": "drive://file/1AbC"}
                    ],
                }
            ],
        }
    )
    assert manifest.source_kind == "drive_file"
