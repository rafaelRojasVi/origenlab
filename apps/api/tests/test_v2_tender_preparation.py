"""Bid preparation without a database: who may call, the body, and the file-link guards.

Every value is invented (public repository). `test_v2_tender_preparation_db.py` runs the command.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.preparation import FILE_KEY, output_storage_path, public_outputs
from origenlab_api.v2.tenders.routes import tender_command_router, tender_read_router

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
CASE = "00000000-0000-4000-8000-0000000000c1"
PREP = "00000000-0000-4000-8000-0000000000d1"


def _op(role: str) -> OperatorIdentity:
    return OperatorIdentity(operator_id=str(uuid.uuid4()), email_norm=f"{role}@example.test",
                            display_name=role, role=role, status="active")


class _Lookup(OperatorLookup):
    def __init__(self) -> None:
        self._by = {o.email_norm: o for o in (_op("sales"), _op("viewer"))}

    def by_email(self, email_norm: str):
        return self._by.get(email_norm)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(tender_command_router)
    app.include_router(tender_read_router)
    app.state.v2_tender_command_repository = object()  # never reached by these tests
    app.state.v2_tender_read_repository = object()
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup())
    return TestClient(app)


def h(role: str = "sales") -> dict[str, str]:
    return {OPERATOR_EMAIL_HEADER: f"{role}@example.test", "Idempotency-Key": "pytest-prep-1"}


def test_a_viewer_may_not_request_a_preparation(client) -> None:
    r = client.post("/v2/commands/prepare-tender-bid", json={"opportunity_id": CASE, "phase": "read"},
                    headers=h("viewer"))
    assert r.status_code == 403


def test_a_viewer_may_not_ask_for_a_file_link(client) -> None:
    r = client.get(f"/v2/tenders/{CASE}/preparations/{PREP}/files/paquete/url", headers=h("viewer"))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "role_may_not_view_bidder"


def test_an_unknown_phase_is_rejected_before_anything_runs(client) -> None:
    r = client.post("/v2/commands/prepare-tender-bid", json={"opportunity_id": CASE, "phase": "write"},
                    headers=h())
    assert r.status_code == 422


@pytest.mark.parametrize("key", ["Paquete", "a.b", "x" * 65])
def test_a_malformed_file_key_is_file_not_found(client, key) -> None:
    r = client.get(f"/v2/tenders/{CASE}/preparations/{PREP}/files/{key}/url", headers=h())
    assert r.status_code == 404 and r.json()["detail"]["code"] == "file_not_found"


def test_file_key_shape() -> None:
    assert FILE_KEY.match("paquete") and FILE_KEY.match("formulario_5-b")
    assert not FILE_KEY.match("") and not FILE_KEY.match("../x")


def test_outputs_are_shown_without_storage_paths() -> None:
    raw = [{"file_key": "paquete", "filename": "paquete.pdf", "kind": "package", "completar_count": 2,
            "storage_path": f"opportunities/{CASE}/bid/{PREP}/paquete.pdf"}]
    assert public_outputs(raw) == [{"file_key": "paquete", "filename": "paquete.pdf", "kind": "package",
                                    "completar_count": 2}]
    assert public_outputs({"not": "a list"}) == []


def test_output_path_is_the_recorded_one_else_the_convention() -> None:
    assert output_storage_path(CASE, PREP, {"file_key": "paquete", "filename": "paquete.pdf"}) == \
        f"opportunities/{CASE}/bid/{PREP}/paquete.pdf"
    assert output_storage_path(CASE, PREP, {"file_key": "k", "filename": "x.docx", "storage_path": "p/q.docx"}) \
        == f"opportunities/{CASE}/bid/{PREP}/k.docx"
    own = f"opportunities/{CASE}/bid/{PREP}/k.xlsx"
    assert output_storage_path(CASE, PREP, {"file_key": "k", "filename": "x", "storage_path": own}) == own
    assert output_storage_path(CASE, PREP, {"file_key": "k", "filename": "x", "storage_path": own + "/../../y"}) is None
    assert output_storage_path(CASE, PREP, {"file_key": "k", "filename": "sin extension"}) is None
