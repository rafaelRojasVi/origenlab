"""Owner-run: the whole triage on an exported folder (`triage_export.py`), offline, writing nothing
anywhere but `<dir>/report.jsonl` — to judge the rules and the model on real mail before enabling.

    cd apps/worker
    uv run python scripts/triage_eval.py ~/data/origenlab-v2-local/triage-eval            # rules + catalog
    ORIGENLAB_WORKER_ANTHROPIC_API_KEY=… uv run python scripts/triage_eval.py <dir> --model-sample 40
    … --model-sample 40 --models claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5   # compare models

The catalog match runs against `<dir>/catalog.jsonl` in memory: the exact model-key match is the
production one; the database's Spanish full-text ranking is approximated by word overlap. The model
stage (opt-in, `--model-sample N`) sends at most N of the messages the rules pass on, exactly the
request production would send. With `--models a,b,…` every listed model reads the same sample and
stdout adds, per model, how many readings ran and their tokens, and how often each other model
agreed with the first one (the reference, normally production's) on intent, stage, urgency and
needs_reply — the evidence for switching the production model. The report (mode 600) holds
subjects and readings; stdout carries counts only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from origenlab_api.v2.mail_rules import fold
from origenlab_worker.catalog_match import MAX_CANDIDATES, ProductCandidate, model_tokens, search_words
from origenlab_worker.mail_text import parse_mail
from origenlab_worker.triage import mentions_for
from origenlab_worker.triage_model import (
    DEFAULT_MODEL,
    ModelRefused,
    anthropic_reader,
    build_request,
    parse_reading,
)
from origenlab_worker.triage_rules import TriageInput, classify


class LocalCatalog:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.by_key: dict[str, list[dict]] = {}
        for r in rows:
            if r.get("model_key"):
                self.by_key.setdefault(r["model_key"], []).append(r)
        self.words = [(r, set(search_words(f"{r.get('name') or ''} {r.get('model_number') or ''} "
                                           f"{r.get('category') or ''}", ""))) for r in rows]

    def match(self, subject: str, body: str) -> list[ProductCandidate]:
        found = [ProductCandidate(r["id"], r["model_number"], r["model_key"], r["name"], "model_key", key)
                 for key in model_tokens(subject, body) for r in self.by_key.get(key, [])][:MAX_CANDIDATES]
        if found:
            return found
        wanted = set(search_words(subject, body))
        scored = sorted(((len(wanted & ws), r) for r, ws in self.words if wanted & ws),
                        key=lambda x: (-x[0], x[1]["id"]))
        return [ProductCandidate(r["id"], r["model_number"], r["model_key"], r["name"], "text", str(n))
                for n, r in scored[:5] if n >= 2]


#: The closed answers a model switch must keep; products and the summary are judged by eye in the report.
_COMPARED = ("intent", "stage", "urgency", "needs_reply")


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path)
    ap.add_argument("--model-sample", type=int, default=0, help="call the model for at most N messages")
    ap.add_argument("--model", default=os.environ.get("ORIGENLAB_WORKER_TRIAGE_MODEL") or DEFAULT_MODEL)
    ap.add_argument("--models", default=None,
                    help="comma-separated model ids that all read the same sample; the first is the reference")
    args = ap.parse_args(argv)
    models = [m.strip() for m in (args.models or args.model).split(",") if m.strip()]
    if not models:
        raise SystemExit("--models needs at least one model id")
    root = args.dir.expanduser()
    catalog = LocalCatalog(_load(root / "catalog.jsonl"))
    reader = None
    if args.model_sample > 0:
        key = os.environ.get("ORIGENLAB_WORKER_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("--model-sample needs ORIGENLAB_WORKER_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY)")
        reader = anthropic_reader(key)
    classes, states, sent, calls = Counter(), Counter(), 0, 0
    tokens_in = tokens_out = 0
    per_model: dict[str, Counter] = {m: Counter() for m in models}
    agreement: dict[str, Counter] = {m: Counter() for m in models[1:]}
    report = []
    started = time.monotonic()
    for row in _load(root / "messages.jsonl"):
        mail = parse_mail((root / "eml" / f"{row['source_record_id']}.eml").read_bytes())
        verdict = classify(TriageInput(row["direction"], tuple(row.get("labels") or ()), mail))
        candidates = catalog.match(mail.subject, mail.body) if verdict.needs_model or verdict.signals.get("quote_word") else []
        reading, state = None, "not_needed" if not verdict.needs_model else "off"
        readings: dict[str, Any] = {}
        if verdict.needs_model and reader is not None and sent < args.model_sample:
            sent += 1
            for model in models:
                calls += 1
                try:
                    got = parse_reading(reader(build_request(mail, verdict, candidates, model=model)),
                                        model=model, candidates=candidates)
                    got_state = "ran"
                    per_model[model]["ran"] += 1
                    per_model[model]["input_tokens"] += got.input_tokens or 0
                    per_model[model]["output_tokens"] += got.output_tokens or 0
                except ModelRefused as exc:
                    got, got_state = None, str(exc)
                    per_model[model][got_state] += 1
                except Exception as exc:  # noqa: BLE001
                    got, got_state = None, f"error_{type(exc).__name__}"
                    per_model[model][got_state] += 1
                readings[model] = got.as_json() if got else got_state
                if model == models[0]:
                    reading, state = got, got_state
                    if got is not None:
                        tokens_in += got.input_tokens or 0
                        tokens_out += got.output_tokens or 0
                elif reading is not None and got is not None:
                    agreement[model]["compared"] += 1
                    for field in _COMPARED:
                        agreement[model][field] += int(getattr(got, field) == getattr(reading, field))
        classes[verdict.triage_class] += 1
        states[state] += 1
        report.append({
            "source_record_id": row["source_record_id"], "date": row.get("internal_date"), "subject": mail.subject,
            "sender_domain": verdict.sender_domain, "class": verdict.triage_class, "reasons": list(verdict.reasons),
            "candidates": [f"{c.model_number} ({c.how})" for c in candidates], "model_state": state,
            "reading": reading.as_json() if reading else None,
            "readings": readings,
            "mentions": [n for n, _ in mentions_for(reading, candidates)],
            "body_start": fold(mail.body)[:160],
        })
    out = root / "report.jsonl"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in report)
    total = sum(classes.values())
    needs = sum(n for c, n in classes.items() if c in ("purchase_order", "lost", "quote_followup", "quote_request",
                                                        "business_other"))
    print(json.dumps({"event": "triage_eval", "messages": total, "to_model": needs,
                      "to_model_share": round(needs / total, 3) if total else None, "classes": dict(classes),
                      "model_states": dict(states), "sampled": sent, "model_calls": calls, "input_tokens": tokens_in,
                      "output_tokens": tokens_out, "elapsed_s": round(time.monotonic() - started, 1),
                      "models": {m: {"ran": 0, "input_tokens": 0, "output_tokens": 0, **c} for m, c in per_model.items()},
                      "agreement": [{"reference": models[0], "model": m, "compared": 0, **{f: 0 for f in _COMPARED},
                                     **c} for m, c in agreement.items()],
                      "report": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
