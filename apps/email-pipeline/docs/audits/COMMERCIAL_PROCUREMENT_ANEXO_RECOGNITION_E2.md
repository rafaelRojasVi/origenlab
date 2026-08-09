# ANEXO-E2 — annex-backed equipment recognition comparison

Status: read-only measurement lane
Owner: email-pipeline-maintainers
Scope: `src/origenlab_email_pipeline/commercial_procurement_anexo_recognition/`

## Purpose

Measure whether the hardened #450/#451 annex evidence improves equipment
recognition **before** any of it is allowed to affect production opportunity
semantics.

This lane changes no production decision. It does not modify PR5D relevance,
PR5E/PR5E.2 queues, prospect ranking, or authorization; it does not persist
commercial state; and it does not touch Gmail, SQLite, or Postgres. It reads
local cached artifacts and writes one gitignored review packet.

## Architecture

```
                     same tender corpus (local detail cache)
                                   |
        +--------------------------+--------------------------+
        |                                                     |
  API/item evidence only                        API/item evidence
        |                                                + annex segments
        v                                                     v
  classify_product_text_unit  <-- same PR5D rules -->  classify_product_text_unit
        |                                                     |
  aggregate_tender_decision   <-- same aggregator -->  aggregate_tender_decision
        |                                                     |
   baseline decision                                  augmented decision
        +--------------------------+--------------------------+
                                   v
                    change class + interpretation + claims
                                   v
                     gitignored review packet + metrics
```

The lane owns **no matching rules of its own**. Baseline and augmented runs call
the same `classify_product_text_unit` and the same `aggregate_tender_decision`;
the only difference between them is which evidence units are supplied. Any
measured difference is therefore attributable to annex evidence rather than to a
second, divergent classifier.

`aggregate_tender_decision` reads only `coalesced_tender_id`, `evidence_ref_ids`,
and `lifecycle_class` from its tender argument, so `recognize._tender_stub`
supplies a minimal `CoalescedProcurementTender`. PR5C lifecycle classification is
deliberately not reproduced; lifecycle is echoed, not decided, here.

## Segmentation: why chunks are not fed in whole

#450 emits chunks at document-structure grain — a whole DOCX table, a whole PDF
page, a block of spreadsheet rows. Recognizing over those directly is actively
misleading. Measured on the real pilot corpus, one PDF page of tender
`745712-19-LP26` asserted five equipment categories at once (`balance`,
`centrifuge`, `incubator`, `magnetic_stirrer`, `shaker`) because every product on
the page shared one text blob. The resulting "claim" pointed at a page, which is
not a reviewable locator.

`segment.py` restores the boundary the source document already had:

| chunk locator | segment kind | segment locator |
|---|---|---|
| `docx_table` | `table_row` | `{"row": n}` (from the `[rN]` marker) |
| `xlsx_rows` | `sheet_row` | `{"row": n}` |
| `pdf_page` | `page_block` | `{"block": n}` (blank-line separated) |
| anything else | `whole_chunk` | `{}` |

After segmentation the same table yields eight precise per-row claims instead of
one page-level blob.

**Nothing is discarded.** A chunk whose structure markers do not parse falls back
to whole-chunk segmentation, and chunks that yield no segment (whitespace or
sub-minimal text) are counted in `annex_chunks_without_segments`.
`assert_read_only_invariants` fails the run if
`segmented + unsegmented != total`.

### Evidence tier

Tier drives PR5D's confidence band, so the mapping is explicit in
`constants.SEGMENT_EVIDENCE_TIERS`. A row of an "Ítem | Producto" annex table is
a product line in the same sense an API item line is, so table and sheet rows
earn `line_product_text`. Prose paragraphs and PDF page blocks are descriptive
text and earn `tender_description`.

## Presence vs absence

The load-bearing rule:

> Annex evidence may prove **presence**. Incomplete annex extraction may **never**
> prove **absence**.

`interpretation` is recorded separately from `change_class` for exactly this
reason:

