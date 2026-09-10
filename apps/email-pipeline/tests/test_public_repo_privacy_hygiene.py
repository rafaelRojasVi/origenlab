"""Public-repository privacy hygiene guard.

``github.com/rafaelRojasVi/origenlab`` is a **public** repository. Real personal and
contact data must not live in the tracked tree: no personal email addresses, phone
numbers, or names paired with contact details.

This module is a regression guard for the privacy remediation pass. It fails if
data that was deliberately removed reappears, or if the DR50 payload stops being
synthetic.

Removed values are matched by **SHA256 digest**, never stored in plaintext -- a
privacy guard must not itself republish the data it protects.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DR50_DATA = REPO / "scripts" / "leads" / "campaigns" / "data"

# Reserved, non-deliverable domains (RFC 2606 / RFC 6761) plus the project's own
# public domain, which is a published business contact rather than personal data.
_RESERVED_SUFFIXES = (".example", ".invalid", ".test", ".localhost")
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SYNTHETIC_PHONE_RE = re.compile(r"^\+56 9 0000 \d{4}$")

# SHA256 of every real contact value removed by the public-repo privacy
# remediation. Plaintext is intentionally absent; see the module docstring.
_REMOVED_DIGESTS = frozenset(
    {
        "05bd42cc5e6ff8a32536b7f8407336b79f54195165e922afab9a1ca82afd8c77",
        "0b006ac67909e8d8f1ab4e1ac2efd6a8599aeb38be16f653debbe37ce2515d20",
        "0d9a41cce67a5a1ac3ca39fd8995c8a9d6f74686756daab2ae33118a9adacd5c",
        "0e43903a6d4e639b1e8ca111af920538d593c97ed4cee4a0c480be85e71ae3cc",
        "108ada25402f48753d55958541a00d3aec2024eaa61a69262e87a4f6a5354c66",
        "115abb748fa6061fb0d6f1deb47b0a43506e6663577bfe662f31179d4040c157",
        "15b79f6974f9380ac6766f4eb261f8ac40d0910e11db363b04893869e579406d",
        "15d84410beedb6a8c201a6a8e8bb15fa7a128a584d4d320c4be35656a63bb97b",
        "2f79f1e70317386ef85d139677c4b82fc4b3eca8ba31f2311511c3f8318653f0",
        "377e814988fbe944f57afb1ebdf9a5e816f72f1c03e44017cefc59d70fce1fdc",
        "38e3fd1c2f13df4ab21166453baeefaafbc303cba06d5dfd88e077beaaa0e846",
        "451e464a5c0604ca35aa2e28f3c819661436028f3255f84e430bf957bbc169cd",
        "4df058302ab91a93c13e9ddac9a51e0c97f136abe4065a59938f803412fd9bbe",
        "536c9b48d65019f69a0148782918d84f154e763cff70e9ac5bc4a6bfd602dc6c",
        "54663e88b6656b89fe0b3b652264b36c3c1cbb0423b9acea1574eb0f4a55b6f2",
        "5da88ce65d609648333b03bccf3f885bdcad79654dbe1fccc85b2e924422a437",
        "6cc6d31b5d5be46be01acf23dcb8c748281ada456112b0db58400adc28e42a03",
        "751b8a8d52e0d81be43a03e2c072080ac881c4a682001491d9d4204f672dc453",
        "7ce810fe1575d4a45849e6e8289a870ffddd3336c40c62aad95d785883217e01",
        "7dd514b6d47fb48cf95f649577dd7a56b8ec3f678f7c29334d5e10624af2a634",
        "7f5dcb4fa7594d2b968055266a261dcd75e7dab516a71b9df2bb0d9bb811456e",
        "803e933d42758d097f6a48e67de09a14297a25d8c61aece481be979987a7753e",
        "82876d655a22dccaeae99abc5f99e8fc2bbab30be23e6f22c560bc54260ea25e",
        "832d4540f7045abdcb90d17772334d2292d214ac4aace7d9d54cea6426dcb8b2",
        "87896b532cbb9308835620f3507051ae1f136c10352c157dce5eadb258b68b3c",
        "936cfb7df7b9a51ac74b20bef77205a8b88f1209d2b71441f7c39b61343a221e",
        "94e9eed946d28cfeb04f6d126b85280ab1af26cc87a28f14d77131aa8daeded5",
        "975e06e4d6871dc952990ab11e0f6b01cfa560e009aead6ad6780835da7f3d70",
        "9bb50f98b8ec48bce086ddc1926842a4f3c8da8fa81d19f3727208985033be44",
        "ae20a22719b5b08914cfe58ee9e36dbce021c59de8bfd94c53210eeebeedfd34",
        "b3fb6a92853c0ee2a6aefabc00540465b72bfd4b90612ea28e7881b2a22b45e6",
        "b7ed008006ab7d1d221a41a999e7da1dc1bdf12d3cbba0192a8f2acafbed9442",
        "b7f7151e704c8725f87f323b2a372d4b7fa7902404dad5392f8fb281aec5a1b2",
        "be82687dfd2841622b394e536e80198e242e7fcc039f6fc883a0d14107beab4d",
        "bf5e88e4cceb415ec51d2c01db75a64df16a9ac4166e202fba094d6ed272a578",
        "c211d95a4ed51be4639840270e179197cd3dc95dd96d401dafa26ee8fe8033e4",
        "c5aaa49078d3b89ff2113ec3b6196a9a3ad7dde9210d5c39fba2a2b96fe67a02",
        "c93f2942c626cb8cb1d9b00a3eb6a4c8d2541e5464a92892747715fdbf37629d",
        "ce5d013a521c6bee8bcc6fafc2a614bdd8ca43a266aa8c3be5e97d9669afb859",
        "d310bbb65d4928b1fa721e63286db8b7803c6f47a8af1e4442349bed7e6e057b",
        "d966f4feb07e767e50af3195ed0519e4aef3ed47b262efb0347c9ea596c33a62",
        "db62b7c19d42ebbad27edbee9b1aac1661f2deab661c87e989ae2440adea70e1",
        "e0a797b545947b9408d699f175d3b02d03de74408d71c75703b006bb300ae58b",
        "e2eece2e42521985f9d1671ffcce42411ce0224195b4c14c48f80f6dcf6f8d7d",
        "e4064f943f17981d84e27d37e824ea082d74bf6213918a7052238a68fc5ba5c8",
        "e4a544544d07a17016b149dfd444dd6095a39b822377599a818b86c05bf43973",
        "e8f0cde9ecb9fa6d17f99ad224c506b105ace2a555867ee8f7b29a14ff420e61",
        "e9881e04c4a4eb39cb4b8c520304c54c7ac8e8b7a8306fb79753a4f3bcd6a136",
        "ecc0896414e1ea483c03401fd767b8801793ea78fb6fcad9402a727eee01ca80",
        "f3b1cd7cc0498f02f1d1ee1e03f076e6ac6f2919d07ded8a72e47f4b49f8289f",
        "f6ab1ebba3b61f73497442278e171dd1a066717603569787957112e4c93a8d67",
        "fa3572d0594a7eb949fd7ad821c4baaf229d2ac464af9b22c6107b7aa70ad45d",
        "fe7d22a352b8e6c113a4d02c828ddc610e351163b14ed3296801c1cddea7127b",
        "ff824a0ebcc9cd0ec4b8ec9dc0e3bab4cf564df895642d16fce532a698da56f2",
    }
)


# Deliberately deferred, tracked as an open remediation item: two real personal
# addresses are load-bearing business-rule keys in ``warm_case_sender_rules`` (the
# classifier branches on their exact value), and one test pins that behaviour.
# Removing them changes runtime classification, so it is a behavioural change rather
# than a privacy edit and is being decided separately. Do not extend this list
# without an explicit decision -- it is an exception, not a general escape hatch.
_DEFERRED_ALLOWLIST = frozenset(
    {
        "apps/email-pipeline/src/origenlab_email_pipeline/warm_case_sender_rules.py",
        "apps/email-pipeline/tests/test_commercial_intel_rules.py",
    }
)


def _is_reserved(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return domain.endswith(_RESERVED_SUFFIXES) or domain in _RESERVED_DOMAINS


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _tracked_files() -> list[Path]:
    root = REPO.parents[1]  # monorepo root
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable; cannot enumerate the tracked tree")
    return [root / p for p in out.split("\0") if p]


def test_dr50_payload_contacts_are_synthetic() -> None:
    """The committed DR50 payload must carry no real contact data."""
    rows = json.loads((DR50_DATA / "dr50_payload_v1.json").read_text(encoding="utf-8"))
    offenders: list[str] = []
    for row in rows:
        for key, value in row.items():
            if not isinstance(value, str) or not value.strip():
                continue
            if "email" in key:
                if not _is_reserved(value):
                    offenders.append(f"{key} is not a reserved domain")
            elif "phone" in key and not _SYNTHETIC_PHONE_RE.match(value.strip()):
                offenders.append(f"{key} is not a synthetic phone number")
    assert not offenders, (
        "DR50 payload contains non-synthetic contact data: "
        + "; ".join(sorted(set(offenders)))
    )


def test_dr50_manifest_declares_synthetic_content() -> None:
    manifest = json.loads(
        (DR50_DATA / "dr50_manifest_v1.json").read_text(encoding="utf-8")
    )
    assert manifest.get("content") == "synthetic", (
        "dr50_manifest_v1.json must declare content=synthetic so the payload is "
        "never mistaken for real operator data"
    )


def test_removed_contact_data_has_not_reappeared() -> None:
    """Fail if any value scrubbed by the privacy remediation returns to the tree."""
    this_file = Path(__file__).resolve()
    hits: list[str] = []
    for path in _tracked_files():
        if path.resolve() == this_file or not path.is_file():
            continue
        try:
            rel_path = str(path.relative_to(REPO.parents[1]))
        except ValueError:
            rel_path = str(path)
        if rel_path in _DEFERRED_ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for email in set(_EMAIL_RE.findall(text)):
            if _digest(email) in _REMOVED_DIGESTS:
                hits.append(rel_path)
                break
    assert not hits, (
        "Contact data removed by the public-repo privacy remediation has reappeared in: "
        + ", ".join(sorted(set(hits)))
        + " -- use a reserved domain (example.invalid) instead."
    )
