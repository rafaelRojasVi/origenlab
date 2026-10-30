#!/usr/bin/env python3
"""Load the sends of one or more V1-lane campaigns into the CRM (and append each later wave).

    uv run python scripts/v1_sent_campaign_import.py --sqlite ~/data/origenlab-email/sqlite/emails.sqlite \\
        --campaign-id cyber-2026-10 --campaign-id ohaus-2026-10 --authorize-hosted-connection          # dry run
    … --apply                                                                                         # commit

Without ``--authorize-hosted-connection`` the target is the loopback DSN in
``ORIGENLAB_V2_IMPORT_DSN``. With it, the hosted project, through the same target file and
``OL_HOSTED_PASSWORD`` (the migrator login) the hosted data load uses — nothing is read from the
repository but the target file. Prints counts only, never an address. Exit 0 ok, 2 refused or
failed (a database error prints its class and SQLSTATE only), 3 connection failed. The wave runner calls it after each wave, non-fatal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from origenlab_api.v2.v1_sent_campaign_import import SentCampaignRefused, apply_sent_campaign, extract_sent_campaign

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _connect(args: argparse.Namespace):
    import psycopg

    if args.authorize_hosted_connection:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from hosted_data_load_io import REPO_ROOT, connect_target, hosted_target

        conn = connect_target(hosted_target(REPO_ROOT, os.environ))
    else:
        dsn = os.environ.get("ORIGENLAB_V2_IMPORT_DSN", "").strip()
        if urlparse(dsn).hostname not in _LOOPBACK:
            raise SystemExit("ORIGENLAB_V2_IMPORT_DSN must be a loopback DSN; use --authorize-hosted-connection for hosted")
        conn = psycopg.connect(dsn)
    conn.autocommit = False
    return conn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sqlite", required=True, type=Path)
    p.add_argument("--campaign-id", required=True, action="append", dest="campaign_ids")
    p.add_argument("--apply", action="store_true", help="commit; without it every transaction is rolled back")
    p.add_argument("--authorize-hosted-connection", action="store_true")
    args = p.parse_args(argv)
    try:
        campaigns = [extract_sent_campaign(args.sqlite.expanduser(), cid) for cid in args.campaign_ids]
    except SentCampaignRefused as exc:
        print(json.dumps({"refused": str(exc)}, ensure_ascii=False))
        return 2
    try:
        conn = _connect(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the message may carry the host; print the class only
        print(json.dumps({"connection_failed": exc.__class__.__name__}))
        return 3
    status = 0
    with conn:
        for c in campaigns:
            try:
                result = apply_sent_campaign(conn, c, dry_run=not args.apply)
                print(json.dumps({"campaign": c.v1_campaign_id, **result.as_dict()}, ensure_ascii=False))
            except SentCampaignRefused as exc:
                print(json.dumps({"campaign": c.v1_campaign_id, "refused": str(exc)}, ensure_ascii=False))
                status = 2
            except Exception as exc:  # noqa: BLE001 - a database error's detail may quote an address
                sqlstate = getattr(exc, "sqlstate", None)
                print(json.dumps({"campaign": c.v1_campaign_id, "failed": exc.__class__.__name__, "sqlstate": sqlstate}))
                status = 2
    return status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
