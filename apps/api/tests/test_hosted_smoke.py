"""scripts/hosted_smoke.py: allowlist → probe paths, expected statuses, verdicts. No network."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hosted_smoke.py"
_ALLOWLIST = Path(__file__).resolve().parents[2] / "dashboard-proxy" / "src" / "allowlist.ts"


def _load():
    spec = importlib.util.spec_from_file_location("hosted_smoke", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hs = _load()


def _ts(*literals: str) -> str:
    body = "\n".join(f"  {lit}," for lit in literals)
    return f"export const ALLOWED_UPSTREAM_PATHS: readonly RegExp[] = [\n{body}\n];\n"


# ------------------------------------------------------------------ allowlist → paths


def test_every_real_allowlist_entry_becomes_paths_its_regex_matches() -> None:
    text = _ALLOWLIST.read_text(encoding="utf-8")
    entries = hs.allowlist_entries(text)
    assert len(entries) >= 60
    for pattern, paths in entries:
        assert paths, pattern
        for path in paths:
            assert re.fullmatch(pattern, path), (pattern, path)


def test_a_uuid_segment_becomes_the_nil_uuid() -> None:
    uuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    entries = hs.allowlist_entries(_ts(rf"/^\/v2\/contacts\/{uuid}$/"))
    assert entries[0][1] == ["/v2/contacts/00000000-0000-0000-0000-000000000000"]


def test_an_alternation_becomes_one_path_per_branch() -> None:
    entries = hs.allowlist_entries(_ts(r"/^\/v2\/x\/(a|b|c)$/"))
    assert entries[0][1] == ["/v2/x/a", "/v2/x/b", "/v2/x/c"]


@pytest.mark.parametrize("literal", [r"/^\/v2\/.+$/", r"/^\/v2\/[a-z]+$/", r"/^\/v2\/x?$/"])
def test_a_pattern_it_cannot_expand_is_refused_not_skipped(literal: str) -> None:
    with pytest.raises(hs.UnsupportedPattern):
        hs.allowlist_entries(_ts(literal))


def test_the_post_lists_are_not_probed() -> None:
    text = _ts(r"/^\/v2\/contacts$/") + "export const CASE_COMMAND_POST_PATHS = [\n  /^\/v2\/commands\/x$/,\n];\n"
    assert [p for _, ps in hs.allowlist_entries(text) for p in ps] == ["/v2/contacts"]


# ------------------------------------------------------------------ expected status


def test_expected_status_per_route_class() -> None:
    assert hs.expected_status("/health") == "200"
    assert hs.expected_status("/auth/google/login") == "3xx"
    assert hs.expected_status("/auth/google/callback") == "3xx"
    assert hs.expected_status("/v2/contacts") == "401"


@pytest.mark.parametrize("status,ok", [(401, True), (404, True), (200, False), (502, False)])
def test_a_route_behind_a_switch_may_be_off(status: int, ok: bool) -> None:
    path = "/v2/workspace/marketing/test-send-history"
    assert hs.expected_status(path) == "401|404"
    assert (hs.route_failures([_probe(path, status)]) == []) is ok


def test_a_switched_off_route_is_reported() -> None:
    notes = hs.switch_notes([_probe("/v2/workspace/marketing/test-send-history", 404)])
    assert notes == ["/v2/workspace/marketing/test-send-history: switched off (ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED)"]


def test_every_public_route_in_the_table_is_still_allowlisted() -> None:
    paths = {p for _, ps in hs.allowlist_entries(_ALLOWLIST.read_text(encoding="utf-8")) for p in ps}
    assert set(hs.PUBLIC_ROUTES) <= paths
    assert set(hs.SWITCHED_ROUTES) <= paths


# ------------------------------------------------------------------ verdicts


def _probe(path: str, status: int | None, seconds: float = 0.2, error: str = "") -> object:
    return hs.Probe(path=path, status=status, seconds=seconds, error=error)


def test_all_expected_statuses_pass() -> None:
    probes = [_probe("/health", 200), _probe("/auth/google/login", 302), _probe("/v2/contacts", 401)]
    assert hs.route_failures(probes) == []


@pytest.mark.parametrize("status,word", [
    (404, "not deployed"), (502, "broken"), (200, "unprotected"), (403, "refused by the Worker"),
])
def test_a_wrong_status_names_the_path_and_the_reason(status: int, word: str) -> None:
    failures = hs.route_failures([_probe("/v2/contacts", status)])
    assert len(failures) == 1
    assert "/v2/contacts" in failures[0] and word in failures[0]


def test_a_slow_probe_fails() -> None:
    failures = hs.route_failures([_probe("/v2/contacts", 401, seconds=2.5)])
    assert failures and "2.5" in failures[0]


def test_a_network_error_fails() -> None:
    failures = hs.route_failures([_probe("/v2/contacts", None, error="timed out")])
    assert failures and "timed out" in failures[0]


def test_a_network_error_is_retried_once_but_a_status_never_is() -> None:
    calls: list[str] = []

    def flaky(path: str) -> tuple[int, bytes]:
        calls.append(path)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return 401, b""

    assert hs.probe("/v2/contacts", flaky).status == 401
    assert len(calls) == 2

    calls.clear()
    assert hs.probe("/v2/contacts", lambda p: (calls.append(p) or (502, b""))).status == 502
    assert len(calls) == 1


# ------------------------------------------------------------------ version


SHA = "0123456789abcdef0123456789abcdef01234567"


def test_a_deployed_commit_on_main_and_current_passes() -> None:
    v = hs.version_verdict(SHA, on_main=lambda s: True, behind=lambda s: 0)
    assert v.failures == [] and v.warnings == []


def test_no_version_is_a_warning_until_the_api_reports_it() -> None:
    v = hs.version_verdict(None, on_main=lambda s: True, behind=lambda s: 0)
    assert v.failures == [] and v.warnings


def test_a_commit_not_on_main_fails() -> None:
    v = hs.version_verdict(SHA, on_main=lambda s: False, behind=lambda s: 0)
    assert v.failures and SHA[:12] in v.failures[0]


def test_behind_main_on_the_api_is_a_warning_with_the_count() -> None:
    v = hs.version_verdict(SHA, on_main=lambda s: True, behind=lambda s: 3)
    assert v.failures == [] and "3" in v.warnings[0]


# ------------------------------------------------------------------ production database


def test_every_repo_migration_applied_passes(tmp_path: Path) -> None:
    for name in ("20260901000000_a.sql", "20260902000000_b.sql"):
        (tmp_path / name).write_text("select 1;")
    applied = {"20260901000000", "20260902000000", "20250101000000"}  # extra ledger rows are fine
    assert hs.pending_migration_failures(applied, tmp_path) == []


def test_a_merged_migration_not_applied_fails_and_names_the_first(tmp_path: Path) -> None:
    for name in ("20260901000000_a.sql", "20260902000000_b.sql", "20260903000000_c.sql"):
        (tmp_path / name).write_text("select 1;")
    failures = hs.pending_migration_failures({"20260901000000"}, tmp_path)
    assert failures == ["2 migration(s) on main not applied in production, first 20260902000000"]


def test_without_secrets_the_database_checks_say_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OL_PROD_MIGRATOR_PASSWORD", raising=False)
    v = hs.database_checks()
    assert v.failures == []
    assert v.warnings and v.warnings[0].startswith("NOT CHECKED")


def test_main_never_prints_a_response_body(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    secret = "SECRET-BODY-MARKER"

    def fake_fetch_factory(base_url: str):
        def fetch(path: str) -> tuple[int, bytes]:
            if path == "/health":
                return 200, json.dumps({"ok": True, "version": None, "x": secret}).encode()
            code = {"200": 200, "3xx": 302}.get(hs.expected_status(path), 401)
            return code, secret.encode()
        return fetch

    monkeypatch.setattr(hs, "http_fetch", fake_fetch_factory)
    monkeypatch.delenv("OL_PROD_MIGRATOR_PASSWORD", raising=False)
    assert hs.main([]) == 0
    assert secret not in capsys.readouterr().out


# ------------------------------------------------------------------ with your own session

COOKIE = "abc.def-SESSIONVALUE"


def test_a_bare_cookie_value_becomes_the_session_cookie() -> None:
    assert hs.session_cookie_header(COOKIE) == f"__Host-origenlab_session={COOKIE}"


def test_a_named_cookie_is_kept_and_nothing_else_is_accepted() -> None:
    assert hs.session_cookie_header(f"__Host-origenlab_session={COOKIE}") == f"__Host-origenlab_session={COOKIE}"
    with pytest.raises(ValueError):
        hs.session_cookie_header(f"other=1; __Host-origenlab_session={COOKIE}")


def test_the_authenticated_pass_skips_health_and_the_google_redirects() -> None:
    paths = ["/health", "/auth/google/login", "/auth/google/callback", "/auth/session", "/v2/contacts"]
    assert hs.authenticated_paths(paths) == ["/auth/session", "/v2/contacts"]


def test_authenticated_expectations() -> None:
    assert hs.expected_authenticated("/v2/contacts") == "200"
    assert hs.expected_authenticated(f"/v2/contacts/{hs.NIL_UUID}") == "200|404"
    assert hs.expected_authenticated("/v2/workspace/marketing/test-send-history") == "200|404"


def _aprobe(path: str, status: int | None, body: bytes = b"{}", seconds: float = 0.3) -> object:
    return hs.AuthProbe(path=path, status=status, body=body, seconds=seconds)


def test_authenticated_reads_that_answer_json_pass() -> None:
    probes = [_aprobe("/v2/contacts", 200, b'{"items": []}'), _aprobe(f"/v2/cases/{hs.NIL_UUID}", 404)]
    assert hs.authenticated_failures(probes) == []


@pytest.mark.parametrize("status,body,word", [
    (401, b"", "sign in again"),
    (403, b"", "refused"),
    (500, b"", "broken"),
    (200, b"<html>", "not JSON"),
])
def test_authenticated_failures_say_why(status: int, body: bytes, word: str) -> None:
    failures = hs.authenticated_failures([_aprobe("/v2/contacts", status, body)])
    assert len(failures) == 1 and word in failures[0] and "/v2/contacts" in failures[0]


def test_a_slow_authenticated_read_fails() -> None:
    failures = hs.authenticated_failures([_aprobe("/v2/contacts", 200, b"{}", seconds=3.5)])
    assert failures and "3.5" in failures[0]


def test_the_session_and_bodies_never_reach_the_output(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    marker_body = "BODY-MARKER-42"
    seen_cookies: list[str | None] = []
    seen_methods: list[str] = []

    def fake_fetch_factory(base_url: str, cookie: str | None = None):
        def fetch(path: str) -> tuple[int, bytes]:
            seen_cookies.append(cookie)
            seen_methods.append("GET")
            if cookie is None:
                code = {"200": 200, "3xx": 302}.get(hs.expected_status(path), 401)
                return code, b'{"version": null}' if path == "/health" else b""
            return 200, json.dumps({"x": marker_body}).encode()
        return fetch

    monkeypatch.setattr(hs, "http_fetch", fake_fetch_factory)
    monkeypatch.setenv("OL_SMOKE_SESSION", COOKIE)
    monkeypatch.delenv("OL_PROD_MIGRATOR_PASSWORD", raising=False)
    assert hs.main([]) == 0
    out = capsys.readouterr().out
    assert COOKIE not in out and marker_body not in out
    assert "authenticated reads" in out
    assert f"__Host-origenlab_session={COOKIE}" in seen_cookies


def test_the_scheduled_run_does_not_nag_about_the_on_demand_session(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def fake_fetch_factory(base_url: str, cookie: str | None = None):
        def fetch(path: str) -> tuple[int, bytes]:
            code = {"200": 200, "3xx": 302}.get(hs.expected_status(path), 401)
            return code, b'{"version": null}' if path == "/health" else b""
        return fetch

    monkeypatch.setattr(hs, "http_fetch", fake_fetch_factory)
    monkeypatch.delenv("OL_SMOKE_SESSION", raising=False)
    monkeypatch.delenv("OL_PROD_MIGRATOR_PASSWORD", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    hs.main([])
    assert "OL_SMOKE_SESSION" not in capsys.readouterr().out


def test_a_slow_probe_is_timed_again_before_it_fails() -> None:
    timings = iter([2.6, 0.4])
    clock = {"t": 0.0}

    def fetch(path: str) -> tuple[int, bytes]:
        clock["t"] += next(timings)
        return 401, b""

    p = hs.probe("/v2/contacts", fetch, clock=lambda: clock["t"])
    assert p.status == 401 and p.seconds == pytest.approx(0.4)


def test_a_probe_slow_twice_stays_slow() -> None:
    timings = iter([2.6, 2.7])
    clock = {"t": 0.0}

    def fetch(path: str) -> tuple[int, bytes]:
        clock["t"] += next(timings)
        return 401, b""

    p = hs.probe("/v2/contacts", fetch, clock=lambda: clock["t"])
    assert p.seconds == pytest.approx(2.7)
    assert hs.route_failures([p])


def test_worker_failures_name_each_failed_condition() -> None:
    assert hs.worker_failures({"heartbeat": True, "sweep_scheduled": True, "triage_caught_up": True}) == []
    assert hs.worker_failures({"heartbeat": True, "sweep_scheduled": False, "triage_caught_up": False}) == [
        "workers: the triage sweep was not scheduled in the last 5 minutes",
        "workers: captured mail older than 15 minutes is waiting for triage",
    ]
    assert hs.worker_failures({"heartbeat": False}) == ["workers: no worker heartbeat in the last 3 minutes"]
