#!/usr/bin/env python3
"""Local review view for the staged Gmail quotation candidates (Q1–Q3).

    uv run python scripts/quote_evidence_review.py --operator you@origenlab.cl

Serves one page on 127.0.0.1 only. It reads the staging files (and, if present, the
document identity report from `scripts/quote_document_identity.py`, with links to each
staged PDF and its extracted text) and appends operator decisions to a JSONL ledger; it opens no database, calls no Google API and creates no
opportunity, quote, revision or evidence row. See `origenlab_api.v2.quote_evidence_review`.

It is deliberately not part of the API app or the dashboard: opening a POST route through
the dashboard proxy is a separate decision the owner has not taken.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from origenlab_api.v2.quote_evidence_review import (
    BASIS_CONFIRMED_PROPOSAL,
    BASIS_OPERATOR_ENTERED,
    DECISION_CONFIRM,
    DECISION_PENDING,
    DECISION_REJECT,
    OPPORTUNITY_CREATE_NEW,
    OPPORTUNITY_EXISTING,
    REVIEWABLE_QUEUES,
    DecisionLedger,
    EmailChain,
    ItemState,
    ReviewItem,
    ReviewRefused,
    Staging,
    email_chain,
    load_staging,
    review_summary,
    thread_sizes,
)
from origenlab_api.v2.quote_document_review import (
    DOCUMENT_LEDGER_FILE,
    OPPORTUNITY_PLANNED,
    PROPOSED_QUOTATION,
    DocumentCandidate,
    DocumentDecisionLedger,
    DocumentReview,
    DocumentState,
    build_document_review,
    document_review_summary,
    opportunity_plans,
    planned_opportunity_ids,
    record_document_decision,
)
from origenlab_api.v2.quote_confirmation_reconciliation import (
    DOC_STATUS_CONFIRMED,
    DOC_STATUS_REJECTED,
    DOC_STATUSES,
    QUEUE_BUCKETS,
    QUEUE_SETTLED_BY_DOCUMENT,
    SOURCE_EMAIL_RECONCILIATION,
    EffectiveDocumentState,
    EmailDocumentStatus,
    EmailReconciliation,
    effective_document_states,
    email_confirmation_refusal,
    email_document_status,
    email_queue_statuses,
    queue_bucket,
    reconcile_email_confirmations,
    record_email_decision,
)
from origenlab_api.v2.quote_drive_evidence import (
    DRIVE_HASH_MATCH,
    DriveEvidence,
    DriveEvidenceIndex,
    build_drive_evidence,
    load_drive_fetches,
)

DEFAULT_STAGING = Path.home() / "data/origenlab-v2-migration/audits/quote-evidence-staging-20260924"
DEFAULT_LEDGER = (
    Path.home() / "data/origenlab-v2-migration/audits/quote-evidence-review-20260924/decisions.jsonl"
)
DEFAULT_IDENTITY_DIR = Path.home() / "data/origenlab-v2-migration/audits/quote-document-identity-20260924"
IDENTITY_JSON = "quote_document_identity.json"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
LOOPBACK = ("127.0.0.1", "localhost")

WARNING_LABELS = {
    "shared_cn": "El mismo CN aparece en otros correos (aviso: posible reenvío o revisión)",
    "cross_era_cn": "El mismo CN aparece también en el archivo histórico",
    "shared_document_bytes": "Los mismos bytes de documento están en otros correos",
    "ambiguous_cn": "Varios CN propuestos: no hay una propuesta única",
    "no_cn": "Ningún CN en los nombres de archivo",
    "direction_unclear": "Dirección dudosa: puede no ser una cotización a cliente",
    "quote_document_without_cn": "Un archivo dice «cotización» pero no trae CN",
}
STATUS_LABELS = {"pending": "Pendiente", "confirmed": "Confirmada", "rejected": "Rechazada"}
# The email queue: the email decision, plus emails settled by their documents without one.
QUEUE_LABELS = {
    "pending": "Pendiente",
    QUEUE_SETTLED_BY_DOCUMENT: "Sin decisión de correo (resuelto por documento)",
    "confirmed": "Confirmada",
    "rejected": "Rechazada",
}
# What the canonical document decisions say about an email's quotation documents.
EMAIL_DOC_LABELS = {
    "confirmed_by_document": "Cotización confirmada por documento",
    "rejected_by_document": "Rechazada por documento",
    "documents_partially_decided": "Documentos decididos en parte",
    "document_pending": "Documento pendiente",
    "no_quotation_document": "Sin documento de cotización",
}
IDENTITY_LABELS = {
    "exact_duplicate": "Duplicado exacto (mismos bytes)",
    "same_client_same_quote": "Mismo cliente, misma cotización",
    "same_client_different_quote": "Mismo cliente, otra cotización",
    "different_explicit_client": "Cliente explícito distinto",
    "conflicting_client_evidence": "Evidencia de cliente en conflicto",
    "insufficient_evidence": "Evidencia insuficiente",
    "brochure_or_generic_document": "Folleto o documento genérico",
    "unique_explicit_quote": "Cotización única con cliente explícito",
}
REVIEW_LABELS = {
    "quote_number_conflict": "El texto trae más de un número de cotización",
    "filename_number_mismatch": "El número del nombre de archivo no coincide con el del texto",
    "no_client_anchor": "El documento no nombra a un cliente (sin línea At./Señores/Cliente)",
    "no_usable_text": "Sin texto utilizable (ni capa de texto ni OCR)",
    "client_conflict": "Anclas de cliente en conflicto",
    "filename_proposal_disagrees_with_documents": "El CN propuesto (nombre de archivo) no coincide con los números impresos",
    "several_quotations_in_one_message": "Varias cotizaciones en un mismo correo",
    "not_staged": "No está en staging",
    "printed_number_on_other_client_document": "El mismo número impreso aparece en un documento para otro cliente",
    "printed_number_on_other_document": "El mismo número impreso aparece en otro documento (¿reenvío o revisión?)",
    "other_client_in_same_email": "Otro documento del mismo correo nombra a otro cliente",
    "no_printed_quote_number": "El documento no imprime un número de cotización",
}
DOC_STATUS_LABELS = {"proposed_quotation": "Propuesta", "needs_review": "Requiere revisión"}
RECONCILIATION_LABELS = {
    "reconciled": "Conciliada con su documento canónico",
    "no_quotation_document_in_email": "Sin documento de cotización en el correo: no se concilia",
    "several_quotation_documents_in_email": "Varias cotizaciones en el correo: confirmación a nivel de correo rechazada",
    "document_client_or_number_not_unambiguous": "El documento no tiene un cliente y un número inequívocos: no se concilia",
    "email_number_disagrees_with_printed_number": "El número del correo no coincide con el impreso: no se concilia",
    "duplicate_email_confirmations_disagree": "Correos duplicados confirmados con oportunidades distintas: no se concilia",
}

CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#57534e;--line:#e7e5e4;--warn:#92400e;--warnbg:#fef3c7;
--ok:#166534;--okbg:#dcfce7;--bad:#991b1b;--badbg:#fee2e2}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:20px 0 8px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);
padding:6px 8px;text-align:left;vertical-align:top}th{font-weight:600;color:var(--muted)}
code,.mono{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}
pre{white-space:pre-wrap;font:12px/1.35 ui-monospace,monospace;background:#fff;border:1px solid var(--line);padding:8px}
.muted{color:var(--muted)}.chip{display:inline-block;border-radius:4px;padding:1px 6px;margin:1px;font-size:12px}
.warn{background:var(--warnbg);color:var(--warn)}.ok{background:var(--okbg);color:var(--ok)}
.bad{background:var(--badbg);color:var(--bad)}.box{border:1px solid var(--line);border-radius:6px;
padding:12px;background:#fff;margin:8px 0}fieldset{border:1px solid var(--line);border-radius:6px;margin:8px 0}
label{display:block;margin:4px 0}input[type=text],textarea{width:100%;max-width:480px;box-sizing:border-box}
.chip.muted{border:1px solid var(--line)}nav a{margin-right:12px}.err{background:var(--badbg);color:var(--bad);padding:8px;border-radius:6px}
"""


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def page(title: str, body: str) -> bytes:
    return (
        "<!doctype html><html lang=es><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>"
    ).encode("utf-8")


