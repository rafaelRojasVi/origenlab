#!/usr/bin/env python3
"""
Build an HTML report with visuals from ml_explore.json: KMeans clusters, HDBSCAN summary,
equipment mentions bar chart. No raw JSON — charts and cards you can open in a browser.

Requires `ml_explore.json` from `email_ml_explore.py` (HDBSCAN section needs `uv sync --group ml`).

  uv sync --group ml
  uv run python scripts/reports/build_ml_report.py --json path/to/ml_explore.json --out path/to/ml_report.html
  uv run python scripts/reports/build_ml_report.py --batch path/to/run_NAME   # uses run_NAME/ml_explore.json, writes run_NAME/ml_report.html
"""
from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build ml_report.html from ml_explore.json")
    ap.add_argument("--json", type=Path, help="Path to ml_explore.json")
    ap.add_argument("--out", type=Path, help="Output HTML path (default: same dir as JSON, ml_report.html)")
    ap.add_argument("--batch", type=Path, help="Batch folder: use batch/ml_explore.json and write batch/ml_report.html")
    args = ap.parse_args()

    if args.batch:
        args.batch = args.batch.resolve()
        json_path = args.batch / "ml_explore.json"
        out_path = args.batch / "ml_report.html"
    elif args.json:
        json_path = Path(args.json).resolve()
        out_path = (Path(args.out) if args.out else json_path.parent / "ml_report.html").resolve()
    else:
        raise SystemExit("Use --json FILE or --batch DIR")

    if not json_path.is_file():
        raise SystemExit(f"JSON not found: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    # Embed as JSON for the page script (escape for </script>)
    data_js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    n_sample = data.get("n_sample", 0)
    kmeans_k = data.get("kmeans_k", 0)
    agg_k = data.get("agglomerative_k", 0)
    hdb = data.get("hdbscan") or {}
    hdb_clusters = hdb.get("clusters") if isinstance(hdb, dict) else None
    hdb_noise = hdb.get("noise_points") if isinstance(hdb, dict) else None
    hdb_skipped = hdb.get("skipped") if isinstance(hdb, dict) else None

    equipment = data.get("equipment_model_mentions") or []
    # Chart.js: top 20 by count
    eq_top = equipment[:20]
    [f"{e.get('family','')} — {e.get('span','')}"[:50] for e in eq_top]
    [e.get("count", 0) for e in eq_top]

    kmeans_clusters = data.get("kmeans_clusters") or {}
    # Sort by cluster size (desc)
    cluster_items = sorted(
        kmeans_clusters.items(),
        key=lambda x: -len(x[1]) if isinstance(x[1], list) else 0,
    )

    clusters_html = ""
    for cid, subjects in cluster_items:
        sub_list = subjects if isinstance(subjects, list) else []
        size = len(sub_list)
        sub_lines = "".join(
            f"<li class=\"mono\">{escape((s or '')[:100])}</li>"
            for s in sub_list[:15]
        )
        if size > 15:
            sub_lines += f"<li class=\"muted\">… y {size - 15} más</li>"
        clusters_html += f"""
        <div class="card cluster-card">
          <h3>Cluster {escape(str(cid))} <span class="badge">{size:,}</span></h3>
          <ol>{sub_lines}</ol>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>ML results — KMeans, HDBSCAN, equipment</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{ --bg: #0f1419; --card: #1a2332; --text: #e7ecf3; --muted: #8b9cb3; --accent: #3d9cf0; --green: #3dd68c; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem 4rem; line-height: 1.5; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
    .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }}
    section {{ margin-bottom: 2.5rem; }}
    h2 {{ font-size: 1.1rem; color: var(--accent); border-bottom: 1px solid #2a3544; padding-bottom: 0.5rem; }}
    .card {{ background: var(--card); border-radius: 10px; padding: 1rem 1.25rem; border: 1px solid #2a3544; margin-bottom: 1rem; }}
    .kpi {{ display: flex; flex-wrap: wrap; gap: 1rem; margin: 1rem 0; }}
    .kpi span {{ background: var(--card); padding: 0.6rem 1rem; border-radius: 8px; border: 1px solid #2a3544; }}
    .kpi strong {{ color: var(--green); }}
    .cluster-card ol {{ margin: 0.5rem 0 0 1.2rem; padding: 0; font-size: 0.85rem; }}
    .cluster-card li {{ margin: 0.2rem 0; }}
    .mono {{ font-family: ui-monospace, monospace; word-break: break-word; }}
    .muted {{ color: var(--muted); }}
    .badge {{ background: var(--accent); color: var(--bg); padding: 0.15rem 0.5rem; border-radius: 6px; font-size: 0.85rem; }}
    canvas {{ max-height: 380px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }}
  </style>
</head>
<body>
  <h1>ML results — embeddings, KMeans, Agglomerative, HDBSCAN</h1>
  <p class="sub">Generado desde <code>ml_explore.json</code>. Misma muestra que los números abajo.</p>

  <section>
    <h2>Resumen</h2>
    <div class="kpi">
      <span>Muestra: <strong>{n_sample:,}</strong></span>
      <span>KMeans k: <strong>{kmeans_k}</strong></span>
      <span>Agglomerative k: <strong>{agg_k}</strong></span>
      <span>HDBSCAN: <strong>{hdb_clusters or '—'} clusters</strong>, <strong>{hdb_noise or 0:,} noise</strong></span>
    </div>
    {f'<p class="muted">HDBSCAN: {escape(str(hdb_skipped))}</p>' if hdb_skipped else ''}
  </section>

  <section>
    <h2>Menciones de equipos (regex)</h2>
    <div class="card" style="max-width: 800px;">
      <canvas id="equipmentChart"></canvas>
    </div>
  </section>

  <section>
    <h2>KMeans — clusters (asuntos de ejemplo)</h2>
    <p class="sub">Cada cluster es un grupo de mensajes similares por embedding. Id y tamaño; abajo hasta 15 asuntos.</p>
    <div class="grid">
      {clusters_html}
    </div>
  </section>

  <p class="sub">JSON completo: <code>ml_explore.json</code> en la misma carpeta. Jerarquía (dendrogram) y scatter 2D requieren exportar embeddings en el pipeline.</p>

  <script>
    const data = {data_js};
    const eq = data.equipment_model_mentions || [];
    const eqTop = eq.slice(0, 20);
    new Chart(document.getElementById('equipmentChart'), {{
      type: 'bar',
      data: {{
        labels: eqTop.map(e => (e.family + ' — ' + (e.span || '')).slice(0, 45)),
        datasets: [{{ label: 'Menciones', data: eqTop.map(e => e.count), backgroundColor: 'rgba(61, 156, 240, 0.6)' }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{ legend: {{ display: false }}}},
        scales: {{ x: {{ beginAtZero: true, ticks: {{ color: '#8b9cb3' }}, grid: {{ color: '#2a3544' }} }}, y: {{ ticks: {{ color: '#8b9cb3', font: {{ size: 10 }} }}, grid: {{ display: false }} }} }}
      }}
    }});
  </script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print("Wrote:", out_path)


if __name__ == "__main__":
    main()
