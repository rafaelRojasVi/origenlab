"""Smoke tests for the ``origenlab_email_pipeline.core`` implementation surface.

The former facade layer (``core.config``, ``core.db``, ``core.gmail.*``,
``core.leads.*``, ``core.suppliers.*``, facade twins in ``core.outbound`` /
``core.mart``) was removed in the 2026-08 commercial platform reset. ``core``
now contains real implementation modules only; root-level modules remain the
import path for everything else. These tests guard both facts.

No DB mutations, no network, no email sending — attribute checks and imports only.
"""

from __future__ import annotations

from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parents[1] / "src" / "origenlab_email_pipeline" / "core"


def test_core_real_modules_import() -> None:
    from origenlab_email_pipeline.core import reports_out, safety, step_runner
    from origenlab_email_pipeline.core import research_automation

    assert callable(safety.require_apply_for_mutation)
    assert callable(step_runner.run_step_sequence)
    assert reports_out.__name__ == "origenlab_email_pipeline.core.reports_out"
    assert research_automation.__name__ == "origenlab_email_pipeline.core.research_automation"


def test_core_outbound_real_modules_import() -> None:
    from origenlab_email_pipeline.core.outbound import broad_marketing_contacts
    from origenlab_email_pipeline.core.outbound import do_not_repeat_master

    assert (
        broad_marketing_contacts.__name__
        == "origenlab_email_pipeline.core.outbound.broad_marketing_contacts"
    )
    assert (
        do_not_repeat_master.__name__
        == "origenlab_email_pipeline.core.outbound.do_not_repeat_master"
    )


def test_core_mart_real_modules_import() -> None:
    from origenlab_email_pipeline.core.mart import (
        MartBuildOptions,
        ensure_fast_indexes,
        run_business_mart_build,
    )

    assert MartBuildOptions is not None
    assert callable(ensure_fast_indexes)
    assert callable(run_business_mart_build)


def test_core_facade_layer_stays_removed() -> None:
    removed = (
        "config.py",
        "db.py",
        "sqlite_migrate.py",
        "gmail",
        "leads",
        "suppliers",
        "mart/business_mart.py",
        "mart/business_mart_schema.py",
        "outbound/candidate_export_gate.py",
        "outbound/outbound_core.py",
        "outbound/marketing_supplier_domains.py",
    )
    for rel in removed:
        assert not (_CORE_DIR / rel).exists(), f"facade path resurrected: core/{rel}"
