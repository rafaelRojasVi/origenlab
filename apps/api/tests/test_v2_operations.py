"""«Operaciones» status cards: thresholds and Spanish summaries (pure, no database)."""

from __future__ import annotations

import pytest

from origenlab_api.v2 import operations as op


@pytest.mark.parametrize("f,status", [
    ({"authorization_state": None}, "bad"),
    ({"authorization_state": "revoked", "minutes_since_sync": 1}, "bad"),
    ({"authorization_state": "authorized", "minutes_since_sync": 1, "today": 3}, "ok"),
    ({"authorization_state": "authorized", "minutes_since_sync": 6, "today": 3}, "warn"),
    ({"authorization_state": "authorized", "minutes_since_sync": 21, "today": 3}, "bad"),
    ({"authorization_state": "authorized", "minutes_since_sync": None, "today": 0}, "bad"),
])
def test_correo(f, status) -> None:
    c = op.correo_card({"last_synced_at": None, "events": [], **f})
    assert (c["key"], c["status"]) == ("correo", status)
    assert c["summary"]


def test_correo_without_a_mailbox_says_so() -> None:
    assert op.correo_card({"authorization_state": None})["summary"] == "Gmail no configurado"


@pytest.mark.parametrize("waiting,oldest,status", [(0, None, "ok"), (2, 10, "ok"), (2, 16, "warn"), (21, 30, "bad")])
def test_clasificacion(waiting, oldest, status) -> None:
    c = op.clasificacion_card({"waiting": waiting, "oldest_wait_min": oldest, "today": 0, "newest_at": None, "events": []})
    assert (c["key"], c["status"]) == ("clasificacion", status)


@pytest.mark.parametrize("pending,over_a_day,status", [(0, 0, "ok"), (3, 0, "ok"), (3, 1, "warn")])
def test_cotizaciones_and_drive(pending, over_a_day, status) -> None:
    f = {"pending": pending, "pending_over_a_day": over_a_day, "today": 0, "newest_at": None, "events": []}
    assert op.cotizaciones_card(f)["status"] == status
    assert op.drive_card(f)["status"] == status


def test_reglas() -> None:
    assert op.reglas_card(None, 0)["status"] == "info"  # commands not mounted in this process
    assert op.reglas_card({"enabled": False, "last_run": None}, 0)["status"] == "warn"
    restarted = op.reglas_card({"enabled": True, "last_run": None}, 0)
    assert restarted["status"] == "warn" and restarted["summary"].startswith("Aún no")
    assert op.reglas_card({"enabled": True, "last_run": {"minutes_ago": 3, "applied": 1}}, 4)["status"] == "ok"
    assert op.reglas_card({"enabled": True, "last_run": {"minutes_ago": 16, "applied": 0}}, 0)["status"] == "bad"


def test_version_and_respaldo_are_informational() -> None:
    v = op.version_card("a" * 40)
    assert v["status"] == "info" and "aaaaaaa" in v["summary"]
    assert op.version_card(None)["summary"]
    assert op.respaldo_card()["status"] == "info"


def test_every_card_has_the_same_shape() -> None:
    cards = [op.correo_card({"authorization_state": None}), op.reglas_card(None, 0), op.version_card(None),
             op.respaldo_card(), op.clasificacion_card({}), op.cotizaciones_card({}), op.drive_card({})]
    for c in cards:
        assert set(c) == {"key", "title", "status", "summary", "at", "facts", "events"}, c["key"]
        assert c["status"] in {"ok", "warn", "bad", "info"}
