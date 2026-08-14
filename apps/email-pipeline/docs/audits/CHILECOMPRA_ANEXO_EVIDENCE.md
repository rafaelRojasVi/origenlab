# ChileCompra anexo evidence — acquisition and extraction audit

Status: read-only evidence lane. **Not PR5F.** Nothing here feeds PR5D relevance,
PR5E.1 prospect strength, PR5E.2 queues, contact resolution, outreach, or
persistence.

Package: `src/origenlab_email_pipeline/chilecompra_anexo_evidence/`

## Why anexos are multi-document evidence

A licitación is not "one tender = one PDF". A single ficha can publish dozens or
hundreds of rows across several `VerAntecedentes.aspx` listing pages, mixing
administrative bases, technical specifications, economic forms, templates, and
revised documents in PDF, DOCX, XLSX, XLS, CSV, TXT, XML, and ZIP form. The same
filename can appear twice with different bytes (a revision), and the same bytes
can appear under two filenames.

The equipment requirement that matters is frequently *not* in the file whose name
looks technical. It shows up inside a table in an administrative document, in a
worksheet past sheet 3, in a row past 100, in a column past 12, or inside a ZIP.
So this lane extracts everything it can and never filters by filename before
extraction.

## Completeness semantics

Every discovered attachment row gets exactly one outcome. There is no silent skip:

`extraction_success`, `extracted_empty`, `needs_ocr`, `needs_converter`,
`unsupported_format`, `encrypted`, `corrupt`, `partial_due_to_safety_limit`,
`download_failed`, `extraction_failed`.

`extraction_complete=true` requires **every** attachment and every archive member
to end in `extraction_success` or `extracted_empty`, with no incomplete reason
codes and every discovered row downloaded. Anything else keeps the bundle
incomplete.

This is the point of the whole lane: a later classifier must be able to
distinguish **"we read everything and found no equipment"** from **"we did not
manage to read everything"**. Positive evidence can still exist inside an
incomplete bundle — it just cannot be read as an absence proof.

## Supported vs not supported

| Format | Behavior |
|---|---|
| PDF | Page-by-page chunks, page number + page count retained |
| DOCX | Paragraphs, tables, table cells, headers, footers |
| XLSX | All worksheets including hidden; bounded row chunks with sheet + row range |
| XLS | `xlrd` when the `data-tools` group is installed, else `needs_converter` |
| CSV | Bounded row chunks, no 200-row cap |
| TXT | Encoding detection with fallback |
| XML | Local parse only; entity declarations refused |
| ZIP | Recursive member extraction with full provenance |
| Image | `needs_ocr` |
| Legacy DOC / OLE | `needs_converter` (no safe converter established) |
| PPTX | `needs_converter` |
| RAR / 7z | `unsupported_format` |
| Unknown binary | `unsupported_format` |

OCR is deliberately **not** implemented. Image-only PDFs report `needs_ocr` with
their page count preserved; no text is invented. OCR becomes a separate lane only
if real corpus evidence shows it is needed.

## Acquisition: inventory before download

`list_licitacion_attachments()` performs GETs only — ficha page, then every
listing page — and returns the complete inventory with zero attachment postbacks.
Knowing the true discovered count first is what makes the budget honest:

- if `attachments_discovered > max_attachment_count`, the run fails **before any
  body download** with `attachment_count_budget_exceeded` and reports the real
  discovered count. It never keeps "the first N".
- the same principle applies to `total_bytes_budget_exceeded`, which can only be
  detected mid-stream; every remaining row is still recorded as not downloaded.

`iter_licitacion_attachments()` yields one `DownloadedAttachment` at a time over a
single cookie session, so aggregate body memory stays bounded to the current file.
The #438 `fetch_licitacion_attachments()` and `save_licitacion_attachments()`
entry points are untouched and remain list-returning.

### Session binding

The portal is ASP.NET WebForms, so `__VIEWSTATE` and the anexo postbacks are only
valid inside the cookie session that produced them. An inventory therefore carries
the opener it was built with, and `iter_licitacion_attachments()` consumes it
through exactly that session: passing a different opener raises, and an inventory
with no bound session is refused rather than silently retried on a fresh cookie
jar. The binding is a runtime object reference only — excluded from `repr` and
equality, and never serialized into a shareable artifact.

## Provenance and chunk model

- `TenderAttachmentBundle` — counts, completeness flags, incomplete reason codes,
  deterministic semantic digest.
- `AttachmentRecord` — one per portal row: listing/row ordinals, portal filename,
  safe filename, tipo, descripción, fecha, Content-Type, detected format,
  extension, byte count, SHA-256, download status, outcome, warnings, duplicate
  relationship.
- `ArchiveMemberRecord` — container attachment ID, member path, member SHA-256,
  depth, format, outcome. Nested members keep `outer.zip::inner.zip::file.pdf`
  paths; provenance is never flattened.
- `EvidenceChunk` — bounded text with `chunk_id`, locator type and fields
  (PDF page, XLSX sheet + row range, DOCX section/paragraph/table, CSV row range,
  archive member path), `text_sha256`, char count, warnings.

Attachment IDs derive from tender + listing ordinal + row ordinal only, so
filesystem or input ordering cannot change them. The semantic digest sorts by
stable IDs and hashes chunk text rather than embedding it.

## Duplicates and revisions

- same filename + same bytes → both provenance rows kept, extraction reused by
  SHA-256.
- different filename + same bytes → both rows kept, extraction reused.
- same filename + different bytes → distinct documents, both extracted, flagged
  `same_name_different_content`, never overwritten.
