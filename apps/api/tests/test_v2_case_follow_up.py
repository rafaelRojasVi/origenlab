"""`set-case-follow-up` («Ventas › Seguimiento»): the encargado and the next channel of a case, as a pair.

The in-process tests check the request shape; the database tests run in a disposable
`origenlab_test_<hex>` as the unprivileged `origenlab_api` role. Every value is fictitious.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from origenlab_api.v2.case_commands import (
    ADVANCE_CASE_STAGE,
    SET_CASE_FOLLOW_UP,
    AdvanceCaseStageBody,
    SetCaseFollowUpBody,
)
from origenlab_api.v2.commands import CommandRefused
from v2_command_harness import needs_db

from test_v2_case_command_boundary import (  # noqa: F401 - fixtures and helpers of the case boundary
    CASE_ID,
    _FakeCaseRepo,
    _client,
    _headers,
    _open_case,
    _repo,
    _run,
    disposable_database,
    world,
)

PATH = "/v2/commands/set-case-follow-up"
BODY = {"opportunity_id": CASE_ID, "opportunity_version": 1, "follow_up_operator_id": None, "channel": "llamada"}


def _follow_up(repo, world, case_id, version, operator_id, channel, **kwargs):
    return _run(repo, SET_CASE_FOLLOW_UP, SetCaseFollowUpBody(
        opportunity_id=case_id, opportunity_version=version, follow_up_operator_id=operator_id, channel=channel,
        **({"note": kwargs.pop("note")} if "note" in kwargs else {})), world, **kwargs)


def _row(dsn, case_id):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select follow_up_operator_id::text, follow_up_channel, version from crm.opportunity "
                    "where id = %s", (case_id,))
        return cur.fetchone()


def _events(dsn, case_id):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select payload from crm.domain_event where aggregate_id = %s "
                    "and event_type = 'opportunity.follow_up_set' order by seq", (case_id,))
        return [r[0] for r in cur.fetchall()]


def test_bad_channel_and_extra_fields_are_422_and_both_fields_are_required() -> None:
    repo = _FakeCaseRepo()
    for bad in ({"channel": "sms"}, {"channel": ""}, {"follow_up_operator_id": 7}, {"price": 10}, {"status": "won"}):
        assert _client(repo).post(PATH, json={**BODY, **bad}, headers=_headers()).status_code == 422, bad
    for missing in ("follow_up_operator_id", "channel"):
        without = {k: v for k, v in BODY.items() if k != missing}
        assert _client(repo).post(PATH, json=without, headers=_headers()).status_code == 422, missing
    assert repo.calls == []
    for channel in ("llamada", "correo", "whatsapp", None):
        ok = _client(repo).post(PATH, json={**BODY, "channel": channel}, headers=_headers())
        assert ok.status_code == 200, channel
        assert repo.calls[-1]["fields"]["channel"] == channel


def test_set_case_follow_up_carries_nothing_but_the_case_the_version_the_pair_and_a_note() -> None:
    assert set(SetCaseFollowUpBody.model_fields) == {
        "opportunity_id", "opportunity_version", "follow_up_operator_id", "channel", "note"}


@needs_db
def test_sets_operator_and_channel(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con encargado")
    receipt = _follow_up(repo, world, case_id, version, world["other_operator_id"], "correo")
    assert receipt["command"] == SET_CASE_FOLLOW_UP
    assert receipt["opportunity_version"] == version + 1
    assert (receipt["operator_id"], receipt["channel"]) == (world["other_operator_id"], "correo")
    assert _row(disposable_database, case_id) == (world["other_operator_id"], "correo", version + 1)
    assert _events(disposable_database, case_id) == [{
        "operator_id": world["other_operator_id"], "channel": "correo",
        "previous_operator_id": None, "previous_channel": None}]
    # The second command uses the version the first returned, and records what it replaced.
    _follow_up(repo, world, case_id, receipt["opportunity_version"], world["operator_id"], "whatsapp")
    assert _events(disposable_database, case_id)[-1] == {
        "operator_id": world["operator_id"], "channel": "whatsapp",
        "previous_operator_id": world["other_operator_id"], "previous_channel": "correo"}


@needs_db
def test_clears_to_null(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso que se limpia")
    first = _follow_up(repo, world, case_id, version, world["operator_id"], "llamada")
    second = _follow_up(repo, world, case_id, first["opportunity_version"], None, None)
    assert (second["operator_id"], second["channel"]) == (None, None)
    assert _row(disposable_database, case_id) == (None, None, version + 2)
    assert _events(disposable_database, case_id)[-1] == {
        "operator_id": None, "channel": None,
        "previous_operator_id": world["operator_id"], "previous_channel": "llamada"}


@needs_db
def test_unknown_operator_is_422(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso sin ese encargado")
    with pytest.raises(CommandRefused) as refused:
        _follow_up(repo, world, case_id, version, str(uuid.uuid4()), "llamada")
    assert (refused.value.status_code, refused.value.code) == (422, "follow_up_operator_not_found")
    assert _row(disposable_database, case_id) == (None, None, version)
    assert _events(disposable_database, case_id) == []


@needs_db
def test_closed_case_is_rejected(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso que se cierra")
    closed = _run(repo, ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
        opportunity_id=case_id, opportunity_version=version, stage="abandoned",
        close_reason="Compró a otro proveedor", note="el cliente avisó"), world)
    with pytest.raises(CommandRefused) as refused:
        _follow_up(repo, world, case_id, closed["opportunity_version"], world["operator_id"], "llamada")
    assert refused.value.status_code == 409
    assert _events(disposable_database, case_id) == []


@needs_db
def test_stale_version_is_409(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con versión vieja")
    _follow_up(repo, world, case_id, version, world["operator_id"], "llamada")
    with pytest.raises(CommandRefused) as refused:
        _follow_up(repo, world, case_id, version, None, None)
    assert (refused.value.status_code, refused.value.code) == (409, "case_version_conflict")


@needs_db
def test_idempotent_replay(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso que se repite")
    first = _follow_up(repo, world, case_id, version, world["operator_id"], "llamada", key="follow-up-replay-1")
    again = _follow_up(repo, world, case_id, version, world["operator_id"], "llamada", key="follow-up-replay-1")
    assert again == {**first, "replayed": True} and first["replayed"] is False
    assert len(_events(disposable_database, case_id)) == 1
    assert _row(disposable_database, case_id)[2] == version + 1
