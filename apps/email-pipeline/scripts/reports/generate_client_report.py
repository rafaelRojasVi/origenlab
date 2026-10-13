#!/usr/bin/env python3
"""
Build a client-ready report folder: HTML dashboard + summary.json.
Scales to large SQLite DB (single-pass aggregates + one streaming pass for domains).

  uv run python scripts/reports/generate_client_report.py
  uv run python scripts/reports/generate_client_report.py --name pilot_mar2025
  uv run python scripts/reports/generate_client_report.py --embeddings-sample 1500 --embeddings-clusters 10

Output: ORIGENLAB_REPORTS_DIR/<run_id>/index.html, summary.json, (optional) clusters.json
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import re
import sqlite3
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path


def _repo_root() -> Path:
    # scripts/reports/generate_client_report.py -> apps/email-pipeline
    return Path(__file__).resolve().parents[2]


_ROOT = _repo_root()
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.client_report_domains import primary_domain, recip_domains
from origenlab_email_pipeline.client_report_serialize import dumps_report_json
from origenlab_email_pipeline.client_report_text import decode_mime_header_value, is_bounce_sender_for_report
from origenlab_email_pipeline.client_report_metrics import (
    run_attachment_extract_metrics,
    run_attachment_metrics,
    run_merged_aggregate,
    run_year_cotiz_only,
    run_year_counts,
)
from origenlab_email_pipeline.config import load_settings


def _domain_process_chunk(
    payload: tuple[str, int, int, list[str]],
) -> tuple[dict, dict, dict, dict, dict, dict, int]:
    """Worker: count domains for id in [lo, hi]. Returns six Counter dicts + rows processed."""
    db_path, lo, hi, excl_list = payload
    excl = frozenset(excl_list)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    sender_dom = Counter()
    recip_dom = Counter()
    sender_raw = Counter()
    sender_dom_ops = Counter()
    recip_dom_ext = Counter()
    sender_raw_ops = Counter()
    n = 0
    try:
        for s, r in conn.execute(
            "SELECT sender, recipients FROM emails WHERE id >= ? AND id <= ?",
            (lo, hi),
        ):
            n += 1
            s, r = s or "", r or ""
            sender_raw[s[:500]] += 1
            if not is_bounce_sender_for_report(s):
                sender_raw_ops[s[:500]] += 1
            d = primary_domain(s)
            if d != "(no address)":
                sender_dom[d] += 1
                if not is_bounce_sender_for_report(s):
                    sender_dom_ops[d] += 1
            for rd in recip_domains(r):
                recip_dom[rd] += 1
                if rd not in excl:
                    recip_dom_ext[rd] += 1
    finally:
        conn.close()
    return (
        dict(sender_dom),
        dict(recip_dom),
        dict(sender_raw),
        dict(recip_dom_ext),
        dict(sender_dom_ops),
        dict(sender_raw_ops),
        n,
    )


def _merge_dom_results(
    results: list[tuple[dict, dict, dict, dict, dict, dict, int]],
) -> tuple[Counter, Counter, Counter, Counter, Counter, Counter, int]:
    a = Counter()
    b = Counter()
    c = Counter()
    d = Counter()
    e = Counter()
    f = Counter()
    total_rows = 0
    for t in results:
        a.update(t[0])
        b.update(t[1])
        c.update(t[2])
        d.update(t[3])
        e.update(t[4])
        f.update(t[5])
        total_rows += t[6]
    return a, b, c, d, e, f, total_rows


def stream_domain_counts(
    db_path: Path,
    conn: sqlite3.Connection,
    total: int,
    top_n: int,
    sample_limit: int | None,
    exclude_recip_domains: frozenset[str],
    workers: int,
) -> tuple[
    list[dict],
    list[dict],
    list[dict],
    list[dict],
    list[dict],
    list[dict],
    bool,
]:
    from tqdm import tqdm

    sampled = sample_limit is not None
    excl_list = sorted(exclude_recip_domains)

    if sample_limit:
        cur = conn.execute(
            "SELECT sender, recipients FROM emails ORDER BY RANDOM() LIMIT ?",
            (sample_limit,),
        )
        sender_dom = Counter()
        recip_dom = Counter()
        sender_raw = Counter()
        sender_dom_ops = Counter()
        recip_dom_ext = Counter()
        sender_raw_ops = Counter()
        for row in tqdm(
            cur,
            total=sample_limit,
            desc="Domains (random sample)",
            unit="msg",
            miniters=500,
            dynamic_ncols=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
        ):
            s, r = row[0] or "", row[1] or ""
            sender_raw[s[:500]] += 1
            if not is_bounce_sender_for_report(s):
                sender_raw_ops[s[:500]] += 1
            d = primary_domain(s)
            if d != "(no address)":
                sender_dom[d] += 1
                if not is_bounce_sender_for_report(s):
                    sender_dom_ops[d] += 1
            for rd in recip_domains(r):
                recip_dom[rd] += 1
                if rd not in exclude_recip_domains:
                    recip_dom_ext[rd] += 1
    else:
        r = conn.execute("SELECT MIN(id), MAX(id) FROM emails").fetchone()
        id_lo, id_hi = r[0] or 1, r[1] or 1
        n_chunks = max(workers * 4, 8)
        span = max(id_hi - id_lo + 1, 1)
        step = max(span // n_chunks, 1)
        chunks: list[tuple[str, int, int, list[str]]] = []
        x = id_lo
        while x <= id_hi:
            hi = min(x + step - 1, id_hi)
            chunks.append((str(db_path.resolve()), x, hi, excl_list))
            x = hi + 1

        ctx = mp.get_context("spawn")
        merged: list = []
        desc = f"Domains (parallel ×{workers}, full table)"
        with ctx.Pool(workers) as pool:
            for res in tqdm(
                pool.imap_unordered(_domain_process_chunk, chunks, chunksize=1),
                total=len(chunks),
                desc=desc,
                unit="chunk",
                dynamic_ncols=True,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} chunks [{elapsed}<{remaining}]",
            ):
                merged.append(res)
        sender_dom, recip_dom, sender_raw, recip_dom_ext, sender_dom_ops, sender_raw_ops, _ = (
            _merge_dom_results(merged)
        )

    def top(counter: Counter, n: int) -> list[dict]:
        return [{"name": k, "count": v} for k, v in counter.most_common(n)]

    return (
        top(sender_dom, top_n),
        top(recip_dom, top_n),
        top(sender_raw, 50),
        top(recip_dom_ext, top_n),
        top(sender_dom_ops, top_n),
        top(sender_raw_ops, 40),
        sampled,
    )


def build_html(summary: dict) -> str:
    """Self-contained dashboard (Chart.js CDN)."""
    chart_json = json.dumps(
        {
            "years": summary["by_year"],
            "yearsCotiz": summary.get("by_year_cotizacion") or [],
            "classifications": summary["classifications_chart"],
            "equipment": summary["equipment_chart"],
        },
        ensure_ascii=False,
    )
    folder_display = escape(summary.get("folder_display") or summary["run_id"])
    title = "OrigenLab — informe de archivo de correo"

    rows_year = "".join(
        f"<tr><td>{escape(str(x['year']))}</td><td>{x['count']:,}</td></tr>"
        for x in summary["by_year"]
    )
    rows_send = "".join(
        f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>"
        for x in summary["top_sender_domains"][:35]
    )
    rows_recip = "".join(
        f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>"
        for x in summary["top_recipient_domains"][:35]
    )
    rows_raw = "".join(
        f"<tr><td class=\"mono\">{escape(decode_mime_header_value(x['name'])[:120])}</td><td>{x['count']:,}</td></tr>"
        for x in summary["top_senders_raw"][:25]
    )
    excl = ", ".join(summary.get("exclude_recip_domains") or ["labdelivery.cl"])
    rows_recip_ext = "".join(
        f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>"
        for x in (summary.get("top_recipient_domains_external") or [])[:35]
    )
    rows_send_ops = "".join(
        f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>"
        for x in (summary.get("top_sender_domains_operational") or [])[:35]
    )
    rows_raw_ops = "".join(
        f"<tr><td class=\"mono\">{escape(decode_mime_header_value(x['name'])[:120])}</td><td>{x['count']:,}</td></tr>"
        for x in (summary.get("top_senders_operational") or [])[:25]
    )
    rows_year_cotiz = "".join(
        f"<tr><td>{escape(str(x['year']))}</td><td>{x['count']:,}</td></tr>"
        for x in summary.get("by_year_cotizacion") or []
    )
    cross_rows = "".join(
        f"<tr><td>{escape(l)}</td><td>{c:,}</td><td>{escape(pct)}</td></tr>"
        for l, c, pct in summary.get("cross_cotiz_equipo_table") or []
    )
    if not cross_rows:
        cross_rows = "<tr><td colspan=\"3\">(sin filas — ampliar términos si hace falta)</td></tr>"

    class_rows = "".join(
        f"<tr><td>{escape(l)}</td><td>{c:,}</td><td>{pct}</td></tr>"
        for l, c, pct in summary["classification_table"]
    )
    eq_rows = "".join(
        f"<tr><td>{escape(l)}</td><td>{c:,}</td><td>{pct}</td></tr>"
        for l, c, pct in summary["equipment_table"]
    )

    embed_block = ""
    if summary.get("embeddings_note"):
        embed_block = f"<section><h2>Embeddings sample</h2><p>{escape(summary['embeddings_note'])}</p>"
        cluster_summary = summary.get("cluster_summary") or []
        if cluster_summary:
            embed_block += "<table><thead><tr><th>Cluster</th><th>Mensajes</th><th>Asuntos de ejemplo</th></tr></thead><tbody>"
            for c in cluster_summary:
                subjs = "<br/>".join(escape(s) for s in (c.get("subjects") or [])[:5])
                embed_block += f"<tr><td>{c.get('id', '')}</td><td>{c.get('n', 0):,}</td><td class=\"mono\" style=\"font-size:0.8rem\">{subjs}</td></tr>"
            embed_block += "</tbody></table>"
        embed_block += "</section>"

    business_filter_block = ""
    bf = summary.get("business_filter")
    if bf and isinstance(bf, dict) and "error" not in bf:
        s = bf.get("summary") or {}
        pc = s.get("primary_category_counts") or {}
        rollup = s.get("rollup_counts") or {}
        vc = s.get("view_counts") or {}
        t_bf = max(s.get("total_classified") or 1, 1)
        rows_pc = "".join(
            f"<tr><td>{escape(k)}</td><td>{v:,}</td><td>{100 * v / t_bf:.1f}%</td></tr>"
            for k, v in sorted(pc.items(), key=lambda x: -x[1])
        )
        rows_rollup = "".join(
            f"<tr><td>{escape(k)}</td><td>{v:,}</td></tr>" for k, v in sorted(rollup.items())
        )
        rows_vc = "".join(
            f"<tr><td>{escape(k)}</td><td>{vc.get(k, 0):,}</td></tr>" for k in ("all_messages", "operational_no_ndr", "business_only", "business_only_external")
        )
        top_all = s.get("top_sender_domains_all") or []
        top_ops = s.get("top_sender_domains_operational_no_ndr") or []
        top_bo = s.get("top_sender_domains_business_only") or []
        top_bo_ext = s.get("top_sender_domains_business_only_external") or []
        top_senders_bo = s.get("top_senders_business_only") or []
        rows_dom_all = "".join(f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>" for x in top_all[:25])
        rows_dom_ops = "".join(f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>" for x in top_ops[:25])
        rows_dom_bo = "".join(f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>" for x in top_bo[:25])
        rows_dom_bo_ext = "".join(f"<tr><td>{escape(x['name'])}</td><td>{x['count']:,}</td></tr>" for x in top_bo_ext[:25])
        rows_senders_bo = "".join(f"<tr><td class=\"mono\">{escape(decode_mime_header_value(x['name'])[:100])}</td><td>{x['count']:,}</td></tr>" for x in top_senders_bo[:20])
        business_filter_block = f"""
  <section class="card" style="border-color:#3dd68c">
    <h2>Exact vs Heuristic vs Exploratory</h2>
    <p class="sub">Este informe combina: <strong>Exact</strong> = conteos por filas/fechas en la base; <strong>Heuristic</strong> = categorías por reglas (dominios, palabras clave, rebote/NDR); <strong>Exploratory</strong> = clusters/embedding en muestras. Las categorías de negocio abajo son heurísticas, no verdad de terreno.</p>
  </section>
  <section>
    <h2>Filtro de negocio — categoría principal</h2>
    <p class="sub">Clasificación por reglas (dominio, asunto, cuerpo). Una categoría principal por mensaje (precedencia).</p>
    <table><thead><tr><th>Categoría</th><th>Cantidad</th><th>%</th></tr></thead><tbody>{rows_pc}</tbody></table>
  </section>
  <section>
    <h2>Filtro de negocio — flags</h2>
    <table><thead><tr><th>Flag</th><th>Cantidad</th></tr></thead><tbody>{rows_rollup}</tbody></table>
  </section>
  <section>
    <h2>Vistas filtradas (mensajes por vista)</h2>
    <table><thead><tr><th>Vista</th><th>Mensajes</th></tr></thead><tbody>{rows_vc}</tbody></table>
  </section>
  <div class="grid">
    <section class="card">
      <h2>Dominios remitentes — todos</h2>
      <table><thead><tr><th>Dominio</th><th>Count</th></tr></thead><tbody>{rows_dom_all}</tbody></table>
    </section>
    <section class="card">
      <h2>Dominios remitentes — operativo sin NDR</h2>
      <table><thead><tr><th>Dominio</th><th>Count</th></tr></thead><tbody>{rows_dom_ops}</tbody></table>
    </section>
    <section class="card">
      <h2>Dominios remitentes — business_only</h2>
      <table><thead><tr><th>Dominio</th><th>Count</th></tr></thead><tbody>{rows_dom_bo}</tbody></table>
    </section>
    <section class="card">
      <h2>Dominios remitentes — business_only_external</h2>
      <table><thead><tr><th>Dominio</th><th>Count</th></tr></thead><tbody>{rows_dom_bo_ext}</tbody></table>
    </section>
  </div>
  <section>
    <h2>Remitentes exactos — business_only</h2>
    <table><thead><tr><th>From</th><th>Count</th></tr></thead><tbody>{rows_senders_bo}</tbody></table>
  </section>