def status_chip(state: ItemState, doc_status: EmailDocumentStatus | None = None) -> str:
    """The email's own decision. Without one, an email whose documents are all decided is not
    shown as pending: its status comes from the documents, shown beside it."""
    bucket = queue_bucket(state, doc_status)
    cls = {"confirmed": "ok", "rejected": "bad", QUEUE_SETTLED_BY_DOCUMENT: "muted"}.get(bucket, "warn")
    return f"<span class='chip {cls}' title='decisión a nivel de correo'>{e(QUEUE_LABELS[bucket])}</span>"


def email_doc_chip(doc_status: EmailDocumentStatus | None) -> str:
    """The email's documentary status, from the canonical document decisions."""
    if doc_status is None:
        return "<span class=muted>—</span>"
    tone = {DOC_STATUS_CONFIRMED: "ok", DOC_STATUS_REJECTED: "bad", "no_quotation_document": "muted"}.get(
        doc_status.status, "warn"
    )
    counts = (
        f" <span class=muted>({len(doc_status.confirmed)} confirmada(s), {len(doc_status.rejected)} rechazada(s), "
        f"{len(doc_status.pending)} pendiente(s))</span>"
        if len(doc_status.confirmed) + len(doc_status.rejected) + len(doc_status.pending) >= 2
        else ""
    )
    return (
        f"<span class='chip {tone}' title='estado documental: {e(doc_status.status)}'>"
        f"{e(EMAIL_DOC_LABELS[doc_status.status])}</span>{counts}"
    )


def identity_chip(candidate: dict[str, Any] | None) -> str:
    if not candidate or not candidate.get("classification"):
        return "<span class=muted>—</span>"
    cls = candidate["classification"]
    tone = "bad" if candidate.get("needs_human_review") else ("ok" if cls == "unique_explicit_quote" else "warn")
    return f"<span class='chip {tone}' title='{e(cls)}'>{e(IDENTITY_LABELS.get(cls, cls))}</span>"