| interpretation | meaning |
|---|---|
| `presence_proven` | an annex segment recognized an equipment category |
| `absence_unproven` | no annex claim, **and** part of the corpus was unread |
| `no_annex_claim_extraction_complete` | no annex claim, whole corpus read |
| `no_anexo_evidence_available` | no annex bundle for this tender at all |

Coverage debt propagates from the #450 outcome vocabulary: `needs_ocr`,
`needs_converter`, `unsupported_format`, `encrypted`, `corrupt`,
`partial_due_to_safety_limit`, `download_failed`, `extraction_failed`.
`extracted_empty` is **not** debt — an empty document was genuinely read.

## Provenance contract

Every claim carries tender id, attachment id, evidence attachment id, safe
filename, attachment SHA-256, detected format, document role, archive member
path, chunk id, contributing chunk ids, segment id, locator type, locator,
segment locator, a rendered `locator_display`, a bounded excerpt, the excerpt
digest, matched rule ids, the unit decision id, taxonomy/rules versions, whether
the category existed in baseline, and the annex effect.

There is no code path that emits a category without a resolvable chunk and
locator; `assert_read_only_invariants` rejects any claim referencing an unknown
chunk or segment.

## Document roles do not gate recognition

Role tags are ranking/context metadata only. Every document is searched
regardless of role, because the #450 pilot found specification tables inside
administrative documents. A parametrised test asserts recognition is identical
across all five role tags.

## Known false-positive risk: accessory-headed lines

Annex documents interleave the equipment specification with accessory and
spare-part lists, so reading annexes amplifies an existing PR5D behavior:

```
"Sonotrodo de repuesto para procesador ultrasónico"
  -> strong_equipment_class / ultrasonic_processor
```

PR5D rules are production and stay frozen, so this lane does not suppress the
claim. `risk.py` flags it as `accessory_headed_line` — advisory metadata that
fires when an accessory noun precedes the equipment match *and* the match is
introduced by "para". Lines that lead with the equipment are not flagged, so
`"Centrífuga de sobremesa con rotor de ángulo fijo"` and `"Centrífuga para tubos
eppendorf"` both stay clean.

## Known recognition gap

11 of the 25 canonical equipment classes have no detection pattern in PR5D rules
today, including `osmometer`, `reactor`, `spectrophotometer`, and
`chromatography_hplc`. Annex reading surfaces real demand for instruments the
current rules cannot categorize — the observed annex table listed
espectrofotómetro, termociclador, and destilador de agua, none of which resolve.
This is a rules-coverage finding, not an ANEXO-E2 defect.

## Outputs

Written atomically through `write_atomically` into a gitignored directory under
`reports/out/`:

| file | contents |
|---|---|
| `summary.json` | aggregate-only metrics; carries no per-tender rows |
| `comparison_rows.jsonl` | one row per tender, baseline vs augmented |
| `annex_claims.jsonl` | one row per (segment, category) claim with provenance |
| `coverage_summary.json` | per-tender coverage debt |
| `walkthrough.md` | representative cases, including failures |

Every artifact passes `assert_no_portal_tokens` before it reaches disk, so `qs=`,
`enc=`, and `ticket=` cannot leak into a shareable packet.

## Running it

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_anexo_recognition_comparison.py \
  --anexo-evidence-dir reports/out/active/current/<anexo evidence bundle> \
  --out-dir reports/out/active/current/anexo_e2_comparison \
  --json-summary
```

The script rejects `--apply`, `--send`, `--persist`, `--network`, `--ticket`,
`--gmail`, `--postgres`, `--outreach`, and `--schedule`.

## Corpus limitation observed at build time

Over the 263 locally cached tenders, only 3 have annex bundles from the #450
pilot. On those 3 the annexes **confirmed** baseline claims (29 strengthened
claims) and created no new positives, because the API item lines for that tender
already enumerated the equipment.

Separately, **52 of the 233 baseline-unresolved tenders defer their
specification to an annex** ("según anexo", "anexo técnico", "bases técnicas",
"especificaciones técnicas") and **none of them has a cached annex bundle**. The
value of annex reading for exactly the cases it should help is therefore
currently *unmeasured*, not disproven. Acquiring bundles for that 52-tender
subset is the concrete prerequisite for a production-integration decision.
