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


def test_the_rules_card_measures_the_last_pass_against_the_database_clock() -> None:
    state = {"enabled": True, "last_run": {"at": "2026-10-13T12:00:00+00:00", "applied": 2}}
    out = op.rules_state_for_card(state, "2026-10-13T12:17:30+00:00")
    assert out["last_run"]["minutes_ago"] == 17
    assert op.reglas_card(out, 2)["status"] == "bad"
    assert op.rules_state_for_card(None, "2026-10-13T12:00:00+00:00") is None


def test_every_mail_rule_is_explained_on_the_operaciones_page() -> None:
    # The page's Spanish guide (apps/dashboard/src/crm/operations.ts) must name every rule the API has.
    import re
    from pathlib import Path

    from origenlab_api.v2.mail_rules import RULE_IDS

    guide = (Path(__file__).resolve().parents[2] / "dashboard/src/crm/operations.ts").read_text(encoding="utf-8")
    block = guide[guide.index("export const RULE_GUIDE"):guide.index("const RULE_WHY")]
    assert re.findall(r"^  (R\d+): \{", block, re.M) == list(RULE_IDS)


def test_the_rules_card_is_red_when_the_switch_is_blocked_and_amber_on_refusals() -> None:
    blocked = op.reglas_card({"enabled": True, "blocked": "Admin ya no es administrador",
                              "last_run": {"minutes_ago": 2, "applied": 0, "skipped": "operator_not_admin"}}, 0)
    assert blocked["status"] == "bad" and "administrador" in blocked["summary"]
    skipped = op.reglas_card({"enabled": True, "last_run": {"minutes_ago": 2, "skipped": "operator_not_admin"}}, 0)
    assert skipped["status"] == "bad"
    refused = op.reglas_card({"enabled": True, "last_run": {"minutes_ago": 2, "applied": 1, "refused": 2}}, 1)
    assert refused["status"] == "warn"
    out = op.rules_state_for_card({"enabled": True, "blocked": "x", "last_run": {"at": "2026-10-13T12:00:00+00:00",
                                   "skipped": "operator_not_admin", "refused": 1}}, "2026-10-13T12:01:00+00:00")
    assert (out["blocked"], out["last_run"]["skipped"], out["last_run"]["refused"]) == ("x", "operator_not_admin", 1)


def test_the_facts_are_shared_for_a_short_while_across_requests() -> None:
    calls = []
    cache = op.FactsCache(ttl_seconds=30, clock=lambda: now[0])
    now = [1000.0]
    load = lambda: calls.append(1) or {"n": len(calls)}  # noqa: E731
    assert cache.get(load) == {"n": 1}
    now[0] += 29
    assert cache.get(load) == {"n": 1} and len(calls) == 1
    now[0] += 2
    assert cache.get(load) == {"n": 2}


def test_sent_quotes_without_case_read_the_number_from_the_pdf_and_skip_known_ones() -> None:
    rows = [
        {"source_record_id": "s-1", "sent_at": "2026-10-01T15:00:00+00:00", "thread_id": "t-1",
         "filename": "CN09877-Persona Ficticia.pdf"},
        {"source_record_id": "s-2", "sent_at": "2026-10-02T15:00:00+00:00", "thread_id": "t-2",
         "filename": "CN09876-Empresa Ficticia SA.pdf"},          # number already a CRM quote
        {"source_record_id": "s-3", "sent_at": "2026-10-03T15:00:00+00:00", "thread_id": "t-3",
         "filename": "CN09877-Persona Ficticia.pdf"},          # same number, newer email
        {"source_record_id": "s-4", "sent_at": "2026-10-03T15:00:00+00:00", "thread_id": "t-4",
         "filename": "Folleto.pdf"},
    ]
    items = op.sent_quote_items(rows, existing_numbers={"09876-26"})
    assert items == [{"source_record_id": "s-3", "quote_number": "09877-26",
                      "sent_at": "2026-10-03T15:00:00+00:00", "thread_id": "t-3"}]
    assert "filename" not in items[0]  # PDF names can hold a person's name
