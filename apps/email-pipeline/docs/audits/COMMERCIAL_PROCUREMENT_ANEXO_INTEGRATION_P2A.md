# ANEXO-P2A — guarded production-compatible annex integration

Status: **implemented and locally validated**. Default: **off**. No production authorization change.

## Purpose and boundary

P2A lets the institution-prospect planner consume a previously built, local
ChileCompra annex evidence bundle. It does not download annexes, make network
requests, persist commercial state, authorize contact or outreach, mutate the
catalog, start PR5F/P2B, or add a second operator queue policy.

The production dependency graph is:

```text
PR5C
  -> canonical baseline PR5D
  -> verified annex ProductTextUnit augmentation
  -> affected-tender reaggregation by canonical PR5D aggregation_policy_v3
  -> PR5E rebuilt against the augmented PR5D semantic digest
  -> institution prospects
  -> existing operator queues and current_opportunity_blockers
```

Disabled mode follows the pre-P2A graph exactly. It performs no annex import or
read and emits no annex-only schema or artifact.

## Explicit activation

The canonical entrypoint accepts the paired flags:

```text
--enable-annex-opportunity-evidence
--annex-evidence-dir <existing-local-E1-bundle>
```

Either flag without the other is an error. The planner API enforces the same
pairing. The evidence directory must exist and must be disjoint from the atomic
output directory. Injecting a prebuilt PR5E result in enabled mode is rejected,
because it could carry a stale baseline PR5D dependency.

## Verified evidence boundary

Enabled mode requires all four E1 artifacts:

- `summary.json`
- `attachment_manifest.json`
- `extraction_manifest.json`
- `evidence_chunks.jsonl`

The strict loader rejects malformed or empty artifacts, duplicate/cross-tender
identities, unsupported versions, broken attachment/member/chunk joins,
unrecomputable IDs, text hash/count drift, extraction outcome/count drift,
coverage inconsistencies, semantic-digest drift, and unredacted portal tokens.
Only canonical Mercado Público PR5C identities can bind. Extra bundles are
reported unused and cannot synthesize tenders.

Every segment reconciles to exactly one deterministic PR5D unit and canonical
unit decision. Incomplete extraction preserves the positive/absence asymmetry:
independently recognized equipment can survive, while an incomplete bundle
with no positive equipment evidence cannot create negative proof.

## Three separate temporal oracles

P2A never weakens `current_opportunity_blockers`.

1. **Current production:** real publication/close chronology applies. Expired
   annex-backed tenders remain outside `current_opportunity_queue`.
2. **Time-independent annex eligibility:** calls the existing blocker function
   and removes only its explicit lifecycle/elapsed-close blockers in a separate,
   precisely named reconciliation. It never labels the result current.
3. **Historical production replay:** deterministic fixtures preserve the real
   status/publication/close facts and use constructed acquisition stamps at or
   before each replay `as_of_utc`. They do not claim a contemporaneous archived
   snapshot existed.

The historical fixture windows are `2026-06-20T12:00:00Z` for
`1057510-40-LP26` and `2026-07-10T12:00:00Z` for `4034-16-LE26` plus
`1057510-53-LP26`. Their union—not either individual replay—is the three P1
counterfactual eligibility cases.

## Enabled-only audit artifacts

- `annex_integration_reconciliation.json`
- `annex_opportunity_provenance.jsonl`

The sidecar uses stable production identities and retains annex claim, unit,
attachment, chunk, segment, locator, source-format, coverage-debt, and semantic
digest lineage. It contains no raw annex text/bytes and explicitly records false
contact/outreach authorization, false persistent mutation, false SQLite/
Postgres/Gmail writes, false network acquisition, and false PR5F/P2B state.

Generated operator plans may change in enabled mode; persistent commercial
state does not. Recognition remains separate from catalog capability, so
autoclave and microscope evidence may be recognized while remaining outside
OrigenLab's recorded sellable catalog.

## P2A-v2 final implementation validation

Final local validation: **2026-08-13**.

Implementation branch:

`feat/anexo-p2a-guarded-operator-integration-v2`

Production integration validated through commit:

`011fd48 feat(procurement): wire guarded annex evidence into operator planner`

### Repository validation

Full email-pipeline suite:

- 5141 passed
- 37 skipped
- 3 xfailed
- 1 expected duplicate ZIP member warning

`./scripts/validate.sh`:

- 164 passed
- 3 xfailed
- Operator status: `READY`

Focused validation:

- P2A bundle, augmentation, temporal, wiring and audit: 74 passed
- ANEXO and PR5D regression: 327 passed
- PR5E institution regression: 101 passed

