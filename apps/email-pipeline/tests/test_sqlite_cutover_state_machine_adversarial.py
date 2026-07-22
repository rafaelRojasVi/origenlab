"""Post-closure adversarial state-machine and journal transition tests.

Not PR-G. Does not authorize a cutover. SyntheticWorld / tmp only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CUTOVER_SCHEMA_VERSION,
    PERMANENTLY_ABANDONED_MIDS,
    STAGE_ORDER,
    CutoverError,
    CutoverFailureCategory,
    CutoverJournal,
    CutoverStage,
    abort_before_swap,
    apply_stage,
    attempt_rollback_before_writers,
    load_journal,
    next_stage,
    previous_stage,
    rollback_finalize,
    write_journal,
)
from sqlite_adversarial_support import (
    JULY19_MID,
    MAIN_SHA,
    MID,
    assert_no_forbidden,
    backup_staging,
    journal_dict_skeleton,
    make_opts,
    make_world,
)


def test_stage_order_excludes_abandoned_and_is_unique() -> None:
    assert CutoverStage.ABANDONED not in STAGE_ORDER
    assert STAGE_ORDER[0] is CutoverStage.PLAN_PREFLIGHT
    assert STAGE_ORDER[-1] is CutoverStage.COMPLETED
    values = [s.value for s in STAGE_ORDER]
    assert len(values) == len(set(values))
    assert CutoverStage.ABANDONED.value not in values


def test_next_previous_stage_boundaries() -> None:
    assert previous_stage(CutoverStage.PLAN_PREFLIGHT) is None
    assert next_stage(CutoverStage.COMPLETED) is None
    assert next_stage(CutoverStage.PLAN_PREFLIGHT) is CutoverStage.PAUSE_WRITERS
    with pytest.raises(ValueError):
        next_stage(CutoverStage.ABANDONED)


@pytest.mark.parametrize(
    "bad_stage",
    [
        CutoverStage.ATOMIC_SWAP,
        CutoverStage.COMPLETED,
        CutoverStage.CREATE_CURRENT_BACKUP,
        CutoverStage.READONLY_SMOKE,
    ],
)
def test_missing_journal_refuses_non_pause_stage(
    tmp_path: Path, bad_stage: CutoverStage
) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    opts = make_opts(
        world,
        prod,
        reports,
        fp,
        stage=bad_stage,
        apply=True,
        approve_swap=bad_stage
        in {CutoverStage.APPROVE_SWAP, CutoverStage.ATOMIC_SWAP},
        backup=backup,
        staging=staging,
    )
    with pytest.raises(CutoverError) as ei:
        apply_stage(opts)
    assert ei.value.category in {
        CutoverFailureCategory.AMBIGUOUS,
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.PREFLIGHT,
    }
    assert not (prod.parent / ".origenlab_cutover_journals").exists() or not any(
        (prod.parent / ".origenlab_cutover_journals").glob("*.journal.json")
    )


def test_skip_ahead_refuses_non_sequential(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.CREATE_CURRENT_BACKUP,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "non-sequential" in str(ei.value).lower() or ei.value.category in {
        CutoverFailureCategory.AMBIGUOUS,
        CutoverFailureCategory.SAFETY,
    }
    journal = load_journal(
        world,
        prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json",
    )
    assert journal is not None
    assert journal.stage == CutoverStage.PAUSE_WRITERS.value


def test_wrong_main_sha_refuses_before_mutation(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    bad = "0" * 40
    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.PAUSE_WRITERS,
                apply=True,
                backup=backup,
                staging=staging,
                main_sha=bad,
            )
        )
    assert ei.value.category in {
        CutoverFailureCategory.PREFLIGHT,
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.VERIFY,
    }


def test_wrong_fingerprint_refuses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                "deadbeef-wrong-fingerprint",
                stage=CutoverStage.PAUSE_WRITERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )


def test_july19_mid_refuses_all_mutating_ops(tmp_path: Path) -> None:
    assert JULY19_MID in PERMANENTLY_ABANDONED_MIDS
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    for stage in (
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
        CutoverStage.ATOMIC_SWAP,
    ):
        with pytest.raises(CutoverError) as ei:
            apply_stage(
                make_opts(
                    world,
                    prod,
                    reports,
                    fp,
                    stage=stage,
                    apply=True,
                    approve_swap=stage is CutoverStage.ATOMIC_SWAP,
                    backup=backup,
                    staging=staging,
                    maintenance_id=JULY19_MID,
                )
            )
        assert ei.value.category is CutoverFailureCategory.SAFETY


def test_malformed_journal_schema_version_refuses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    raw = json.loads(world.read_text(jpath))
    raw["schema_version"] = CUTOVER_SCHEMA_VERSION + 99
    world.write_text_atomic(jpath, json.dumps(raw))
    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert ei.value.category is CutoverFailureCategory.AMBIGUOUS
    assert "schema_version" in str(ei.value)


def test_malformed_journal_json_refuses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    jdir = prod.parent / ".origenlab_cutover_journals"
    jdir.mkdir(parents=True, exist_ok=True)
    jpath = jdir / f"{MID}.journal.json"
    world.files[str(jpath)] = b"{not-json"
    world.modes[str(jpath)] = 0o644
    backup, staging = backup_staging(root)
    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert ei.value.category is CutoverFailureCategory.AMBIGUOUS


def test_bool_as_int_journal_fields_lock_progression(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    raw = json.loads(world.read_text(jpath))
    # Inject int-as-bool for a PR-C safety field if present, else writers_resumed.
    raw["writers_resumed"] = 1
    raw["writer_resume_started"] = 0
    world.write_text_atomic(jpath, json.dumps(raw))
    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert ei.value.category in {
        CutoverFailureCategory.AMBIGUOUS,
        CutoverFailureCategory.SAFETY,
    }


def test_unknown_stage_enum_in_journal_refuses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    raw = json.loads(world.read_text(jpath))
    raw["stage"] = "not_a_real_stage"
    world.write_text_atomic(jpath, json.dumps(raw))
    with pytest.raises((CutoverError, ValueError)) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    # Must not silently advance.
    reloaded = json.loads(world.read_text(jpath))
    assert reloaded["stage"] == "not_a_real_stage"
    if isinstance(ei.value, CutoverError):
        assert ei.value.category in {
            CutoverFailureCategory.AMBIGUOUS,
            CutoverFailureCategory.SAFETY,
        }


def test_abandoned_cannot_progress_to_completed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    journal = load_journal(world, jpath)
    assert journal is not None
    journal.stage = CutoverStage.ABANDONED.value
    journal.abandoned = True
    journal.rollback_verified = True
    journal.rollback_finalized = True
    write_journal(world, jpath, journal)
    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.COMPLETED,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert ei.value.category in {
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.AMBIGUOUS,
    }
    reloaded = load_journal(world, jpath)
    assert reloaded is not None
    assert reloaded.stage == CutoverStage.ABANDONED.value
    assert reloaded.abandoned is True


def test_completed_journal_refuses_further_apply(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    journal = load_journal(world, jpath)
    assert journal is not None
    journal.stage = CutoverStage.COMPLETED.value
    write_journal(world, jpath, journal)
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.RESUME_WRITERS_COMMIT,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )


def test_mixed_maintenance_id_in_journal_refuses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    raw = json.loads(world.read_text(jpath))
    raw["maintenance_id"] = "otherMid99999999"
    if isinstance(raw.get("approved_plan"), dict):
        raw["approved_plan"]["maintenance_id"] = MID
    world.write_text_atomic(jpath, json.dumps(raw))
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )


def test_repeat_pause_writers_is_idempotent_or_refuses_cleanly(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    opts = make_opts(
        world,
        prod,
        reports,
        fp,
        stage=CutoverStage.PAUSE_WRITERS,
        apply=True,
        backup=backup,
        staging=staging,
    )
    apply_stage(opts)
    # Second pause_writers should not silently create a second plan / corrupt stage.
    try:
        apply_stage(opts)
    except CutoverError as exc:
        assert_no_forbidden(str(exc))
        assert exc.category in {
            CutoverFailureCategory.AMBIGUOUS,
            CutoverFailureCategory.SAFETY,
            CutoverFailureCategory.PREFLIGHT,
            CutoverFailureCategory.APPLY,
        }
    journal = load_journal(
        world,
        prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json",
    )
    assert journal is not None
    assert journal.stage in {
        CutoverStage.PAUSE_WRITERS.value,
        CutoverStage.STOP_READERS.value,
    }


def test_rollback_finalize_without_proof_refuses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.PLAN_PREFLIGHT,
                apply=True,
                approve_swap=True,
                backup=backup,
                staging=staging,
            )
        )
    assert ei.value.category in {
        CutoverFailureCategory.AMBIGUOUS,
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.PREFLIGHT,
        CutoverFailureCategory.VERIFY,
    }
    journal = load_journal(
        world,
        prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json",
    )
    assert journal is not None
    assert journal.stage != CutoverStage.ABANDONED.value
    assert journal.stage != CutoverStage.COMPLETED.value
