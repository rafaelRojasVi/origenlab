"""The release/smoke worker check reads the same reading versions this worker writes."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

from origenlab_worker.bounce import DELIVERY_FAILURE_VALUE_NORM
from origenlab_worker.triage import REQUESTER_VALUE_NORM, VALUE_NORM

REPO = Path(__file__).resolve().parents[3]


def _verify_workers():
    sys.path.insert(0, str(REPO / "scripts/deploy"))
    spec = importlib.util.spec_from_file_location("verify_workers", REPO / "scripts/deploy/verify_workers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_uses_the_versions_the_worker_writes():
    # A hard-coded triage:v1 in the check flagged every message read at v3 as untriaged.
    assert _verify_workers().evidence_value_norms() == {
        "triage": VALUE_NORM, "delivery": DELIVERY_FAILURE_VALUE_NORM, "requester": REQUESTER_VALUE_NORM}


def _hosted_smoke():
    spec = importlib.util.spec_from_file_location("hosted_smoke_under_test", REPO / "apps/api/scripts/hosted_smoke.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_smoke_checks_capture_so_a_revoked_consent_is_not_silent(monkeypatch):
    """Without require_capture a revoked Gmail consent passed every worker condition."""
    hs = _hosted_smoke()
    calls = []

    def worker_conditions(env, since=None, *, require_capture=False):
        calls.append(require_capture)
        conditions = {"heartbeat": True, "sweep_scheduled": True, "triage_caught_up": True}
        if require_capture:
            conditions["capture_recent"] = False
        return conditions

    monkeypatch.setitem(sys.modules, "hosted_env", types.SimpleNamespace(database_env=lambda d: {}))
    monkeypatch.setitem(sys.modules, "verify_workers", types.SimpleNamespace(worker_conditions=worker_conditions))
    versions = "\n".join(p.name.split("_", 1)[0] for p in (REPO / "supabase/migrations").glob("*.sql"))
    monkeypatch.setattr(hs.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=versions, stderr=""))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("OL_PROD_MIGRATOR_PASSWORD", "pytest-not-a-password")
    v = hs.database_checks()
    assert calls == [True]
    assert v.failures == [hs.WORKER_CONDITION_FAILURES["capture_recent"]]
    assert "authorized" in v.failures[0]


def test_capture_recent_measures_the_sync_running_not_mail_arriving():
    """A quiet hour with no new mail must not alarm: the condition reads the mailbox's sync stamp
    (set on every successful run) and its authorization, never comms.message."""
    source = (REPO / "scripts/deploy/verify_workers.py").read_text(encoding="utf-8")
    capture_sql = source[source.index('columns["capture_recent"]'):source.index("select = ")]
    assert "last_synced_at" in capture_sql and "authorization_state = 'authorized'" in capture_sql
    assert "comms.message" not in capture_sql and "internal_date" not in capture_sql
    worker_db = (REPO / "apps/worker/src/origenlab_worker/database.py").read_text(encoding="utf-8")
    advance = worker_db[worker_db.index("def advance_cursor"):worker_db.index("def message_exists")]
    assert "last_synced_at = now()" in advance
