"""Give a test a *configured* operator identity, without a real address.

The two warm-case rules that key on which operator wrote can only fire when the identity
module has been configured. In production that configuration is a file outside Git; in a test
it is this helper, and the addresses it installs are fictitious and undeliverable.

Both module-level values are replaced together. ``INTERNAL_OPERATOR_EMAILS`` is derived from
the identity at import time, so patching only one of them would leave the module describing
two different operators.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from origenlab_email_pipeline import warm_case_sender_rules
from origenlab_email_pipeline.operator_identity import OperatorIdentity

#: Stand-ins for the operator's two personal mailboxes. Reserved by RFC 2606.
PAYMENTS_OPERATOR_EMAIL = "operador.pagos@example.invalid"
COMMERCIAL_OPERATOR_EMAIL = "operadora.comercial@example.invalid"


def configure_operator_identity(monkeypatch: pytest.MonkeyPatch) -> OperatorIdentity:
    """Install the fictitious-but-configured identity for the duration of one test."""
    identity = OperatorIdentity(
        role_addresses=MappingProxyType(
            {
                "payments_operator": PAYMENTS_OPERATOR_EMAIL,
                "commercial_operator": COMMERCIAL_OPERATOR_EMAIL,
            }
        ),
        is_configured=True,
    )
    monkeypatch.setattr(warm_case_sender_rules, "OPERATOR_IDENTITY", identity)
    monkeypatch.setattr(
        warm_case_sender_rules,
        "INTERNAL_OPERATOR_EMAILS",
        warm_case_sender_rules.PUBLISHED_OPERATOR_MAILBOXES | identity.personal_addresses,
    )
    return identity


@pytest.fixture
def configured_operator_identity(monkeypatch: pytest.MonkeyPatch) -> OperatorIdentity:
    return configure_operator_identity(monkeypatch)
