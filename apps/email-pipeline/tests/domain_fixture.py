"""Domain membership helpers for tests (CodeQL-safe fixtures)."""

from __future__ import annotations

from urllib.parse import urlparse


def hostname_fixture(domain: str) -> str:
    """Normalize a synthetic test domain via URL parsing (not substring checks)."""
    parsed = urlparse(f"https://{domain.strip()}/")
    host = parsed.hostname
    if host is None:
        raise AssertionError(f"invalid test domain fixture: {domain!r}")
    return host


def assert_domain_in_collection(domain: str, collection: set[str] | frozenset[str]) -> None:
    host = hostname_fixture(domain)
    assert host in collection, f"expected {host!r} in collection"


def assert_domains_in_collection(
    domains: set[str] | frozenset[str],
    collection: set[str] | frozenset[str],
) -> None:
    expected_hosts = {hostname_fixture(domain) for domain in domains}
    assert expected_hosts <= set(collection)
