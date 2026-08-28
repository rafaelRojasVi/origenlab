from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

_TDIR = Path(__file__).resolve().parent.parent
if str(_TDIR) not in sys.path:
    sys.path.insert(0, str(_TDIR))

from origenlab_email_pipeline.db import insert_attachment, insert_email
from historical_quote_register.sqlite_fixture import build_fixture_db, open_writable_fixture


def _seed_one_quote_and_reply(db_path: Path) -> None:
    conn = open_writable_fixture(db_path)
    email_id = insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        folder="[Gmail]/Enviados", message_id="<sent-1@origenlab.cl>",
        subject="Cotización COT-2026-014 equipo homogeneizador", sender="contacto@origenlab.cl",
        recipients="compras@universidadaustral.cl", date_raw="2026-05-10", date_iso="2026-05-10T10:00:00",
        body="", has_attachments=True, attachment_count=1,
    )
    insert_attachment(
        conn, email_id=email_id, part_index=0, filename="COT-2026-014.pdf",
        content_type="application/pdf", content_disposition="attachment",
        size_bytes=1234, content_id=None, is_inline=False, sha256="deadbeef" * 8,
        saved_path=None,
    )
    insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/INBOX", folder="INBOX",
        message_id="<reply-1@universidadaustral.cl>", subject="RE: Cotización COT-2026-014",
        sender="compras@universidadaustral.cl", recipients="contacto@origenlab.cl",
        date_raw="2026-05-12", date_iso="2026-05-12T09:00:00", body="",
        top_reply_clean="Gracias, la estamos revisando.",
    )
    conn.commit()
    conn.close()


def test_cli_end_to_end_deterministic_and_no_mutation(tmp_path):
    db_path = build_fixture_db(tmp_path, name="archive.sqlite")
    _seed_one_quote_and_reply(db_path)
    before_bytes = db_path.read_bytes()

    out_dir_1 = tmp_path / "run1"
    out_dir_2 = tmp_path / "run2"
    script = Path(__file__).resolve().parents[2] / "scripts" / "reports" / "build_historical_customer_quote_register.py"

    for out_dir in (out_dir_1, out_dir_2):
        result = subprocess.run(
            [sys.executable, str(script), "--db", str(db_path), "--out-dir", str(out_dir)],
            capture_output=True, text=True, cwd=str(script.parents[2]),
        )
        assert result.returncode == 0, result.stderr

    assert db_path.read_bytes() == before_bytes  # canonical archive byte-for-byte unchanged

    csv_1 = (out_dir_1 / "historical_customer_quotes.csv").read_text(encoding="utf-8")
    csv_2 = (out_dir_2 / "historical_customer_quotes.csv").read_text(encoding="utf-8")
    assert csv_1 == csv_2  # deterministic re-run

    with (out_dir_1 / "historical_customer_quotes.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["quote_number"] == "COT-2026-014"
    assert rows[0]["response_state"] == "replied"

    manifest = json.loads((out_dir_1 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["no_mutation_declaration"]
    assert manifest["counts"]["candidates"] == 1
