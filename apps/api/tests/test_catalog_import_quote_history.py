"""The quote-history importer: plan (pure) and apply / verify / rollback (disposable PostgreSQL).

Every quote, client, brand, model and amount here is invented, and the texts are generated here in
the two OrigenLab layouts; nothing is copied from a real extraction or PDF.
"""
from __future__ import annotations

import csv
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.commands import V2CatalogRepository
from origenlab_api.v2.catalog.reads import V2CatalogReads
from origenlab_api.v2.catalog.routes import ReviewDocumentLineBody
from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "catalog"
sys.path.insert(0, str(_SCRIPTS))
import _common  # noqa: E402
import import_quote_history as iqh  # noqa: E402

CLIENT_NAME = "Instituto Ficticio de Pruebas"
CLIENT_CONTACT = "Persona Inventada Ejemplo"
CLIENT_UNIT = "Unidad Imaginaria"
CLIENT_EMAIL = "persona.inventada@example.test"
CLIENT_PHONE = "+56 9 8000 1111"


def _sha() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _docx_text(rows: list[tuple[str, str, str]], *, letterhead: str = "OrigenLab") -> str:
    """The docx/web layout: header over three lines, one row per block, `$ 1.236.000` amounts."""
    out = [f"{letterhead}                                   ventas@example.test",
           "                                       Valdivia, 2 de enero de 2026", "",
           f"Sra. {CLIENT_CONTACT}", CLIENT_NAME, "", "",
           "                                                 DETALLE",
           "           REF                                                                                TOTAL",
           "  ITEM", "", ""]
    for item, ref, amount in rows:
        out += [f"   {item:<6}{ref:<11}Equipo sintético de prueba                        $ {amount}", "", ""]
    out += ["                                                              NETO              $ 9.999.000"]
    return "\n".join(out) + "\n"


def _xlsx_text(rows: list[tuple[str, str, str]]) -> str:
    out = ["OrigenLab                                                     ventas@example.test", "",
           " ITEM              MARCA / REF.                    DETALLE / ESPECIFICACIONES            CANT.      "
           "PRECIO UNIT. TOTAL"]
    for item, ref, amount in rows:
        out.append(f"        {item} ACME · {ref:<20}Equipo sintético                              1              "
                   f"${amount.replace('.', ',')}")
    out.append("                                                                   SUBTOTAL NETO        $9,999,000")
    return "\n".join(out) + "\n"


def _line(item: str, model: str | None, total: int | None, **extra) -> dict:
    return {"item": item, "parent_item": None, "kind": "equipment", "brand": "ACME", "model": model,
            "description": f"Equipo sintético {model}", "qty": 1, "unit_price": total, "line_total": total,
            "optional": False, **extra}


def _extraction(sha: str, lines: list[dict], **overrides) -> dict:
    base = {"sha256": sha, "is_origenlab_issued_quote": True, "quote_number": "00001-26", "date": "2026-01-02",
            "city": "Ciudad Sintética", "client_contact": CLIENT_CONTACT, "client_institution": CLIENT_NAME,
            "client_unit": CLIENT_UNIT, "client_type": "university", "currency": "CLP", "prices_include_iva": False,
            "lines": lines,
            "subtotal_net": sum(ln["line_total"] for ln in lines if isinstance(ln["line_total"], int)),
            "iva_amount": 0,
            "total": 0, "conditions": {"delivery": "pronto"}, "template": "docx_web2026",
            "sum_check": {"lines_sum": 0, "stated_net": 0, "ok": True},
            "extraction_issues": [f"el contacto es {CLIENT_CONTACT}"]}
    return {**base, **overrides}


def _write_doc(root: Path, extraction: dict, text: str) -> None:
    (root / "extract").mkdir(parents=True, exist_ok=True)
    (root / "txt").mkdir(parents=True, exist_ok=True)
    (root / "extract" / f"{extraction['sha256']}.json").write_text(json.dumps(extraction, ensure_ascii=False),
                                                                 encoding="utf-8")
    (root / "txt" / f"{extraction['sha256']}.txt").write_text(text, encoding="utf-8")


