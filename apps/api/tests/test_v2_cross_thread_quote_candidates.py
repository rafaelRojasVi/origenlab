"""Review-candidate matching must fail closed instead of merging cases by a shared mailbox."""

from origenlab_api.v2.crm_workspace import (
    _cross_thread_addresses,
    _cross_thread_quote_code,
)


def test_exact_case_reference_can_select_one_printed_cn_token() -> None:
    assert _cross_thread_quote_code("Compra de productos_ cotización N°01259-26") == "CN01259"
    assert _cross_thread_quote_code("Cotización CN1247-26") == "CN01247"
    assert _cross_thread_quote_code("Solicitud sin número") is None


def test_two_different_numbers_are_ambiguous() -> None:
    assert _cross_thread_quote_code("01259-26 o 01260-26") is None


def test_partial_references_cannot_match() -> None:
    assert _cross_thread_quote_code("cotización 01259") is None
    assert _cross_thread_quote_code("IDa01259-26") is None


def test_shared_company_mailbox_is_not_a_person_match() -> None:
    case = _cross_thread_addresses(
        "Irina <inadiazgalvez@gmail.com>", "contacto@origenlab.cl",
    )
    earlier = _cross_thread_addresses(
        "Tatiana <contacto@origenlab.cl>",
        "Irina <inadiazgalvez@gmail.com>; Otro <otro@example.com>",
    )
    assert case & earlier == {"inadiazgalvez@gmail.com"}
    unrelated = _cross_thread_addresses(
        "Tatiana <contacto@origenlab.cl>", "otra.persona@example.com",
    )
    assert case.isdisjoint(unrelated)


def test_only_origenlab_mailbox_never_suffices() -> None:
    assert not _cross_thread_addresses("contacto@origenlab.cl", "contacto@origenlab.cl")
