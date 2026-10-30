"""`people:v1`: who wrote an inbound email, as its headers and signature state it. Pure; no network."""

from __future__ import annotations

from origenlab_worker.people_reading import NO_PEOPLE_CLASSES, OWN_DOMAINS, PEOPLE_VALUE_NORM, people_value
from origenlab_worker.triage_model import ModelSender

SENDER = "pjsoto@hotmail.invalid"
HEADERS = {SENDER: "Juan Pedro Soto Rivas"}
READ = ModelSender(name="Pedro Soto", title=None, institution="Universidad Ficticia", phone=None)
SIGNATURE = "Pedro Soto\nUniversidad Ficticia"


def value(**over):
    args = {"direction": "inbound", "sender_address": SENDER, "header_names": HEADERS, "reading_sender": READ,
            "signature_text": SIGNATURE, "co_addresses": ["lab@ficticia.invalid"], "forwarded": False}
    return people_value(**{**args, **over})


def test_the_norm_is_versioned() -> None:
    assert PEOPLE_VALUE_NORM == "people:v1"


def test_outbound_has_no_reading() -> None:
    assert value(direction="outbound") is None


def test_forwarded_has_no_reading() -> None:
    assert value(forwarded=True) is None


def test_own_domain_sender_has_no_reading() -> None:
    assert value(sender_address="persona.ficticia@origenlab.cl", header_names={"persona.ficticia@origenlab.cl": "Ana"}) is None


def test_no_sender_address_has_no_reading() -> None:
    assert value(sender_address=None) is None


def test_value_shape() -> None:
    assert value() == {
        "version": 1,
        "sender_address": SENDER,
        "header_name": "Juan Pedro Soto Rivas",
        "signature": {"name": "Pedro Soto", "title": None, "institution": "Universidad Ficticia", "phone": None},
        "co_addresses": ["lab@ficticia.invalid"],
        "signature_raw": SIGNATURE,
    }


def test_co_addresses_capped_at_20() -> None:
    many = [f"p{i}@ficticia.invalid" for i in range(30)]
    assert value(co_addresses=many)["co_addresses"] == many[:20]


def test_signature_raw_is_capped_at_800() -> None:
    assert len(value(signature_text="x" * 2000)["signature_raw"]) == 800


def test_without_a_model_reading_the_header_name_and_signature_still_make_a_reading() -> None:
    v = value(reading_sender=None)
    assert v is not None and v["signature"] is None and v["header_name"] == "Juan Pedro Soto Rivas"


def test_a_model_sender_with_every_field_null_is_no_signature() -> None:
    assert value(reading_sender=ModelSender(None, None, None, None))["signature"] is None


def test_nothing_known_about_the_writer_is_no_reading() -> None:
    assert value(header_names={}, reading_sender=None, signature_text="") is None


def test_a_labdelivery_sender_is_ours_and_has_no_reading() -> None:
    assert value(sender_address="contacto@labdelivery.cl", header_names={"contacto@labdelivery.cl": "Otra Ficticia"}) is None


def test_own_co_addresses_are_dropped() -> None:
    co = ["contacto@labdelivery.cl", "lab@ficticia.invalid", "persona.ficticia@origenlab.cl"]
    assert value(co_addresses=co)["co_addresses"] == ["lab@ficticia.invalid"]


def test_own_domains_are_the_apis() -> None:
    from origenlab_api.v2.person_suggestions import OWN_DOMAINS as API_OWN_DOMAINS

    assert OWN_DOMAINS == API_OWN_DOMAINS == {"origenlab.cl", "labdelivery.cl"}


def test_machine_classes_are_every_class_no_person_wrote() -> None:
    from origenlab_worker.triage import HUMAN_CLASSES
    from origenlab_worker.triage_rules import CLASSES

    assert NO_PEOPLE_CLASSES == {"outbound", "bounce", "auto_reply", "calendar", "unsubscribe", "bulk",
                                 "notification", "empty", "unreadable", "tender_notice"}
    assert NO_PEOPLE_CLASSES | set(HUMAN_CLASSES) == set(CLASSES) and not NO_PEOPLE_CLASSES & set(HUMAN_CLASSES)
