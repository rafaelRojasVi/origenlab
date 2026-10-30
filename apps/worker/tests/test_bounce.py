"""NDR analysis: which delivery failures block the address automatically.

Owner 2026-10-29: the four hard kinds the owner approved by hand on 19 and 21 Oct block by
themselves — the mailbox does not exist (5.1.1), Microsoft 365 «recipient not in directory»
(550 5.4.1 Recipient address rejected: Access denied), the domain does not exist (NXDOMAIN) and
a disabled / inactive mailbox (5.2.1). Temporary failures, full mailboxes, delays, mail loops,
spam/policy rejections and multi-recipient reports stay evidence only. The wording below is the
real wording of those reports; every address is invented.
"""

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
    assert got.block_reason == "no_such_user"


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


def test_nxdomain_blocks_the_address() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; ventas@dominio-invalido.invalid\nAction: failed\nStatus: 5.1.2\n"
        "Diagnostic-Code: smtp; DNS Error: DNS type 'mx' lookup of dominio-invalido.invalid responded with code "
        "NXDOMAIN Domain name not found: dominio-invalido.invalid\n",
    ))
    assert got.batch == "B" and got.batch_reason == "nxdomain_or_domain_not_found"
    assert got.block_reason == "domain_not_found"
    assert got.auto_block_addresses == ("ventas@dominio-invalido.invalid",)


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


def test_m365_recipient_not_in_directory_blocks_the_address() -> None:
    got = analyze_bounce(raw(
        "Undeliverable: ¿Necesitas renovar tus balanzas?",
        "Final-Recipient: rfc822; ex.empleado@empresa.invalid\nAction: failed\nStatus: 5.4.1\n"
        "Diagnostic-Code: smtp; 550 5.4.1 Recipient address rejected: Access denied. For more information see "
        "https://aka.ms/EXOSmtpErrors [SJ1PEPF00001234.namprd03.prod.outlook.com 2026-10-22T13:00:00.000Z]\n",
    ))
    assert got.block_reason == "not_in_directory"
    assert got.auto_block_addresses == ("ex.empleado@empresa.invalid",)


def test_disabled_or_inactive_mailbox_blocks_the_address() -> None:
    gmail = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; inactiva@gmail.invalid\nAction: failed\nStatus: 5.2.1\n"
        "Diagnostic-Code: smtp; 550-5.2.1 The email account that you tried to reach is inactive. For more "
        "550-5.2.1 information, go to 550 5.2.1 https://support.google.com/mail/?p=DisabledUser\n",
    ))
    other = analyze_bounce(raw(
        "Undelivered Mail Returned to Sender",
        "Final-Recipient: rfc822; cerrada@cliente.invalid\nAction: failed\n"
        "Diagnostic-Code: smtp; 554 30 Sorry, your message to cerrada@cliente.invalid cannot be delivered. "
        "This mailbox is disabled (554.30).\n",
    ))
    assert (gmail.block_reason, gmail.auto_block_addresses) == ("mailbox_disabled", ("inactiva@gmail.invalid",))
    assert (other.block_reason, other.auto_block_addresses) == ("mailbox_disabled", ("cerrada@cliente.invalid",))


def test_gmail_account_does_not_exist_blocks_the_address() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; nadie@gmail.invalid\nAction: failed\nStatus: 5.1.1\n"
        "Diagnostic-Code: smtp; The email account that you tried to reach does not exist. Please try "
        "double-checking the recipient's email address for typos or unnecessary spaces.\n",
    ))
    assert (got.block_reason, got.auto_block_addresses) == ("no_such_user", ("nadie@gmail.invalid",))


def test_mail_loop_is_never_auto_blocked() -> None:
    got = analyze_bounce(raw(
        "Undeliverable",
        "Final-Recipient: rfc822; bucle@empresa.invalid\nAction: failed\nStatus: 5.4.14\n"
        "Diagnostic-Code: smtp;554 5.4.14 Hop count exceeded - possible mail loop ATTR34\n",
    ))
    assert got.block_reason is None and got.auto_block_addresses == ()


def test_temporary_failure_is_never_auto_blocked() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; lento@cliente.invalid\nAction: failed\nStatus: 4.4.3\n"
        "Diagnostic-Code: smtp; 451 4.4.3 Temporary server error. Please try again later\n",
    ))
    assert got.block_reason is None and got.auto_block_addresses == ()


def test_spam_rejection_that_says_access_denied_is_never_auto_blocked() -> None:
    got = analyze_bounce(raw(
        "Undeliverable",
        "Final-Recipient: rfc822; real@empresa.invalid\nAction: failed\nStatus: 5.7.1\n"
        "Diagnostic-Code: smtp; 550 5.7.1 Service unavailable, Client host blocked using Spamhaus; "
        "Access denied.\n",
    ))
    assert got.block_reason is None and got.auto_block_addresses == ()


def test_mailbox_full_wins_over_a_hard_looking_phrase() -> None:
    got = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "Final-Recipient: rfc822; lleno@cliente.invalid\nStatus: 5.2.2\n"
        "552 5.2.2 The email account that you tried to reach is over quota.\n",
    ))
    assert got.block_reason is None and got.auto_block_addresses == ()


