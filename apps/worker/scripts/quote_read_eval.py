#!/usr/bin/env python3
"""The quote reader's switch-on gate (private spec §6): re-read the quote PDFs whose lines are
already stored (the 2026-10-06 history import) and say how often the reader agrees.

Owner-run, read-only, outside the cron:

    uv run --no-sync python scripts/quote_read_eval.py \\
        --db "$READ_ONLY_DSN" --pdfs DIR [--pdfs DIR ...] --models off,claude-opus-5-5 \\
        --out ~/data/…/quote-read-eval-<ts> [--limit 20]

* `--db`: a database holding the stored `quote_document` records and their lines (the local
  `origenlab_mirror` copy works; only SELECTs are run).
* `--pdfs`: folders of `<sha256>.pdf` files (the private audit folders); a stored document whose PDF
  is in none of them is counted `pdf_missing`.
* `--models`: comma-separated; `off` is the template-only reader. A model needs
  `ORIGENLAB_WORKER_ANTHROPIC_API_KEY` in the environment.
* `--out`: one JSON line per document and model (codes and counts only) and `summary.json`; it must
  be outside the repository.

The gate: a model passes when neto totals agree on ≥ 95 % of documents and line totals on ≥ 95 % of
stored lines. Prints one line per model; never a client, a description or a price.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

GATE_PCT = 95.0


def _amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).normalize()
    except InvalidOperation:
        return None


def _neto(doc: Mapping[str, Any]) -> Decimal | None:
    """The printed neto, else the sum of the non-optional lines (most history documents have no
    printed neto stored)."""
    printed = _amount(doc.get("net_total"))
    if printed is not None:
        return printed
    totals = [_amount(line[1]) for line in doc["lines"] if not (len(line) > 2 and line[2])]
    if not totals or any(t is None for t in totals):
        return None
    return sum(totals, Decimal(0)).normalize()


def compare(stored: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Document- and line-level agreement of one new reading with the stored one. Lines are
    `(model_key, line_total[, optional])`; a stored line matches when the new reading has the same
    key and total."""
    left = Counter((line[0], _amount(line[1])) for line in stored["lines"])
    right = Counter((line[0], _amount(line[1])) for line in new["lines"])
    matched = sum((left & right).values())
    return {
        "net_agrees": _neto(stored) == _neto(new),
        "currency_agrees": stored.get("currency") == new.get("currency"),
        "lines_stored": len(stored["lines"]),
        "lines_matched": matched,
        "line_count_agrees": len(stored["lines"]) == len(new["lines"]),
    }


def summarize(results: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_model: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in results:
        by_model[str(r["model"])].append(r)
    out: dict[str, dict[str, Any]] = {}
    for model, rows in by_model.items():
        docs = len(rows)
        read = [r for r in rows if r.get("outcome") == "read"]
        stored_lines = sum(int(r.get("lines_stored") or 0) for r in rows)
        net_pct = round(100.0 * sum(1 for r in read if r.get("net_agrees")) / docs, 1) if docs else 0.0
        line_pct = round(100.0 * sum(int(r.get("lines_matched") or 0) for r in read) / stored_lines, 1) \
            if stored_lines else 0.0
        out[model] = {
            "documents": docs, "read": len(read),
            "refused": dict(Counter(str(r.get("code")) for r in rows if r.get("outcome") == "refused")),
            "net_agreement_pct": net_pct, "line_agreement_pct": line_pct,
            "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
            "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
            "passes_gate": net_pct >= GATE_PCT and line_pct >= GATE_PCT,
        }
    return out


_STORED_SQL = """
select s.payload_sha256 as sha, s.payload ->> 'currency' as currency, s.payload ->> 'net_total' as net_total,
       coalesce((select json_agg(json_build_object('k', l.model_key, 't', l.line_total::text, 'o', l.optional)
                                   order by l.line_no)
                   from evidence.document_line l where l.source_record_id = s.id), '[]'::json) as lines
  from evidence.source_record s
 where s.kind = 'quote_document'
 order by s.payload_sha256
"""


def _stored(dsn: str) -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(_STORED_SQL)
        rows = cur.fetchall()
    return [{"sha": sha, "currency": cur_, "net_total": net,
             "lines": [(ln["k"], ln["t"], ln["o"]) for ln in (lines or [])]} for sha, cur_, net, lines in rows]


def _find_pdf(dirs: list[Path], sha: str) -> bytes | None:
    for d in dirs:
        path = d / f"{sha}.pdf"
        if path.is_file():
            return path.read_bytes()
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--pdfs", action="append", required=True, type=Path)
    ap.add_argument("--models", default="off")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=0, help="at most this many documents (0 = all)")
    args = ap.parse_args(argv)

    repo = Path(__file__).resolve().parents[3]
    out = args.out.expanduser().resolve()
    if out == repo or repo in out.parents:
        print("refused: --out is inside the repository", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    from origenlab_worker.quote_reader import MODEL_OFF, ReadRefused, read_document

    key = os.environ.get("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", "").strip()
    readers: dict[str, Any] = {}
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        if model == MODEL_OFF:
            readers[model] = None
            continue
        if not key:
            print("refused: a model needs ORIGENLAB_WORKER_ANTHROPIC_API_KEY", file=sys.stderr)
            return 2
        from origenlab_worker.triage_model import anthropic_reader

        readers[model] = anthropic_reader(key, timeout_s=120.0)

    stored = _stored(args.db)
    if args.limit:
        stored = stored[: args.limit]
    results: list[dict[str, Any]] = []
    with (out / "results.jsonl").open("w", encoding="utf-8") as fh:
        for doc in stored:
            pdf = _find_pdf(args.pdfs, doc["sha"])
            for model, reader in readers.items():
                row: dict[str, Any] = {"model": model, "lines_stored": len(doc["lines"])}
                if pdf is None:
                    row.update(outcome="refused", code="pdf_missing")
                else:
                    try:
                        reading, log = read_document(pdf, doc["sha"], reader=reader, model=model)
                        new = {"currency": reading.payload.get("currency"), "net_total": reading.payload.get("net_total"),
                               "lines": [(ln.get("model_key"), ln.get("line_total"), ln.get("optional"))
                                         for ln in reading.lines]}
                        row.update(outcome="read", **compare(doc, new),
                                   input_tokens=log.get("input_tokens"), output_tokens=log.get("output_tokens"))
                    except ReadRefused as exc:
                        row.update(outcome="refused", code=exc.code)
                results.append(row)
                fh.write(json.dumps({**row, "sha": doc["sha"][:12]}) + "\n")
    summary = summarize(results)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for model, s in summary.items():
        print(json.dumps({"model": model, **s}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
