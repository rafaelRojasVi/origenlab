"""Read-only attachment metadata validation (Phase 2.3)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.attachment_report_sql import (
    attachment_business_doc_filename_extension_expr_sql,
    attachment_business_doc_predicate_sql,
    attachment_delivery_noise_predicate_sql,
)

SUMMARY_KEYS: frozenset[str] = frozenset(
    {
        "total_emails",
        "total_attachments",
        "emails_with_attachments",
        "emails_non_inline",
        "emails_business_doc",
        "drift_has_attachments_no_rows",
        "drift_attachment_count_mismatch",
        "missing_filename_with_size",
        "zero_size_attachments",
        "duplicate_sha256_groups",
    }
)


@dataclass(frozen=True, slots=True)
class AttachmentValidationResult:
    """Structured metrics from read-only attachment validation."""

    total_emails: int
    total_attachments: int
    emails_with_attachments: int
    distinct_email_id_attachments: int
    sum_attachment_count: int
    attachment_rows_with_email: int
    orphan_attachment_rows: int
    emails_non_inline: int
    emails_business_doc: int
    class_images: int
    class_pdf: int
    class_excel: int
    class_word: int
    class_archives: int
    class_noise: int
    class_other: int
    top_business_doc_extensions: list[tuple[str, int]]
    cotiz_business: int
    by_content_type: list[tuple[str, int]]
    by_extension: list[tuple[str, int]]
    inline_vs_non_inline: list[tuple[str, int]]
    pdf_emails: int
    excel_emails: int
    word_emails: int
    image_emails: int
    drift_has_attachments_no_rows: int
    drift_attachment_count_mismatch: int
    missing_filename_with_size: int
    zero_size_attachments: int
    duplicate_sha256_groups: int
    zero_by_content_type: list[tuple[str, int]]
    zero_by_extension: list[tuple[str, int]]
    zero_by_inline: list[tuple[str, int]]
    zero_business_docs: int
    zero_byte_samples: list[tuple[str | None, str | None, int | None]]
    sample_attachments: dict[str, list[tuple[int, str | None, str | None, int | None, str | None]]] = field(
        default_factory=dict
    )

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total_emails": self.total_emails,
            "total_attachments": self.total_attachments,
            "emails_with_attachments": self.emails_with_attachments,
            "emails_non_inline": self.emails_non_inline,
            "emails_business_doc": self.emails_business_doc,
            "drift_has_attachments_no_rows": self.drift_has_attachments_no_rows,
            "drift_attachment_count_mismatch": self.drift_attachment_count_mismatch,
            "missing_filename_with_size": self.missing_filename_with_size,
            "zero_size_attachments": self.zero_size_attachments,
            "duplicate_sha256_groups": self.duplicate_sha256_groups,
        }


def run_attachment_validation(conn: sqlite3.Connection) -> AttachmentValidationResult:
    """Collect attachment validation metrics (read-only; does not mutate SQLite)."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    total_emails = cur.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    total_attachments = cur.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    emails_with_attachments = cur.execute(
        "SELECT COUNT(*) FROM emails WHERE has_attachments = 1"
    ).fetchone()[0]

    distinct_email_id_att = cur.execute(
        "SELECT COUNT(DISTINCT email_id) FROM attachments"
    ).fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM emails WHERE has_attachments = 1"
    ).fetchone()[0]
    sum_attachment_count = cur.execute(
        "SELECT SUM(attachment_count) FROM emails"
    ).fetchone()[0]
    att_with_email = cur.execute(
        "SELECT COUNT(*) FROM attachments a WHERE EXISTS (SELECT 1 FROM emails e WHERE e.id = a.email_id)"
    ).fetchone()[0]
    orphan_att = cur.execute(
        "SELECT COUNT(*) FROM attachments a WHERE NOT EXISTS (SELECT 1 FROM emails e WHERE e.id = a.email_id)"
    ).fetchone()[0]

    _noise = attachment_delivery_noise_predicate_sql("a")
    _business_doc = attachment_business_doc_predicate_sql("a")
    emails_non_inline = cur.execute(
        """
        SELECT COUNT(DISTINCT a.email_id) FROM attachments a
        INNER JOIN emails e ON e.id = a.email_id
        WHERE COALESCE(a.is_inline, 0) = 0
        """
    ).fetchone()[0]
    emails_business_doc = cur.execute(
        f"""
        SELECT COUNT(DISTINCT a.email_id) FROM attachments a
        INNER JOIN emails e ON e.id = a.email_id
        WHERE {_business_doc}
        """
    ).fetchone()[0]

    class_images = cur.execute(
        "SELECT COUNT(*) FROM attachments a INNER JOIN emails e ON e.id = a.email_id WHERE LOWER(COALESCE(a.content_type,'')) LIKE 'image/%'"
    ).fetchone()[0]
    class_pdf = cur.execute(
        "SELECT COUNT(*) FROM attachments a INNER JOIN emails e ON e.id = a.email_id WHERE LOWER(COALESCE(a.content_type,'')) LIKE 'application/pdf%' OR LOWER(COALESCE(a.filename,'')) LIKE '%.pdf'"
    ).fetchone()[0]
    class_excel = cur.execute(
        """
        SELECT COUNT(*) FROM attachments a INNER JOIN emails e ON e.id = a.email_id
        WHERE LOWER(COALESCE(a.filename,'')) GLOB '*.xls' OR LOWER(COALESCE(a.filename,'')) GLOB '*.xlsx' OR LOWER(COALESCE(a.filename,'')) GLOB '*.csv'
           OR LOWER(COALESCE(a.content_type,'')) LIKE '%spreadsheet%' OR LOWER(COALESCE(a.content_type,'')) LIKE '%ms-excel%'
        """
    ).fetchone()[0]
    class_word = cur.execute(
        """
        SELECT COUNT(*) FROM attachments a INNER JOIN emails e ON e.id = a.email_id
        WHERE LOWER(COALESCE(a.filename,'')) GLOB '*.doc' OR LOWER(COALESCE(a.filename,'')) GLOB '*.docx'
           OR LOWER(COALESCE(a.content_type,'')) LIKE '%msword%' OR LOWER(COALESCE(a.content_type,'')) LIKE '%wordprocessing%'
        """
    ).fetchone()[0]
    class_archives = cur.execute(
        """
        SELECT COUNT(*) FROM attachments a INNER JOIN emails e ON e.id = a.email_id
        WHERE LOWER(COALESCE(a.filename,'')) GLOB '*.zip' OR LOWER(COALESCE(a.filename,'')) GLOB '*.rar'
           OR LOWER(COALESCE(a.content_type,'')) LIKE '%zip%' OR LOWER(COALESCE(a.content_type,'')) LIKE '%x-zip%' OR LOWER(COALESCE(a.content_type,'')) LIKE '%rar%'
        """
    ).fetchone()[0]
    class_noise = cur.execute(
        f"""
        SELECT COUNT(*) FROM attachments a INNER JOIN emails e ON e.id = a.email_id
        WHERE {_noise}
        """
    ).fetchone()[0]
    class_other = cur.execute(
        f"""
        SELECT COUNT(*) FROM attachments a INNER JOIN emails e ON e.id = a.email_id
        WHERE LOWER(COALESCE(a.content_type,'')) NOT LIKE 'image/%'
          AND LOWER(COALESCE(a.content_type,'')) NOT LIKE 'application/pdf%' AND LOWER(COALESCE(a.filename,'')) NOT LIKE '%.pdf'
          AND NOT (LOWER(COALESCE(a.filename,'')) GLOB '*.xls' OR LOWER(COALESCE(a.filename,'')) GLOB '*.xlsx' OR LOWER(COALESCE(a.filename,'')) GLOB '*.csv'
               OR LOWER(COALESCE(a.content_type,'')) LIKE '%spreadsheet%' OR LOWER(COALESCE(a.content_type,'')) LIKE '%ms-excel%')
          AND NOT (LOWER(COALESCE(a.filename,'')) GLOB '*.doc' OR LOWER(COALESCE(a.filename,'')) GLOB '*.docx'
               OR LOWER(COALESCE(a.content_type,'')) LIKE '%msword%' OR LOWER(COALESCE(a.content_type,'')) LIKE '%wordprocessing%')
          AND NOT (LOWER(COALESCE(a.filename,'')) GLOB '*.zip' OR LOWER(COALESCE(a.content_type,'')) LIKE '%zip%' OR LOWER(COALESCE(a.content_type,'')) LIKE '%x-zip%')
          AND NOT ({_noise})
        """
    ).fetchone()[0]

    ext_expr_main = attachment_business_doc_filename_extension_expr_sql("a")
    top_business_doc_extensions = [
        (str(row[0]), int(row[1]))
        for row in cur.execute(
            f"""
        SELECT {ext_expr_main} AS ext, COUNT(*) AS c
        FROM attachments a INNER JOIN emails e ON e.id = a.email_id
        WHERE {_business_doc} AND a.filename IS NOT NULL AND instr(a.filename, '.') > 0
        GROUP BY ext ORDER BY c DESC LIMIT 10
        """
        )
    ]

    cotiz_business = cur.execute(
        f"""
        SELECT COUNT(DISTINCT e.id) FROM emails e
        INNER JOIN attachments a ON a.email_id = e.id AND {_business_doc}
        WHERE (e.subject LIKE '%cotización%' OR e.subject LIKE '%cotizacion%' OR e.subject LIKE '%Cotización%' OR e.subject LIKE '%Cotizacion%'
               OR e.top_reply_clean LIKE '%cotización%' OR e.top_reply_clean LIKE '%cotizacion%')
        """
    ).fetchone()[0]

    by_content_type = [
        (str(row["ct"]), int(row["c"]))
        for row in cur.execute(
            """
        SELECT COALESCE(content_type,'(none)') AS ct, COUNT(*) AS c
        FROM attachments
        GROUP BY ct
        ORDER BY c DESC
        LIMIT 15
        """
        )
    ]

    by_extension = [
        (str(row["ext"]), int(row["c"]))
        for row in cur.execute(
            """
        SELECT LOWER(
            CASE
                WHEN instr(substr(filename, instr(filename, '.') + 1), '.') = 0
                THEN substr(filename, instr(filename, '.') + 1)
                WHEN instr(substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1), '.') = 0
                THEN substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1)
                ELSE substr(substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1), instr(substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1), '.') + 1)
            END
        ) AS ext,
               COUNT(*) AS c
        FROM attachments
        WHERE filename IS NOT NULL AND instr(filename, '.') > 0
        GROUP BY ext
        ORDER BY c DESC
        LIMIT 15
        """
        )
    ]

    inline_vs_non_inline: list[tuple[str, int]] = []
    for row in cur.execute(
        """
        SELECT COALESCE(is_inline, 0) AS inline_flag, COUNT(*) AS c
        FROM attachments
        GROUP BY inline_flag
        ORDER BY inline_flag
        """
    ):
        label = "inline" if row["inline_flag"] == 1 else "attachment"
        inline_vs_non_inline.append((label, int(row["c"])))

    def count_emails_with_ext(conds: str) -> int:
        q = f"""
        SELECT COUNT(DISTINCT email_id)
        FROM attachments
        WHERE {conds}
        """
        return cur.execute(q).fetchone()[0]

    pdf_emails = count_emails_with_ext(
        "LOWER(content_type) LIKE 'application/pdf%' OR LOWER(filename) LIKE '%.pdf'"
    )
    excel_emails = count_emails_with_ext(
        "LOWER(filename) GLOB '*.xls' OR LOWER(filename) GLOB '*.xlsx' OR LOWER(filename) GLOB '*.csv'"
    )
    word_emails = count_emails_with_ext(
        "LOWER(filename) GLOB '*.doc' OR LOWER(filename) GLOB '*.docx'"
    )
    image_emails = count_emails_with_ext("LOWER(content_type) LIKE 'image/%'")

    drift_has = cur.execute(
        """
        SELECT COUNT(*) FROM emails e
        LEFT JOIN (
          SELECT email_id, COUNT(*) AS c FROM attachments GROUP BY email_id
        ) a ON e.id = a.email_id
        WHERE COALESCE(e.has_attachments,0) = 1 AND COALESCE(a.c,0) = 0
        """
    ).fetchone()[0]
    drift_count = cur.execute(
        """
        SELECT COUNT(*) FROM emails e
        LEFT JOIN (
          SELECT email_id, COUNT(*) AS c FROM attachments GROUP BY email_id
        ) a ON e.id = a.email_id
        WHERE COALESCE(e.attachment_count,0) != COALESCE(a.c,0)
        """
    ).fetchone()[0]

    missing_name = cur.execute(
        """
        SELECT COUNT(*) FROM attachments
        WHERE filename IS NULL AND size_bytes > 0
        """
    ).fetchone()[0]
    zero_size = cur.execute(
        "SELECT COUNT(*) FROM attachments WHERE size_bytes = 0"
    ).fetchone()[0]
    duplicate_hash = cur.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT sha256 FROM attachments
          WHERE sha256 IS NOT NULL
          GROUP BY sha256
          HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]

    zero_by_content_type = [
        (str(row[0]), int(row[1]))
        for row in cur.execute(
            """
        SELECT COALESCE(content_type, '(none)') AS ct, COUNT(*) AS c
        FROM attachments WHERE size_bytes = 0
        GROUP BY content_type ORDER BY c DESC LIMIT 15
        """
        )
    ]

    ext_expr = """
        LOWER(CASE
            WHEN instr(substr(filename, instr(filename, '.') + 1), '.') = 0
            THEN substr(filename, instr(filename, '.') + 1)
            WHEN instr(substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1), '.') = 0
            THEN substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1)
            ELSE substr(substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1), instr(substr(substr(filename, instr(filename, '.') + 1), instr(substr(filename, instr(filename, '.') + 1), '.') + 1), '.') + 1)
        END)
    """
    zero_by_extension = [
        (str(row[0]), int(row[1]))
        for row in cur.execute(
            f"""
        SELECT {ext_expr} AS ext, COUNT(*) AS c
        FROM attachments
        WHERE size_bytes = 0 AND filename IS NOT NULL AND instr(filename, '.') > 0
        GROUP BY ext ORDER BY c DESC LIMIT 15
        """
        )
    ]

    zero_by_inline: list[tuple[str, int]] = []
    for row in cur.execute(
        """
        SELECT COALESCE(is_inline, 0) AS inv, COUNT(*) AS c
        FROM attachments WHERE size_bytes = 0
        GROUP BY is_inline
        """
    ):
        label = "inline" if row[0] == 1 else "attachment"
        zero_by_inline.append((label, int(row[1])))

    zero_business = cur.execute(
        """
        SELECT COUNT(*) FROM attachments
        WHERE size_bytes = 0
        AND (LOWER(content_type) LIKE 'application/pdf%'
             OR LOWER(filename) LIKE '%.pdf'
             OR LOWER(filename) GLOB '*.doc' OR LOWER(filename) GLOB '*.docx'
             OR LOWER(filename) GLOB '*.xls' OR LOWER(filename) GLOB '*.xlsx')
        """
    ).fetchone()[0]

    zero_samples = [
        (row[0], row[1], row[2])
        for row in cur.execute(
            """
        SELECT filename, content_type, is_inline
        FROM attachments WHERE size_bytes = 0
        LIMIT 10
        """
        )
    ]

    sample_attachments: dict[str, list[tuple[int, str | None, str | None, int | None, str | None]]] = {}
    for label, cond in [
        ("PDF", "LOWER(content_type) LIKE 'application/pdf%' OR LOWER(filename) LIKE '%.pdf'"),
        ("Excel", "LOWER(filename) GLOB '*.xls' OR LOWER(filename) GLOB '*.xlsx' OR LOWER(filename) GLOB '*.csv'"),
        ("Word", "LOWER(filename) GLOB '*.doc' OR LOWER(filename) GLOB '*.docx'"),
        ("Image", "LOWER(content_type) LIKE 'image/%'"),
    ]:
        q = f"""
        SELECT a.email_id, a.filename, a.content_type, a.size_bytes, e.subject
        FROM attachments a
        JOIN emails e ON e.id = a.email_id
        WHERE {cond}
        LIMIT 5
        """
        sample_attachments[label] = [
            (int(row["email_id"]), row["filename"], row["content_type"], row["size_bytes"], row["subject"])
            for row in cur.execute(q)
        ]

    return AttachmentValidationResult(
        total_emails=int(total_emails),
        total_attachments=int(total_attachments),
        emails_with_attachments=int(emails_with_attachments),
        distinct_email_id_attachments=int(distinct_email_id_att),
        sum_attachment_count=int(sum_attachment_count or 0),
        attachment_rows_with_email=int(att_with_email),
        orphan_attachment_rows=int(orphan_att),
        emails_non_inline=int(emails_non_inline),
        emails_business_doc=int(emails_business_doc),
        class_images=int(class_images),
        class_pdf=int(class_pdf),
        class_excel=int(class_excel),
        class_word=int(class_word),
        class_archives=int(class_archives),
        class_noise=int(class_noise),
        class_other=int(class_other),
        top_business_doc_extensions=top_business_doc_extensions,
        cotiz_business=int(cotiz_business),
        by_content_type=by_content_type,
        by_extension=by_extension,
        inline_vs_non_inline=inline_vs_non_inline,
        pdf_emails=int(pdf_emails),
        excel_emails=int(excel_emails),
        word_emails=int(word_emails),
        image_emails=int(image_emails),
        drift_has_attachments_no_rows=int(drift_has),
        drift_attachment_count_mismatch=int(drift_count),
        missing_filename_with_size=int(missing_name),
        zero_size_attachments=int(zero_size),
        duplicate_sha256_groups=int(duplicate_hash),
        zero_by_content_type=zero_by_content_type,
        zero_by_extension=zero_by_extension,
        zero_by_inline=zero_by_inline,
        zero_business_docs=int(zero_business),
        zero_byte_samples=zero_samples,
        sample_attachments=sample_attachments,
    )