def test_a_global_delivery_status_report_is_read() -> None:
    """RFC 6533 reports (``message/global-delivery-status``) carry the same fields; Gmail sends
    them for some recipients. Before 2026-10-29 their status part was skipped."""
    from email.message import EmailMessage as _M

    outer = _M()
    outer["From"] = "Mail Delivery Subsystem <mailer-daemon@googlemail.invalid>"
    outer["To"] = "contacto@origenlab.cl"
    outer["Subject"] = "Delivery Status Notification (Failure)"
    outer.make_mixed()
    outer.set_type("multipart/report")
    human = _M()
    human.set_content("Your message could not be delivered.")
    outer.attach(human)
    status = _M()
    status.set_payload("Reporting-MTA: dns; googlemail.com\n\nFinal-Recipient: rfc822; borrada@cliente.invalid\n"
                       "Action: failed\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 5.1.1 User unknown\n")
    status.replace_header("Content-Type", "message/global-delivery-status") if status.get("Content-Type") else status.add_header("Content-Type", "message/global-delivery-status")
    outer.attach(status)
    got = analyze_bounce(outer.as_bytes())
    assert (got.block_reason, got.auto_block_addresses) == ("no_such_user", ("borrada@cliente.invalid",))


def test_version_two_and_the_reason_are_in_the_stored_observation() -> None:
    from origenlab_worker.bounce import DELIVERY_FAILURE_VALUE_NORM

    got = analyze_bounce(raw("Undeliverable", "Final-Recipient: rfc822; x@empresa.invalid\nStatus: 5.4.1\n"
                             "550 5.4.1 Recipient address rejected: Access denied.\n"))
    assert DELIVERY_FAILURE_VALUE_NORM == "delivery:v2"
    assert got.as_json()["block_reason"] == "not_in_directory"
    assert got.as_json()["delivery_failure_version"] == 2


def _status(addr: str, status: str, diag: str) -> bytes:
    return raw("Delivery Status Notification (Failure)",
               f"Final-Recipient: rfc822; {addr}\nAction: failed\nStatus: {status}\nDiagnostic-Code: smtp; {diag}\n")


def test_gmail_does_not_exist_split_across_lines_blocks() -> None:
    got = analyze_bounce(_status("nadie2@gmail.invalid", "5.1.3",
                                 "The email account that you tried to reach does not\n exist. Please try double-checking"))
    assert (got.block_reason, got.auto_block_addresses) == ("no_such_user", ("nadie2@gmail.invalid",))


def test_spanish_account_does_not_exist_under_5_7_1_blocks() -> None:
    got = analyze_bounce(_status("antigua@cliente.invalid", "5.7.1",
                                 "550-5.7.1 Estimad@, su correo no pudo ser entregado correctamente\n550-5.7.1 debido a que "
                                 "la cuenta de destino no existe, esta mal escrita o esta\n550-5.7.1 desactivada."))
    assert got.block_reason == "no_such_user"


def test_no_such_recipient_behind_a_filter_under_5_7_1_blocks() -> None:
    got = analyze_bounce(_status("baja@fundacion.invalid", "5.7.1", "550 No such recipient here"))
    assert got.block_reason == "no_such_user"


def test_mimecast_invalid_recipient_blocks() -> None:
    got = analyze_bounce(_status("ido@empresa.invalid", "5.1.1",
                                 "550 Invalid Recipient - https://community.mimecast.com/docs/DOC-1369#550"))
    assert got.block_reason == "no_such_user"


def test_null_mx_and_no_mail_route_block_as_domain_not_found() -> None:
    null_mx = analyze_bounce(_status("x@sinmx.invalid", "5.1.10",
                                     "DNS Error: DNS type 'mx' lookup of sinmx.invalid\n responded with code NOERROR The "
                                     "domain sinmx.invalid doesn't receive email\n according to the administrator: returned Null MX."))
    no_route = analyze_bounce(_status("y@sinruta.invalid", "5.4.3",
                                      "DNS Error: DNS type 'mx' lookup of sinruta.invalid responded\n with code NOERROR DNS "
                                      "type 'mx' lookup of sinruta.invalid had no relevant answers.\n DNS type 'aaaa' lookup "
                                      "of sinruta.invalid responded with code NOERROR"))
    assert null_mx.block_reason == "domain_not_found" and no_route.block_reason == "domain_not_found"


def test_a_spam_filter_rejection_still_holds_even_with_a_hard_phrase() -> None:
    got = analyze_bounce(_status("real2@empresa.invalid", "5.7.1",
                                 "550 5.7.1 Message rejected as spam; recipient not found in allow list"))
    assert got.block_reason is None


def test_spam_inside_a_hostname_or_message_id_is_not_a_spam_rejection() -> None:
    filtered_host = analyze_bounce(raw(
        "Undelivered Mail Returned to Sender",
        "This is the mail system at host mx5.spamtador.invalid.\n"
        "<ido2@cliente.invalid>: host 192.0.2.7 said: 550 5.1.1 <ido2@cliente.invalid>: Recipient address rejected: "
        "User unknown (in reply to RCPT TO command)\nFinal-Recipient: rfc822; ido2@cliente.invalid\nAction: failed\n",
    ))
    odd_id = analyze_bounce(raw(
        "Delivery Status Notification (Failure)",
        "X-Original-Message-ID: <CAxkspamq@mail.gmail.invalid>\nFinal-Recipient: rfc822; dormida@gmail.invalid\n"
        "Action: failed\nStatus: 5.2.1\nDiagnostic-Code: smtp; 550-5.2.1 The email account that you tried to reach is inactive.\n",
    ))
    assert filtered_host.block_reason == "no_such_user"
    assert odd_id.block_reason == "mailbox_disabled"


def test_chilean_no_existe_dicha_persona_blocks() -> None:
    got = analyze_bounce(_status("persona@cliente.invalid", "5.0.350",
                                 "550 5.0.350 Remote server returned an error -> 550 No existe dicha persona en esta direccion."))
    assert got.block_reason == "no_such_user"
