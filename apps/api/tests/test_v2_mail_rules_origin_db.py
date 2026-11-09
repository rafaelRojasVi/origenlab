"""The channel a case came from (`crm.opportunity.origin`), set by the mail rules that create it.

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role. Every name,
address and institution is fictitious.
"""

from __future__ import annotations

import json
import uuid

from test_v2_mail_rules_db import (  # noqa: F401 - `db` and `world` are the module's fixtures, reused
    _apply_only,
    _forward,
    _mail,
    _one,
    _owner,
    _requester_check,
    _web_form_mail,
    db,
    world,
)
from v2_command_harness import needs_db

pytestmark = needs_db


def _origin(db, case_id):
    return _one(db, "select origin from crm.opportunity where id = %s", case_id)[0]


def _created_payload(db, case_id):
    return _one(db, "select payload from crm.domain_event where aggregate_id = %s "
                    "and event_type = 'opportunity.created'", case_id)[0]


def test_r7_a_plain_inbound_request_opens_a_case_from_correo(db, world) -> None:
    applied = _apply_only(db, world, _plain_request(db, world))
    assert applied["rule_id"] == "R7"
    assert _origin(db, applied["case_id"]) == "correo"
    # The opening event says it too: the history never needs the column to know.
    assert _created_payload(db, applied["case_id"])["origin"] == "correo"


def _plain_request(db, world, sender=None):
    sender = sender or f"Ana Ficticia <ana-{uuid.uuid4().hex[:6]}@{world['new_domain']}>"
    with _owner(db) as conn:
        request = _mail(conn, thread=f"t-origin-{uuid.uuid4().hex[:8]}", direction_hint=None, sender=sender,
                        recipients="contacto@origenlab.cl", subject="Solicitud de cotización de equipos")
        _requester_check(conn, request)
    return request


def test_r7_a_free_mail_request_is_correo_too(db, world) -> None:
    applied = _apply_only(db, world, _plain_request(db, world, sender="Persona Ficticia <persona-ficticia-7f3a@gmail.com>"))
    assert (applied["rule_id"], _origin(db, applied["case_id"])) == ("R7", "correo")


def test_a_web_form_message_opens_its_case_from_web(db, world) -> None:
    with _owner(db) as conn:
        form = _web_form_mail(conn, reply_to=f"ana-{uuid.uuid4().hex[:6]}@{world['new_domain']}")
    applied = _apply_only(db, world, form)
    assert applied["rule_id"] == "R7"
    assert _origin(db, applied["case_id"]) == "web"
    assert _created_payload(db, applied["case_id"])["origin"] == "web"


def test_a_web_form_from_a_known_institution_is_web_too(db, world) -> None:
    with _owner(db) as conn:
        form = _web_form_mail(conn, reply_to=f"ana-{uuid.uuid4().hex[:6]}@{world['client_domain']}")
    applied = _apply_only(db, world, form)
    assert (applied["rule_id"], _origin(db, applied["case_id"])) == ("R7", "web")


def test_r12_a_web_form_joining_an_existing_case_leaves_its_origin_alone(db, world) -> None:
    sender = f"ana-{uuid.uuid4().hex[:6]}@{world['new_domain']}"
    case_id = _apply_only(db, world, _plain_request(db, world, sender=f"Ana <{sender}>"))["case_id"]
    assert _origin(db, case_id) == "correo"
    with _owner(db) as conn:
        form = _web_form_mail(conn, reply_to=sender)
    joined = _apply_only(db, world, form)
    assert (joined["rule_id"], joined["case_id"]) == ("R12", case_id)
    assert _origin(db, case_id) == "correo"
    # Nor does an operator's correction get overwritten: a case with its origin already set by a
    # person keeps it when a form joins.
    with _owner(db) as conn:
        conn.execute("update crm.opportunity set origin = 'llamada' where id = %s", (case_id,))
        second = _web_form_mail(conn, reply_to=sender)
    assert _apply_only(db, world, second)["case_id"] == case_id
    assert _origin(db, case_id) == "llamada"


def test_r11_a_request_forwarded_from_labdelivery_is_ex_labdelivery(db, world) -> None:
    with _owner(db) as conn:
        fid, _ = _forward(conn, f"ana@nuevo-{world['tag']}.test", origin="labdelivery")
    applied = _apply_only(db, world, fid)
    assert applied["rule_id"] == "R11"
    assert _origin(db, applied["case_id"]) == "ex_labdelivery"
    assert _created_payload(db, applied["case_id"])["origin"] == "ex_labdelivery"


def test_r11_a_labdelivery_copy_merged_with_our_own_is_still_ex_labdelivery(db, world) -> None:
    email = f"ana@nuevo-{world['tag']}.test"
    with _owner(db) as conn:
        ours, _ = _forward(conn, email)
        _forward(conn, email, origin="labdelivery")
    applied = _apply_only(db, world, ours)
    assert (applied["rule_id"], _origin(db, applied["case_id"])) == ("R11", "ex_labdelivery")


def test_r11_any_other_forwarded_request_is_correo(db, world) -> None:
    with _owner(db) as conn:
        fid, _ = _forward(conn, f"ana@nuevo-{world['tag']}.test", company=f"Laboratorio Nuevo {world['tag']}")
    applied = _apply_only(db, world, fid)
    assert applied["rule_id"] == "R11"
    assert _origin(db, applied["case_id"]) == "correo"


def test_r11_a_forward_joining_an_open_case_leaves_its_origin_alone(db, world) -> None:
    case_id = _apply_only(db, world, world["e_known"])["case_id"]
    with _owner(db) as conn:
        reply = _mail(conn, thread=world["t1"], direction_hint=None,
                      sender=f"Compras <compras@{world['client_domain']}>",
                      recipients="contacto@origenlab.cl", subject="RE: Cotización")
    _apply_only(db, world, reply)
    before = _origin(db, case_id)
    with _owner(db) as conn:
        fid, _ = _forward(conn, f"compras@{world['client_domain']}", origin="labdelivery")
    joined = _apply_only(db, world, fid)
    assert (joined["rule_id"], joined["case_id"]) == ("R11", case_id)
    assert _origin(db, case_id) == before


def test_a_case_a_sent_quote_creates_has_no_origin(db, world) -> None:
    """R3/R10 open a case for a quote already sent: nothing says which channel the request came by."""
    applied = _apply_only(db, world, world["e_known"])
    assert (applied["rule_id"], _origin(db, applied["case_id"])) == ("R3", None)