No obsolete active integration reference remained to
`verified.evidence`, `segment_chunks()` or `evidence=evidence`.

### Verified real E1 bundle

Validated evidence directory:

`reports/out/active/current/chilecompra_anexo_evidence_e2_deferred52`

Strict loader result:

- bundle tenders: 52
- attachments discovered: 142
- attachments downloaded: 142
- evidence chunks: 2127
- evidence semantic digest:
  `932ecc9c23bc9487e539270b13d4f752333e1e26ccf13bcb3a4f04900e208476`

The older `chilecompra_anexo_evidence_20260809T030308Z` bundle was correctly
rejected because it predates required canonical provenance fields and was not
used for final P2A validation.

### Real baseline versus enabled dry-run

Both runs used identical production-read inputs:

- SQLite: `/home/rafael/data/origenlab-email/sqlite/emails.sqlite`
- detail cache: `reports/out/active/current/chilecompra_detail_cache`
- detail cache files: 275
- equipment manifest:
  `equipment_first_operator_queue_chilecompra_api_20260813.manifest.json`
- manifest generated at: `2026-08-13T00:12:01+00:00`
- refresh state:
  `reports/out/active/current/chilecompra_equipment_auto_refresh_state.json`
- as-of: `2026-08-13T02:00:00Z`
- run context: `production_dry_run`

Results:

- baseline RC: 0
- enabled RC: 0

SQLite metadata before and after both runs was identical:

- mtime epoch: `1786576034`
- size: `65574350848`
- inode: `469426`

No SQLite mutation occurred.

### Real annex reconciliation

Enabled reconciliation:

- bundle tenders: 52
- bound tenders: 47
- unused tenders: 5
- incomplete tenders: 12
- applied annex units: 3599
- withheld annex units: 1050
- positive annex claims: 31

Unused evidence tenders:

- `1398-52-L126`
- `1702-25-L126`
- `2099-23-L126`
- `2099-24-L126`
- `3710-50-L126`

They did not synthesize production tenders.

Integration semantic digest:

`00eaa2b2c9610bf02becf1ca3d3e50dc456a418817e414ee9004022638d50c3c`

Dependency digests:

- baseline PR5D:
  `72865a990fce1becc6e277d35516147c1a0e70bc1f2e9ea6edb8c52db382eece`
- augmented PR5D:
  `047c41735b111f703209b88f70321cc1487334ea230b59adc1ae2a6838247266`
- enabled PR5E:
  `289f1009b884692d73f5e51a80386101f9b57f9a84d4f1f491853c32b843397f`
- enabled institution plan:
  `c37ad09ad42955cf187757ccbef263d81df82949eb21f0ef2ffa474e105b56a7`

### Current-opportunity safety

Current opportunity queue:

- baseline: 5
- enabled: 5
- annex-caused entries: 0
- annex-caused removals: 0

The exact time-independent and expired-but-annex-eligible trio was:

- `1057510-40-LP26`
- `1057510-53-LP26`
- `4034-16-LE26`

All three remained outside the current opportunity queue.

Their provenance records explicitly report:

- `annex_queue_eligible_except_temporal_gate = true`
- `entry_caused_by_annex = false`
- `lifecycle_not_active_open`
- `close_timestamp_elapsed`

The three provenance records retain deterministic attachment, evidence
attachment, chunk, segment, annex claim, production claim, ProductTextUnit,
unit decision, locator and semantic-digest lineage.

### Observed intelligence delta

P2A changed historical and review intelligence while preserving current
opportunity eligibility:

- equipment purchase signals: 55 -> 63
- profiles with equipment purchase evidence: 25 -> 26
- historical prospect queue: 809 -> 816
- contact gap queue: 586 -> 590
- line evidence review queue: 15610 -> 15663
- current opportunity queue: 5 -> 5

### Safety authority

The enabled reconciliation recorded every following authority as false:

- contact authorization
- outreach authorization
- persistent state mutation
- SQLite writes
- Postgres writes
- Gmail writes
- network annex acquisition
- PR5F started
- P2B started

Final machine assertion:

`P2A_REAL_REPLAY_CONTRACT=PASS`

`all_write_authorities=false`

## Final P2A conclusion

P2A is implemented, production-compatible, opt-in and read-only.

It improves annex-backed historical and commercial recognition without
weakening production lifecycle or close-time gates. It preserves incomplete
evidence coverage debt, keeps recognition separate from catalog capability,
rebuilds PR5E from augmented PR5D, and does not authorize persistence,
contact, outreach, Gmail, PR5F or P2B.

Any activation beyond local planning remains a separate human decision.

