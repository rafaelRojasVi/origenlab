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
  SHA-256 with an `extraction_reused_from:` marker.
- different filename + same bytes → both rows kept, extraction reused.
- same filename + different bytes → distinct documents, both extracted, flagged
  `same_name_different_content`, never overwritten.
- contradictory requirements across revisions are preserved. Conflict resolution
  belongs to a later layer, not to extraction.

## Security limits

All limits are parameters and are tested with intentionally tiny values.

- Archive preflight reads the central directory only: member count, cumulative
  and per-member uncompressed bytes, compression ratio, and recursion depth.
  A zip bomb is rejected before decompression.
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

## Cache and output

Raw payloads go to a SHA-256 content-addressed cache under gitignored
`reports/out/`, written exclusively (`O_EXCL`, `O_NOFOLLOW`) via temp + rename,
and re-verified against their own hash on read. The review bundle is published
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
