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

`--truth <truth.jsonl>` also runs the context matcher (`case_match.py`) over `<dir>/cases.jsonl` and
scores it against a person's answers (`case_id` = that case; `missing_case` with no `case_id` = a deal
with no case yet; neither = no case), next to what the Gmail thread alone would have found. A second
stdout line carries the counts. `--pick-sample N` (needs the key) also asks Claude to pick the case
(`case_match_model.py`) for at most N answered emails layer 1 is not sure of, showing it the other
owner-reviewed answers from the same sender as past decisions (never its own), and prints a third line:
the combination (the model's pick where it ran, layer 1 elsewhere) with the calls and tokens.
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
from origenlab_api.v2.mail_rules import FREE_MAIL_DOMAINS
from origenlab_worker.case_match import Candidate, CaseCard, MatchInput, MatchResult, match_case
from origenlab_worker.case_match_model import PastDecision, build_pick_request, offered_cases, parse_pick
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


def score_truth(truth_rows: list[dict], results: dict[str, MatchResult],
                thread_only: dict[str, str | None]) -> dict[str, int]:
    """How the matcher's first suggestion compares with a person's answer, as counts."""
    n: Counter = Counter()
    for t in truth_rows:
        r = results.get(t["source_record_id"])
        if r is None:
            continue
        ids = [c.opportunity_id for c in r.candidates]
        linked = r.decision in ("exact", "propose")
        top = ids[0] if linked and ids else None
        expected = t.get("case_id")
        if expected:
            n["existing_case_rows"] += 1
            n["top1_right"] += int(top == expected)
            n["top3_right"] += int(linked and expected in ids)
            n["thread_only_right"] += int(thread_only.get(t["source_record_id"]) == expected)
            wrong = top != expected
        elif t.get("missing_case"):
            n["new_case_rows"] += 1
            n["new_case_proposed"] += int(r.decision == "new_case")
            wrong = linked
        else:
            n["none_rows"] += 1
            n["false_link"] += int(linked)
            wrong = linked
        if linked and r.candidates and set(r.candidates[0].signals) == {"sender_on_case"}:
            n["guardrail_sender_only"] += 1
        if t.get("reviewed_by") == "owner":
            n["owner_reviewed_rows"] += 1
            n["owner_reviewed_wrong"] += int(wrong)
    keys = ("existing_case_rows", "top1_right", "top3_right", "thread_only_right", "new_case_rows",
            "new_case_proposed", "none_rows", "false_link", "guardrail_sender_only", "owner_reviewed_rows",
            "owner_reviewed_wrong")
    return {k: n[k] for k in keys}


def _who(sender: str | None) -> str:
    domain = (sender or "").rsplit("@", 1)[-1].lower()
    return (sender or "").lower() if domain in FREE_MAIL_DOMAINS else domain


def pick_results(truth_rows: list[dict], results: dict[str, MatchResult], inputs: dict[str, MatchInput],
                 cards: list[CaseCard], reader: Any, model: str, sample: int) -> tuple[dict[str, MatchResult], Counter]:
    """Layer 2 over the answered emails layer 1 is not sure of; each sees the other owner answers from
    the same sender (leave-one-out) as past decisions. Returns the combined results and the counts."""
    titles = {c.opportunity_id: c.title for c in cards}
    owner = [t for t in truth_rows if t.get("reviewed_by") == "owner" and t["source_record_id"] in inputs]
    combined, n = dict(results), Counter()
    for t in truth_rows:
        sid = t["source_record_id"]
        if sid not in inputs or results[sid].decision == "exact" or n["pick_calls"] >= sample:
            continue
        inp = inputs[sid]
        past = [PastDecision(inputs[o["source_record_id"]].sender or "", inputs[o["source_record_id"]].subject,
                             o.get("case_id"), titles.get(o.get("case_id") or ""),
                             "linked" if o.get("case_id") else ("new_case" if o.get("missing_case") else "no_case"))
                for o in owner if o["source_record_id"] != sid
                and _who(inputs[o["source_record_id"]].sender) == _who(inp.sender)]
        n["pick_calls"] += 1
        offered = {c.opportunity_id for c in offered_cases(results[sid], cards)}
        try:
            pick = parse_pick(reader(build_pick_request(inp, results[sid], cards, past, model=model)),
                              offered=offered, model=model)
        except ModelRefused as exc:
            n[f"pick_{exc}"] += 1
            continue
        except Exception as exc:  # noqa: BLE001
            n[f"pick_error_{type(exc).__name__}"] += 1
            continue
        n["pick_input_tokens"] += pick["usage"]["input_tokens"] or 0
        n["pick_output_tokens"] += pick["usage"]["output_tokens"] or 0
        q = results[sid].quote_numbers
        if pick["choice"] not in ("new_case", "none"):
            # What rule R12 hangs on: a pick naming a case, by confidence — right when it is the case
            # the person answered; only «high» is linked automatically in production.
            n[f"pick_{pick['confidence']}_{'right' if pick['choice'] == t.get('case_id') else 'wrong'}"] += 1
        if pick["choice"] == "new_case":
            combined[sid] = MatchResult("new_case", (), q, (pick["new_case"] or {}).get("quote_number"))
        elif pick["choice"] == "none":
            combined[sid] = MatchResult("none", (), q, None)
        else:
            combined[sid] = MatchResult("propose", (Candidate(pick["choice"], 0, ("model",)),), q, None)
    return combined, n


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path)
    ap.add_argument("--model-sample", type=int, default=0, help="call the model for at most N messages")
    ap.add_argument("--model", default=os.environ.get("ORIGENLAB_WORKER_TRIAGE_MODEL") or DEFAULT_MODEL)
    ap.add_argument("--models", default=None,
                    help="comma-separated model ids that all read the same sample; the first is the reference")
    ap.add_argument("--truth", type=Path, default=None, help="score the context matcher against these answers")
    ap.add_argument("--pick-sample", type=int, default=0, help="with --truth: ask the model to pick for at most N")
    args = ap.parse_args(argv)
    models = [m.strip() for m in (args.models or args.model).split(",") if m.strip()]
    if not models:
        raise SystemExit("--models needs at least one model id")
    root = args.dir.expanduser()
    catalog = LocalCatalog(_load(root / "catalog.jsonl"))
    reader = None
    if args.model_sample > 0 or args.pick_sample > 0:
        key = os.environ.get("ORIGENLAB_WORKER_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("--model-sample or --pick-sample needs ORIGENLAB_WORKER_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY)")
        reader = anthropic_reader(key)
    classes, states, sent, calls = Counter(), Counter(), 0, 0
    tokens_in = tokens_out = 0
    per_model: dict[str, Counter] = {m: Counter() for m in models}
    agreement: dict[str, Counter] = {m: Counter() for m in models[1:]}
    report = []
    cards = [CaseCard.from_row(c) for c in _load(root / "cases.jsonl")] if args.truth else []
    # Attachment names per Gmail thread, as `comms.attachment` holds them in production.
    thread_files: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    if args.truth:
        for row in _load(root / "messages.jsonl"):
            if row.get("thread_id"):
                names = parse_mail((root / "eml" / f"{row['source_record_id']}.eml").read_bytes()).attachment_names
                thread_files.setdefault(row["thread_id"], []).append((row["source_record_id"], names))
    results: dict[str, MatchResult] = {}
    inputs: dict[str, MatchInput] = {}
    thread_only: dict[str, str | None] = {}
    started = time.monotonic()
    for row in _load(root / "messages.jsonl"):
        mail = parse_mail((root / "eml" / f"{row['source_record_id']}.eml").read_bytes())
        verdict = classify(TriageInput(row["direction"], tuple(row.get("labels") or ()), mail))
        candidates = catalog.match(mail.subject, mail.body) if verdict.needs_model or verdict.signals.get("quote_word") else []
        reading, state = None, "not_needed" if not verdict.needs_model else "off"
        readings: dict[str, Any] = {}
        if verdict.needs_model and reader is not None and args.model_sample > 0 and sent < args.model_sample:
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
        match = None
        if args.truth:
            sid = row["source_record_id"]
            inputs[sid] = MatchInput(
                row.get("sender"), tuple(row.get("recipients") or ()), row.get("thread_id"), row["direction"],
                mail.subject, mail.body, mail.attachment_names, row.get("internal_date"),
                tuple(n for other, names in thread_files.get(row.get("thread_id") or "", []) if other != sid
                      for n in names))
            match = results[sid] = match_case(inputs[sid], cards)
            on_thread = [c.opportunity_id for c in cards if row.get("thread_id") and row["thread_id"] in c.threads]
            thread_only[sid] = on_thread[0] if len(on_thread) == 1 else None
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
            **({"case_match": match.as_json()} if match else {}),
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
    if args.truth:
        truth_rows = _load(args.truth.expanduser())
        print(json.dumps({"event": "triage_eval_truth", **score_truth(truth_rows, results, thread_only)}))
        if args.pick_sample > 0:
            combined, counts = pick_results(truth_rows, results, inputs, cards, reader, models[0], args.pick_sample)
            print(json.dumps({"event": "triage_eval_truth_combined", "model": models[0],
                              **score_truth(truth_rows, combined, thread_only), **dict(counts)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
