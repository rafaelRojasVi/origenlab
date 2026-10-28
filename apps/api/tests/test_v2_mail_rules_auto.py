"""The automatic email-rules timer: when it starts, that it stops, and that a bad pass never kills it.
The pass itself is exercised against a real database in `test_v2_mail_rules_db.py`."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from origenlab_api.v2.mail_rules_auto import AUTOMATIC_RULES, AutoMailRules, AutoMailRulesRunner


class _Repo:
    def __init__(self, commands_enabled=True):
        self.commands_enabled = commands_enabled


class _CountingAuto(AutoMailRules):
    def __init__(self, interval, fail_first=False):
        super().__init__(_Repo(), interval_seconds=interval)
        self.passes = 0
        self.fail_first = fail_first
        self.two = threading.Event()

    def run_once(self):
        self.passes += 1
        if self.passes >= 2:
            self.two.set()
        if self.fail_first and self.passes == 1:
            raise RuntimeError("database away")
        return {"applied": 0, "refused": 0, "pending": 0}


def test_only_safe_intake_and_link_rules_are_automatic() -> None:
    # R9 (the institution a sent CN PDF names, one open case) owner-approved 2026-10-13.
    # R14 and R15 (the obvious decisions) owner-approved 2026-10-19, each with its own kill flag.
    # R17 (a client's reply → «Conversación») owner-approved 2026-10-26.
    assert AUTOMATIC_RULES == {"R1", "R2", "R3", "R4", "R7", "R9", "R11", "R12", "R13", "R14", "R15", "R16",
                               "R17"}


def test_a_kill_flag_keeps_its_rule_out_of_the_pass() -> None:
    assert AutoMailRules(_Repo(), disabled_rules=frozenset({"R14"})).rules == AUTOMATIC_RULES - {"R14"}
    assert AutoMailRules(_Repo()).rules == AUTOMATIC_RULES


def test_interval_zero_or_no_commands_never_starts_the_timer() -> None:
    off = _CountingAuto(0)
    runner = AutoMailRulesRunner(off)
    runner.start()
    assert runner._thread is None
    unmounted = AutoMailRules(_Repo(commands_enabled=False), interval_seconds=60)
    assert unmounted.timer_running is False


def test_the_timer_survives_a_failed_pass_and_stops_with_the_app() -> None:
    auto = _CountingAuto(1, fail_first=True)
    auto.interval_seconds = 0.01  # the setting is whole seconds; a test cannot wait for one
    runner = AutoMailRulesRunner(auto)
    runner.start()
    assert auto.two.wait(5), "a failed pass stopped the timer"
    runner.stop()
    assert runner._thread is None
    passes = auto.passes
    threading.Event().wait(0.05)
    assert auto.passes == passes


# --- people first (Task 6): the people pass runs before the mail rules ------------------------------


class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a):
        pass

    def fetchone(self):
        return self._row


class _OrderRepo:
    commands_enabled = True

    def __init__(self, calls, enabled=True, role="admin"):
        self.calls = calls
        body = '{"enabled": %s, "note": "x"}' % ("true" if enabled else "false")
        self._row = ("00000000-0000-4000-8000-000000000001", datetime.now(UTC), body, "a@example.test",
                     "Admin", role, "active", 1)

    @contextmanager
    def _read(self):
        yield _Cur(self._row)

    def plan(self):
        self.calls.append("mail.plan")
        return [], {}, None


class _People:
    def __init__(self, calls):
        self.calls = calls

    def plan(self):
        self.calls.append("people.plan")
        return [SimpleNamespace(rule_id="P2", tier="strong"), SimpleNamespace(rule_id="W1", tier="weak"),
                SimpleNamespace(rule_id="P5", tier="strong")]

    def apply_planned(self, operator, action, *, automatic):
        self.calls.append(("people.apply", action.rule_id, automatic))
        return {"code": "x"} if action.rule_id == "P5" else {"rule_id": action.rule_id}


def test_people_pass_runs_before_mail_rules() -> None:
    calls: list = []
    auto = AutoMailRules(_OrderRepo(calls), people_repo=_People(calls), people_enabled=True)
    result = auto.run_once()
    assert calls == ["people.plan", ("people.apply", "P2", True), ("people.apply", "P5", True), "mail.plan"]
    assert result["people"] == {"applied": 1, "refused": 1, "skipped": None}


def test_people_pass_off_by_default() -> None:
    calls: list = []
    result = AutoMailRules(_OrderRepo(calls), people_repo=_People(calls)).run_once()
    assert calls == ["mail.plan"]
    assert result["people"]["skipped"] == "disabled" and result["people"]["applied"] == 0


def test_people_pass_skipped_when_switch_off() -> None:
    calls: list = []
    auto = AutoMailRules(_OrderRepo(calls, enabled=False), people_repo=_People(calls), people_enabled=True)
    result = auto.run_once()
    assert calls == [] and result["people"]["skipped"] == "off"
    calls.clear()
    auto = AutoMailRules(_OrderRepo(calls, role="sales"), people_repo=_People(calls), people_enabled=True)
    assert auto.run_once()["people"]["skipped"] == "operator_not_admin" and calls == []


def test_a_failed_people_pass_does_not_stop_the_mail_rules() -> None:
    calls: list = []

    class _Broken(_People):
        def plan(self):
            raise RuntimeError("away")

    result = AutoMailRules(_OrderRepo(calls), people_repo=_Broken(calls), people_enabled=True).run_once()
    assert calls == ["mail.plan"] and result["people"]["skipped"] == "error"


def test_people_pass_takes_at_most_fifty_strong_actions() -> None:
    calls: list = []

    class _Many(_People):
        def plan(self):
            return [SimpleNamespace(rule_id="P2", tier="strong")] * 60

    result = AutoMailRules(_OrderRepo(calls), people_repo=_Many(calls), people_enabled=True).run_once()
    assert result["people"]["applied"] == 50