"""
    elif bf and isinstance(bf, dict) and bf.get("error"):
        business_filter_block = f"<section><h2>Filtro de negocio</h2><p class=\"sub\">Error: {escape(bf['error'])}</p></section>"

    attachments_block = ""
    att = summary.get("attachments")
    if att and isinstance(att, dict):
        ac = att.get("attachment_counts_by_broad_class") or {}
        top_ext = att.get("top_business_doc_extensions") or []
        rows_ext = "".join(
            f"<tr><td>.{escape(x['ext'])}</td><td>{x['count']:,}</td></tr>"
            for x in top_ext[:10]
        )
        attachments_block = f"""
  <section class="card" style="border-color:#3dd68c">
    <h2>Adjuntos — resumen (Phase 2.3)</h2>
    <p class="sub">Solo adjuntos vinculados a emails existentes. Business-doc: PDF, Word, Excel/CSV, zip, XML (sin imagen ni delivery/report).</p>
    <div class="kpi">
      <span>Emails con adjuntos: <strong>{att.get('emails_with_attachments', 0):,}</strong></span>
      <span>Con adjuntos no inline: <strong>{att.get('emails_with_non_inline_attachments', 0):,}</strong></span>
      <span>Con adjuntos business-doc: <strong>{att.get('emails_with_business_doc_attachments', 0):,}</strong></span>
      <span>Cotización + business-doc: <strong>{att.get('cotizacion_emails_with_business_doc_attachments', 0):,}</strong></span>
    </div>
    <p class="sub">Conteo por clase: imágenes {ac.get('images', 0):,} · PDF {ac.get('pdf', 0):,} · Excel/CSV {ac.get('excel_csv', 0):,} · Word {ac.get('word', 0):,} · archivos {ac.get('archives', 0):,} · delivery/report {ac.get('delivery_report_noise', 0):,} · otros {ac.get('other_docs', 0):,}</p>
    <table><thead><tr><th>Extensión (business-doc)</th><th>Cantidad</th></tr></thead><tbody>{rows_ext or '<tr><td colspan="2">—</td></tr>'}</tbody></table>
  </section>
