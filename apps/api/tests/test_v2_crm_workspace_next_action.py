"""The suggested next step names the last touch on the dashboard's one clock, not the quote alone."""

from __future__ import annotations

import pytest

from origenlab_api.v2.crm_workspace import suggest_next_action

LATEST = {"quote_number": "012092A-26", "status": "sent", "sent_at": "2026-05-08T12:00:00+00:00"}
OPP = {"stage": "negotiating", "closed_at": None}


def _text(**kw) -> str:
    return suggest_next_action(OPP, [], LATEST, **kw)["text"]


def test_a_quote_nobody_touched_names_its_sent_date() -> None:
    assert _text() == "Hacer seguimiento de 012092A-26 (enviada 08-05-2026)"
    assert _text(last_contact={"inbound": None, "outbound": None}) == _text()


def test_our_follow_up_email_after_the_quote_is_the_last_contact_and_a_note_is_not() -> None:
    """A note is a reminder to oneself («Mandar correo esta semana»), not a contact with the
    client: it never moves the clock (owner, 2026-10-12)."""
    mail = {"inbound": None, "outbound": {"at": "2026-07-01T10:00:00+00:00"}}
    assert _text(last_contact=mail) == "Hacer seguimiento de 012092A-26 (último contacto 01-07-2026)"
    # The suggestion takes no note at all: a note cannot move it.
    with pytest.raises(TypeError):
        _text(last_note={"created_at": "2026-10-04T10:00:00+00:00"})


def test_a_client_reply_after_our_last_touch_asks_for_an_answer() -> None:
    contact = {"inbound": {"at": "2026-10-02T15:00:00+00:00"}, "outbound": {"at": "2026-09-28T12:00:00+00:00"}}
    assert _text(last_contact=contact) == "Responder al correo del 02-10-2026 sobre 012092A-26"


def test_mail_before_the_quote_does_not_count() -> None:
    old = {"inbound": {"at": "2026-05-01T15:00:00+00:00"}, "outbound": {"at": "2026-05-08T12:00:30+00:00"}}
    assert _text(last_contact=old) == "Hacer seguimiento de 012092A-26 (enviada 08-05-2026)"
