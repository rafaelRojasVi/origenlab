"""Re-derive the Wave 1A RFC 2047 addendum from the immutable Wave 1A bundle.

``docs/DATA.md`` §7.1 records three RFC 2047 decoded addresses that are absent
from the Wave 1A contacted union and that load as
``contact_control(kind=prior_contact, scope=address, purpose=marketing)`` with
``source = wave1a_rfc2047_addendum``. It also states that they are a **separate
loader input file, kept alongside the bundle and hashed independently**, and
that *the immutable bundle is never edited to include them*.

That separate file was never materialized. The addresses themselves were not
lost: the bundle's own ``reports/parse_failure_summary.json`` carries them under
``rfc2047_diagnostic.recovered_addresses_not_in_contacted_union``, which the
bundle README documents as a diagnostic-only RFC 2047 decode of the zero-address
Sent rows. This module reconstitutes the missing loader input from that recorded
evidence — it decodes nothing, infers nothing and invents nothing.

Guarantees:

* the bundle is read only, and every file it reads is verified against the
  bundle's own ``SHA256SUMS`` first;
* the bundle is never written to; the addendum is written to an operator-named
  directory outside every Git working tree;
* each address is independently re-verified as absent from
  ``derived/recipient_ledger.jsonl.gz`` — an address already in the contacted
  union contradicts the documented mapping and refuses the run;
* the output is deterministic and never replaces an existing file;
* the addendum carries real addresses, so it and its ``.sha256`` sidecar are
  written ``0600`` into an output root tightened to ``0700``, atomically and
  never at a wider mode (``bundle.write_private_bytes``);
* no address is ever printed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from origenlab_email_pipeline.migration.bundle import (
    assert_no_collision,
    assert_output_root_private,
    write_private_bytes,
)

#: The ``contact_control.source`` these rows carry, per ``docs/DATA.md`` §7.1.
ADDENDUM_SOURCE_LABEL = "wave1a_rfc2047_addendum"

_SUMMARY_REL = "reports/parse_failure_summary.json"
_LEDGER_REL = "derived/recipient_ledger.jsonl.gz"
# Named ``_BLOCK`` / ``_FIELD`` rather than ``_KEY``: these are JSON field names
# in the bundle's own report, and a ``*_KEY = "<string>"`` shape trips the secret
# scanner's generic-credential rule. Renaming beats suppressing a finding.
_DIAGNOSTIC_BLOCK = "rfc2047_diagnostic"
_ADDRESSES_FIELD = "recovered_addresses_not_in_contacted_union"


class AddendumRefused(Exception):
    """The derivation failed closed. Messages never carry an address."""


@dataclass(frozen=True)
class AddendumResult:
    path: Path
    sidecar: Path
    sha256: str
    count: int
    source_label: str
    warnings: tuple[str, ...] = ()
    console_lines: tuple[str, ...] = ()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_against_sha256sums(bundle: Path, relpaths: tuple[str, ...]) -> dict[str, str]:
    """Verify the named bundle files against the bundle's own ``SHA256SUMS``."""
    sums = bundle / "SHA256SUMS"
    if not sums.is_file():
        raise AddendumRefused("the bundle has no SHA256SUMS; integrity cannot be verified")
    listed: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        listed[name.strip()] = digest.strip()

    verified: dict[str, str] = {}
    for rel in relpaths:
        target = bundle / rel
        if not target.is_file():
            raise AddendumRefused(f"the bundle is missing {rel}")
        if rel not in listed:
            raise AddendumRefused(f"SHA256SUMS does not list {rel}")
        actual = _sha256_file(target)
        if actual != listed[rel]:
            raise AddendumRefused(
                f"SHA256SUMS mismatch for {rel}; the bundle is not the one it claims to be"
            )
        verified[rel] = actual
    return verified


def _contacted_union_addresses(ledger: Path) -> set[str]:
    addresses: set[str] = set()
    with gzip.open(ledger, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("in_contacted_union"):
                address = row.get("email_norm")
                if isinstance(address, str):
                    addresses.add(address.strip().lower())
    return addresses


def derive_wave1a_rfc2047_addendum(
    *,
    bundle_dir: Path,
    output_dir: Path,
    expect_count: int | None = None,
) -> AddendumResult:
    """Write the separate, independently hashed Wave 1A RFC 2047 loader input."""
    bundle = Path(bundle_dir).expanduser().resolve()
    if not bundle.is_dir():
        raise AddendumRefused("the Wave 1A bundle directory does not exist")

    out_dir = assert_output_root_private(output_dir)
    target = out_dir / f"{bundle.name}_rfc2047_addendum.jsonl"
    sidecar = Path(f"{target}.sha256")
    assert_no_collision(target)
    assert_no_collision(sidecar)

    digests = _verify_against_sha256sums(bundle, (_SUMMARY_REL, _LEDGER_REL))

    summary = json.loads((bundle / _SUMMARY_REL).read_text(encoding="utf-8"))
    diagnostic = summary.get(_DIAGNOSTIC_BLOCK)
    if not isinstance(diagnostic, dict):
        raise AddendumRefused(
            f"the bundle's parse-failure summary has no {_DIAGNOSTIC_BLOCK} block; "
            "the addendum cannot be derived and must not be invented"
        )
    recovered = diagnostic.get(_ADDRESSES_FIELD)
    if not isinstance(recovered, list) or not all(isinstance(v, str) for v in recovered):
        raise AddendumRefused(
            f"{_DIAGNOSTIC_BLOCK}.{_ADDRESSES_FIELD} is absent or not a list of addresses"
        )

    addresses = sorted({value.strip().lower() for value in recovered if value.strip()})
    if not addresses:
        raise AddendumRefused("the recorded addendum address list is empty")

    union = _contacted_union_addresses(bundle / _LEDGER_REL)
    already = sorted(set(addresses) & union)
    if already:
        raise AddendumRefused(
            f"{len(already)} recorded addendum address(es) are already in the contacted "
            "union; the recorded mapping and the ledger disagree"
        )

    warnings: list[str] = []
    if expect_count is not None and expect_count != len(addresses):
        warnings.append(
            f"expected {expect_count} addendum address(es), derived {len(addresses)} — "
            "the derived value is the fact; the expectation is only an operator hint"
        )

    payload = b"".join(
        json.dumps(
            {
                "address": address,
                "source": ADDENDUM_SOURCE_LABEL,
                "kind": "prior_contact",
                "scope": "address",
                "purpose": "marketing",
                "derived_from_bundle": bundle.name,
                "derived_from_file": _SUMMARY_REL,
                "derived_from_file_sha256": digests[_SUMMARY_REL],
            },
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for address in addresses
    )
    write_private_bytes(target, payload)
    digest = hashlib.sha256(payload).hexdigest()
    write_private_bytes(sidecar, f"{digest}  {target.name}\n".encode("utf-8"))

    console = [
        f"wave1a rfc2047 addendum: {len(addresses)} address(es)",
        f"source: {ADDENDUM_SOURCE_LABEL} (kind=prior_contact, scope=address, purpose=marketing)",
        f"derived from: {bundle.name}/{_SUMMARY_REL}",
        f"derived_from_file_sha256: {digests[_SUMMARY_REL]}",
        f"addendum sha256: {digest}",
        "addresses are never printed; the addendum file holds them",
        *[f"WARNING: {w}" for w in warnings],
    ]

    return AddendumResult(
        path=target,
        sidecar=sidecar,
        sha256=digest,
        count=len(addresses),
        source_label=ADDENDUM_SOURCE_LABEL,
        warnings=tuple(warnings),
        console_lines=tuple(console),
    )
