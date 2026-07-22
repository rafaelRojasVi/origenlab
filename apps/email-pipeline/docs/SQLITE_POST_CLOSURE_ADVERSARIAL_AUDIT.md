# SQLite post-closure adversarial verification

**Not PR-G.** Canonical roadmap remains **A–F only** (closed via PRs #387, #393, #394, #395, #397, #400).

This document records a post-closure adversarial test expansion and a strictly
read-only production SQLite reconnaissance. It does **not** authorize a cutover.

## Boundaries

- July 19 MID `cutover20260719T163633Z` remains permanently non-resumable.
- Waves 3B/3C remain blocked.
- All destructive / corruption / concurrency / backup / swap / permission /
  crash tests use temporary synthetic databases only.
- Live production SQLite was never opened with `sqlite3.connect`, never backed
  up, never checkpointed, never swapped, and never chmod'd by this effort.

## Production read-only reconnaissance (sanitized)

Path resolution used `canonical_production_sqlite_path()` only
(`ORIGENLAB_SQLITE_PATH` is never cutover identity).

Allowed operations performed:

- `lstat` / existence checks for main, `-wal`, `-shm`, `-journal`
- `statvfs` free-capacity buckets
- header-only parse (`parse_sqlite_header_meta`, 100 bytes)
- `systemctl --user is-active` / `is-enabled` for API + health timer
- read-only `/proc` writer inventory via `FilesystemAdapters.list_writers`
- non-mutating `plan_preflight` (`apply=False`) with an installed
  `sqlite3.connect` guard

Sanitized aggregates (no absolute paths / PIDs / inodes / tokens):

| Observation | Value |
|-------------|-------|
| main present / regular | yes |
| main size bucket | ~127 GiB (`136895754240` bytes) |
| WAL present | yes (size 0) |
| SHM present | yes (32 KiB) |
| rollback `-journal` | absent |
| header page_size | 4096 |
| header journal format | wal |
| page_count | 33421815 |
| API active/enabled | active / enabled |
| health timer active/enabled | active / enabled |
| mail/mirror pause | absent |
| FD classification hits | none observed |
| lock records | absent ×3 |
| stale artifact categories | journal_json:2, staging:1 |
| preflight blockers | 9 (pauses, readers, flock, barrier, WAL, plan) |
| member mtime mutations during recon | **0** |

Full sanitized JSON was written only under a timestamped `/tmp` directory and
was **not** committed.

## New test modules

| Module | Focus |
|--------|-------|
| `tests/sqlite_adversarial_support.py` | Synthetic corpus factory + shared helpers |
| `tests/test_sqlite_cutover_state_machine_adversarial.py` | Illegal transitions, MID lockout, terminals |
| `tests/test_sqlite_cutover_fault_injection_matrix.py` | `fail_after` / journal-write faults |
| `tests/test_sqlite_cutover_filesystem_races.py` | Header/corpus/sidecars/FS edge cases |
| `tests/test_sqlite_cutover_fd_observation_matrix.py` | FD taxonomy precedence matrix |
| `tests/test_sqlite_cutover_journal_fuzz.py` | Journal + CLI fuzz / evidence sanitization |
| `tests/test_sqlite_backup_property_matrix.py` | Backup + OperationalError matrix |
| `tests/test_sqlite_cutover_http_adversarial.py` | Loopback smoke HTTP hostility |
| `tests/test_sqlite_cutover_permission_matrix.py` | Barrier / sidecar combinations |
| `tests/test_sqlite_cutover_concurrency.py` | Lock races / external re-enable / tamper |

## Production defects found

1. **`load_journal` non-object JSON (`AttributeError`)**  
   Malformed journals whose JSON root was a list/null/string/int raised
   `AttributeError` instead of a fail-closed `CutoverError(AMBIGUOUS)`.
   Minimal fix: reject non-dict roots before field access.
   Regression: `test_load_journal_rejects_malformed` covers `[]`/`null`/`"string"`/`1`.

## Residual risks still open

- `ambiguous_pre_exchange` (fail-closed; no positive classifier)
- same-UID `/proc` denial for unrelated processes
- short backups may omit `during_copy`
- partial cleanup unlink failures (must not erase primary failure)
- `sanitize_evidence` currently gates absolute paths / email-like text only
  (token-shaped strings alone are not rejected by that helper)
- environment: WSL restart, capacity, credentials, operator error

## Authorization

This audit does **not** authorize preflight against production beyond the
already-completed read-only recon, does **not** authorize `--apply`, and does
**not** authorize Waves 3B/3C.