"""

    extracts_block = ""
    ex = summary.get("attachment_extracts")
    if ex and isinstance(ex, dict):
        by_status = ex.get("by_status") or {}
        by_method = ex.get("by_method") or {}
        by_type = ex.get("top_doc_types_success") or []
        rows_type = "".join(
            f"<tr><td>{escape(str(x.get('doc_type')))}</td><td>{int(x.get('count') or 0):,}</td></tr>"
            for x in by_type[:8]
        )
        extracts_block = f"""
  <section class="card" style="border-color:#e0b040">
    <h2>Adjuntos — contenido extraído (Phase 2.4)</h2>
    <p class="sub">Post-pass opcional (sin OCR). Texto truncado y señales heurísticas.</p>
    <div class="kpi">
      <span>Total filas extracción: <strong>{int(ex.get('extracts_total') or 0):,}</strong></span>
      <span>Éxito: <strong>{int(by_status.get('success', 0)):,}</strong></span>
      <span>Vacío: <strong>{int(by_status.get('empty', 0)):,}</strong></span>
      <span>Saltado: <strong>{int(by_status.get('skipped', 0)):,}</strong></span>
    </div>
    <p class="sub">Por método: PDF {int(by_method.get('pdf_text', 0)):,} · DOCX {int(by_method.get('docx', 0)):,} · XLSX {int(by_method.get('xlsx', 0)):,} · CSV {int(by_method.get('csv', 0)):,} · XML {int(by_method.get('xml', 0)):,}</p>
    <table><thead><tr><th>Doc type (success)</th><th>Cantidad</th></tr></thead><tbody>{rows_type or '<tr><td colspan="2">—</td></tr>'}</tbody></table>
  </section>
