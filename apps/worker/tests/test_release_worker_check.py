"""The release/smoke worker check reads the same reading versions this worker writes."""

from __future__ import annotations

import importlib.util
import sys
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