def format_attachment_validation_report(result: AttachmentValidationResult, db_path: Any) -> str:
    """Render human-readable report matching legacy script stdout."""
    lines: list[str] = []
    append = lines.append

    append("=== Attachments validation (Phase 2.3) ===")
    append(f"DB: {db_path} \n")

    append("--- Metric audit ---")
    append("  total_emails:            COUNT(*) FROM emails")
    append("  total_attachments:        COUNT(*) FROM attachments")
    append("  emails_with_attachments:  COUNT(*) FROM emails WHERE has_attachments = 1")
    append("")
    append(f"Total emails: {result.total_emails:,}")
    append(f"Total attachments: {result.total_attachments:,}")
    append(f"Emails with attachments: {result.emails_with_attachments:,}")

    append("\n--- Side-by-side counts ---")
    append(f"  COUNT(*) FROM emails:                      {result.total_emails:,}")
    append(f"  COUNT(*) FROM attachments:                {result.total_attachments:,}")
    append(f"  COUNT(DISTINCT email_id) FROM attachments: {result.distinct_email_id_attachments:,}")
    append(f"  COUNT(*) FROM emails WHERE has_attachments=1: {result.emails_with_attachments:,}")
    append(f"  SUM(attachment_count) FROM emails:         {result.sum_attachment_count:,}")
    append(f"  Attachment rows with existing email_id:   {result.attachment_rows_with_email:,}")
    append(f"  Orphan attachment rows (email_id not in emails): {result.orphan_attachment_rows:,}")

    append("\n--- Attachment reporting refinement ---")
    append(f"  Total emails with attachments:        {result.emails_with_attachments:,}")
    append(f"  Emails with non-inline attachments:  {result.emails_non_inline:,}")
    append(f"  Emails with business-doc attachments: {result.emails_business_doc:,}")

    append("  Attachment counts by broad class (existing emails only):")
    append(f"    images:              {result.class_images:,}")
    append(f"    pdf:                 {result.class_pdf:,}")
    append(f"    excel/csv:           {result.class_excel:,}")
    append(f"    word:                {result.class_word:,}")
    append(f"    archives:            {result.class_archives:,}")
    append(f"    delivery/report:     {result.class_noise:,}")
    append(f"    other docs:          {result.class_other:,}")

    append("  Top business-doc extensions (top 10):")
    for ext, count in result.top_business_doc_extensions:
        append(f"    .{ext}: {count:,}")

    append(f"  Cotización emails with business-doc attachments: {result.cotiz_business:,}")

    append("\n--- By content_type (top 15) ---")
    for ct, count in result.by_content_type:
        append(f"  {ct!r}: {count:,}")

    append("\n--- By extension (top 15) ---")
    for ext, count in result.by_extension:
        append(f"  .{ext}: {count:,}")

    append("\n--- Inline vs non-inline ---")
    for label, count in result.inline_vs_non_inline:
        append(f"  {label}: {count:,}")

    append("\n--- Type-specific email counts ---")
    append(f"  Emails with PDF: {result.pdf_emails:,}")
    append(f"  Emails with Excel (xls/xlsx/csv): {result.excel_emails:,}")
    append(f"  Emails with Word (doc/docx): {result.word_emails:,}")
    append(f"  Emails with images: {result.image_emails:,}")

    append("\n--- Consistency checks (emails vs attachments) ---")
    append(f"  has_attachments=1 but no attachment rows: {result.drift_has_attachments_no_rows:,}")
    append(f"  attachment_count != actual row count: {result.drift_attachment_count_mismatch:,}")

    append("\n--- Diagnostics ---")
    append(f"  Attachments with no filename but size > 0: {result.missing_filename_with_size:,}")
    append(f"  Attachments with size_bytes = 0: {result.zero_size_attachments:,}")
    append(f"  Distinct sha256 values with duplicates: {result.duplicate_sha256_groups:,}")

    append("\n--- Zero-byte attachments (size_bytes = 0) ---")
    append("  By content_type (top 15):")
    for ct, count in result.zero_by_content_type:
        append(f"    {ct!r}: {count:,}")
    append("  By extension (top 15):")
    for ext, count in result.zero_by_extension:
        append(f"    .{ext}: {count:,}")
    append("  By is_inline:")
    for label, count in result.zero_by_inline:
        append(f"    {label}: {count:,}")
    append(f"  Zero-byte among likely business docs (pdf/doc/xls/xlsx): {result.zero_business_docs:,}")
    append("  Sample filenames (up to 10):")
    for filename, content_type, is_inline in result.zero_byte_samples:
        append(f"    {filename!r} | {content_type!r} | is_inline={is_inline}")

    append("\n--- Sample attachments (PDF / Excel / Word / image) ---")
    for label in ("PDF", "Excel", "Word", "Image"):
        append(f"\n[{label} attachments — sample up to 5]")
        for email_id, filename, content_type, size_bytes, subject in result.sample_attachments.get(label, []):
            append(
                f"  email_id={email_id} size={size_bytes} "
                f"type={content_type!r} filename={filename!r} subject={subject!r}"
            )

    append("\nDone.")
    return "\n".join(lines)
