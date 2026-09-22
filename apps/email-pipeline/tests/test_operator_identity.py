"""The local operator identity is configuration, never a literal in the tree.

``github.com/rafaelRojasVi/origenlab`` is public. Two of the operator's own mailboxes are
personal Gmail addresses **and** load-bearing business-rule keys: the warm-case classifier
branches on them by exact value. Deleting them would change classification; leaving them in
Git republishes personal data. So they move out of the tree and stay reachable by *role*.

These tests pin the three properties that make that safe: the shipped default is fictitious,
a real file is read when present, and a malformed file is refused rather than half-applied.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.operator_identity import (
    FICTITIOUS_OPERATOR_IDENTITY,
    OPERATOR_IDENTITY_ENV_VAR,
    OperatorIdentity,
    OperatorIdentityError,
    load_operator_identity,
)


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "operator_identity.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_default_identity_is_fictitious_and_non_deliverable() -> None:
    """With no file configured, every personal address is on a reserved domain."""
    identity = load_operator_identity(path=None)
    assert identity == FICTITIOUS_OPERATOR_IDENTITY
    assert not identity.is_configured
    for address in identity.role_addresses.values():
        assert address.endswith(".invalid"), address


def test_shipped_example_file_is_fictitious() -> None:
    """The versioned example must be safe to copy and safe to publish."""
    example = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "operator_identity.example.json"
    )
    payload = json.loads(example.read_text(encoding="utf-8"))
    for address in payload["roles"].values():
        assert address.endswith(".invalid"), address


def test_file_supplies_the_real_addresses_by_role(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "roles": {
                "payments_operator": "Pagos.Real@Example.CL",
                "commercial_operator": "comercial.real@example.cl",
            }
        },
    )
    identity = load_operator_identity(path=path)
    assert identity.is_configured
    # Normalised: a configured address is compared against a lowercased sender.
    assert identity.role_addresses["payments_operator"] == "pagos.real@example.cl"
    assert identity.address_for("commercial_operator") == "comercial.real@example.cl"
    assert identity.personal_addresses == frozenset(
        {"pagos.real@example.cl", "comercial.real@example.cl"}
    )


def test_env_var_names_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, {"roles": dict(FICTITIOUS_OPERATOR_IDENTITY.role_addresses)})
    monkeypatch.setenv(OPERATOR_IDENTITY_ENV_VAR, str(path))
    assert load_operator_identity().is_configured


def test_missing_file_named_explicitly_is_refused(tmp_path: Path) -> None:
    """Silently falling back would make a typo look like a working configuration."""
    with pytest.raises(OperatorIdentityError, match="no existe"):
        load_operator_identity(path=tmp_path / "absent.json")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"roles": []},
        {"roles": {"payments_operator": "not-an-address"}},
        {"roles": {"unknown_role": "someone@example.cl"}},
        {"roles": {"payments_operator": ""}},
    ],
)
def test_malformed_file_is_refused(tmp_path: Path, payload: object) -> None:
    with pytest.raises(OperatorIdentityError):
        load_operator_identity(path=_write(tmp_path, payload))


def test_partial_file_keeps_the_fictitious_address_for_the_missing_role(
    tmp_path: Path,
) -> None:
    """A half-filled file configures what it names and nothing else."""
    path = _write(tmp_path, {"roles": {"payments_operator": "pagos@example.cl"}})
    identity = load_operator_identity(path=path)
    assert identity.address_for("payments_operator") == "pagos@example.cl"
    assert identity.address_for("commercial_operator") == (
        FICTITIOUS_OPERATOR_IDENTITY.address_for("commercial_operator")
    )


def test_identity_is_a_value_and_cannot_be_mutated() -> None:
    identity: OperatorIdentity = load_operator_identity(path=None)
    with pytest.raises((AttributeError, TypeError)):
        identity.role_addresses["payments_operator"] = "x@example.cl"  # type: ignore[index]
