#!/usr/bin/env python3
"""
Prueba varios modelos no supervisados sobre una muestra de correos + extracción de modelos de equipo (regex).

  uv sync --group ml
  uv run python scripts/ml/email_ml_explore.py --limit 5000 --kmeans 14 --out reports/out/ml_explore.json

Supervisado: no incluido aquí (hace falta etiquetas). Ver docs/ml/AI_ML_IMPLEMENTED_SUMMARY.md
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.progress import print_compute_banner, tqdm_stderr

# Editable: (etiqueta_informe, regex) — case insensitive
EQUIPMENT_MODEL_PATTERNS: list[tuple[str, str]] = [
    ("Steinlite SB", r"\bSB\s*-?\s*\d{2,4}\b|\bSB\d{3,4}\b"),
    ("Ohaus Adventurer", r"\bAdventurer\b"),
    ("Ohaus Scout", r"\bScout\s+(Pro\s+)?\w*\d*"),
    ("Ohaus Explorer", r"\bExplorer\b"),
    ("Mettler Toledo", r"\bXS\d{4}\b|\bXPE?\d*\b|\bML\d{2,4}\b|\bME-T?\d"),
    ("Sartorius", r"\bQuintix\b|\bSecura\b|\bCubis\b"),
    ("Hielscher", r"\bUP\d{2,5}\s*(ST|HDT)?\b"),
    ("IKA", r"\bRCT\s*basic\b|\bC-MAG\b|\bHS\s*\d+"),
    ("Memmert", r"\bINB?\d+\b|\bUF\d+"),
    ("Binder", r"\bBD\s*\d+|\bFP\s*\d+"),
    ("HPLC genérico", r"\b1260\b|\b1290\b|\bUltimate\s*3000\b"),
    ("Agilent", r"\b7890\b|\b8890\b|\b1100\b|\b1200\b"),
    ("Waters", r"\bAcquity\b|\bAlliance\b"),
    ("Medidor humedad", r"\bPMB\b|\bPMB\s*\d+|\bMoisture\s*Analyzer\b"),
]

COMPILED = [(label, re.compile(pat, re.I)) for label, pat in EQUIPMENT_MODEL_PATTERNS]


def extract_models(text: str) -> list[tuple[str, str]]:
    out = []
    for label, rx in COMPILED:
        for m in rx.finditer(text or ""):
            out.append((label, m.group(0)[:80]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--kmeans", type=int, default=12)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
        from sklearn.cluster import AgglomerativeClustering, KMeans
        import torch
    except ImportError:
        print("Necesitas: uv sync --group ml", file=sys.stderr)
        sys.exit(1)

    db = args.db or load_settings().resolved_sqlite_path()
    if not db.is_file():
        sys.exit("DB no encontrada")

    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        """
        SELECT id, subject, sender, body FROM emails
        WHERE length(trim(coalesce(body,''))) >= 80
        ORDER BY RANDOM() LIMIT ?
        """,
        (args.limit,),
    )
    print_compute_banner(
        uses_gpu=False,
        workload="SQLite: loading random emails for ML explore",
        extra_detail=f"Up to {args.limit:,} rows",
    )
    iterator = tqdm_stderr(
        cur,
        total=args.limit,
        desc="Load emails",
        unit="emails",
    )
    rows = []
    texts = []
    for r in iterator:
        subj, snd, body = r[1] or "", r[2] or "", (r[3] or "")[:1200]
        rows.append({"id": r[0], "subject": subj[:120], "sender": snd[:80], "snippet": body[:200]})
        texts.append(f"Subject: {subj}\nFrom: {snd}\n{body}")
    conn.close()
    if len(rows) < 50:
        sys.exit("Muy pocas filas")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print_compute_banner(
        uses_gpu=True,
        workload="Embeddings (SentenceTransformer.encode)",
        extra_detail=f"all-MiniLM-L6-v2 | device={device} | rows={len(rows)}",
    )
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device=device)
    bs = 64
    n_txt = len(texts)
    n_batch = (n_txt + bs - 1) // bs
    print(
        f"[compute] Embedding {n_txt} texts in {n_batch} batches (stderr progress bar)",
        file=sys.stderr,
        flush=True,
    )
    parts: list = []
    for start in tqdm_stderr(
        range(0, n_txt, bs),
        total=n_batch,
        desc="Embedding batches",
        unit="batch",
    ):
        chunk = texts[start : start + bs]
        parts.append(
            model.encode(
                chunk,
                batch_size=len(chunk),
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        )
    X = np.vstack(parts)
    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)

    k = min(args.kmeans, len(rows) // 4)
    k = max(3, k)
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels_km = km.fit_predict(Xn)

    n_agg = min(k + 2, len(rows) // 3)
    n_agg = max(3, n_agg)
    agg = AgglomerativeClustering(n_clusters=n_agg, metric="cosine", linkage="average")
    agg.fit_predict(Xn)

    hdb_info = None
    try:
        import hdbscan

        cl = hdbscan.HDBSCAN(min_cluster_size=max(15, len(rows) // 80), metric="euclidean")
        labels_h = cl.fit_predict(Xn)
        ncl = len(set(labels_h)) - (1 if -1 in labels_h else 0)
        noise = list(labels_h).count(-1)
        hdb_info = {"clusters": ncl, "noise_points": int(noise)}
    except ImportError:
        hdb_info = {"skipped": "uv sync --group ml (includes hdbscan)"}

    # Model mentions (full sample texts for regex)
    model_hits: Counter[tuple[str, str]] = Counter()
    for t in texts:
        for label, span in extract_models(t):
            model_hits[(label, span.strip())] += 1
    top_models = [
        {"family": a, "span": b, "count": c}
        for (a, b), c in model_hits.most_common(60)
    ]

    by_km: dict[int, list] = {}
    for i, lab in enumerate(labels_km):
        by_km.setdefault(int(lab), []).append(rows[i]["subject"])

    payload = {
        "n_sample": len(rows),
        "embedding_model": "all-MiniLM-L6-v2",
        "kmeans_k": k,
        "agglomerative_k": n_agg,
        "hdbscan": hdb_info,
        "kmeans_clusters": {
            str(kk): subs[:12] for kk, subs in sorted(by_km.items(), key=lambda x: -len(x[1]))
        },
        "equipment_model_mentions": top_models,
        "note": "Modelos por regex; ampliar EQUIPMENT_MODEL_PATTERNS en el script. Clusters no supervisados.",
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Wrote:", args.out)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:8000])
        if len(json.dumps(payload)) > 8000:
            print("\n… truncado; usa --out FILE.json", file=sys.stderr)


if __name__ == "__main__":
    main()
