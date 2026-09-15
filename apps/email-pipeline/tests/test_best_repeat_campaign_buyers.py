from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "campaigns"
    / "export_best_repeat_campaign_buyers.py"
)
_spec = importlib.util.spec_from_file_location("_buyer_export", SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _candidate(email: str, **overrides):
    row = {
        "contact_email": email,
        "recipient_name": "",
        "institution_name": "",
        "role": "",
        "sources": set(),
        "lead_fit_bucket": "",
        "lead_priority_score": 0.0,
        "prospect_score": 0.0,
        "evidence_summary": "",
    }
    row.update(overrides)
    return row


def _roles(**overrides):
    from collections import Counter

    data = {
        "client_emails": Counter(),
        "supplier_emails": Counter(),
        "client_domains": Counter(),
        "supplier_domains": Counter(),
        "procurement_buyers": Counter(),
        "purchase_buyers": Counter(),
    }
    data.update(overrides)
    return data


def test_seller_functional_mailbox_is_blocked_without_buyer_evidence() -> None:
    c = _candidate("ventas@example.cl", institution_name="Example")
    assert mod._extra_block_reason(c, _roles()) == "seller_or_support_mailbox"


def test_explicit_client_evidence_overrides_seller_local_part() -> None:
    from collections import Counter

    c = _candidate("ventas@example.cl", institution_name="Example")
    roles = _roles(client_emails=Counter({"ventas@example.cl": 1}))
    assert mod._extra_block_reason(c, roles) is None


def test_supplier_email_blocks_without_buyer_evidence() -> None:
    from collections import Counter

    c = _candidate("person@supplier.cl", institution_name="Supplier")
    roles = _roles(supplier_emails=Counter({"person@supplier.cl": 2}))
    assert mod._extra_block_reason(c, roles) == "commercial_supplier_email"


def test_supplier_domain_blocks_without_client_domain_evidence() -> None:
    from collections import Counter

    c = _candidate("person@supplier.cl", institution_name="Supplier")
    roles = _roles(supplier_domains=Counter({"supplier.cl": 2}))
    assert mod._extra_block_reason(c, roles) == "commercial_supplier_domain"


def test_buyer_adjustment_rewards_client_and_lab_context() -> None:
    from collections import Counter

    c = _candidate(
        "laboratorio@hospital.cl",
        institution_name="Hospital Example",
        lead_fit_bucket="high_fit",
    )
    roles = _roles(client_emails=Counter({"laboratorio@hospital.cl": 1}))
    bonus, reasons = mod._buyer_adjustment(c, roles)
    assert bonus > 40
    assert "commercial_client:1" in reasons
    assert "buyer_role_or_local" in reasons
    assert "buyer_org_context" in reasons
    assert "high_fit" in reasons