"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0f1419;
      --card: #1a2332;
      --text: #e7ecf3;
      --muted: #8b9cb3;
      --accent: #3d9cf0;
      --green: #3dd68c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 1.5rem 2rem 4rem;
      line-height: 1.5;
    }}
    h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 0.25rem; }}
    .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }}
    section {{ margin-bottom: 2.5rem; }}
    h2 {{ font-size: 1.1rem; color: var(--accent); border-bottom: 1px solid #2a3544; padding-bottom: 0.5rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.25rem; }}
    .card {{
      background: var(--card);
      border-radius: 10px;
      padding: 1rem 1.25rem;
      border: 1px solid #2a3544;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
    th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #2a3544; }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:hover td {{ background: rgba(61,156,240,0.06); }}
    .mono {{ font-family: ui-monospace, monospace; font-size: 0.78rem; word-break: break-all; }}
    .kpi {{ display: flex; flex-wrap: wrap; gap: 1rem; margin: 1rem 0; }}
    .kpi span {{ background: var(--card); padding: 0.6rem 1rem; border-radius: 8px; border: 1px solid #2a3544; }}
    .kpi strong {{ color: var(--green); }}
    canvas {{ max-height: 320px; }}
  </style>
</head>
<body>
  <h1>OrigenLab — informe de archivo de correo</h1>
  <p class="sub">Carpeta: <strong>{folder_display}</strong> · Generado: {escape(summary['generated_at'])} · Base: <span class="mono">{escape(summary['db'])}</span></p>
  <p class="sub" style="color:#e0b040">{escape(summary.get('domain_stats_note',''))}</p>

  <div class="kpi">
    <span>Total mensajes: <strong>{summary['totals']['total']:,}</strong></span>
    <span>Con fecha: <strong>{summary['totals']['with_date']:,}</strong></span>
    <span>Con cuerpo: <strong>{summary['totals']['with_body']:,}</strong></span>
    <span>Estilo rebote/NDR (heurística): <strong>{summary['totals']['bounce_like']:,}</strong></span>
  </div>

  <section class="card" style="border-color:#3d9cf0">
    <h2>Alcance del informe &amp; qué es rebote/NDR</h2>
    <p class="sub"><strong>NDR</strong> = aviso automático de que un correo <em>no se entregó</em> (Mailer-Daemon, “Delivery failed”). No es negocio; por eso hay tablas <em>operativas</em> sin esos remitentes.</p>
    <p class="sub">Este informe mide <strong>menciones y red</strong>, no unidades vendidas ni facturación. Texto completo para el cliente: <a href="ALCANCE_INFORME.md" style="color:var(--accent)">ALCANCE_INFORME.md</a> (misma carpeta).</p>
  </section>

  <div class="grid">
    <div class="card"><h2>Volumen por año</h2><canvas id="chartYears"></canvas></div>
    <div class="card"><h2>Mensajes con “cotiz…” por año</h2><canvas id="chartYearsCotiz"></canvas></div>
    <div class="card"><h2>Clasificación (menciones en asunto+cuerpo)</h2><canvas id="chartClass"></canvas></div>
    <div class="card"><h2>Equipamiento (menciones)</h2><canvas id="chartEq"></canvas></div>
  </div>

  <section>
    <h2>Clasificación — detalle</h2>
    <p class="sub">Un mismo correo puede entrar en varias filas. Porcentaje sobre total mensajes.</p>
    <table><thead><tr><th>Categoría</th><th>Cantidad</th><th>%</th></tr></thead><tbody>{class_rows}</tbody></table>
  </section>

  <section>
    <h2>Equipos / líneas (detalle)</h2>
    <table><thead><tr><th>Tipo</th><th>Mensajes</th><th>%</th></tr></thead><tbody>{eq_rows}</tbody></table>
  </section>

  <section>
    <h2>Cotización ∧ equipo (mismo mensaje menciona ambos)</h2>
    <p class="sub">Proxy de “en qué equipos aparece la palabra cotización”. No implica venta cerrada.</p>
    <table><thead><tr><th>Cruce</th><th>Cantidad</th><th>Nota</th></tr></thead><tbody>{cross_rows}</tbody></table>
  </section>

  <div class="grid">
    <section class="card">
      <h2>Contrapartes Para/Cc (sin dominio propio)</h2>
      <p class="sub">Excluye: {escape(excl)} — para ver clientes/proveedores fuera del buzón.</p>
      <table><thead><tr><th>Dominio</th><th>Apariciones</th></tr></thead><tbody>{rows_recip_ext}</tbody></table>
    </section>
    <section class="card">
      <h2>Dominios que más envían (operativo, sin NDR)</h2>
      <p class="sub">Sin Mailer-Daemon / postmaster.</p>
      <table><thead><tr><th>Dominio</th><th>Mensajes</th></tr></thead><tbody>{rows_send_ops}</tbody></table>
    </section>
  </div>

  <div class="grid">
    <section class="card">
      <h2>Dominios que más envían (a ustedes) — todos</h2>
      <table><thead><tr><th>Dominio</th><th>Mensajes</th></tr></thead><tbody>{rows_send}</tbody></table>
    </section>
    <section class="card">
      <h2>Para/Cc — todos los dominios (incl. propio)</h2>
      <table><thead><tr><th>Dominio</th><th>Apariciones</th></tr></thead><tbody>{rows_recip}</tbody></table>
    </section>
  </div>

  <section>
    <h2>Remitentes exactos (operativo, sin NDR)</h2>
    <table><thead><tr><th>From</th><th>Count</th></tr></thead><tbody>{rows_raw_ops}</tbody></table>
  </section>

  <section>
    <h2>Remitentes exactos (todos, incl. rebotes)</h2>
    <table><thead><tr><th>From</th><th>Count</th></tr></thead><tbody>{rows_raw}</tbody></table>
  </section>

  <section>
    <h2>Volumen por año (tabla)</h2>
    <table><thead><tr><th>Año</th><th>Mensajes</th></tr></thead><tbody>{rows_year}</tbody></table>
  </section>

  <section>
    <h2>Año × cotización (tabla)</h2>
    <table><thead><tr><th>Año</th><th>Mensajes con cotiz…</th></tr></thead><tbody>{rows_year_cotiz}</tbody></table>
  </section>

  {embed_block}
  {attachments_block}
  {extracts_block}
  {business_filter_block}

  <p class="sub">JSON completo: <a href="summary.json" style="color:var(--accent)">summary.json</a> · Ejecutar de nuevo: <code style="background:#1a2332;padding:2px 8px;border-radius:4px">uv run python scripts/reports/generate_client_report.py</code></p>

  <script>
    const DATA = {chart_json};

    const yearsC = DATA.yearsCotiz || [];
    new Chart(document.getElementById('chartYearsCotiz'), {{
      type: 'bar',
      data: {{
        labels: yearsC.map(x => x.year),
        datasets: [{{
          label: 'Con cotización',
          data: yearsC.map(x => x.count),
          backgroundColor: 'rgba(224, 176, 64, 0.75)',
          borderRadius: 4
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ ticks: {{ color: '#8b9cb3' }}, grid: {{ color: '#2a3544' }} }},
          y: {{ ticks: {{ color: '#8b9cb3' }}, grid: {{ color: '#2a3544' }} }}
        }}
      }}
    }});

    const years = DATA.years || [];
    new Chart(document.getElementById('chartYears'), {{
      type: 'bar',
      data: {{
        labels: years.map(x => x.year),
        datasets: [{{
          label: 'Mensajes',
          data: years.map(x => x.count),
          backgroundColor: 'rgba(61, 156, 240, 0.7)',
          borderRadius: 4
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ ticks: {{ color: '#8b9cb3' }}, grid: {{ color: '#2a3544' }} }},
          y: {{ ticks: {{ color: '#8b9cb3' }}, grid: {{ color: '#2a3544' }} }}
        }}
      }}
    }});

    const cl = DATA.classifications || [];
    new Chart(document.getElementById('chartClass'), {{
      type: 'doughnut',
      data: {{
        labels: cl.map(x => x.label),
        datasets: [{{
          data: cl.map(x => x.count),
          backgroundColor: ['#3d9cf0','#3dd68c','#e0b040','#c07dff','#ff7b7b','#5ce0e0','#999']
        }}]
      }},
      options: {{ plugins: {{ legend: {{ position: 'right', labels: {{ color: '#e7ecf3' }} }} }} }}
    }});

    const eq = DATA.equipment || [];
    new Chart(document.getElementById('chartEq'), {{
      type: 'bar',
      data: {{
        labels: eq.map(x => x.label),
        datasets: [{{
          label: 'Mensajes',
          data: eq.map(x => x.count),
          backgroundColor: 'rgba(61, 216, 140, 0.65)',
          borderRadius: 4
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ ticks: {{ color: '#8b9cb3' }}, grid: {{ color: '#2a3544' }} }},
          y: {{ ticks: {{ color: '#8b9cb3', maxRotation: 0 }}, grid: {{ display: false }} }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate HTML + JSON client report from emails.sqlite")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--name", type=str, default=None, help="Run folder suffix (default: timestamp)")
    ap.add_argument("--out", type=Path, default=None, help="Report directory (overrides reports root)")
    ap.add_argument("--top-domains", type=int, default=50)
    ap.add_argument(
        "--domain-sample",
        type=int,
        default=None,
        metavar="N",
        help="Estimate domain stats from N random rows only (fast on huge DB). Default: full scan.",
    )
    ap.add_argument(
        "--fast",
        action="store_true",
        help="SQL aggregates + año only; skip domain streaming (instant). Re-run with --domain-sample 500000 for dominios.",
    )
    ap.add_argument("--embeddings-sample", type=int, default=0, help="If >0, run ML sample + write clusters.json")
    ap.add_argument("--embeddings-clusters", type=int, default=10)
    ap.add_argument(
        "--exclude-recip-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="Dominio a excluir en tabla Contrapartes Para/Cc (repeatable). Default: labdelivery.cl",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=0,
        metavar="N",
        help="Procesos paralelos para dominios (full table). 0 = all CPUs.",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="Todo: sin muestreo de dominios, todos los CPUs, embeddings 2000 si hay ML.",
    )
    ap.add_argument(
        "--with-business-filter",
        action="store_true",
        help="Run business-only tagging and add filter section + artifacts to report.",
    )
    ap.add_argument(
        "--business-filter-sample",
        type=int,
        default=None,
        metavar="N",
        help="If set, run business filter on N rows only (faster). Use with --with-business-filter.",
    )
    args = ap.parse_args()
    if args.full:
        args.domain_sample = None
        if args.embeddings_sample == 0:
            args.embeddings_sample = 2000
        args.embeddings_clusters = max(args.embeddings_clusters, 14)
    # Use GPU harder when CUDA (embeddings only)
    try:
        import torch

        if torch.cuda.is_available() and args.embeddings_sample > 0:
            args.embeddings_sample = max(args.embeddings_sample, 3500)
    except Exception:
        pass
    workers = args.workers or max(1, (mp.cpu_count() or 4))
    exclude_recip = frozenset(
        (args.exclude_recip_domain or []) or ["labdelivery.cl"]
    )

    settings = load_settings()
    db_path = args.db or settings.resolved_sqlite_path()
    if not db_path.is_file():
        print("DB not found:", db_path, file=sys.stderr)
        sys.exit(1)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if args.name:
        run_id = f"{run_id}_{re.sub(r'[^a-zA-Z0-9_-]+', '_', args.name).strip('_')}"
    out_dir = args.out or (settings.resolved_reports_dir() / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    from tqdm import tqdm

    n_total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    cal_n = min(8000, max(n_total, 1))
    print(f"Rows: {n_total:,} | Calibrating SQL speed on {cal_n:,} rows…")
    t_cal = time.perf_counter()
    conn.execute(
        f"""
        SELECT SUM(CASE WHEN LOWER(COALESCE(subject,'')||COALESCE(body,'')) LIKE '%cotiz%' THEN 1 ELSE 0 END)
        FROM (SELECT subject, body FROM emails LIMIT {cal_n})
        """
    ).fetchone()
    cal_sec = max(time.perf_counter() - t_cal, 1e-6)
    rate = cal_n / cal_sec
    eta_merge = n_total / rate
    eta_year_c = eta_merge * 0.85
    print(
        f"  ~{rate:,.0f} rows/s (LIKE on subject+body) → merged SQL ~{eta_merge/60:.1f} min | año×cotiz ~{eta_year_c/60:.1f} min"
    )

    def run_sql_with_pulse(desc: str, runner, est_sec: float):
        """runner(conn) runs in a dedicated thread with its own SQLite connection (thread-safe)."""
        t0 = time.perf_counter()
        out = [None]
        err = [None]

        def work():
            try:
                c = sqlite3.connect(str(db_path))
                c.row_factory = sqlite3.Row
                try:
                    out[0] = runner(c)
                finally:
                    c.close()
            except Exception as e:
                err[0] = e

        th = threading.Thread(target=work, daemon=True)
        th.start()
        est_sec = max(est_sec, 5.0)
        with tqdm(
            total=100,
            desc=desc,
            unit="%",
            dynamic_ncols=True,
            bar_format="{desc} {bar} {n:3d}% | {elapsed} (ETA ~" + f"{est_sec:.0f}s)",
        ) as pbar:
            while th.is_alive():
                th.join(timeout=0.2)
                elapsed = time.perf_counter() - t0
                pbar.n = min(99, int(100 * elapsed / est_sec))
                pbar.refresh()
            pbar.n = 100
            pbar.refresh()
        th.join()
        if err[0]:
            raise err[0]
        return out[0]

    print(
        "Phase 1/3: SQL en CPU/disco (SQLite no usa GPU). La GPU solo acelera embeddings si --embeddings-sample > 0."
    )
    print("Phase 1/3: single SQL scan (classification + cruces)…")
    agg = run_sql_with_pulse("SQL merged", run_merged_aggregate, eta_merge)
    total = int(agg["total"])
    cross_agg = {k: agg[k] for k in (
        "cotiz_microscopio", "cotiz_centrifuga", "cotiz_balanza", "cotiz_cromatografia",
        "cotiz_autoclave", "cotiz_phmetro", "cotiz_humedad_granos",
    )}

    print("Phase 2/3: año total + año×cotización (2nd full scan for cotiz-by-year)…")
    by_year = run_year_counts(conn)
    by_year_cotiz = run_sql_with_pulse("SQL año×cotiz", run_year_cotiz_only, eta_year_c)

    # Embeddings use GPU — run BEFORE domain streaming so CUDA is not idle behind 400k Python rows
    embeddings_note_early: str | None = None
    clusters_written = False
    cluster_summary_for_html: list[dict] = []
    if args.embeddings_sample > 0:
        try:
            from sklearn.cluster import AgglomerativeClustering
            from sentence_transformers import SentenceTransformer
            import torch

            import numpy as np
            from origenlab_email_pipeline.progress import print_compute_banner, tqdm_stderr

            try:
                from tqdm import tqdm as _tqdm_em
            except ImportError:
                _tqdm_em = None

            device = "cuda" if torch.cuda.is_available() else "cpu"
            batch = 160 if device == "cuda" else 32

            cur = conn.execute(
                """
                SELECT id, subject, sender, body FROM emails
                WHERE length(trim(coalesce(body,''))) >= 50
                ORDER BY RANDOM() LIMIT ?
                """,
                (args.embeddings_sample,),
            )
            print_compute_banner(
                uses_gpu=False,
                workload="SQLite: rows for optional embeddings",
                extra_detail=f"up to {args.embeddings_sample:,} random rows",
            )
            _it = cur
            if _tqdm_em is not None:
                _it = _tqdm_em(
                    cur,
                    total=args.embeddings_sample,
                    desc="Load embedding sample",
                    unit="emails",
                    file=sys.stderr,
                    disable=False,
                    dynamic_ncols=True,
                )
            rows = []
            for r in _it:
                subj, snd, body = r[1] or "", r[2] or "", (r[3] or "")[:900]
                rows.append(
                    {
                        "id": r[0],
                        "subject": subj[:200],
                        "sender": snd[:120],
                        "text": f"Subject: {subj}\nFrom: {snd}\n{body}",
                    }
                )
            if len(rows) >= 20:
                print_compute_banner(
                    uses_gpu=True,
                    workload="Embeddings (client report clusters)",
                    extra_detail=f"device={device} batch={batch} n={len(rows)}",
                )
                model = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2", device=device
                )
                texts_emb = [x["text"] for x in rows]
                n_t, n_b = len(texts_emb), (len(texts_emb) + batch - 1) // batch
                emb_parts: list = []
                for start in tqdm_stderr(
                    range(0, n_t, batch),
                    total=n_b,
                    desc="Embedding batches",
                    unit="batch",
                ):
                    ch = texts_emb[start : start + batch]
                    emb_parts.append(
                        model.encode(
                            ch,
                            batch_size=len(ch),
                            show_progress_bar=False,
                            convert_to_numpy=True,
                            normalize_embeddings=True,
                        )
                    )
                emb = np.vstack(emb_parts)
                n_c = min(args.embeddings_clusters, len(rows) // 3)
                n_c = max(2, n_c)
                lab = AgglomerativeClustering(
                    n_clusters=n_c, metric="cosine", linkage="average"
                ).fit_predict(emb)
                clusters_out: dict[int, list] = {}
                for i, c in enumerate(lab):
                    clusters_out.setdefault(int(c), []).append(
                        {"subject": rows[i]["subject"], "sender": rows[i]["sender"]}
                    )
                payload = {
                    "model": "all-MiniLM-L6-v2",
                    "device": device,
                    "n_sample": len(rows),
                    "n_clusters": n_c,
                    "clusters": {
                        str(k): v[:15]
                        for k, v in sorted(
                            clusters_out.items(), key=lambda x: -len(x[1])
                        )
                    },
                }
                (out_dir / "clusters.json").write_bytes(dumps_report_json(payload))
                clusters_written = True
                cluster_summary_for_html = [
                    {
                        "id": k,
                        "n": len(v),
                        "subjects": [
                            decode_mime_header_value(x.get("subject") or "(sin asunto)")[:90]
                            for x in v[:5]
                        ],
                    }
                    for k, v in sorted(
                        clusters_out.items(), key=lambda x: -len(x[1])
                    )
                ]
                embeddings_note_early = (
                    f"Muestra {len(rows)} mensajes, {n_c} clusters, GPU/CPU: {device}. Detalle abajo y en clusters.json"
                )
                print("Embeddings OK → clusters.json")
            else:
                embeddings_note_early = "Muy pocas filas para clusters."
        except Exception as e:
            embeddings_note_early = f"Embeddings omitidos: {e}"
            print("Embeddings error:", e, file=sys.stderr)

    top_send_dom: list[dict] = []
    top_recip_dom: list[dict] = []
    top_raw: list[dict] = []
    top_recip_ext: list[dict] = []
    top_send_ops: list[dict] = []
    top_raw_ops: list[dict] = []
    domain_sampled = False
    if not args.fast:
        print(f"Phase 3/3: dominios ({'sample' if args.domain_sample else f'{workers} workers, all rows'})…")
        (
            top_send_dom,
            top_recip_dom,
            top_raw,
            top_recip_ext,
            top_send_ops,
            top_raw_ops,
            domain_sampled,
        ) = stream_domain_counts(
            db_path,
            conn,
            total,
            args.top_domains,
            args.domain_sample,
            exclude_recip,
            workers,
        )
    # Business filter pass (optional)
    business_filter_data = None
    if args.with_business_filter:
        try:
            from origenlab_email_pipeline.email_business_filters import run_filter_pass

            bf_limit = getattr(args, "business_filter_sample", None)
            print(f"Business filter pass ({'sample ' + str(bf_limit) if bf_limit else 'full'})…")
            bf_summary, bf_sample, bf_domains = run_filter_pass(
                db_path, bf_limit, args.top_domains, 500
            )
            business_filter_data = {
                "summary": bf_summary,
                "sample": bf_sample,
                "domain_by_view": bf_domains,
            }
            (out_dir / "business_filter_summary.json").write_bytes(
                dumps_report_json({**bf_summary, "db": str(db_path.resolve())})
            )
            (out_dir / "business_only_sample.json").write_bytes(dumps_report_json(bf_sample))
            with (out_dir / "category_counts.csv").open("w", newline="", encoding="utf-8") as f:
                w = __import__("csv").writer(f)
                w.writerow(["category", "count"])
                for cat, count in sorted(
                    bf_summary["primary_category_counts"].items(), key=lambda x: -x[1]
                ):
                    w.writerow([cat, count])
            with (out_dir / "sender_domain_by_view.csv").open("w", newline="", encoding="utf-8") as f:
                w = __import__("csv").writer(f)
                w.writerow(["view", "domain", "count"])
                for view, items in bf_domains.items():
                    for item in items:
                        w.writerow([view, item["domain"], item["count"]])
            print("  → business_filter_summary.json, business_only_sample.json, category_counts.csv, sender_domain_by_view.csv")
        except Exception as e:
            print("Business filter error:", e, file=sys.stderr)
            business_filter_data = {"error": str(e)}

    conn.close()

    t = max(total, 1)
    classifications_chart = [
        {"label": "Cotización", "count": int(agg["cotizacion"])},
        {"label": "Proveedor (palabra)", "count": int(agg["proveedor"])},
        {"label": "Factura / invoice", "count": int(agg["factura_invoice"])},
        {"label": "Pedido / OC", "count": int(agg["pedido_oc"])},
        {"label": "Universidad / .edu", "count": int(agg["universidad"])},
        {"label": "Rebote / NDR (heur.)", "count": int(agg["bounce_like"])},
    ]
    equipment_labels = [
        ("Microscopio", int(agg["eq_microscopio"])),
        ("Centrífuga", int(agg["eq_centrifuga"])),
        ("Espectrofotómetro", int(agg["eq_espectrofotometro"])),
        ("pHmetro", int(agg["eq_phmetro"])),
        ("Autoclave", int(agg["eq_autoclave"])),
        ("Balanza", int(agg["eq_balanza"])),
        ("Cromatografía / HPLC", int(agg["eq_cromatografia"])),
        ("Incubadora", int(agg["eq_incubadora"])),
        ("Titulador", int(agg["eq_titulador"])),
        ("Liofilizador", int(agg["eq_liofilizador"])),
        ("Horno / mufla", int(agg["eq_horno_mufla"])),
        ("Pipetas", int(agg["eq_pipetas"])),
        ("Humedad granos", int(agg["eq_humedad_granos"])),
    ]
    equipment_chart = [{"label": a, "count": b} for a, b in equipment_labels if b > 0]
    if not equipment_chart:
        equipment_chart = [{"label": "(ningún match)", "count": 0}]

    classification_table = [
        ("Cotización (cotiz)", int(agg["cotizacion"]), f"{100 * agg['cotizacion'] / t:.1f}%"),
        ("Proveedor", int(agg["proveedor"]), f"{100 * agg['proveedor'] / t:.1f}%"),
        ("Factura / invoice", int(agg["factura_invoice"]), f"{100 * agg['factura_invoice'] / t:.1f}%"),
        ("Pedido / OC", int(agg["pedido_oc"]), f"{100 * agg['pedido_oc'] / t:.1f}%"),
        ("Universidad / educación", int(agg["universidad"]), f"{100 * agg['universidad'] / t:.1f}%"),
        ("Rebote / NDR", int(agg["bounce_like"]), f"{100 * agg['bounce_like'] / t:.1f}%"),
    ]
    equipment_table = [
        (a, b, f"{100 * b / t:.2f}%") for a, b in equipment_labels if b > 0
    ]

    cotiz_n = max(int(agg["cotizacion"]), 1)
    cross_labels = [
        ("Cotiz ∧ microscopio", int(cross_agg["cotiz_microscopio"])),
        ("Cotiz ∧ centrífuga", int(cross_agg["cotiz_centrifuga"])),
        ("Cotiz ∧ balanza", int(cross_agg["cotiz_balanza"])),
        ("Cotiz ∧ HPLC/cromat.", int(cross_agg["cotiz_cromatografia"])),
        ("Cotiz ∧ autoclave", int(cross_agg["cotiz_autoclave"])),
        ("Cotiz ∧ pHmetro", int(cross_agg["cotiz_phmetro"])),
        ("Cotiz ∧ humedad granos", int(cross_agg["cotiz_humedad_granos"])),
    ]
    cross_table = [
        (lab, c, f"{100 * c / cotiz_n:.1f}% del subconjunto cotización")
        for lab, c in cross_labels
        if c > 0
    ]

    attachment_metrics = run_attachment_metrics(db_path)
    attachment_extract_metrics = run_attachment_extract_metrics(db_path)

    summary = {
        "run_id": run_id,
        "folder_display": out_dir.name,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "db": str(db_path.resolve()),
        "totals": {
            "total": total,
            "with_date": int(agg["with_date"]),
            "with_body": int(agg["with_body"]),
            "bounce_like": int(agg["bounce_like"]),
        },
        "aggregates": {k: int(v) for k, v in agg.items()},
        "by_year": by_year,
        "top_sender_domains": top_send_dom,
        "top_recipient_domains": top_recip_dom,
        "top_senders_raw": top_raw,
        "top_recipient_domains_external": top_recip_ext or [],
        "exclude_recip_domains": sorted(exclude_recip),
        "top_sender_domains_operational": top_send_ops,
        "top_senders_operational": top_raw_ops,
        "by_year_cotizacion": by_year_cotiz,
        "cross_cotiz_equipo": {k: int(v) for k, v in cross_agg.items()},
        "cross_cotiz_equipo_table": cross_table,
        "domain_stats_note": (
            "Dominios estimados sobre muestra aleatoria (ver domain_sample_size)."
            if domain_sampled
            else "Dominios sobre todos los mensajes (pasada completa)."
            if not args.fast
            else "Dominios omitidos (--fast). Ejecute sin --fast o con --domain-sample N."
        ),
        "domain_sample_size": (args.domain_sample if domain_sampled else (total if not args.fast else None)),
        "classifications_chart": classifications_chart,
        "equipment_chart": equipment_chart,
        "classification_table": classification_table,
        "equipment_table": equipment_table,
        "embeddings_note": embeddings_note_early,
        "cluster_summary": cluster_summary_for_html,
        "business_filter": business_filter_data,
        "attachments": attachment_metrics,
        "attachment_extracts": attachment_extract_metrics,
    }

    scope_src = _ROOT / "docs" / "REPORT_SCOPE_CLIENT.md"
    if scope_src.is_file():
        (out_dir / "ALCANCE_INFORME.md").write_text(scope_src.read_text(encoding="utf-8"), encoding="utf-8")

    (out_dir / "summary.json").write_bytes(dumps_report_json(summary))
    (out_dir / "index.html").write_text(build_html(summary), encoding="utf-8")
    print("Wrote:", out_dir / "index.html")
    print("Wrote:", out_dir / "summary.json")
    if clusters_written or (out_dir / "clusters.json").is_file():
        print("Wrote:", out_dir / "clusters.json")


if __name__ == "__main__":
    main()
