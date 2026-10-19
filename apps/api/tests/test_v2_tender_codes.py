import pytest

from origenlab_api.v2.tenders.codes import TENDER_CODE_RE, TenderReferenceInvalid, parse_tender_reference, portal_url

PORTAL = "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx"


@pytest.mark.parametrize(
    "raw",
    [
        "1234567-12-le26",
        " 1234567-12-LE26 ",
        f"{PORTAL}?idlicitacion=1234567-12-LE26",
        f"{PORTAL}?codigo=1234567-12-le26",
    ],
)
def test_parse_accepts(raw):
    assert parse_tender_reference(raw) == "1234567-12-LE26"


@pytest.mark.parametrize("code", ["1057500-25-L126", "1057049-388-CO26", "1234567-12-LE26"])
def test_code_shapes_including_l1(code):
    assert TENDER_CODE_RE.match(code)
    assert parse_tender_reference(code.lower()) == code


def test_opaque_qs_link_refused():
    with pytest.raises(TenderReferenceInvalid) as e:
        parse_tender_reference(f"{PORTAL}?qs=AbC==")
    assert e.value.code == "tender_code_not_in_link"


@pytest.mark.parametrize("raw", ["", "hola", "1234567-LE26", "https://example.com/?idlicitacion=1234567-12-LE26"])
def test_refused(raw):
    with pytest.raises(TenderReferenceInvalid) as e:
        parse_tender_reference(raw)
    assert e.value.code == "tender_reference_invalid"


def test_portal_url():
    assert portal_url("1234567-12-LE26").endswith("idlicitacion=1234567-12-LE26")
