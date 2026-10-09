"""Conservative NDR analysis: only reviewed Batch-A no-such-user is auto-blockable."""

from __future__ import annotations

from email.message import EmailMessage

from origenlab_worker.bounce import analyze_bounce


def raw(subject: str, body: str, *, failed: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Mail Delivery Subsystem <mailer-daemon@googlemail.invalid>"
    msg["To"] = "contacto@origenlab.cl"
    msg["Subject"] = subject
    if failed:
        msg["X-Failed-Recipients"] = failed
    msg.set_content(body)
    return msg.as_bytes()


def test_clear_single_no_such_user_is_batch_a_and_auto_blockable() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; dead@cliente.invalid\n"
        "Action: failed\nStatus: 5.1.1\n"
        "Diagnostic-Code: smtp; 550 5.1.1 User unknown; address does not exist.\n",
    ))
    assert (got.reason_code, got.batch, got.batch_reason, got.recipient_count) == (
        "bounce_no_such_user", "A", "no_such_user_final", 1,
    )
    assert got.auto_block_addresses == ("dead@cliente.invalid",)


def test_mailbox_full_is_never_auto_blocked() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; full@cliente.invalid\n"
        "Action: failed\nStatus: 5.2.2\n552 5.2.2 Mailbox full; quota exceeded.\n",
    ))
    assert got.batch == "C" and got.batch_reason == "mailbox_full_or_quota"
    assert got.auto_block_addresses == ()


def test_spf_policy_or_access_denied_is_never_auto_blocked() -> None:
    got = analyze_bounce(raw(
        "Undeliverable",
        "Final-Recipient: rfc822; real@cliente.invalid\n"
        "Action: failed\nStatus: 5.7.1\n554 5.7.1 Message rejected by policy; SPF no valido; access denied.\n",
    ))
    assert got.batch == "D" and got.auto_block_addresses == ()


def test_nxdomain_is_held_instead_of_treating_the_mailbox_as_dead() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Your message wasn't delivered to ventas@dominio-invalido.invalid.\n"
        "DNS Error: lookup of dominio-invalido.invalid returned NXDOMAIN.\n",
    ))
    assert got.batch == "B" and got.batch_reason == "nxdomain_or_domain_not_found"
    assert got.auto_block_addresses == ()


def test_delay_is_never_auto_blocked() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Delay)",
        "Final-Recipient: rfc822; slow@cliente.invalid\nStatus: 4.2.0\nDelivery delayed.\n",
    ))
    assert got.batch == "E" and got.batch_reason == "delay_dsn_excluded"
    assert got.auto_block_addresses == ()


def test_multi_recipient_report_is_held_even_when_both_look_dead() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; first@cliente.invalid\n"
        "Final-Recipient: rfc822; second@cliente.invalid\n"
        "Status: 5.1.1\n550 5.1.1 User unknown.\n",
    ))
    assert got.recipient_count == 2 and got.batch == "E"
    assert got.batch_reason == "multi_recipient_uncertain"
    assert got.auto_block_addresses == ()


def test_x_failed_recipient_can_supply_the_exact_single_address() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "550 5.1.1 User unknown; address not found.",
        failed="missing@cliente.invalid",
    ))
    assert got.batch == "A"
    assert got.auto_block_addresses == ("missing@cliente.invalid",)
