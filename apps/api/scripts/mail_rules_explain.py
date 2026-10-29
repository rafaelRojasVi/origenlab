#!/usr/bin/env python3
"""Explain the mail-rules planner's decisions for the current triage queue.

    uv run python scripts/mail_rules_explain.py [--case CASE_ID] [--limit N]

Connects to the database (DATABASE_URL or .env), loads the Snapshot for the triage queue,
runs `plan()`, and prints a human-readable explanation of:

  - how each evidence item is classified (direction, _from_the_client, newest_inbound),
  - which rule fired on it and why, and
  - the supplier_free_addresses detected in the snapshot.

Goal 5 (2026-10-29): helps verify that `newest_inbound` excludes supplier free-mail
replies and automatic mail so R19 acts on the client's «no» rather than a later
courier notice or supplier answer.

Read-only; never writes to the database.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# ---------------------------------------------------------------------------

def _load_env() -> None:
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env):
        return
    with open(env) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# ---------------------------------------------------------------------------

from origenlab_api.v2.mail_rules import (
    FREE_MAIL_DOMAINS,
    _Index,
    _from_the_client,
    _sender,
    domain_of,
    is_automatic_mail,
    plan,
)
from origenlab_api.v2.mail_rules_repository import build_snapshot


def _yesno(b: bool) -> str:
    return "yes" if b else "no"


def _truncate(s: str | None, n: int = 60) -> str:
    if not s:
        return "—"
    return s[:n] + "…" if len(s) > n else s


def _explain_from_client(ev: Any, ix: _Index) -> str:
    """One-line explanation of why _from_the_client returned True or False."""
    sender = _sender(ev)
    if ev.direction != "inbound":
        return f"outbound ({ev.direction})"
    if sender is None:
        return "no sender"
    dom = domain_of(sender)
    from origenlab_api.v2.mail_rules import OWN_DOMAINS
    if dom in OWN_DOMAINS:
        return f"own domain ({dom})"
    if ix.is_supplier_domain(dom):
        return f"supplier domain ({dom})"
    if ix.is_supplier_address(sender):
        return f"supplier free-mail address ({sender})"
    if is_automatic_mail(ev.sender, ev.triage_class):
        return f"automatic (class={ev.triage_class!r}, sender={ev.sender!r})"
    from origenlab_api.v2.mail_rules import fold, AUTO_REPLY_PHRASES
    subject = fold(ev.subject)
    for p in AUTO_REPLY_PHRASES:
        if p in subject:
            return f"auto-reply subject ({p!r})"
    return "ok — from the client"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", metavar="ID", help="limit output to one case")
    ap.add_argument("--limit", type=int, default=200, help="max evidence items to show (default 200)")
    args = ap.parse_args(argv)

    db_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("ERROR: set DATABASE_URL or SUPABASE_DB_URL", file=sys.stderr)
        sys.exit(1)

    import psycopg
    print("Connecting…", file=sys.stderr)
    with psycopg.connect(db_url) as conn:
        snap = build_snapshot(conn)

    ix = _Index(snap)

    # ── supplier free-mail addresses ──────────────────────────────────────────
    if ix.supplier_free_addresses:
        print(f"\n{'─'*72}")
        print(f"Supplier free-mail addresses ({len(ix.supplier_free_addresses)}):")
        for addr in sorted(ix.supplier_free_addresses):
            print(f"  {addr}")
    else:
        print("\n(no supplier free-mail addresses)")

    # ── newest_inbound per case ───────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("newest_inbound (client only, excludes suppliers & auto, goal 5):")
    if ix.newest_inbound:
        for cid, at in sorted(ix.newest_inbound.items()):
            case = ix.cases.get(cid)
            title = case.title if case else "?"
            print(f"  {cid}  {at.isoformat()}  {_truncate(title, 40)}")
    else:
        print("  (none)")

    # ── evidence classification ───────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("Evidence classification (newest_inbound eligibility):\n")
    print(f"  {'id':<10}  {'dir':<9}  {'from_client':<6}  {'class':<14}  {'why / sender'}")
    print(f"  {'-'*10}  {'-'*9}  {'-'*6}  {'-'*14}  {'-'*40}")
    shown = 0
    for ev in sorted(snap.evidence, key=lambda e: e.id):
        if args.case and args.case not in (ev.linked_case_ids or ()):
            continue
        fc = _from_the_client(ev, ix)
        why = _explain_from_client(ev, ix)
        cls = ev.triage_class or "—"
        print(f"  {ev.id:<10}  {ev.direction:<9}  {_yesno(fc):<6}  {cls:<14}  {why}")
        shown += 1
        if shown >= args.limit:
            print(f"  … (truncated at {args.limit})")
            break

    # ── planned actions ───────────────────────────────────────────────────────
    actions = plan(snap)
    print(f"\n{'─'*72}")
    print(f"Planned actions ({len(actions)}):\n")
    for a in actions:
        reasons_str = " | ".join(a.reasons[:3])
        cmds = ", ".join(s["command"] for s in a.commands) if a.commands else "—"
        print(f"  [{a.rule_id}] {a.mode:<8}  evidence={a.evidence_id}  case={a.case_id or '—'}")
        print(f"         cmds: {cmds}")
        print(f"         why:  {_truncate(reasons_str, 80)}")

    if not actions:
        print("  (no actions)")

    print()


if __name__ == "__main__":
    main()