def render_identity_summary(identity: dict[str, Any] | None) -> str:
    if identity is None:
        return (
            "<div class=box><b>Identidad de documentos:</b> <span class=muted>sin informe. "
            "Genera uno con <code>scripts/quote_document_identity.py</code>.</span></div>"
        )
    sm = identity["summary"]
    rows = "".join(
        f"<tr><td>{e(IDENTITY_LABELS.get(k, k))}</td><td class=mono>{e(k)}</td><td>{n}</td></tr>"
        for k, n in sm["by_classification"].items()
    )
    docs = ", ".join(f"{e(k)} {n}" for k, n in sm["documents_by_class"].items())
    methods = ", ".join(f"{e(k)} {n}" for k, n in sm["documents_by_extraction"].items())
    return (
        "<div class=box><b>Identidad de documentos</b> "
        f"<span class=muted>(informe {e(identity.get('generated_at'))}; sólo lo que dice cada PDF)</span>"
        f"<p>{sm['candidates']} candidatos · {sm['candidates_needing_review']} requieren revisión humana · "
        f"{sm['distinct_documents']} documentos distintos por SHA-256 ({docs}) · extracción: {methods}</p>"
        f"<table><thead><tr><th>Clasificación</th><th></th><th>Candidatos</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def render_index(
    staging: Staging,
    ledger: DecisionLedger,
    queue: str | None,
    status: str | None,
    identity: dict[str, Any] | None = None,
    review: DocumentReview | None = None,
    doc_states: dict[str, DocumentState] | None = None,
    doc: str | None = None,
) -> bytes:
    summary = review_summary(staging, ledger)
    by_email = {c["email_id"]: c for c in (identity or {}).get("candidates", [])}
    states = ledger.states()
    queue_status = email_queue_statuses(staging, states, review, doc_states or {})
    bucket_counts = {b: 0 for b in QUEUE_BUCKETS}
    doc_counts = {d: 0 for d in DOC_STATUSES}
    for bucket, ds in queue_status.values():
        bucket_counts[bucket] += 1
        if ds is not None:
            doc_counts[ds.status] += 1
    sizes = thread_sizes(staging)
    rows = []
    for it in staging.items:
        st = states.get(it.email_id, ItemState())
        bucket, ds = queue_status[it.email_id]
        if queue and it.queue != queue:
            continue
        if status and bucket != status:
            continue
        if doc and (ds is None or ds.status != doc):
            continue
        warns = "".join(f"<span class='chip warn'>{e(w)}</span>" for w in it.warnings)
        rows.append(
            f"<tr><td><a href='/item/{it.email_id}'>{it.email_id}</a><br>"
            f"<span class=mono>{e(it.gmail_message_id)}</span></td>"
            f"<td>{e(it.sent_at)}</td><td>{e(it.subject)}</td>"
            f"<td class=mono>{e(' '.join(it.proposed_quote_numbers)) or '—'}</td>"
            f"<td>{warns}</td><td>{identity_chip(by_email.get(it.email_id))}</td>"
            f"<td>{status_chip(st, ds)}</td><td>{email_doc_chip(ds)}</td>"
            f"<td><a href='/item/{it.email_id}/cadena'>{sizes.get(it.gmail_thread_id or '', 1)}</a></td></tr>"
        )
    links = " ".join(
        f"<a href='/?{urlencode({'queue': q})}'>{e(q)} ({summary['by_queue'][q]})</a>"
        for q in REVIEWABLE_QUEUES
    )
    status_links = "Correo: " + " ".join(
        f"<a href='/?{urlencode({'status': s})}'>{e(QUEUE_LABELS[s])} ({bucket_counts[s]})</a>"
        for s in QUEUE_BUCKETS
    )
    doc_links = (
        "Documento: " + " ".join(
            f"<a href='/?{urlencode({'doc': d})}'>{e(EMAIL_DOC_LABELS[d])} ({doc_counts[d]})</a>"
            for d in DOC_STATUSES
        )
        if review is not None
        else ""
    )
    body = (
        "<h1>Revisión de cotizaciones Gmail (Q1–Q3)</h1>"
        "<p class=muted>Sólo lectura sobre los archivos de staging. Las decisiones se agregan a un "
        "registro local; no se crea ningún caso, cotización, revisión ni evidencia. "
        f"{summary['historical_records_not_reviewable']} registros históricos L1 quedan como "
        "evidencia histórica y no se revisan aquí.</p>"
        f"<nav><a href='/'>Todos ({summary['reviewable']})</a> {links}"
        f"{' <a href=/documentos>Documentos de cotización</a>' if identity is not None else ''}</nav>"
        f"<nav>{status_links}</nav>"
        + (f"<nav>{doc_links}</nav>" if doc_links else "")
        + "<p class=muted>Pendiente = sin decisión de correo y con documentos de cotización aún sin decidir. "
        "Un correo cuyos documentos ya están decididos no cuenta como pendiente; su decisión de correo "
        "(o su ausencia) se muestra aparte.</p>"
        + render_identity_summary(identity)
        + "<table><thead><tr><th>Correo / Gmail id</th><th>Fecha</th><th>Asunto</th>"
        "<th>CN propuesto</th><th>Avisos</th><th>Identidad del documento</th><th>Estado del correo</th>"
        "<th>Estado documental</th><th>Hilo</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return page("Revisión de cotizaciones", body)


def _found(f: dict[str, Any] | None) -> str:
    if not f:
        return "<span class=muted>—</span>"
    return f"{e(f['value'])}<br><span class='mono muted'>línea {f['line_no']}: {e(f['line'])}</span>"


def render_identity_section(candidate: dict[str, Any] | None, documents: dict[str, dict[str, Any]]) -> str:
    if candidate is None:
        return "<p class=muted>Sin informe de identidad para este correo.</p>"
    reasons = "".join(f"<li class=warn>{e(REVIEW_LABELS.get(r, r))} <span class=mono>({e(r)})</span></li>"
                      for r in candidate["review_reasons"]) or "<li class=muted>Ninguno</li>"
    evidence = "".join(f"<li class=mono>{e(x)}</li>" for x in candidate["classification_evidence"]) or "<li>—</li>"
    doc_blocks = []
    for cd in candidate["documents"]:
        d = documents.get(cd["sha256"])
        if d is None:
            continue
        clients = "".join(
            "<tr><td>" + _found(c["addressee"]) + "</td>"
            f"<td>{e(c['contact']) or '—'}</td><td>{e(c['organization']) or '—'}</td>"
            f"<td>{_found(c['rut'])}</td><td>{_found(c['address'])}</td>"
            "<td>" + "<br>".join(e(x["value"]) for x in c["extra_lines"]) + "</td></tr>"
            for c in d["clients"]
        ) or "<tr><td colspan=6 class=muted>Ninguna línea At./Señores/Cliente en el documento</td></tr>"
        numbers = "".join(
            f"<li>{e(q['raw'])} <span class='mono muted'>línea {q['evidence']['line_no']}: {e(q['evidence']['line'])}</span></li>"
            for q in d["quote_numbers_found"]
        ) or "<li class=muted>—</li>"
        products = "".join(f"<li>{_found(p)}</li>" for p in d["products"]) or "<li class=muted>—</li>"
        doc_reasons = " ".join(f"<span class='chip bad'>{e(r)}</span>" for r in d["review_reasons"])
        doc_blocks.append(
            f"<div class=box><b>{e(' | '.join(d['filenames']))}</b> "
            f"<span class='chip {'ok' if d['document_class'] == 'quotation' else 'warn'}'>{e(d['document_class'])}</span>{doc_reasons}<br>"
            f"<span class=mono>{e(d['sha256'])}</span> · extracción: {e(d['extraction_method'])}"
            f"{' (' + e(d['extraction_detail']) + ')' if d.get('extraction_detail') else ''}<br>"
            f"<a href='/doc/{e(d['sha256'])}'>Abrir PDF</a> · <a href='/doc/{e(d['sha256'])}/texto'>Texto extraído</a>"
            + (
                ""
                if d["document_class"] in ("brochure_or_generic_document", "not_pdf")
                else f"<p><b>Número(s) impreso(s):</b></p><ul>{numbers}</ul>"
                f"<p><b>Fecha:</b> {_found(d['document_date'])}</p>"
                "<table><thead><tr><th>Destinatario (línea exacta)</th><th>Contacto</th><th>Organización</th>"
                f"<th>RUT</th><th>Dirección</th><th>Otras líneas</th></tr></thead><tbody>{clients}</tbody></table>"
                f"<p><b>Producto / modelo:</b></p><ul>{products}</ul>"
            )
            + "</div>"
        )
    relations = "".join(
        f"<tr><td><a href='/item/{r['other_email_id']}'>{r['other_email_id']}</a></td>"
        f"<td>{identity_chip({'classification': r['classification'], 'needs_human_review': r['needs_human_review']})}</td>"
        f"<td class=mono>{e(', '.join(r['links']))}</td>"
        "<td class=mono>" + "<br>".join(e(x) for p in r["document_pairs"] for x in p["evidence"]) + "</td></tr>"
        for r in candidate["relations"]
    ) or "<tr><td colspan=4 class=muted>Ningún otro candidato comparte bytes, hilo, cliente impreso, RUT o número.</td></tr>"
    internal = (
        f"<p><b>Dentro de este correo:</b> {identity_chip({'classification': candidate['internal_classification']})}</p>"
        if candidate.get("internal_classification")
        else ""
    )
    return (
        f"<p>{identity_chip(candidate)} <span class=mono>{e(candidate['classification'])}</span> · "
        f"CN por nombre de archivo: <span class=mono>{e(' '.join(candidate['proposed_quote_numbers_from_filenames'])) or '—'}</span> · "
        f"números impresos: <span class=mono>{e(' '.join(candidate['document_quote_numbers'])) or '—'}</span></p>"
        f"{internal}<p><b>Requiere revisión humana por:</b></p><ul>{reasons}</ul>"
        f"<p><b>Texto exacto que causó la clasificación:</b></p><ul>{evidence}</ul>"
        + "".join(doc_blocks)
        + "<h3>Relación con otros candidatos</h3><p class=muted>Sólo por vínculos exactos; un folleto "
        "compartido no identifica a un cliente. Nada se fusiona.</p>"
        "<table><thead><tr><th>Correo</th><th>Clasificación</th><th>Vínculo</th><th>Evidencia</th></tr></thead>"
        f"<tbody>{relations}</tbody></table>"
    )


def doc_status_chip(state: DocumentState) -> str:
    cls = {"confirmed": "ok", "rejected": "bad"}.get(state.status, "warn")
    return f"<span class='chip {cls}'>{e(STATUS_LABELS[state.status])}</span>"


def proposed_chip(c: DocumentCandidate) -> str:
    tone = "ok" if c.proposed_status == PROPOSED_QUOTATION else "bad"
    return f"<span class='chip {tone}'>{e(DOC_STATUS_LABELS[c.proposed_status])}</span>"


def _client_cell(c: DocumentCandidate) -> str:
    lines = c.client_evidence_lines()
    return "<br>".join(f"<span class=mono>{e(x)}</span>" for x in lines) or "<span class=bad>sin cliente explícito</span>"


def render_email_documents(
    cands: tuple[DocumentCandidate, ...], states: dict[str, DocumentState], email_id: int
) -> str:
    if not cands:
        return "<p class=muted>La auditoría de identidad no encontró ningún documento de cotización en este correo.</p>"
    rows = "".join(
        f"<tr><td><a href='/documento/{c.sha256}'>{e(c.sha256[:12])}…</a></td>"
        f"<td>{e(next(o.filename for o in c.occurrences if o.email_id == email_id))}</td>"
        f"<td>{_client_cell(c)}</td>"
        f"<td class=mono>{e(' '.join(c.printed_quote_numbers)) or '—'}</td>"
        f"<td>{proposed_chip(c)}</td><td>{doc_status_chip(states.get(c.sha256, DocumentState()))}</td>"
        f"<td>{'<span class=muted>canónico</span>' if c.canonical.email_id == email_id else '<span class="chip warn">duplicado de ' + str(c.canonical.email_id) + '</span>'}"
        f"{'<br>' + str(len(c.duplicate_occurrences)) + ' otro(s) correo(s)' if c.duplicate_occurrences else ''}</td></tr>"
        for c in cands
    )
    note = (
        f"<p class=warn>Este correo trae {len(cands)} cotizaciones distintas: cada una se decide por separado. "
        "Pueden compartir una oportunidad y siguen siendo cotizaciones separadas.</p>"
        if len(cands) >= 2
        else ""
    )
    return (
        note + "<table><thead><tr><th>SHA-256</th><th>Archivo</th><th>Cliente (línea exacta)</th>"
        "<th>Número impreso</th><th>Estado propuesto</th><th>Decisión</th><th>Ocurrencias</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_reconciliation(r: EmailReconciliation | None, email_id: int) -> str:
    """What the current email-level confirmation maps to. The email history is not changed."""
    if r is None:
        return ""
    tone = "ok" if r.reconciled else "bad"
    target = (
        f" → documento canónico <a href='/documento/{r.document_sha256}'><span class=mono>{e(r.document_sha256)}</span></a>"
        if r.reconciled
        else ""
    )
    return (
        "<h3>Conciliación de la confirmación de este correo</h3>"
        f"<p><span class='chip {tone}'>{e(RECONCILIATION_LABELS.get(r.outcome, r.outcome))}</span>{target}</p>"
        f"<p class=muted>{e(r.detail)}. La decisión del documento es la canónica; la del correo queda "
        "como evidencia histórica y no se borra ni se reescribe. "
        f"<a href='/item/{email_id}/cadena'>Ver la cadena de correos</a>.</p>"
    )


def render_item(
    item: ReviewItem,
    state: ItemState,
    error: str | None = None,
    identity: dict[str, Any] | None = None,
    documents: tuple[DocumentCandidate, ...] | None = None,
    doc_states: dict[str, DocumentState] | None = None,
    reconciliation: EmailReconciliation | None = None,
    confirm_refusal: str | None = None,
    doc_status: EmailDocumentStatus | None = None,
) -> bytes:
    several = documents is not None and len(documents) >= 2
    docs = "".join(
        f"<tr><td>{e(d.filename)}</td><td class=mono>{e(d.sha256)}</td>"
        f"<td>{'<span class=\"chip ok\">verificado</span>' if d.bytes_hash_verified else '<span class=\"chip bad\">sin verificar</span>'}</td>"
        f"<td class=mono>{e(' '.join(d.cn_tokens)) or '—'}</td>"
        f"<td class=mono>{d.source_attachment_id}</td></tr>"
        for d in item.documents
    )
    ident = "".join(
        f"<tr><th>{e(k)}</th><td class=mono>{e(v) or '<span class=bad>falta</span>'}</td></tr>"
        for k, v in (
            ("email_id", item.email_id),
            ("gmail_message_id", item.gmail_message_id),
            ("gmail_thread_id", item.gmail_thread_id),
            ("rfc822_message_id", item.rfc822_message_id),
            ("raw_sha256", item.raw_sha256),
            ("dedupe_key", item.dedupe_key),
            ("source_uri", item.source_uri),
            ("gmail_label_ids", " ".join(item.gmail_label_ids)),
            ("source_record_sha256", item.source_record_sha256),
        )
    )
    warns = "".join(f"<li class=warn>{e(WARNING_LABELS.get(w, w))}</li>" for w in item.warnings) or "<li>—</li>"
    others = ""
    if item.same_number_other_emails:
        others += "<p>Mismo CN en: " + ", ".join(
            f"<a href='/item/{n}'>{n}</a>" for n in item.same_number_other_emails
        ) + " <span class=muted>(aviso, no vincula nada)</span></p>"
    if item.same_document_other_emails:
        others += "<p>Mismos bytes en: " + ", ".join(
            f"<a href='/item/{n}'>{n}</a>" for n in item.same_document_other_emails
        ) + "</p>"
    missing = "".join(f"<li>{e(m)}</li>" for m in item.missing)
    history = "".join(
        f"<tr><td>{e(h['recorded_at'])}</td><td>{e(h['operator'])}</td><td>{e(h['decision'])}</td>"
        f"<td class=mono>{e((h.get('opportunity') or {}).get('mode'))} "
        f"{e((h.get('opportunity') or {}).get('opportunity_id'))}</td>"
        f"<td class=mono>{e(h.get('quote_number'))} {e(h.get('quote_number_basis'))}</td>"
        f"<td>{e(h.get('reason'))}</td></tr>"
        for h in reversed(state.history)
    ) or "<tr><td colspan=6 class=muted>Sin decisiones</td></tr>"
    single = item.proposed_quote_numbers[0] if not item.cn_is_ambiguous else ""
    basis_default = BASIS_CONFIRMED_PROPOSAL if single else BASIS_OPERATOR_ENTERED
    proposal_radio = (
        f"<label><input type=radio name=quote_number_basis value={BASIS_CONFIRMED_PROPOSAL}"
        f"{' checked' if basis_default == BASIS_CONFIRMED_PROPOSAL else ''}> Confirmo la propuesta "
        f"<code>{e(single)}</code></label>"
        if single
        else "<p class=warn>CN ambiguo: no hay propuesta única. Escribe el número y explica por qué.</p>"
    )
    if several:
        confirm_radio = (
            "<p class=warn>Confirmar a nivel de correo no está disponible: este correo trae varias "
            "cotizaciones. Decide cada documento en la tabla «Cotizaciones en este correo».</p>"
        )
    elif confirm_refusal:
        confirm_radio = (
            f"<p class=warn>Confirmar a nivel de correo no está disponible: {e(confirm_refusal)}. "
            "Decide el documento en la tabla «Cotizaciones en este correo».</p>"
        )
    else:
        confirm_radio = (
            f"<label><input type=radio name=decision value={DECISION_CONFIRM}> Confirmar cotización a cliente "
            "<span class=muted>(queda como evidencia histórica y se concilia a su único documento)</span></label>"
        )
    form = f"""
<form method=post action='/item/{item.email_id}/decide'>
<fieldset><legend>Decisión</legend>
<label><input type=radio name=decision value={DECISION_PENDING} checked> Dejar pendiente</label>
{confirm_radio}
<label><input type=radio name=decision value={DECISION_REJECT}> Rechazar: no es una cotización</label>
</fieldset>
<fieldset><legend>Oportunidad (sólo al confirmar; nunca se sugiere)</legend>
<label><input type=radio name=opportunity_mode value={OPPORTUNITY_EXISTING}> Existente — opportunity_id:</label>
<input type=text name=opportunity_id autocomplete=off placeholder='UUID escrito por el operador'>
<label><input type=radio name=opportunity_mode value={OPPORTUNITY_CREATE_NEW}> Crear una nueva (se registra la intención; no se crea nada ahora)</label>
</fieldset>
<fieldset><legend>Número de cotización (sólo al confirmar)</legend>
{proposal_radio}
<label><input type=radio name=quote_number_basis value={BASIS_OPERATOR_ENTERED}{' checked' if basis_default == BASIS_OPERATOR_ENTERED else ''}> Lo escribo / corrijo yo:</label>
<input type=text name=quote_number value='{e(single)}' autocomplete=off>
</fieldset>
<label>Motivo / nota (obligatorio al rechazar o con CN ambiguo)<br><textarea name=reason rows=2></textarea></label>
<button type=submit>Registrar decisión</button>
</form>"""
    body = (
        f"<nav><a href='/'>← Cola</a> <a href='/item/{item.email_id}/cadena'>Cadena de correos</a></nav>"
        + (f"<p class=err>{e(error)}</p>" if error else "")
        + f"<h1>{e(item.subject) or '(sin asunto)'}</h1>"
        f"<p>Correo: {status_chip(state, doc_status)} · Documento: {email_doc_chip(doc_status)}</p>"
        f"<p class=muted>{e(item.queue)} · {e(item.sent_at)} · dirección: {e(item.direction_hint)}</p>"
        f"<div class=box><b>De:</b> {e(item.sender)}<br><b>Para:</b> {e(item.recipients)}"
        f"<br><b>Asunto (crudo):</b> <span class=mono>{e(item.subject_raw)}</span></div>"
        f"<h2>Identificadores exactos</h2><table>{ident}</table>"
        "<h2>Documentos verificados</h2><table><thead><tr><th>Archivo</th><th>SHA-256</th>"
        f"<th>Bytes</th><th>CN</th><th>attachment_id</th></tr></thead><tbody>{docs}</tbody></table>"
        f"<h2>CN propuesto</h2><p class=mono>{e(' '.join(item.proposed_quote_numbers)) or '—'} "
        "<span class=muted>(propuesta desde el nombre de archivo, no un número aceptado)</span></p>"
        f"<h2>Avisos</h2><ul>{warns}</ul>{others}"
        + (
            "<h2>Identidad del documento (lo que dice el PDF)</h2>"
            + render_identity_section(
                next((c for c in identity["candidates"] if c["email_id"] == item.email_id), None),
                {d["sha256"]: d for d in identity["documents"]},
            )
            if identity is not None
            else ""
        )
        + (
            "<h2>Cotizaciones en este correo (una decisión por documento)</h2>"
            + render_email_documents(documents, doc_states or {}, item.email_id)
            + render_reconciliation(reconciliation, item.email_id)
            if documents is not None
            else ""
        )
        + f"<h2>Falta</h2><ul>{missing}</ul>"
        f"<h2>Decidir</h2>{form}"
        "<h2>Historial (sólo se agrega, nunca se reescribe)</h2><table><thead><tr><th>Cuándo</th>"
        "<th>Operador</th><th>Decisión</th><th>Oportunidad</th><th>Número</th><th>Motivo</th></tr>"
        f"</thead><tbody>{history}</tbody></table>"
    )
    return page(f"Correo {item.email_id}", body)


def render_document_candidate(
    c: DocumentCandidate,
    state: DocumentState,
    email_states: dict[int, ItemState],
    planned: dict[str, tuple[str, ...]],
    error: str | None = None,
    drive: tuple[DriveEvidence, ...] | None = None,
    email_doc_statuses: dict[int, EmailDocumentStatus | None] | None = None,
) -> bytes:
    ident = c.identity
    email_doc_statuses = email_doc_statuses or {}
    occ = "".join(
        f"<tr><td><a href='/item/{o.email_id}'>{o.email_id}</a>"
        f"{' <span class=\"chip ok\">canónico</span>' if i == 0 else ' <span class=\"chip warn\">duplicado</span>'}"
        f"<br>{status_chip(email_states.get(o.email_id, ItemState()), email_doc_statuses.get(o.email_id))}</td>"
        f"<td class=mono>{e(o.sent_at)}</td><td>{e(o.filename)}</td>"
        f"<td class=mono>{e(o.gmail_message_id)}<br>{e(o.rfc822_message_id)}</td>"
        f"<td class=mono>{e(o.gmail_thread_id)}<br><a href='/item/{o.email_id}/cadena'>cadena</a></td>"
        f"<td class=mono>{o.source_attachment_id}</td></tr>"
        for i, o in enumerate(c.occurrences)
    )
    clients = "".join(
        "<tr><td>" + _found(cl.addressee.as_dict()) + "</td>"
        f"<td>{e(cl.contact) or '—'}</td><td>{e(cl.organization) or '—'}</td>"
        f"<td>{_found(cl.rut.as_dict() if cl.rut else None)}</td>"
        f"<td>{_found(cl.address.as_dict() if cl.address else None)}</td></tr>"
        for cl in ident.clients
    ) or "<tr><td colspan=5 class=bad>Ninguna línea At./Señores/Cliente en el documento</td></tr>"
    numbers = "".join(
        f"<li><code>{e(q.raw)}</code> <span class='mono muted'>línea {q.evidence.line_no}: {e(q.evidence.line)}</span></li>"
        for q in ident.quote_numbers_found
    ) or "<li class=bad>Ninguno impreso</li>"
    reasons = "".join(
        f"<li class=warn>{e(REVIEW_LABELS.get(r, r))} <span class=mono>({e(r)})</span></li>" for r in c.review_reasons
    ) or "<li class=muted>Ninguno</li>"
    related = "".join(
        f"<tr><td><a href='/documento/{r.other_sha256}'>{e(r.other_sha256[:12])}…</a></td>"
        f"<td class=mono>{e(', '.join(r.via))}</td>"
        f"<td>{identity_chip({'classification': r.classification})}</td>"
        "<td class=mono>" + "<br>".join(e(x) for x in r.evidence) + "</td></tr>"
        for r in c.related
    ) or "<tr><td colspan=4 class=muted>Ningún otro documento de cotización comparte correo o número impreso.</td></tr>"
    email_warn = render_decision_source(state)
    email_history = "".join(
        f"<tr><td><a href='/item/{n}'>{n}</a> · <a href='/item/{n}/cadena'>cadena</a></td>"
        f"<td>{e(h['recorded_at'])}</td><td>{e(h['operator'])}</td><td>{e(h['decision'])}</td>"
        f"<td class=mono>{e(h.get('quote_number'))}</td><td class=mono>{e(h.get('gmail_message_id'))}<br>"
        f"{e(h.get('rfc822_message_id'))}</td><td>{e(h.get('reason'))}</td></tr>"
        for n in c.email_ids
        for h in reversed(email_states.get(n, ItemState()).history)
    ) or "<tr><td colspan=7 class=muted>Ninguna decisión a nivel de correo</td></tr>"
    history = "".join(
        f"<tr><td>{e(h['recorded_at'])}</td><td>{e(h['operator'])}</td><td>{e(h['decision'])}</td>"
        f"<td class=mono>{e((h.get('opportunity') or {}).get('mode'))} "
        f"{e((h.get('opportunity') or {}).get('opportunity_id') or (h.get('opportunity') or {}).get('planned_opportunity_id'))}</td>"
        f"<td class=mono>{e(h.get('quote_number'))} {e(h.get('quote_number_basis'))}</td>"
        f"<td>{e(h.get('reason'))}</td></tr>"
        for h in reversed(state.history)
    ) or "<tr><td colspan=6 class=muted>Sin decisiones</td></tr>"
    planned_rows = "".join(
        f"<li><code>{e(pid)}</code> — " + ", ".join(f"<a href='/documento/{s}'>{e(s[:12])}…</a>" for s in shas) + "</li>"
        for pid, shas in planned.items()
    ) or "<li class=muted>Ninguna todavía</li>"
    proposal = c.proposed_quote_number
    printed = ident.quote_number.key if ident.quote_number else ""
    proposal_radio = (
        f"<label><input type=radio name=quote_number_basis value={BASIS_CONFIRMED_PROPOSAL} checked> "
        f"Confirmo el número impreso <code>{e(proposal)}</code></label>"
        if proposal
        else "<p class=warn>Sin propuesta: el documento requiere revisión. Escribe el número y explica por qué.</p>"
    )
    form = f"""
<form method=post action='/documento/{c.sha256}/decide'>
<fieldset><legend>Decisión sobre este documento</legend>
<label><input type=radio name=decision value={DECISION_PENDING} checked> Dejar pendiente</label>
<label><input type=radio name=decision value={DECISION_CONFIRM}> Confirmar cotización a cliente</label>
<label><input type=radio name=decision value={DECISION_REJECT}> Rechazar: no es una cotización</label>
</fieldset>
<fieldset><legend>Oportunidad (sólo al confirmar; nunca se sugiere)</legend>
<label><input type=radio name=opportunity_mode value={OPPORTUNITY_EXISTING}> Existente — opportunity_id:</label>
<label><input type=radio name=opportunity_mode value={OPPORTUNITY_CREATE_NEW}> Crear una nueva (se registra un id planificado; no se crea nada ahora)</label>
<label><input type=radio name=opportunity_mode value={OPPORTUNITY_PLANNED}> Unir a una oportunidad planificada por otro documento — id planificado:</label>
<input type=text name=opportunity_id autocomplete=off placeholder='UUID escrito por el operador'>
<p class=muted>Oportunidades planificadas en este registro (referencia, sin orden de parecido):</p><ul>{planned_rows}</ul>
</fieldset>
<fieldset><legend>Número de cotización (sólo al confirmar)</legend>
{proposal_radio}
<label><input type=radio name=quote_number_basis value={BASIS_OPERATOR_ENTERED}{'' if proposal else ' checked'}> Lo escribo / corrijo yo:</label>
<input type=text name=quote_number value='{e(proposal or printed)}' autocomplete=off>
</fieldset>
<label>Motivo / nota (obligatorio al rechazar, si requiere revisión o si el número ya está en otro documento)<br><textarea name=reason rows=2></textarea></label>
<button type=submit>Registrar decisión del documento</button>
</form>"""
    body = (
        f"<nav><a href='/'>← Cola</a> <a href='/documentos'>Documentos</a> "
        f"<a href='/item/{c.canonical.email_id}'>Correo canónico {c.canonical.email_id}</a></nav>"
        + (f"<p class=err>{e(error)}</p>" if error else "")
        + f"<h1>{e(c.filenames[0])} {proposed_chip(c)} {doc_status_chip(state)}</h1>"
        f"<p class=mono>{e(c.sha256)}</p>"
        f"<p><a href='/doc/{c.sha256}'>Abrir PDF</a> · <a href='/doc/{c.sha256}/texto'>Texto extraído</a> · "
        f"clase {e(ident.document_class)} · extracción {e(ident.extraction_method)}</p>"
        "<h2>Correos que traen estos bytes exactos</h2><p class=muted>Un solo documento canónico; los "
        "demás correos quedan como evidencia duplicada, cada uno con sus identificadores.</p>"
        "<table><thead><tr><th>Correo</th><th>Fecha</th><th>Archivo</th><th>Gmail id / Message-ID</th>"
        f"<th>Hilo</th><th>attachment_id</th></tr></thead><tbody>{occ}</tbody></table>"
        f"<h2>Número impreso</h2><ul>{numbers}</ul>"
        f"<p><b>Fecha:</b> {_found(ident.document_date.as_dict() if ident.document_date else None)}</p>"
        "<h2>Cliente (sólo lo que dice el documento)</h2><table><thead><tr><th>Destinatario (línea exacta)</th>"
        f"<th>Contacto</th><th>Organización</th><th>RUT</th><th>Dirección</th></tr></thead><tbody>{clients}</tbody></table>"
        f"<h2>Requiere revisión humana por</h2><ul>{reasons}</ul>"
        "<h2>Otros documentos comparados</h2><p class=muted>Sólo los del mismo correo o con el mismo número "
        "impreso. Nada se fusiona.</p><table><thead><tr><th>Documento</th><th>Vínculo</th><th>Clasificación</th>"
        f"<th>Evidencia</th></tr></thead><tbody>{related}</tbody></table>"
        + render_drive_evidence(drive)
        + f"<h2>Decidir</h2>{email_warn}{form}"
        "<h2>Historial del documento (canónico; sólo se agrega, nunca se reescribe)</h2><table><thead><tr><th>Cuándo</th>"
        "<th>Operador</th><th>Decisión</th><th>Oportunidad</th><th>Número</th><th>Motivo</th></tr>"
        f"</thead><tbody>{history}</tbody></table>"
        "<h2>Decisiones a nivel de correo (evidencia histórica, intactas)</h2><table><thead><tr><th>Correo</th>"
        "<th>Cuándo</th><th>Operador</th><th>Decisión</th><th>Número</th><th>Gmail id / Message-ID</th>"
        f"<th>Motivo</th></tr></thead><tbody>{email_history}</tbody></table>"
    )
    return page(f"Documento {c.sha256[:12]}", body)


def render_decision_source(state: DocumentState) -> str:
    """Where the document's current status comes from, and which email confirmations back it."""
    if not isinstance(state, EffectiveDocumentState) or not state.email_confirmations:
        return ""
    emails = ", ".join(
        f"<a href='/item/{r['email_id']}'>{r['email_id']}</a> "
        f"(<span class=mono>{e(r['gmail_message_id'])}</span>, {e(r['quote_number'])})"
        for r in state.email_confirmations
    )
    if state.source == SOURCE_EMAIL_RECONCILIATION:
        pid = (state.latest or {}).get("opportunity") or {}
        return (
            f"<p class=ok>Confirmada por conciliación de la decisión a nivel de correo {emails}: número impreso "
            f"<code>{e((state.latest or {}).get('quote_number'))}</code>, oportunidad "
            f"{e(pid.get('mode'))} <code>{e(pid.get('opportunity_id') or pid.get('planned_opportunity_id'))}</code>. "
            "Una decisión sobre este documento la reemplaza; la del correo se conserva.</p>"
        )
    return (
        f"<p class=warn>Confirmación(es) a nivel de correo {emails} reemplazada(s) por la decisión de este "
        "documento, que es la canónica. Se conservan como evidencia histórica.</p>"
    )


def render_drive_evidence(drive: tuple[DriveEvidence, ...] | None) -> str:
    """Read-only Drive evidence, matched by exact Drive file id only."""
    if drive is None:
        return ""
    rows = []
    for d in drive:
        f = d.fetch
        if d.available and f is not None:
            tone = "ok" if d.status == DRIVE_HASH_MATCH else "bad"
            label = "Mismo SHA-256" if d.status == DRIVE_HASH_MATCH else "SHA-256 distinto"
            rows.append(
                f"<tr><td class=mono>{e(d.drive_file_id)}</td><td>{e(f.name)}</td><td class=mono>{e(f.mime_type)}</td>"
                f"<td class=mono>{e(f.modified_time)}</td><td class=mono>{e(f.sha256)}</td>"
                f"<td><span class='chip {tone}'>{label}</span>{'<br>' + e(d.reason) if d.reason else ''}</td></tr>"
            )
        else:
            rows.append(
                f"<tr><td class=mono>{e(d.drive_file_id) or '—'}</td><td colspan=4>"
                "<span class='chip bad'>Drive evidence unavailable</span></td>"
                f"<td class=mono>{e(d.reason)}</td></tr>"
            )
    return (
        "<h2>Evidencia en Google Drive (sólo lectura)</h2><p class=muted>Sólo por el id exacto de archivo "
        "que registra el manifiesto de migración; nunca por nombre, asunto o cliente. El SHA-256 se calcula "
        "localmente. Nada en Drive se modifica.</p><table><thead><tr><th>Drive file id</th><th>Nombre</th>"
        "<th>MIME</th><th>Modificado</th><th>SHA-256 local</th><th>Resultado</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_documents_index(
    review: DocumentReview,
    ledger: DocumentDecisionLedger,
    states: dict[str, DocumentState] | None = None,
) -> bytes:
    states = ledger.states() if states is None else states
    summary = document_review_summary(review, ledger, states)
    known = {c.sha256: c for c in review.candidates}

    def quote_chain(sha: str) -> str:
        # Every message carrying the quote, each with its chain — the email history stays in view.
        c = known.get(sha)
        if c is None:
            return ""
        return " · correos " + ", ".join(
            f"<a href='/item/{n}'>{n}</a> (<a href='/item/{n}/cadena'>cadena</a>)" for n in c.email_ids
        )

    def source(sha: str) -> str:
        st = states.get(sha)
        if isinstance(st, EffectiveDocumentState) and st.source == SOURCE_EMAIL_RECONCILIATION:
            return " <span class='chip warn'>conciliada desde correo</span>"
        return ""
    rows = "".join(
        f"<tr><td><a href='/documento/{c.sha256}'>{e(c.sha256[:12])}…</a></td>"
        f"<td>{e(' | '.join(c.filenames))}</td><td>{_client_cell(c)}</td>"
        f"<td class=mono>{e(' '.join(c.printed_quote_numbers)) or '—'}</td>"
        f"<td>{proposed_chip(c)}</td><td>{doc_status_chip(states.get(c.sha256, DocumentState()))}</td>"
        "<td>" + ", ".join(f"<a href='/item/{n}'>{n}</a>" for n in c.email_ids) + "</td></tr>"
        for c in review.candidates
    )
    plans = "".join(
        f"<tr><td class=mono>{e(p.opportunity_key)}</td><td>{e(p.mode)}</td><td>"
        + "<br>".join(
            f"<code>{e(q.quote_number)}</code> — <a href='/documento/{q.document_sha256}'>{e(q.filenames[0] if q.filenames else q.document_sha256[:12])}</a>"
            f"{source(q.document_sha256)}{quote_chain(q.document_sha256)}"
            for q in p.quotes
        )
        + "</td></tr>"
        for p in opportunity_plans(review, ledger, states)
    ) or "<tr><td colspan=3 class=muted>Ningún documento confirmado todavía</td></tr>"
    body = (
        "<nav><a href='/'>← Cola de correos</a></nav><h1>Documentos de cotización</h1>"
        "<p class=muted>Un candidato por documento exacto (SHA-256). Folletos, documentos genéricos y "
        "archivos que no son PDF no son candidatos. Nada se crea; las decisiones van a un registro local.</p>"
        f"<p>{summary['document_candidates']} documentos · {summary['proposed']} propuestos · "
        f"{summary['needs_review']} requieren revisión · {summary['with_duplicate_emails']} en más de un correo · "
        f"{summary['emails_with_several_quote_documents']} correos con varias cotizaciones · "
        + " · ".join(f"{e(STATUS_LABELS[k])} {n}" for k, n in summary["by_status"].items())
        + "</p><h2>Oportunidades con cotizaciones confirmadas (intención, sin crear)</h2>"
        "<table><thead><tr><th>Oportunidad</th><th>Modo</th><th>Cotizaciones (cada una separada)</th></tr></thead>"
        f"<tbody>{plans}</tbody></table>"
        "<h2>Candidatos</h2><table><thead><tr><th>SHA-256</th><th>Archivo(s)</th><th>Cliente</th>"
        "<th>Número impreso</th><th>Estado propuesto</th><th>Decisión</th><th>Correos</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return page("Documentos de cotización", body)


def _chain_row(
    it: ReviewItem, state: ItemState, *, anchor: bool, extra: str = "", doc_status: EmailDocumentStatus | None = None
) -> str:
    docs = "".join(
        f"<li>{e(d.filename)}<br><span class=mono>{e(d.sha256)}</span>"
        f"{' <span class=\"chip warn\">' + e(' '.join(d.cn_tokens)) + '</span>' if d.cn_tokens else ''}</li>"
        for d in it.documents
    ) or "<li class=muted>—</li>"
    mark = " <span class='chip ok'>este correo</span>" if anchor else ""
    return (
        f"<tr><td><a href='/item/{it.email_id}'>{it.email_id}</a>{mark}<br>{status_chip(state, doc_status)}"
        f"{'<br>' + email_doc_chip(doc_status) if doc_status is not None else ''}</td>"
        f"<td class=mono>{e(it.sent_at)}</td>"
        f"<td>{e(it.sender)}<br><span class=muted>→</span> {e(it.recipients) or '<span class=bad>falta</span>'}</td>"
        f"<td>{e(it.subject) or '—'}</td>"
        f"<td class=mono>{e(it.gmail_message_id)}</td>"
        f"<td class=mono>{e(' '.join(it.proposed_quote_numbers)) or '—'}</td>"
        f"<td><ul>{docs}</ul>{extra}</td></tr>"
    )


_CHAIN_HEAD = (
    "<table><thead><tr><th>email_id</th><th>Fecha</th><th>De → Para</th><th>Asunto</th>"
    "<th>Gmail message id</th><th>CN propuesto</th><th>Adjuntos (SHA-256)</th></tr></thead><tbody>"
)


def render_chain(
    chain: EmailChain,
    states: dict[int, ItemState],
    doc_statuses: dict[int, EmailDocumentStatus | None] | None = None,
) -> bytes:
    doc_statuses = doc_statuses or {}
    rows = "".join(
        _chain_row(m, states.get(m.email_id, ItemState()), anchor=m.email_id == chain.anchor_email_id,
                   doc_status=doc_statuses.get(m.email_id))
        for m in chain.messages
    )
    thread = (
        f"<span class=mono>{e(chain.gmail_thread_id)}</span>"
        if chain.gmail_thread_id
        else "<span class=bad>falta: sin gmail_thread_id no se agrupa nada</span>"
    )
    shared_cn = "".join(
        f"<li><code>{e(cn)}</code> en " + ", ".join(f"<a href='/item/{n}'>{n}</a>" for n in ids) + "</li>"
        for cn, ids in chain.shared_quote_numbers.items()
    ) or "<li class=muted>Ninguno</li>"
    dup_docs = "".join(
        f"<li><span class=mono>{e(d.sha256)}</span><br>"
        + ", ".join(f"<a href='/item/{n}'>{n}</a> ({e(name)})" for n, name in d.occurrences)
        + "</li>"
        for d in chain.duplicate_documents
    ) or "<li class=muted>Ninguno</li>"

    related_blocks = []
    for tid, rels in chain.related_by_thread():
        body_rows = "".join(
            _chain_row(
                r.item,
                states.get(r.item.email_id, ItemState()),
                anchor=False,
                extra=(
                    "<p class=muted>Vínculo exacto: "
                    + (f"CN {e(' '.join(r.shared_quote_numbers))}" if r.shared_quote_numbers else "")
                    + ("; " if r.shared_quote_numbers and r.shared_document_sha256 else "")
                    + (
                        "mismos bytes " + ", ".join(f"<span class=mono>{e(s[:12])}…</span>" for s in r.shared_document_sha256)
                        if r.shared_document_sha256
                        else ""
                    )
                    + "</p>"
                ),
            )
            for r in rels
        )
        related_blocks.append(
            f"<h3>Otro hilo: <span class=mono>{e(tid) if tid else 'sin gmail_thread_id'}</span></h3>"
            f"{_CHAIN_HEAD}{body_rows}</tbody></table>"
        )
    related = "".join(related_blocks) or "<p class=muted>Ningún otro correo comparte un CN o bytes exactos.</p>"

    body = (
        f"<nav><a href='/'>← Cola</a> <a href='/item/{chain.anchor_email_id}'>← Correo {chain.anchor_email_id}</a></nav>"
        f"<h1>Cadena de correos del {chain.anchor_email_id}</h1>"
        "<p class=muted>Sólo lectura. Agrupa únicamente los registros de staging con el mismo "
        "<code>gmail_thread_id</code> exacto, en orden cronológico. Sólo aparecen los mensajes que "
        "están en staging: el hilo en Gmail puede tener más (respuestas sin adjunto), y no se "
        "consulta Gmail ni ninguna base de datos. No se infiere ninguna oportunidad; cada correo "
        "conserva su propia decisión.</p>"
        f"<p><b>gmail_thread_id:</b> {thread} · {len(chain.messages)} mensaje(s) en staging</p>"
        f"{_CHAIN_HEAD}{rows}</tbody></table>"
        f"<h2>CN compartidos dentro del hilo</h2><ul>{shared_cn}</ul>"
        f"<h2>Bytes de documento duplicados dentro del hilo</h2><ul>{dup_docs}</ul>"
        "<h2>Evidencia relacionada (otros hilos — no es el mismo registro)</h2>"
        "<p class=muted>Correos de otros hilos que comparten con esta cadena un CN propuesto "
        "idéntico o los mismos bytes de un adjunto (SHA-256). Es un aviso para el operador: no "
        "se fusiona nada, y un folleto compartido no implica el mismo negocio.</p>"
        f"{related}"
    )
    return page(f"Cadena {chain.anchor_email_id}", body)


def allowed_origins(port: int) -> frozenset[str]:
    """The only Origin values accepted: the page itself, reached by either loopback name."""
    return frozenset(f"http://{h}:{port}" for h in LOOPBACK)


def load_identity(identity_dir: Path | None) -> dict[str, Any] | None:
    """The identity report, if one was generated. Read once, never written."""
    if identity_dir is None or not (identity_dir / IDENTITY_JSON).is_file():
        return None
    with (identity_dir / IDENTITY_JSON).open(encoding="utf-8") as f:
        return json.load(f)


def staged_document_paths(staging: Staging, documents_root: Path) -> dict[str, Path]:
    """sha256 → stored file, for staged verified documents only; nothing outside the root."""
    root = documents_root.resolve()
    out: dict[str, Path] = {}
    for it in staging.items:
        for d in it.documents:
            if not d.stored_path or not d.bytes_hash_verified:
                continue
            path = (root / d.stored_path).resolve()
            if root in path.parents:
                out[d.sha256] = path
    return out


def render_document_text(sha: str, text: str | None, doc: dict[str, Any] | None) -> bytes:
    head = (
        f"<nav><a href='/'>← Cola</a> <a href='/doc/{e(sha)}'>Abrir PDF</a></nav>"
        f"<h1>Texto extraído</h1><p class=mono>{e(sha)}</p>"
    )
    if doc is not None:
        head += (
            f"<p>{e(' | '.join(doc['filenames']))} · clase <b>{e(doc['document_class'])}</b> · "
            f"extracción {e(doc['extraction_method'])} · correos "
            + ", ".join(f"<a href='/item/{n}'>{n}</a>" for n in doc["email_ids"])
            + "</p>"
        )
    body = f"<pre>{e(text)}</pre>" if text is not None else "<p class=muted>Sin texto guardado para este documento.</p>"
    return page(f"Texto {sha[:12]}", head + body)


def make_handler(
    staging: Staging,
    ledger: DecisionLedger,
    operator: str,
    port: int,
    *,
    identity: dict[str, Any] | None = None,
    identity_dir: Path | None = None,
    documents_root: Path | None = None,
    document_ledger: DocumentDecisionLedger | None = None,
    drive_evidence_path: Path | None = None,
) -> type:
    allowed_hosts = {f"{h}:{port}" for h in LOOPBACK}
    origins = allowed_origins(port)
    doc_paths = staged_document_paths(staging, documents_root) if documents_root else {}
    identity_docs = {d["sha256"]: d for d in (identity or {}).get("documents", [])}
    # Document-level review exists only when there is an identity report to read it from.
    review: DocumentReview | None = build_document_review(staging, identity) if identity is not None else None
    if document_ledger is None:
        document_ledger = DocumentDecisionLedger(ledger.path.with_name(DOCUMENT_LEDGER_FILE))

    def email_documents(email_id: int) -> tuple[DocumentCandidate, ...] | None:
        return review.for_email(email_id) if review is not None else None

    # Drive evidence is loaded once, read-only, and only ever attached by exact file id.
    drive_index: DriveEvidenceIndex | None = (
        build_drive_evidence(staging, review, load_drive_fetches(drive_evidence_path)) if review is not None else None
    )

    def doc_states() -> dict[str, DocumentState]:
        """Canonical document states: document decisions, else reconciled email confirmations."""
        if review is None:
            return {}
        return effective_document_states(review, document_ledger, ledger.states())  # type: ignore[return-value]

    def email_doc_status(email_id: int, states: dict[str, DocumentState] | None = None) -> EmailDocumentStatus | None:
        if review is None:
            return None
        return email_document_status(review, email_id, doc_states() if states is None else states)

    def email_reconciliation(email_id: int) -> EmailReconciliation | None:
        if review is None:
            return None
        return next((r for r in reconcile_email_confirmations(review, ledger.states()) if r.email_id == email_id), None)

    def confirm_refusal(email_id: int) -> str | None:
        if review is None:
            return None
        refusal = email_confirmation_refusal(review, email_id)
        return refusal[1] if refusal else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "quote-evidence-review"

        def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            # Not `no-referrer`: under it browsers send `Origin: null` on the page's own form
            # POST, which the origin check must refuse. `same-origin` still leaks nothing
            # cross-site, and keeps the real Origin on same-origin requests.
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self, *, require_origin: bool = False) -> bool:
            # Loopback only, and refuse a foreign Host header (DNS rebinding) or Origin.
            # The Origin is compared as an exact string: scheme, name and port, no path, no
            # `null`. A POST must carry one, so a write always proves where it came from.
            if self.headers.get("Host") not in allowed_hosts:
                self._send(HTTPStatus.FORBIDDEN, b"forbidden host", "text/plain")
                return False
            origin = self.headers.get("Origin")
            if (origin is not None or require_origin) and origin not in origins:
                self._send(HTTPStatus.FORBIDDEN, b"forbidden origin", "text/plain")
                return False
            return True

        def _item_id(self, path: str) -> int | None:
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "item" and parts[1].isdigit():
                return int(parts[1])
            return None

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            url = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                self._send(
                    HTTPStatus.OK,
                    render_index(staging, ledger, qs.get("queue"), qs.get("status"), identity,
                                 review=review, doc_states=doc_states(), doc=qs.get("doc")),
                )
                return
            parts = url.path.strip("/").split("/")
            if review is not None:
                if url.path == "/documentos":
                    self._send(HTTPStatus.OK, render_documents_index(review, document_ledger, doc_states()))
                    return
                if parts[0] == "documento" and len(parts) == 2 and _SHA_RE.match(parts[1]):
                    try:
                        cand = review.candidate(parts[1])
                    except ReviewRefused as exc:
                        self._send(HTTPStatus.NOT_FOUND, page("No encontrado", f"<p class=err>{e(exc)}</p>"))
                        return
                    self._send(HTTPStatus.OK, self._document_page(cand))
                    return
            if parts[0] == "doc" and len(parts) in (2, 3) and _SHA_RE.match(parts[1]):
                self._send_document(parts[1], as_text=len(parts) == 3 and parts[2] == "texto")
                return
            email_id = self._item_id(url.path)
            if email_id is not None and url.path == f"/item/{email_id}/cadena":
                try:
                    chain = email_chain(staging, email_id)
                except ReviewRefused as exc:
                    self._send(HTTPStatus.NOT_FOUND, page("No encontrado", f"<p class=err>{e(exc)}</p>"))
                    return
                states = doc_states()
                statuses = {m.email_id: email_doc_status(m.email_id, states) for m in chain.messages}
                self._send(HTTPStatus.OK, render_chain(chain, ledger.states(), statuses))
                return
            if email_id is not None and url.path.count("/") == 2:
                try:
                    item = staging.item(email_id)
                except ReviewRefused as exc:
                    self._send(HTTPStatus.NOT_FOUND, page("No encontrado", f"<p class=err>{e(exc)}</p>"))
                    return
                self._send(
                    HTTPStatus.OK,
                    render_item(item, ledger.states().get(email_id, ItemState()), identity=identity,
                                documents=email_documents(email_id), doc_states=doc_states(),
                                reconciliation=email_reconciliation(email_id),
                                confirm_refusal=confirm_refusal(email_id),
                                doc_status=email_doc_status(email_id)),
                )
                return
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def _document_page(self, cand: DocumentCandidate, error: str | None = None) -> bytes:
            states = doc_states()
            return render_document_candidate(
                cand, states.get(cand.sha256, DocumentState()), ledger.states(),
                planned_opportunity_ids(states), error,
                drive=drive_index.for_document(cand.sha256) if drive_index is not None else None,
                email_doc_statuses={n: email_doc_status(n, states) for n in cand.email_ids},
            )

        def _send_document(self, sha: str, *, as_text: bool) -> None:
            # Only bytes the staging lists as verified, read from under the documents root.
            path = doc_paths.get(sha)
            if as_text:
                if path is None and sha not in identity_docs:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                    return
                txt = identity_dir / "texts" / f"{sha}.txt" if identity_dir else None
                text = txt.read_text(encoding="utf-8") if txt and txt.is_file() else None
                self._send(HTTPStatus.OK, render_document_text(sha, text, identity_docs.get(sha)))
                return
            if path is None or not path.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            data = path.read_bytes()
            is_pdf = data[:5] == b"%PDF-"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf" if is_pdf else "application/octet-stream")
            self.send_header("Content-Disposition", f"{'inline' if is_pdf else 'attachment'}; filename={sha}{path.suffix}")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok(require_origin=True):
                return
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            if (
                review is not None and len(parts) == 3 and parts[0] == "documento"
                and _SHA_RE.match(parts[1]) and parts[2] == "decide"
            ):
                self._decide_document(parts[1])
                return
            email_id = self._item_id(url.path)
            if email_id is None or url.path != f"/item/{email_id}/decide":
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            form = self._form()
            docs = email_documents(email_id)
            try:
                record_email_decision(
                    staging,
                    ledger,
                    review,
                    email_id,
                    decision=form.get("decision", ""),
                    operator=operator,
                    opportunity_mode=form.get("opportunity_mode") or None,
                    opportunity_id=form.get("opportunity_id") or None,
                    quote_number=form.get("quote_number") if form.get("decision") == DECISION_CONFIRM else None,
                    quote_number_basis=form.get("quote_number_basis") if form.get("decision") == DECISION_CONFIRM else None,
                    reason=form.get("reason"),
                    quote_document_count=len(docs) if docs is not None else None,
                )
            except ReviewRefused as exc:
                try:
                    item = staging.item(email_id)
                except ReviewRefused:
                    self._send(HTTPStatus.UNPROCESSABLE_ENTITY, page("Rechazado", f"<p class=err>{e(exc)}</p>"))
                    return
                state = ledger.states().get(email_id, ItemState())
                self._send(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    render_item(item, state, str(exc), identity, documents=docs, doc_states=doc_states(),
                                reconciliation=email_reconciliation(email_id),
                                confirm_refusal=confirm_refusal(email_id),
                                doc_status=email_doc_status(email_id)),
                )
                return
            self._redirect(f"/item/{email_id}")

        def _form(self) -> dict[str, str]:
            length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _decide_document(self, sha: str) -> None:
            assert review is not None
            form = self._form()
            confirming = form.get("decision") == DECISION_CONFIRM
            try:
                record_document_decision(
                    review,
                    document_ledger,
                    sha,
                    email_states=ledger.states(),
                    current=doc_states(),
                    decision=form.get("decision", ""),
                    operator=operator,
                    opportunity_mode=form.get("opportunity_mode") or None,
                    opportunity_id=form.get("opportunity_id") or None,
                    quote_number=form.get("quote_number") if confirming else None,
                    quote_number_basis=form.get("quote_number_basis") if confirming else None,
                    reason=form.get("reason"),
                )
            except ReviewRefused as exc:
                try:
                    cand = review.candidate(sha)
                except ReviewRefused:
                    self._send(HTTPStatus.UNPROCESSABLE_ENTITY, page("Rechazado", f"<p class=err>{e(exc)}</p>"))
                    return
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, self._document_page(cand, str(exc)))
                return
            self._redirect(f"/documento/{sha}")

        def log_message(self, fmt: str, *args: Any) -> None:
            # Paths only: no bodies, so no addresses reach the terminal log.
            sys.stderr.write(f"{self.command} {urlparse(self.path).path}\n")

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--operator", required=True, help="who is deciding; recorded on every entry")
    ap.add_argument(
        "--identity-dir", type=Path, default=DEFAULT_IDENTITY_DIR,
        help="output of scripts/quote_document_identity.py (shown if present)",
    )
    ap.add_argument(
        "--document-ledger", type=Path, default=None,
        help=f"document decision ledger (default: {DOCUMENT_LEDGER_FILE} beside --ledger)",
    )
    ap.add_argument(
        "--drive-evidence", type=Path, default=None,
        help="read-only Drive fetch results (JSON), matched to documents by exact Drive file id",
    )
    ap.add_argument("--host", default="127.0.0.1", choices=LOOPBACK)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    staging = load_staging(args.staging_dir)
    ledger = DecisionLedger(args.ledger)
    summary = review_summary(staging, ledger)
    print(
        f"{summary['reviewable']} reviewable, {summary['historical_records_not_reviewable']} "
        f"historical held back; ledger {args.ledger}\n"
        f"http://{args.host}:{args.port}/",
        file=sys.stderr,
    )
    identity = load_identity(args.identity_dir)
    document_ledger = DocumentDecisionLedger(args.document_ledger or args.ledger.with_name(DOCUMENT_LEDGER_FILE))
    handler = make_handler(
        staging, ledger, args.operator, args.port,
        identity=identity, identity_dir=args.identity_dir, documents_root=args.staging_dir.parent,
        document_ledger=document_ledger, drive_evidence_path=args.drive_evidence,
    )
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
