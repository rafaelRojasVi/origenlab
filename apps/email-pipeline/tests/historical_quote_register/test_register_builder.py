# tests/historical_quote_register/test_register_builder.py
from __future__ import annotations

import csv

from origenlab_email_pipeline.historical_quote_register.register_builder import (
    RegisterRow,
    write_register_csvs,
)


def _row(**overrides) -> RegisterRow:
    base = dict(
        historical_quote_key="hqk-1", quote_number="COT-2026-014", quote_number_confidence="high",
        client_name=None, client_email="compras@cliente.cl", client_domain="cliente.cl",
        sent_email_id=1, sent_message_id="<m1>", sent_at="2026-05-10T00:00:00", sent_subject="Cotización COT-2026-014",
        attachment_id=1, attachment_filename="cot.pdf", attachment_sha256="abc", attachment_saved_path=None,
        response_state="no_reply", first_client_reply_at=None, latest_client_reply_at=None,
        latest_reply_email_id=None, latest_reply_subject=None,
        outcome_state="unknown", supersedes_historical_quote_key=None, superseded_by_historical_quote_key=None,
        days_without_reply=18,
        source_quote_signal="customer_quote_candidate", matching_method="subject_and_identity",
        confidence="high", review_required=False, review_reason=None,
    )
    base.update(overrides)
    return RegisterRow(**base)


def test_write_register_csvs_splits_review_rows(tmp_path):
    rows = [
        _row(),
        _row(historical_quote_key="hqk-2", review_required=True, review_reason="multiple_conflicting_candidates"),
    ]
    main_path, review_path = write_register_csvs(rows, tmp_path)

    with main_path.open(newline="", encoding="utf-8") as f:
        main_rows = list(csv.DictReader(f))
    with review_path.open(newline="", encoding="utf-8") as f:
        review_rows = list(csv.DictReader(f))

    assert len(main_rows) == 2
    assert len(review_rows) == 1
    assert review_rows[0]["historical_quote_key"] == "hqk-2"
    assert main_rows[0]["quote_number"] == "COT-2026-014"


def test_write_register_csvs_is_atomic_no_partial_file_on_crash(tmp_path, monkeypatch):
    import origenlab_email_pipeline.historical_quote_register.register_builder as rb

    calls = {"n": 0}
    real_replace = rb.os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated crash before first rename")
        return real_replace(src, dst)

    monkeypatch.setattr(rb.os, "replace", flaky_replace)
    try:
        write_register_csvs([_row()], tmp_path)
    except OSError:
        pass
    assert not (tmp_path / "historical_customer_quotes.csv").exists()
