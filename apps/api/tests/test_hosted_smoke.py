"""scripts/hosted_smoke.py: allowlist → probe paths, expected statuses, verdicts. No network."""

from __future__ import annotations

import importlib.util
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


def test_every_public_route_in_the_table_is_still_allowlisted() -> None:
    paths = {p for _, ps in hs.allowlist_entries(_ALLOWLIST.read_text(encoding="utf-8")) for p in ps}
    assert set(hs.PUBLIC_ROUTES) <= paths


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