def _plan(root: Path, out: Path) -> int:
    return iqh.main(["plan", "--extractions", str(root / "extract"), "--texts", str(root / "txt"), "--out", str(out)])


def _standard_docs(root: Path, tag: str) -> tuple[str, str]:
    """Two documents: one docx (verified, disputed, single-source lines), one xlsx (verified)."""
    a, b = _sha(), _sha()
    m1, m2, m3 = f"{tag}-1", f"{tag}-2", f"{tag}-3"
    _write_doc(root, _extraction(a, [_line("1", m1, 1236000), _line("2", m2, 500000), _line("3", m3, 70000)],
                                 quote_number=f"{tag}-A", date="02-01-2026"),
               _docx_text([("1", m1, "1.236.000"), ("2", m2, "550.000")]))
    _write_doc(root, _extraction(b, [_line("1", m1, 1200000)], quote_number=f"{tag}-B", date="2026-03-04",
                                 template="xlsx_master", client_type="private_company"),
               _xlsx_text([("1", m1, "1.200.000")]))
    return a, b


def _load(out: Path) -> tuple[dict, str]:
    data = (out / "plan.json").read_bytes()
    return json.loads(data), importing.sha256_bytes(data)


# ------------------------------------------------------------------ plan (no database)

def test_plan_cross_checks_lines_into_one_document_item_each(tmp_path, capsys) -> None:
    a, b = _standard_docs(tmp_path / "in", "ACME")
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    plan, _ = _load(tmp_path / "out")
    docs = {i["key"]: i for i in plan["items"]}
    assert set(docs) == {f"quote_document:{a}", f"quote_document:{b}"}
    assert {i["action"] for i in plan["items"]} == {"add_quote_document"}
    first = docs[f"quote_document:{a}"]["fields"]
    assert first["payload"] == {"printed_quote_number": "ACME-A", "quote_date": "2026-01-02",
                                "client_type": "university", "currency": "CLP", "net_total": "1806000",
                                "template": "docx_web2026", "file_sha256": a}
    assert [(ln["line_no"], ln["model_key"], ln["line_total"], ln["check_status"]) for ln in first["lines"]] == [
        (1, "ACME1", "1236000", "verified"), (2, "ACME2", "500000", "disputed"), (3, "ACME3", "70000", "single_source")]
    assert {ln["extractor"] for ln in first["lines"]} == {"ai_v1+template_v1"}
    assert [ln["check_status"] for ln in docs[f"quote_document:{b}"]["fields"]["lines"]] == ["verified"]
    assert plan["counts"]["add_quote_document"] == 2
    assert plan["counts"]["lines_verified"] == 2 and plan["counts"]["lines_disputed"] == 1
    assert plan["counts"]["lines_single_source"] == 1


