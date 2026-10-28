"""Read the SII «Registro de Ventas» detail CSV (`RCV_VENTA_<rut>_<yyyymm>.csv`).

The file is what sii.cl → Registro de Compras y Ventas → VENTA → «Descargar Detalles» gives: one
header line, `;`-separated, one row per document. Only the columns below are read; every other
column is ignored, so SII adding one changes nothing. The purchase register (`RCV_COMPRA_…`, a
`RUT Proveedor` column) and the summary (`RCV_RESUMEN_…`, no `Folio`) are refused by name, because
they are the two files that sit next to it in the operator's Downloads folder.

Only facturas (33), facturas exentas (34), notas de débito (56) and notas de crédito (61) are kept;
any other document type is counted as skipped. Amounts are whole pesos.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.quote_document_identity import normalize_rut

KEPT_DOC_TYPES = (33, 34, 56, 61)
MAX_ROWS = 5000

_COLUMNS = {
    "doc_type": "Tipo Doc",
    "client_rut": "Rut cliente",
    "client_name": "Razon Social",
    "folio": "Folio",
    "issued_on": "Fecha Docto",
    "exempt_amount": "Monto Exento",
    "net_amount": "Monto Neto",
    "vat_amount": "Monto IVA",
    "total_amount": "Monto total",
    "reference_doc_type": "Tipo Docto. Referencia",
    "reference_folio": "Folio Docto. Referencia",
}


@dataclass(frozen=True)
class SiiDocument:
    doc_type: int
    folio: int
    issued_on: date
    client_rut: str
    client_name: str
    exempt_amount: int
    net_amount: int
    vat_amount: int
    total_amount: int
    reference_doc_type: int | None
    reference_folio: int | None

    def as_fields(self) -> dict[str, object]:
        return {**self.__dict__, "issued_on": self.issued_on.isoformat()}


@dataclass(frozen=True)
class SiiSalesFile:
    documents: list[SiiDocument]
    skipped_types: dict[int, int]


def _refuse(message: str) -> CommandRefused:
    return CommandRefused(422, "sii_file_invalid", message)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _int(value: str, *, line: int, column: str, required: bool = True) -> int | None:
    value = (value or "").strip()
    if value in ("", "-"):
        if required:
            raise _refuse(f"línea {line}: falta «{column}»")
        return None
    try:
        return int(value.replace(".", ""))
    except ValueError:
        raise _refuse(f"línea {line}: «{column}» no es un número ({value!r})") from None


def parse_sales_register(data: bytes) -> SiiSalesFile:
    text = _decode(data)
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = [h.strip() for h in next(reader, [])]
    if "RUT Proveedor" in header:
        raise _refuse("Este es el registro de COMPRAS. Sube el de VENTAS (RCV_VENTA_…).")
    if "Folio" not in header:
        raise _refuse("Este archivo no es el detalle del Registro de Ventas del SII (RCV_VENTA_…). "
                      "El resumen (RCV_RESUMEN_…) no sirve.")
    missing = [name for name in _COLUMNS.values() if name not in header]
    if missing:
        raise _refuse("Al archivo le faltan columnas del SII: " + ", ".join(missing))
    at = {key: header.index(name) for key, name in _COLUMNS.items()}

    documents: list[SiiDocument] = []
    skipped: dict[int, int] = {}
    seen: set[tuple[int, int]] = set()
    for line, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(documents) + sum(skipped.values()) >= MAX_ROWS:
            raise _refuse(f"el archivo tiene más de {MAX_ROWS} documentos")
        row = row + [""] * (len(header) - len(row))
        cell = {key: row[i].strip() for key, i in at.items()}
        doc_type = _int(cell["doc_type"], line=line, column="Tipo Doc")
        assert doc_type is not None  # noqa: S101 - required
        if doc_type not in KEPT_DOC_TYPES:
            skipped[doc_type] = skipped.get(doc_type, 0) + 1
            continue
        folio = _int(cell["folio"], line=line, column="Folio")
        assert folio is not None  # noqa: S101 - required
        if folio <= 0:
            raise _refuse(f"línea {line}: folio {folio} no es válido")
        if (doc_type, folio) in seen:
            raise _refuse(f"línea {line}: el documento {doc_type} folio {folio} aparece dos veces")
        seen.add((doc_type, folio))
        try:
            issued_on = datetime.strptime(cell["issued_on"], "%d/%m/%Y").date()
        except ValueError:
            raise _refuse(f"línea {line}: «Fecha Docto» no es una fecha ({cell['issued_on']!r})") from None
        rut = normalize_rut(cell["client_rut"])
        if len(rut) < 3:
            raise _refuse(f"línea {line}: falta el RUT del cliente")
        amounts = {k: _int(cell[k], line=line, column=_COLUMNS[k], required=False) or 0
                   for k in ("exempt_amount", "net_amount", "vat_amount")}
        total = _int(cell["total_amount"], line=line, column="Monto total")
        assert total is not None  # noqa: S101 - required
        if total < 0 or any(v < 0 for v in amounts.values()):
            raise _refuse(f"línea {line}: un monto es negativo")
        ref_type = _int(cell["reference_doc_type"], line=line, column="Tipo Docto. Referencia", required=False)
        ref_folio = _int(cell["reference_folio"], line=line, column="Folio Docto. Referencia", required=False)
        if ref_type is None or ref_folio is None or ref_folio <= 0:
            ref_type = ref_folio = None
        documents.append(SiiDocument(
            doc_type=doc_type, folio=folio, issued_on=issued_on, client_rut=rut,
            client_name=cell["client_name"] or rut, total_amount=total,
            reference_doc_type=ref_type, reference_folio=ref_folio, **amounts,
        ))
    if not documents and not skipped:
        raise _refuse("el archivo no trae ningún documento")
    return SiiSalesFile(documents=documents, skipped_types=skipped)
