"""The SII «Registro de Ventas» CSV reader (`v2/invoices/sii_csv.py`). Fictitious rows only."""

from __future__ import annotations

from datetime import date

import pytest

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.invoices.sii_csv import parse_sales_register

# The header exactly as SII prints it (2026-10), so a renamed column fails here first.
HEADER = (
    "Nro;Tipo Doc;Tipo Venta;Rut cliente;Razon Social;Folio;Fecha Docto;Fecha Recepcion;Fecha Acuse Recibo;"
    "Fecha Reclamo;Monto Exento;Monto Neto;Monto IVA;Monto total;IVA Retenido Total;IVA Retenido Parcial;"
    "IVA no retenido;IVA propio;IVA Terceros;RUT Emisor Liquid. Factura;Neto Comision Liquid. Factura;"
    "Exento Comision Liquid. Factura;IVA Comision Liquid. Factura;IVA fuera de plazo;Tipo Docto. Referencia;"
    "Folio Docto. Referencia;Num. Ident. Receptor Extranjero;Nacionalidad Receptor Extranjero;"
    "Credito empresa constructora;Impto. Zona Franca (Ley 18211);Garantia Dep. Envases;Indicador Venta sin Costo;"
    "Indicador Servicio Periodico;Monto No facturable;Total Monto Periodo;Venta Pasajes Transporte Nacional;"
    "Venta Pasajes Transporte Internacional;Numero Interno;Codigo Sucursal;NCE o NDE sobre Fact. de Compra;"
    "Codigo Otro Imp.;Valor Otro Imp.;Tasa Otro Imp."
)


def _row(nro, doc_type, rut, name, folio, day, exempt, net, vat, total, ref_type="", ref_folio=""):
    cells = [str(nro), str(doc_type), "Del Giro", rut, name, str(folio), day, f"{day} 10:00:00", "", "",
             str(exempt), str(net), str(vat), str(total), "0", "0", "0", "0", "0", "-", "0", "0", "0", "0",
             str(ref_type), str(ref_folio), "", "", "0", "", "0", "1", "0", "0", "0", "", "", "", "1", "", "", "", ""]
    return ";".join(cells)


def _file(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


def test_reads_facturas_and_a_nota_with_its_reference() -> None:
    parsed = parse_sales_register(_file(
        _row(1, 33, "12345678-5", "INSTITUTO FICTICIO", 9001, "09/10/2026", 0, 100000, 19000, 119000),
        _row(2, 34, "11.111.111-1", "FUNDACION FICTICIA", 9002, "13/10/2026", 50000, 0, 0, 50000),
        _row(3, 61, "12345678-5", "INSTITUTO FICTICIO", 9003, "20/10/2026", 0, 10000, 1900, 11900, 33, 9001),
    ))
    assert [(d.doc_type, d.folio) for d in parsed.documents] == [(33, 9001), (34, 9002), (61, 9003)]
    first, exenta, nota = parsed.documents
    assert first.issued_on == date(2026, 10, 9)
    assert (first.net_amount, first.vat_amount, first.total_amount) == (100000, 19000, 119000)
    assert first.reference_doc_type is None and first.reference_folio is None
    assert exenta.client_rut == "11111111-1"
    assert (nota.reference_doc_type, nota.reference_folio) == (33, 9001)
    assert parsed.skipped_types == {}


def test_other_document_types_are_counted_not_kept() -> None:
    parsed = parse_sales_register(_file(
        _row(1, 39, "66666666-6", "CLIENTE BOLETA", 1, "01/10/2026", 0, 1000, 190, 1190),
        _row(2, 33, "12345678-5", "INSTITUTO FICTICIO", 9001, "09/10/2026", 0, 100000, 19000, 119000),
    ))
    assert [d.folio for d in parsed.documents] == [9001]
    assert parsed.skipped_types == {39: 1}


def test_a_latin1_file_is_read() -> None:
    data = _file(_row(1, 33, "12345678-5", "SOCIEDAD ÑANDÚ FICTICIA", 9001, "09/10/2026", 0, 1, 0, 1))
    parsed = parse_sales_register(data.decode("utf-8").encode("latin-1"))
    assert parsed.documents[0].client_name == "SOCIEDAD ÑANDÚ FICTICIA"


@pytest.mark.parametrize(("data", "fragment"), [
    (b"Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio\n", "COMPRAS"),
    ("Tipo Documento;Total Documentos;Monto Exento\n".encode(), "resumen"),
    (b"", "resumen"),
    (b"Nro;Tipo Doc;Folio\n1;33;5\n", "faltan columnas"),
    (_file(_row(1, 33, "12345678-5", "X", 9001, "2026-10-09", 0, 1, 0, 1)), "Fecha Docto"),
    (_file(_row(1, 33, "12345678-5", "X", 9001, "09/10/2026", 0, 1, 0, "abc")), "Monto total"),
    (_file(_row(1, 33, "12345678-5", "X", 9001, "09/10/2026", 0, 1, 0, 1),
           _row(2, 33, "12345678-5", "X", 9001, "09/10/2026", 0, 1, 0, 1)), "dos veces"),
    (_file(), "ningún documento"),
])
def test_files_that_are_not_the_sales_detail_are_refused_in_spanish(data: bytes, fragment: str) -> None:
    with pytest.raises(CommandRefused) as refused:
        parse_sales_register(data)
    assert refused.value.code == "sii_file_invalid"
    assert fragment in refused.value.message