def test_disputed_csv_goes_to_out_and_never_to_stdout(tmp_path, capsys) -> None:
    _standard_docs(tmp_path / "in", "DISP")
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    printed = capsys.readouterr()
    with (tmp_path / "out" / "disputed.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [{"quote_number": "DISP-A", "line": "2", "ai_total": "500000", "template_total": "550000"}]
    for value in ("500000", "550000", "DISP-A"):
        assert value not in printed.out and value not in printed.err


def test_no_client_identity_reaches_the_plan(tmp_path, capsys) -> None:
    sha = _sha()
    lines = [_line("1", "ACME-77", 100000, description=f"Equipo para {CLIENT_NAME}"),
             _line("2", "ACME-78", 200000, description=f"Contacto {CLIENT_EMAIL} o {CLIENT_PHONE}"),
             _line("3", "ACME-79", 300000, brand=CLIENT_UNIT)]
    _write_doc(tmp_path / "in", _extraction(sha, lines), _docx_text([("1", "ACME-77", "100.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    raw = (tmp_path / "out" / "plan.json").read_text(encoding="utf-8")
    printed = capsys.readouterr()
    for secret in (CLIENT_NAME, CLIENT_CONTACT, CLIENT_UNIT, CLIENT_EMAIL, CLIENT_PHONE, "Ciudad Sintética", "pronto",
                   "client_institution", "client_contact", "client_unit"):
        assert secret not in raw and secret not in printed.out and secret not in printed.err
    plan, _ = _load(tmp_path / "out")
    [doc] = plan["items"]
    assert [ln["description"] for ln in doc["fields"]["lines"]] == [None, None, "Equipo sintético ACME-79"]
    assert doc["fields"]["lines"][2]["brand"] is None
    assert plan["counts"]["strings_withheld"] == 3


def test_iso_and_dd_mm_yyyy_dates_are_written_as_iso(tmp_path) -> None:
    for n, raw in enumerate(("2026-05-06", "06-05-2026", "06/05/2026")):
        sha = _sha()
        _write_doc(tmp_path / "in", _extraction(sha, [_line("1", f"ACME-D{n}", 1000)], date=raw),
                   _docx_text([("1", f"ACME-D{n}", "1.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    plan, _ = _load(tmp_path / "out")
    assert {i["fields"]["payload"]["quote_date"] for i in plan["items"]} == {"2026-05-06"}


def test_a_date_in_another_format_is_refused_naming_the_file_and_field(tmp_path, capsys) -> None:
    sha = _sha()
    _write_doc(tmp_path / "in", _extraction(sha, [_line("1", "ACME-1", 1000)], date="6 de mayo de 2026"),
               _docx_text([("1", "ACME-1", "1.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == _common.EXIT_REFUSED
    err = capsys.readouterr().err
    assert f"{sha}.json" in err and "date" in err
    assert not (tmp_path / "out" / "plan.json").exists()


def test_excluded_reseller_letterhead_is_skipped(tmp_path) -> None:
    sha = _sha()
    _write_doc(tmp_path / "in", _extraction(sha, [_line("1", "ACME-1", 1000)]),
               _docx_text([("1", "ACME-1", "1.000")], letterhead="Juan Andrés Tejeda — equipos"))
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    plan, _ = _load(tmp_path / "out")
    assert plan["items"] == [] and plan["counts"]["skipped_labdelivery_letterhead"] == 1


def test_a_reseller_text_is_skipped_and_the_clean_one_planned(tmp_path, capsys) -> None:
    dirty, clean = _sha(), _sha()
    _write_doc(tmp_path / "in", _extraction(dirty, [_line("1", "ACME-1", 1000)]),
               _docx_text([("1", "ACME-1", "1.000")]) + "Fono: (00) 00000000 www.labdelivery.example\n")
    _write_doc(tmp_path / "in", _extraction(clean, [_line("1", "ACME-2", 2000)]), _docx_text([("1", "ACME-2", "2.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    plan, _ = _load(tmp_path / "out")
    assert [i["key"] for i in plan["items"]] == [f"quote_document:{clean}"]
    assert plan["counts"]["skipped_labdelivery_letterhead"] == 1
    assert [s["reason"] for s in plan["skipped"]] == ["labdelivery_letterhead"]
    assert dirty not in (tmp_path / "out" / "plan.json").read_text(encoding="utf-8").replace(
        plan["skipped"][0]["path_sha256"], "")


def test_a_reseller_text_whose_extraction_names_the_reseller_is_skipped(tmp_path) -> None:
    sha = _sha()
    _write_doc(tmp_path / "in", _extraction(sha, [_line("1", "ACME-1", 1000)],
                                            extraction_issues=["membrete de Lab Delivery"]),
               _docx_text([("1", "ACME-1", "1.000")], letterhead="Juan Andrés Tejeda — equipos"))
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    plan, _ = _load(tmp_path / "out")
    assert plan["items"] == [] and plan["counts"]["skipped_labdelivery_letterhead"] == 1
    assert [s["reason"] for s in plan["skipped"]] == ["labdelivery_letterhead"]


def test_item_and_total_matches_are_counted_by_reason(tmp_path) -> None:
    sha = _sha()
    _write_doc(tmp_path / "in", _extraction(sha, [_line("1", "ACME-1", 1000), _line("2", "ACME-2", 2000)]),
               _docx_text([("1", "ACME-1", "1.000"), ("2", "9999", "2.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    plan, _ = _load(tmp_path / "out")
    assert [ln["check_status"] for ln in plan["items"][0]["fields"]["lines"]] == ["verified", "verified"]
    assert plan["counts"]["verified_model_total_match"] == 1
    assert plan["counts"]["verified_item_total_match"] == 1


def test_excluded_reseller_in_a_written_string_is_refused(tmp_path) -> None:
    sha = _sha()
    _write_doc(tmp_path / "in", _extraction(sha, [_line("1", "ACME-1", 1000, brand="Lab-Delivery")]),
               _docx_text([("1", "ACME-1", "1.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == _common.EXIT_REFUSED


def test_a_document_not_issued_by_origenlab_is_skipped(tmp_path) -> None:
    sha, other = _sha(), _sha()
    _write_doc(tmp_path / "in", {"sha256": sha, "is_origenlab_issued_quote": False, "what_it_is": "folleto"}, "folleto\n")
    _write_doc(tmp_path / "in", _extraction(other, [_line("1", "ACME-1", 1000)]), _docx_text([("1", "ACME-1", "1.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == 0
    plan, _ = _load(tmp_path / "out")
    assert [i["key"] for i in plan["items"]] == [f"quote_document:{other}"]
    assert plan["counts"]["skipped_not_origenlab_quote"] == 1
    assert [s["reason"] for s in plan["skipped"]] == ["not_origenlab_quote"]


def test_a_missing_text_file_or_a_mismatched_sha_is_refused(tmp_path, capsys) -> None:
    sha = _sha()
    _write_doc(tmp_path / "in", _extraction(sha, [_line("1", "ACME-1", 1000)]), "x\n")
    (tmp_path / "in" / "txt" / f"{sha}.txt").unlink()
    assert _plan(tmp_path / "in", tmp_path / "out") == _common.EXIT_REFUSED
    assert f"{sha}.json" in capsys.readouterr().err

    other = tmp_path / "in2"
    _write_doc(other, _extraction(sha, [_line("1", "ACME-1", 1000)]), "x\n")
    (other / "extract" / f"{sha}.json").rename(other / "extract" / f"{'c' * 64}.json")
    assert _plan(other, tmp_path / "out2") == _common.EXIT_REFUSED


def test_a_malformed_line_is_refused_naming_the_field(tmp_path, capsys) -> None:
    sha = _sha()
    _write_doc(tmp_path / "in", _extraction(sha, [_line("1", "ACME-1", 1000, line_total="mil")]),
               _docx_text([("1", "ACME-1", "1.000")]))
    assert _plan(tmp_path / "in", tmp_path / "out") == _common.EXIT_REFUSED
    assert "lines[0].line_total" in capsys.readouterr().err


def test_plan_is_deterministic_and_carries_no_local_path(tmp_path) -> None:
    _standard_docs(tmp_path / "in", "DET")
    assert _plan(tmp_path / "in", tmp_path / "o1") == 0
    assert _plan(tmp_path / "in", tmp_path / "o2") == 0
    assert (tmp_path / "o1" / "plan.json").read_bytes() == (tmp_path / "o2" / "plan.json").read_bytes()
    assert str(tmp_path) not in (tmp_path / "o1" / "plan.json").read_text(encoding="utf-8")


# ------------------------------------------------------------------ apply / verify / rollback

@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def operator(disposable_database) -> OperatorIdentity:
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    op_id = _owner(disposable_database,
                   "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                   "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active') returning id::text",
                   (email,))[0][0]
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Vendedora Prueba", role="sales",
                            status="active")


def _argv(cmd: str, dsn: str, plan: Path, sha: str, out: Path, operator: OperatorIdentity | None = None,
          *extra: str) -> list[str]:
    argv = [cmd, "--target-dsn", runtime_dsn(dsn), "--plan", str(plan), "--plan-sha256", sha, "--out", str(out)]
    if cmd in ("apply", "rollback"):
        argv += ["--admin-dsn", dsn]
    if operator is not None:
        argv += ["--operator-email", operator.email_norm]
    return argv + list(extra)


def _report(out: Path, name: str) -> dict:
    return json.loads((out / name).read_text(encoding="utf-8"))


def _planned(tmp_path: Path, tag: str) -> tuple[Path, str, str, str]:
    a, b = _standard_docs(tmp_path / "in", tag)
    assert _plan(tmp_path / "in", tmp_path / "plan") == 0
    _, sha = _load(tmp_path / "plan")
    return tmp_path / "plan" / "plan.json", sha, a, b


def _doc_rows(dsn: str, file_sha: str):
    return _owner(dsn, "select id::text, kind, review_status, payload, payload_sha256 from evidence.source_record "
                       "where dedupe_key = %s", (f"quote_document:{file_sha}",))


@needs_db
def test_history_apply_twice_inserts_once(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    tag = f"T{uuid.uuid4().hex[:6].upper()}"
    plan, sha, a, b = _planned(tmp_path, tag)
    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    first = _report(tmp_path / "a1", "apply-report.json")
    assert first["inserted"] == 2 and first["already_present"] == 0
    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    second = _report(tmp_path / "a2", "apply-report.json")
    assert second["inserted"] == 0 and second["already_present"] == 2
    assert iqh.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == 0
    assert _report(tmp_path / "v", "verify-report.json")["counts"] == {"present": 2}

    manifest = _owner(dsn, "select id::text from evidence.source_record where dedupe_key = %s",
                      (f"catalog-import:{sha}",))[0][0]
    [(doc_id, kind, status, payload, payload_sha)] = _doc_rows(dsn, a)
    assert (kind, status, payload_sha) == ("quote_document", "pending", a)
    assert payload == {"printed_quote_number": f"{tag}-A", "quote_date": "2026-01-02", "client_type": "university",
                       "currency": "CLP", "net_total": "1806000", "template": "docx_web2026", "file_sha256": a,
                       "origin_source_record_id": manifest}
    lines = _owner(dsn, "select line_no, model_key, line_total, qty, unit_price, currency, check_status, extractor "
                        "from evidence.document_line where source_record_id = %s order by line_no", (doc_id,))
    assert lines == [
        (1, f"{tag}1", Decimal("1236000.0000"), Decimal("1.000000"), Decimal("1236000.0000"), "CLP", "verified",
         "ai_v1+template_v1"),
        (2, f"{tag}2", Decimal("500000.0000"), Decimal("1.000000"), Decimal("500000.0000"), "CLP", "disputed",
         "ai_v1+template_v1"),
        (3, f"{tag}3", Decimal("70000.0000"), Decimal("1.000000"), Decimal("70000.0000"), "CLP", "single_source",
         "ai_v1+template_v1")]
    assert _owner(dsn, "select count(*) from evidence.document_line where source_record_id = %s",
                  (_doc_rows(dsn, b)[0][0],)) == [(1,)]

    # What Task 9's history reads: ISO dates, newest first; the disputed line listed, never in the median.
    history = V2CatalogReads(psycopg.connect, runtime_dsn(dsn)).price_history(f"{tag}-1")
    assert [(i["date"], i["document_number"], i["client_type"], i["check_status"]) for i in history["items"]] == [
        ("2026-03-04", f"{tag}-B", "private_company", "verified"), ("2026-01-02", f"{tag}-A", "university", "verified")]


@needs_db
def test_no_client_identity_reaches_the_database(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    sha_doc = _sha()
    lines = [_line("1", "ACME-C1", 100000, description=f"Equipo para {CLIENT_NAME}")]
    _write_doc(tmp_path / "in", _extraction(sha_doc, lines), _docx_text([("1", "ACME-C1", "100.000")]))
    assert _plan(tmp_path / "in", tmp_path / "plan") == 0
    plan, sha = tmp_path / "plan" / "plan.json", _load(tmp_path / "plan")[1]
    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    [(doc_id, *_)] = _doc_rows(dsn, sha_doc)
    [(text,)] = _owner(dsn, "select s.payload::text || coalesce((select string_agg(to_jsonb(l)::text, ' ') "
                            "from evidence.document_line l where l.source_record_id = s.id), '') "
                            "from evidence.source_record s where s.id = %s", (doc_id,))
    for secret in (CLIENT_NAME, CLIENT_CONTACT, CLIENT_UNIT, "Ciudad Sintética", "client_institution"):
        assert secret not in text


@needs_db
def test_a_document_already_present_is_skipped_and_kept_by_rollback(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    tag = f"P{uuid.uuid4().hex[:6].upper()}"
    a, b = _standard_docs(tmp_path / "in", tag)
    existing = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, payload_sha256) "
                           "values ('quote_document', %s, '{\"printed_quote_number\": \"earlier\"}'::jsonb, %s) "
                           "returning id::text", (f"quote_document:{a}", a))[0][0]
    assert _plan(tmp_path / "in", tmp_path / "plan") == 0
    plan, sha = tmp_path / "plan" / "plan.json", _load(tmp_path / "plan")[1]
    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    report = _report(tmp_path / "a", "apply-report.json")
    assert report["inserted"] == 1 and report["present_different"] == 1
    assert _owner(dsn, "select count(*) from evidence.document_line where source_record_id = %s", (existing,)) == [(0,)]
    assert iqh.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == 0
    assert _report(tmp_path / "v", "verify-report.json")["counts"] == {"present": 1, "present_different": 1}

    assert iqh.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None, "--confirm-delete-loaded-rows")) == 0
    assert _doc_rows(dsn, a)[0][0] == existing
    assert _doc_rows(dsn, b) == []


@needs_db
def test_rollback_removes_documents_lines_and_manifest_then_reapplies(disposable_database, operator,
                                                                     tmp_path) -> None:
    dsn = disposable_database
    plan, sha, a, b = _planned(tmp_path, f"R{uuid.uuid4().hex[:6].upper()}")
    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    ids = [_doc_rows(dsn, a)[0][0], _doc_rows(dsn, b)[0][0]]
    assert iqh.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None, "--confirm-delete-loaded-rows")) == 0
    deleted = _report(tmp_path / "r", "rollback-report.json")["deleted"]
    assert deleted["evidence.document_line"] == 4 and deleted["evidence.source_record"] == 3
    assert _owner(dsn, "select count(*) from evidence.document_line where source_record_id::text = any(%s)",
                  (ids,)) == [(0,)]
    assert _doc_rows(dsn, a) == [] and _doc_rows(dsn, b) == []
    assert iqh.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == _common.EXIT_MISMATCH
    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    assert _report(tmp_path / "a2", "apply-report.json")["inserted"] == 2


@needs_db
def test_a_reviewed_line_is_kept_by_reruns_and_blocks_rollback(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    plan, sha, a, _ = _planned(tmp_path, f"V{uuid.uuid4().hex[:6].upper()}")
    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    [(line_id,)] = _owner(dsn, "select l.id::text from evidence.document_line l join evidence.source_record s "
                               "on s.id = l.source_record_id where s.dedupe_key = %s and l.check_status = 'disputed'",
                          (f"quote_document:{a}",))
    body = ReviewDocumentLineBody(document_line_id=line_id, line_total=Decimal("550000"), review_note="plantilla manda")
    V2CatalogRepository(psycopg.connect, runtime_dsn(dsn)).execute(
        command_name="review-document-line", operator=operator, fields=body.model_dump(mode="json"),
        idempotency_key=uuid.uuid4().hex, digest=request_digest("review-document-line", body))

    assert iqh.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    assert _report(tmp_path / "a2", "apply-report.json")["kept_later_value"] == 1
    assert _owner(dsn, "select check_status, line_total from evidence.document_line where id = %s", (line_id,)) == [
        ("reviewed", Decimal("550000.0000"))]
    assert iqh.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == 0
    assert _report(tmp_path / "v", "verify-report.json")["counts"] == {"present": 1, "reviewed_later": 1}
    assert iqh.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None,
                          "--confirm-delete-loaded-rows")) == _common.EXIT_REFUSED
    assert _doc_rows(dsn, a) != []


@needs_db
def test_apply_refuses_the_admin_login_as_target(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    plan, sha, a, _ = _planned(tmp_path, f"X{uuid.uuid4().hex[:6].upper()}")
    argv = ["apply", "--target-dsn", dsn, "--admin-dsn", dsn, "--plan", str(plan), "--plan-sha256", sha,
            "--out", str(tmp_path / "a"), "--operator-email", operator.email_norm]
    assert iqh.main(argv) == _common.EXIT_REFUSED
    assert _doc_rows(dsn, a) == []
