"""What a quote PDF says once it was read: the `quote_document` evidence for a revision's SHA-256.

The catalog's quote-history import (or a later reader) records each read PDF as an
`evidence.source_record` of kind `quote_document`, keyed `quote_document:<sha256>`, with its
currency and neto in the payload and its lines in `evidence.document_line`. The case panel
(`crm_workspace`) and the Cotizador's list (`quotes.reads`) read it through the same columns and
the same `quote_contents`, so the two never disagree on a quote's neto. Amounts travel as text, so
no float ever rounds a peso.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Chile's IVA. Quotes are priced neto; the panel shows what the client pays beside it. Only for
#: CLP: a USD or EUR quote is shown neto, as printed.
IVA_RATE = Decimal("0.19")

#: The read PDF's columns (`doc_currency`, `doc_net_total`, `doc_lines`) over `QUOTE_DOCUMENT_JOIN`'s `qd`.
QUOTE_DOCUMENT_COLUMNS = """qd.payload ->> 'currency' as doc_currency, qd.payload ->> 'net_total' as doc_net_total,
           (select json_agg(json_build_object(
                     'line_no', l.line_no, 'item', l.item_label, 'parent_item', l.parent_item,
                     'kind', l.kind, 'brand', l.brand, 'model', l.model, 'description', l.description,
                     'qty', l.qty::text, 'unit_price', l.unit_price::text, 'line_total', l.line_total::text,
                     'optional', l.optional, 'check_status', l.check_status) order by l.line_no)
              from evidence.document_line l where l.source_record_id = qd.id) as doc_lines"""


def quote_document_join(sha256: str) -> str:
    """The `quote_document` record (`qd`) for the PDF whose SHA-256 is the column `sha256`."""
    return (f"left join evidence.source_record qd\n"
            f"             on qd.dedupe_key = 'quote_document:' || lower({sha256}) and qd.kind = 'quote_document'")


def _money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money_text(value: Decimal | None, currency: str | None) -> str | None:
    if value is None:
        return None
    step = Decimal(1) if currency == "CLP" else Decimal("0.01")
    return str(value.quantize(step, rounding=ROUND_HALF_UP))


def quote_contents(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """What one revision's PDF says, from its `quote_document` evidence (`QUOTE_DOCUMENT_COLUMNS`):
    currency, neto, IVA and total for CLP, and the read lines. None when the PDF was never read.
    The neto is the document's own; without one, the sum of its non-optional line totals."""
    currency = row.get("doc_currency")
    raw = row.get("doc_lines")
    lines = json.loads(raw) if isinstance(raw, str) else (raw or [])
    if not currency and not lines:
        return None
    net = _money(row.get("doc_net_total"))
    net_source = "document"
    if net is None:
        totals = [_money(l.get("line_total")) for l in lines if not l.get("optional")]
        if totals and all(t is not None for t in totals):
            net, net_source = sum(totals, Decimal(0)), "lines"
        else:
            net_source = "none"
    tax = net * IVA_RATE if net is not None and currency == "CLP" else None
    return {
        "currency": currency,
        "net_total": _money_text(net, currency),
        "net_source": net_source,
        "tax_total": _money_text(tax, currency),
        "gross_total": _money_text(net + tax, currency) if tax is not None else None,
        "lines": [
            {k: l.get(k) for k in ("line_no", "item", "parent_item", "kind", "brand", "model", "description",
                                   "qty", "unit_price", "line_total", "optional", "check_status")}
            for l in lines
        ],
    }
