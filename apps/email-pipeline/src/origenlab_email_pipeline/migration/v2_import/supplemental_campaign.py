"""Supplemental import of one V1 campaign that was **never sent** — and only such a campaign.

The Wave 1A/1B importer (:mod:`.plan`, :mod:`.apply`) loads exactly the campaigns in the two
hashed safety bundles. V1 holds one campaign neither bundle requested: an ``archived``
campaign created and superseded before anything was sent (zero send attempts). It is real
history — it existed, it had an audience, it was replaced — but it has no send, reply or
suppression fact, so it changes no safety baseline and needs no cross-wave reconciliation.

This module loads such a campaign and nothing else:

* **Read-only source.** The V1 SQLite is opened ``mode=ro`` with ``query_only`` proven, and
  every read runs in one deferred read transaction (:mod:`..readonly_source`).
* **Refusals, not repairs.** The campaign must be ``archived`` in V1, have **zero** send
  attempts, and every recipient must be in a never-sent state (``inactive``/``candidate``/
  ``selected``/``blocked``). Anything that was ever attempted belongs to a safety bundle, and
  this module refuses it.
* **Provenance.** The extracted rows are hashed into a manifest; the manifest is an
  ``evidence.source_record`` (``migration_manifest``) and the campaign's
  ``origin_source_record_id`` points at it. No person, contact point or organization is
  created, and no institution is inferred from a domain: every recipient stays an address.
* **Idempotent and immutable.** The campaign is born ``archived`` (the archived-campaign guard
  lets only the importer's owner role do that). A second apply finds the manifest and the
  campaign, compares them field by field, and inserts nothing; any difference is a refusal.
* **Dry run by default.** :func:`apply_supplemental` writes only with ``dry_run=False``; the
  dry run executes the same transaction and rolls it back, reporting the counts it would
  write.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.migration.readonly_source import open_source_readonly, single_read_transaction
from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused, _require_psycopg, assume_import_role
from origenlab_email_pipeline.migration.v2_import.plan import canonical_address
from origenlab_email_pipeline.migration.v2_import.target import LocalTarget, neutralized_libpq_environment

MANIFEST_VERSION = "v1-unsent-campaign/2026-09-28.v1"
NEVER_SENT_STATES = frozenset({"inactive", "candidate", "selected", "blocked"})
V2_STATE = {"inactive": ("excluded", ["manual_inactive"]), "blocked": ("excluded", ["block"]),
            "candidate": ("snapshotted", []), "selected": ("snapshotted", [])}


class SupplementalRefused(Exception):
    """The campaign is not a never-sent archived V1 campaign, or the target disagrees."""


@dataclass(frozen=True)
class SupplementalCampaign:
    """One never-sent V1 campaign, as extracted. ``recipients`` hold normalized addresses."""

    campaign_id: str
    name: str
    status: str
    sender_email: str
    target_attempt_count: int
    created_at: str
    recipients: tuple[tuple[str, str], ...]  # (address_norm, v1_state), sorted
    source_file: str

    def manifest(self) -> dict[str, Any]:
        """The canonical record the manifest hash is taken over. Addresses are hashed per row,
        so the manifest payload stored in the CRM carries no address."""
        return {
            "manifest_version": MANIFEST_VERSION,
            "v1_campaign_id": self.campaign_id,
            "name": self.name,
            "v1_status": self.status,
            "v1_created_at": self.created_at,
            "target_attempt_count": self.target_attempt_count,
            "send_attempts": 0,
            "recipients": len(self.recipients),
            "recipients_by_v1_state": _by_state(self.recipients),
            "recipient_rows_sha256": hashlib.sha256(
                "\n".join(f"{a}\t{s}" for a, s in self.recipients).encode("utf-8")
            ).hexdigest(),
            "source_file": self.source_file,
        }

    def manifest_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.manifest(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def dedupe_key(self) -> str:
        return f"migration_manifest:v1-unsent-campaign:{self.campaign_id}:{self.manifest_sha256()[:32]}"


def _by_state(recipients: tuple[tuple[str, str], ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, s in recipients:
        out[s] = out.get(s, 0) + 1
    return dict(sorted(out.items()))


def extract(sqlite_path: Path, campaign_id: str) -> SupplementalCampaign:
    """Read one campaign from the V1 SQLite, read-only, or refuse."""
    conn = open_source_readonly(sqlite_path)
    try:
        with single_read_transaction(conn) as tx:
            row = tx.conn.execute(
                "select campaign_id, name, status, sender_email, target_attempt_count, created_at "
                "from outbound_campaign where campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if row is None:
                raise SupplementalRefused(f"V1 has no campaign {campaign_id!r}")
            attempts = tx.conn.execute(
                "select count(*) from outbound_send_attempt where campaign_id = ?", (campaign_id,)
            ).fetchone()[0]
            recips = tx.conn.execute(
                "select email_norm, email, state from outbound_campaign_recipient where campaign_id = ? order by id",
                (campaign_id,),
            ).fetchall()
    finally:
        conn.close()
    cid, name, status, sender, target, created = row
    if status != "archived":
        raise SupplementalRefused(f"campaign {cid!r} is {status!r} in V1; only an archived campaign is loaded here")
    if int(attempts) != 0:
        raise SupplementalRefused(
            f"campaign {cid!r} has {attempts} send attempts; a campaign that was ever attempted belongs to a "
            "hashed safety bundle and the Wave importer, never to this module"
        )
    normalized: dict[str, str] = {}
    for email_norm, email, state in recips:
        state = (state or "").lower()
        if state not in NEVER_SENT_STATES:
            raise SupplementalRefused(f"campaign {cid!r} has a recipient in state {state!r}: it was not a never-sent audience")
        address = canonical_address(email_norm or email, origin="supplemental campaign")
        if address is None:
            raise SupplementalRefused(f"campaign {cid!r} has a recipient whose address does not normalize")
        if address in normalized:
            raise SupplementalRefused(f"campaign {cid!r} has two recipients that normalize to one address")
        normalized[address] = state
    sender_norm = canonical_address(sender, origin="supplemental campaign sender")
    if sender_norm is None:
        raise SupplementalRefused("the campaign sender does not normalize")
    return SupplementalCampaign(
        campaign_id=cid, name=name, status=status, sender_email=sender_norm,
        target_attempt_count=int(target or 1), created_at=created,
        recipients=tuple(sorted(normalized.items())), source_file=Path(sqlite_path).name,
    )


@dataclass
class SupplementalResult:
    dry_run: bool
    target: str
    manifest_sha256: str
    inserted: dict[str, int] = field(default_factory=dict)
    already_present: bool = False
    recipients_by_v2_state: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"dry_run": self.dry_run, "target": self.target, "manifest_sha256": self.manifest_sha256,
                "inserted": self.inserted, "already_present": self.already_present,
                "recipients_by_v2_state": self.recipients_by_v2_state}


def apply_supplemental(campaign: SupplementalCampaign, target: LocalTarget, *, dry_run: bool = True) -> SupplementalResult:
    """Load ``campaign`` into ``target`` in one transaction; roll it back when ``dry_run``."""
    psycopg = _require_psycopg()
    sha = campaign.manifest_sha256()
    result = SupplementalResult(dry_run=dry_run, target=target.redacted(), manifest_sha256=sha)
    by_state: dict[str, int] = {}
    for _, s in campaign.recipients:
        by_state[V2_STATE[s][0]] = by_state.get(V2_STATE[s][0], 0) + 1
    result.recipients_by_v2_state = dict(sorted(by_state.items()))

    with neutralized_libpq_environment(), psycopg.connect(target.dsn, autocommit=False) as conn:
        assume_import_role(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("select id from comms.mailbox where address_norm = %s", (campaign.sender_email,))
                mailbox = cur.fetchone()
                if mailbox is None:
                    raise SupplementalRefused("the campaign's sender mailbox is not in the target; the Wave import creates it")
                cur.execute("select id, payload_sha256 from evidence.source_record where dedupe_key = %s",
                            (campaign.dedupe_key(),))
                found = cur.fetchone()
                cur.execute(
                    "select c.id from outbound.campaign c join evidence.source_record s on s.id = c.origin_source_record_id "
                    "where s.kind = 'migration_manifest' and s.payload->>'v1_campaign_id' = %s",
                    (campaign.campaign_id,),
                )
                existing = cur.fetchall()
                if found is not None or existing:
                    _verify_existing(cur, campaign, found, existing)
                    result.already_present = True
                    result.inserted = {"evidence.source_record": 0, "outbound.campaign": 0, "outbound.campaign_recipient": 0}
                    conn.rollback()
                    return result
                cur.execute(
                    "insert into evidence.source_record (kind, dedupe_key, payload, payload_sha256, review_status) "
                    "values ('migration_manifest', %s, %s, %s, 'pending') returning id",
                    (campaign.dedupe_key(), json.dumps({**campaign.manifest(), "sha256": sha}), sha),
                )
                source_id = cur.fetchone()[0]
                cur.execute(
                    "insert into outbound.campaign (name, status, mailbox_id, max_sends, recontact_interval_days, "
                    "policy_include_suppliers, origin_source_record_id) values (%s, 'archived', %s, %s, null, false, %s) "
                    "returning id",
                    (campaign.name, mailbox[0], campaign.target_attempt_count, source_id),
                )
                campaign_row = cur.fetchone()[0]
                for address, v1_state in campaign.recipients:
                    state, reasons = V2_STATE[v1_state]
                    cur.execute(
                        "insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons) "
                        "values (%s, %s, %s, %s)",
                        (campaign_row, address, state, reasons),
                    )
                result.inserted = {"evidence.source_record": 1, "outbound.campaign": 1,
                                   "outbound.campaign_recipient": len(campaign.recipients)}
        except Exception:
            conn.rollback()
            raise
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return result


def _verify_existing(cur: Any, campaign: SupplementalCampaign, found: Any, existing: list[Any]) -> None:
    """A second apply adopts nothing it has not compared."""
    if found is None or len(existing) != 1:
        raise SupplementalRefused("the target holds this V1 campaign under a different manifest; refusing to add a second")
    if found[1] != campaign.manifest_sha256():
        raise SupplementalRefused("the stored manifest's hash differs from this extraction")
    cur.execute("select name, status, max_sends from outbound.campaign where id = %s", (existing[0][0],))
    name, status, max_sends = cur.fetchone()
    if (name, status, max_sends) != (campaign.name, "archived", campaign.target_attempt_count):
        raise SupplementalRefused("the stored campaign differs from the extraction")
    cur.execute("select address_norm, state from outbound.campaign_recipient where campaign_id = %s",
                (existing[0][0],))
    stored = sorted((a, s) for a, s in cur.fetchall())  # Python order: never the database collation
    planned = sorted((a, V2_STATE[s][0]) for a, s in campaign.recipients)
    if stored != planned:
        raise SupplementalRefused("the stored audience differs from the extraction")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse
    import os

    from origenlab_email_pipeline.migration.v2_import.target import assert_local_target

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sqlite", required=True, type=Path)
    ap.add_argument("--campaign-id", required=True)
    ap.add_argument("--target-dsn-env", default="ORIGENLAB_V2_IMPORT_DSN",
                    help="name of the environment variable holding the loopback target DSN")
    ap.add_argument("--apply", action="store_true", help="commit; without it the transaction is rolled back")
    args = ap.parse_args(argv)
    campaign = extract(args.sqlite, args.campaign_id)
    target = assert_local_target(os.environ[args.target_dsn_env])
    result = apply_supplemental(campaign, target, dry_run=not args.apply)
    print(json.dumps({"manifest": campaign.manifest(), **result.as_dict()}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