- contradictory requirements across revisions are preserved. Conflict resolution
  belongs to a later layer, not to extraction.

Reuse is structured, not a string to be parsed. Every record exposes
`duplicate_of_attachment_id`, `extraction_reused_from_attachment_id`, and
`evidence_attachment_id`; downstream code joins chunks on
`chunk.attachment_id == record.evidence_attachment_id`. Originals point at
themselves, duplicates point at the original, and text is stored once.

## Security limits

All limits are parameters and are tested with intentionally tiny values.

- Archive preflight reads the central directory only: member count, cumulative
  and per-member uncompressed bytes, compression ratio, and recursion depth.
  A zip bomb is rejected before decompression.
- The compression ratio is checked **per member as well as whole-archive**, so a
  single bomb member cannot hide behind ordinary members that dilute the
  aggregate. A member declaring `file_size > 0` with `compress_size <= 0` is
  treated as suspicious and fails closed.
- DOCX, XLSX, and PPTX are ZIP containers, so they run the *same* preflight
  before any part is decompressed. Without it a malicious OOXML file would reach
  `zipfile`/`openpyxl` directly and bypass the archive envelope entirely: a
  117 KB fixture drove 374 MB of peak memory, because the downstream character
  budget only applies *after* decompression. A container that fails preflight
  yields `partial_due_to_safety_limit` (or `encrypted` for encrypted members)
  with `extraction_complete = false` — never a silent "corrupt" or "empty".
- **Declared bounds are not enough.** The central directory is attacker
  controlled: a member can declare 2 KB, carry a CRC matching that truncated
  prefix, and really expand to tens of megabytes. Such a container passes
  preflight, and the parsers then issue their own *unbounded* reads —
  `openpyxl` reads `workbook.xml`, `styles.xml`, the rels and
  `[Content_Types].xml` eagerly, and `python-docx` reads `document.xml`,
  headers, and footers. Measured from an ~80 KB container: **161 MB** peak for
  XLSX and **134 MB** for DOCX, with DOCX still reporting
  `extraction_success`. `verify_actual_expansion()` therefore walks the real
  deflate streams in 64 KiB chunks before any parser runs, counting true output
  and aborting the moment a per-member or aggregate bound is crossed. It reads
  the raw member bytes with `zlib` rather than through `ZipExtFile`, which
  stops at the *declared* size and so can never reveal an understated header.
  The verifier holds one chunk at a time (measured under 4 MB while walking a
  40 MB member), and the same two-stage envelope covers DOCX and XLSX alike.
  Reasons are explicit: `archive_actual_member_bytes_limit`,
  `archive_actual_total_bytes_limit`, `archive_actual_ratio_limit`,
  `archive_actual_size_mismatch`.
- Generic ZIP bundles use the same actual-expansion verifier before member
  iteration. If verification fails, the bundle records
  `partial_due_to_safety_limit`, emits no member/chunk evidence for the rejected
  container, and preserves the exact `archive_actual_*` reason code.
- Accepted parts are additionally read in fixed chunks rather than one large
  capped read, since `read(cap + 1)` lets zipfile decompress greedily and spike
  far past the part's real size before the cap applies.
- Members are read through a capped stream, so an understated size header still
  gets truncated and flagged rather than trusted.
- Path traversal, absolute paths, and drive-qualified members yield no bytes but
  still produce records.
- Encrypted members are refused and recorded, never skipped.
- Nothing is extracted to the filesystem; members are processed in memory.
- Macros and binary parts are detected, flagged, and never executed.
- XML entity declarations are refused outright (no billion-laughs expansion, no
  external entity resolution, no network).
- Opaque portal tokens (`qs`, `enc`, `ticket`) are redacted, and every artifact is
  scanned before publication — publication fails rather than leaking.

Known cost: verifying actual expansion decompresses every OOXML member once
before parsing, so an accepted document is decoded twice. Real anexos are small
(largest observed member 257 KB, largest aggregate 820 KB across 23 cached real
files, worst real expansion ratio 28:1 against a 200:1 policy), and hostile
input aborts early, so the cost is bounded and paid only on container formats.

## Cache and output

Raw payloads go to a SHA-256 content-addressed cache under gitignored
`reports/out/`. A stored object is trusted only when it is a regular,
non-symlink file (opened `O_NOFOLLOW`, `S_ISREG` checked) whose bytes hash to the
digest in its own name; `read()` fails closed and returns `None` otherwise.
The documented replacement policy is that an object failing self-verification is
untrusted and is atomically replaced by the verified bytes on the next `put()`.
Staging uses a uniquely and exclusively created `mkstemp` path rather than a
PID-derived name, so concurrent writers can never collide on — or delete — each
other's temp file, and publication is a same-directory `os.replace`. Racing
writers for one digest therefore always leave exactly the expected bytes.
The review bundle is published
with `write_atomically`, so a half-finished run never installs a manifest
claiming `bundle_complete=true`:

```
reports/out/active/current/chilecompra_anexo_evidence_<timestamp>/
  summary.json
  attachment_manifest.json
  extraction_manifest.json
  evidence_chunks.jsonl
  walkthrough.md
```

Raw downloaded documents are never committed.

## Next integration boundary

The next slice may read `evidence_chunks.jsonl` and the completeness flags. It
must treat `extraction_complete=false` as "absence is unproven" and must not
convert anexo text into relevance, prospect strength, queue membership, or
outreach state without an explicit approved PR.
