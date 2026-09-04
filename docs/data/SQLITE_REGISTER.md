# SQLite database register

Status: canonical
Owner: project-maintainers
Last reviewed: 2026-09-03
Part of: [`docs/refoundation/REFOUNDATION_PLAN.md`](../refoundation/REFOUNDATION_PLAN.md)

Read-only forensic inventory of every SQLite file found at the repo-configured
runtime path (`ORIGENLAB_SQLITE_PATH`) and its immediate directory, plus one
additional path already known from configuration/prior work
(`arch2a-p1-scratch`). This is **not** a filesystem-wide search — per the
re-foundation's Phase 0 safety scope, discovery was limited to the
repo-configured path, its containing directory, and one other path already on
record. If a broader disk-wide duplicate search is ever wanted, that needs a
separate, explicitly operator-run procedure — not something this pass
performs itself.

**Method:** `stat` (size/mtime) plus SQLite header-only PRAGMAs
(`page_size`, `page_count`, `freelist_count`, `schema_version`,
`application_id`, `user_version`) and a `sqlite_master` table-name query, all
via read-only connections (`sqlite3 -readonly`, URI `mode=ro`). No
`PRAGMA integrity_check`/`quick_check`, no `dbstat`, and no `SELECT COUNT(*)`
table scans were run against any file here — these files range from empty to
137 GB, and the repository's own tooling (`apps/email-pipeline/docs/
SQLITE_STORAGE_MAINTENANCE.md`, `scripts/qa/audit_sqlite_deep.py`'s phase
table) already documents why those operations are refused against the live
production path and are impractical to run ad hoc against files of this size
in this pass. Full content SHA-256 was likewise not computed for the same
reason (the project's own post-cutover notes explicitly avoid full-content
hashing of a ~127 GB file); the header/table fingerprint below serves as the
identity marker instead.

No file below is recommended for deletion. Classification vocabulary is the
one required by the re-foundation brief: `AUTHORITATIVE_OPERATIONAL`,
`UNIQUE_EVIDENCE`, `DURABLE_TRANSITIONAL`, `REBUILDABLE_PROJECTION`, `CACHE`,
`MIRROR`, `BACKUP`, `EXPERIMENTAL`, `OBSOLETE_CANDIDATE`, `UNKNOWN`.

## Configured runtime path

`ORIGENLAB_SQLITE_PATH` (from `apps/email-pipeline` `load_settings()`,
documented in `apps/email-pipeline/docs/DATA_LOCATIONS.md`) resolves on the
inspected machine to `/home/rafael/data/origenlab-email/sqlite/emails.sqlite`
— confirmed both in `apps/email-pipeline/.env` and `apps/api/.env`.

## Register

| Path | Size | Modified | Header fingerprint | Tables | Landmark tables present | Classification | Notes |
|---|---|---|---|---|---|---|---|
| `sqlite/emails.sqlite` (+`-wal`,`-shm`) | 66 GB | 2026-09-03 22:57 (actively written — this is live) | page_size 4096, pages 16,032,877, freelist 0, schema_version 1015 | 92 | emails, contact_master, supplier_master, lead_research_prospect, commercial_opportunity, outbound_campaign | **AUTHORITATIVE_OPERATIONAL** | The live database behind `ORIGENLAB_SQLITE_PATH`. Zero freelist is consistent with the post-cutover compacted state described in `SQLITE_POST_CUTOVER_HARDENING_NOTE.md` (~127 GB pre-compaction → ~61 GB active estimate; current 66 GB is in that range). |
| `sqlite/emails.sqlite.pre_cutover.cutover20260722T233536Z` (+`-wal`,`-shm`, all read-only-permissioned) | 137 GB | 2026-07-22 19:25 | pages 33,421,815, freelist 17,440,493 (~52% reclaimable), schema_version 1423 | 70 | emails, contact_master, supplier_master, lead_research_prospect — **missing** `commercial_opportunity`, `outbound_campaign` | **BACKUP** (documented, policy-retained) | Pre-barrier snapshot from the maintenance ID `cutover20260722T233536Z`, which `apps/email-pipeline/docs/SQLITE_POST_CUTOVER_HARDENING_NOTE.md` records as a **successfully completed** production cutover. `SQLITE_STORAGE_MAINTENANCE.md`'s explicit retention rule: "Old same-volume clones … must not be deleted until a current Online Backup API copy on separate storage has completed and passed the deep forensic audit." That condition is the removal gate — not size or age. Schema-incompatible with the live DB (predates `commercial_opportunity`/`outbound_campaign`), confirming it is a genuine point-in-time artifact, not a redundant duplicate of current state. |
| `sqlite/emails.sqlite.pre_cutover.cutover20260719T163633Z` (read-only-permissioned) | 65 GB | 2026-07-19 16:01 | pages 15,962,177, freelist 0, schema_version 2 | 70 | same set as above (missing `commercial_opportunity`/`outbound_campaign`) | **BACKUP** (documented, policy-retained — but see note) | Pre-barrier snapshot from maintenance ID `cutover20260719T163633Z`, which `SQLITE_POST_CUTOVER_HARDENING_NOTE.md` states is "**permanently non-resumable**" (an abandoned/superseded attempt three days before the successful July 22 cutover). The same repository-wide "do not delete same-volume clones" rule formally still applies, but this specific snapshot protects an attempt that never resumed — its disposition may be independently resolvable by an operator sooner than the July 22 one's. Flagged for operator judgment, not reclassified unilaterally here. |
| `sqlite/emails.sqlite.cutover_staging_20260719T163633Z.compaction.manifest.json` | 2.7 KB | 2026-07-19 16:05 | — (JSON, not a database) | — | — | **EVIDENCE** | Completion-marker manifest from the online-backup/compaction tooling (`backup_sqlite_online.py`/`compact_sqlite_offline.py` design in `SQLITE_STORAGE_MAINTENANCE.md`). Paired with the July 19 pre-cutover file above. |
| `sqlite/emails.sqlite.staged.cutover.cutover20260722T233536Z.compaction.manifest.json` | 2.8 KB | 2026-07-23 01:57 | — (JSON) | — | — | **EVIDENCE** | Completion-marker manifest paired with the (successful) July 22 cutover. |
| `sqlite/.origenlab_cutover_journals/` (4 files: `cutover20260719T163633Z.journal.json`(+`.prev`,`.private.json`(+`.prev`)), `cutover20260722T233536Z.journal.json`(+`.prev`,`.private.json`(+`.prev`))) | 3.5–7.2 KB each | Jul 19–23 | — (JSON) | — | — | **EVIDENCE** | Cutover-orchestrator state journals referenced by `SQLITE_POST_CUTOVER_HARDENING_NOTE.md` ("Completed/abandoned journals stay terminal under existing rules"). Operational state, not application data — out of scope for any action here. |
| `sqlite/emails-before-sag-supplier-fix-20260901-201253.sqlite` (+`-shm` 33 KB, `-wal` empty) | 66 GB | 2026-09-01 20:14 | pages 16,024,208 (~current live size), freelist 0, schema_version 1 | 92 | emails, contact_master, supplier_master, lead_research_prospect, commercial_opportunity, outbound_campaign (schema-current) | **BACKUP** (undocumented provenance) | Recent (2 days before the pinned research SHA), schema-current, ordinary read-write permissions — unlike the two cutover snapshots, this does **not** match the documented `backup_sqlite_online.py` naming convention (`emails_offline_<UTC timestamp>Z.sqlite`) or the cutover tooling's naming. `schema_version=1` (vs. the live DB's 1015) suggests it was produced by a logical rebuild/reload rather than a raw page-level physical copy, though the exact script that made it was not identified in this pass. Its name implies a deliberate pre-change safety snapshot ahead of supplier-related work; no doc found describing "sag-supplier-fix" specifically. **Recommend operator confirms intent and safe-retention window rather than treating the recency/naming alone as sufficient provenance.** |
| `sqlite/emails.before_email_mart_features_20260609_215126.sqlite` | 137 GB | 2026-06-09 21:57 | pages 33,421,815 (identical page count to the July 22 pre-cutover file), freelist 17,477,339, schema_version 645 | 70 | emails, contact_master, supplier_master, lead_research_prospect — missing `commercial_opportunity`/`outbound_campaign` | **BACKUP** (undocumented provenance) | Ordinary read-write permissions (not barrier-protected like the cutover files), predates the commercial-opportunity/outbound-campaign schema era, 3 months old relative to the pinned research SHA. No doc found describing a retention policy for this specific ad hoc snapshot. Largest single disk consumer among the non-cutover-protected files (137 GB) with the least-recent apparent relevance — a reasonable first candidate for an **operator-led** (not automatic) disposition review once its "mart features" migration has been confirmed stable for this long, but no caller/reference search was performed to support a stronger recommendation than that. |
| `sqlite/backups/emails_before_gmail_refresh_20260522_170343.sqlite` | 0 bytes | 2026-05-22 17:37 | `PRAGMA page_count` returns `0`; `file(1)` reports "empty" | n/a — not a valid SQLite database | n/a | **OBSOLETE_CANDIDATE** (evidence-backed, not size/age-backed) | A 0-byte file cannot be a valid SQLite database — this is conclusive, not inferred from size or age. Most likely a failed/interrupted backup write from 2026-05-22. Distinct from every other row in this table: the classification here rests on the file being provably non-functional as a database, not on staleness. Still not deleted in this pass per Phase 0 safety rules — flagged for operator confirmation. |
| `/home/rafael/data/arch2a-p1-scratch/emails_scratch.sqlite` | 66 GB | 2026-08-20 17:32 | pages 16,011,782, freelist 0, schema_version 1 | 88 | emails, contact_master, supplier_master, lead_research_prospect, commercial_opportunity — missing `outbound_campaign` | **EXPERIMENTAL** | Outside the `origenlab-email/sqlite` tree entirely, in a directory named for "ARCH2A" — matches the "ARCH-2A" Postgres-mirror-parity work referenced in `docs/architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md` and in prior session memory (ARCH-2B mirror-parity tests are still skipped pending `ORIGENLAB_TEST_SQLITE_PATH` as of early September 2026). `schema_version=1` again suggests a rebuilt/reloaded copy rather than a physical clone — consistent with a purpose-built test fixture. **Likely still in active use for ongoing ARCH-2A/2B testing — do not treat as disposable without confirming that work is complete.** |

## Summary for the disposition matrix / decision register

- **Do not delete, gate unchanged:** both `pre_cutover.cutover*` files and their manifests/journals — protected by the repository's own documented retention rule (separate-storage Online Backup API copy + passed deep forensic audit).
- **Operator decision needed, not urgent:** the July 19 pre-cutover file (abandoned attempt — the same rule technically still applies, but its rationale differs from the July 22 one); the two undocumented "before_X" ad hoc snapshots (schema-current Sept 1 one likely still wanted; the June 9 one is the largest disk consumer with the least apparent current relevance).
- **Operator decision needed, evidence-conclusive:** the 0-byte `backups/emails_before_gmail_refresh_20260522_170343.sqlite` is not a valid database; safe to review for removal once an operator confirms, but not removed here.
- **Confirm before touching:** `arch2a-p1-scratch/emails_scratch.sqlite` — likely still load-bearing for active ARCH-2A/2B test work.
- **No action implied:** the live `emails.sqlite` and the cutover-orchestrator's own journal/manifest files are all correctly in place and working as documented.
