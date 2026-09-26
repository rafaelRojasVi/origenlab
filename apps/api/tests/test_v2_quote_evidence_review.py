"""Operator review of staged Gmail quotation candidates: files in, an append-only ledger out.

Every fixture is invented (`example.cl`); nothing here comes from the real staging.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import http.client
import importlib.util
import json
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

from origenlab_api.v2 import quote_evidence_review as qr

_API_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _API_ROOT / "src/origenlab_api/v2/quote_evidence_review.py"
_SCRIPT = _API_ROOT / "scripts/quote_evidence_review.py"

RFC_ID = "<CAx+Odd=Chars_1.2@mail.example.cl>"
QUEUE_FIELDS = [
    "email_id", "tier", "direction_hint", "sent_at", "gmail_message_id",
    "proposed_quote_numbers", "n_documents", "all_bytes_verified", "filenames",
    "same_number_other_emails", "cross_era_number_collision", "queue",
]


def _doc(att: int, email: int, name: str, sha: str, cn: list[str]) -> dict:
    return {
        "source_email_id": email, "source_attachment_id": att, "filename": name,
        "sha256": sha, "cn_tokens": cn, "bytes_hash_verified": True,
        "stored_path": f"gmail-g1-reconciliation-20260924/documents/{sha}.pdf",
    }


def _gmail(email: int, gid: str, direction: str, cn: list[str], docs: list[dict], rfc: str,
           thread: str | None = None, sent_at: str | None = None) -> dict:
    return {
        "kind": "gmail_message",
        "dedupe_key": f"gmail_message:{gid}",
        "source_uri": f"gmail://msg/{gid}",
        "payload": {
            "source_email_id": email, "tier": "G1_gmail_doc", "direction_hint": direction,
            "sent_at": sent_at or f"2026-05-0{email % 10}T10:00:00-04:00",
            "subject_raw": "=?utf-8?B?Q290aXphY2nDs24gZXF1aXBv?=",
            "sender": "Ventas <ventas@example.cl>",
            "recipients": "Compras <compras@cliente.example.cl>",
            "rfc822_message_id": rfc, "gmail_message_id": gid,
            "gmail_thread_id": thread or f"t{gid}", "gmail_label_ids": ["SENT"],
            "raw_sha256": hashlib.sha256(gid.encode()).hexdigest(),
            "documents": docs, "proposed_quote_numbers": cn,
            "opportunity_id": None, "quote_number": None,
        },
        "review_status": "pending",
    }


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _fixture() -> tuple[list[dict], list[dict]]:
    records = [
        _gmail(1001, "19d0000000000001", "customer_quote_candidate", ["CN01005"],
               [_doc(1, 1001, "CN01005-Cliente Uno.pdf", SHA_A, ["CN01005"])], RFC_ID),
        _gmail(1002, "19d0000000000002", "customer_quote_candidate", ["CN01005"],
               [_doc(2, 1002, "CN01005-Cliente Uno.pdf", SHA_A, ["CN01005"])], "<two@mail.example.cl>"),
        _gmail(1003, "19d0000000000003", "customer_quote_candidate", ["CN01007", "CN01010"],
               [_doc(3, 1003, "CN1010-Cliente Dos.pdf", SHA_B, ["CN01010"]),
                _doc(4, 1003, "CN1007-Cliente Dos.pdf", SHA_C, ["CN01007"])], "<three@mail.example.cl>"),
        _gmail(1004, "19d0000000000004", "ambiguous", [],
               [_doc(5, 1004, "Cotización equipo.pdf", "d" * 64, [])], "<four@mail.example.cl>"),
        {
            "kind": "v1_historical_quote_candidate",
            "dedupe_key": "v1_historical_quote_candidate:emails_sqlite_email:2001",
            "source_uri": "origenlab-emails-sqlite://emails/2001",
            "payload": {"source_email_id": 2001, "documents": [], "proposed_quote_numbers": ["CN05118"]},
            "review_status": "pending",
        },
    ]
    queue = [
        {"email_id": "1001", "gmail_message_id": "19d0000000000001", "proposed_quote_numbers": "CN01005",
         "same_number_other_emails": "1002", "cross_era_number_collision": "False", "queue": "Q1_gmail_single_cn"},
        {"email_id": "1002", "gmail_message_id": "19d0000000000002", "proposed_quote_numbers": "CN01005",
         "same_number_other_emails": "1001", "cross_era_number_collision": "False", "queue": "Q1_gmail_single_cn"},
        {"email_id": "1003", "gmail_message_id": "19d0000000000003", "proposed_quote_numbers": "CN01007 CN01010",
         "same_number_other_emails": "", "cross_era_number_collision": "False", "queue": "Q2_gmail_zero_or_multiple_cn"},
        {"email_id": "1004", "gmail_message_id": "19d0000000000004", "proposed_quote_numbers": "",
         "same_number_other_emails": "", "cross_era_number_collision": "False", "queue": "Q3_gmail_direction_unclear"},
        {"email_id": "2001", "gmail_message_id": "", "proposed_quote_numbers": "CN05118",
         "same_number_other_emails": "", "cross_era_number_collision": "False", "queue": "Q4_legacy_historical"},
    ]
    return records, queue


def _write_staging(root: Path, records: list[dict], queue: list[dict]) -> Path:
    staging = root / "staging"
    staging.mkdir()
    (staging / qr.SOURCE_RECORDS_FILE).write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    with (staging / qr.QUEUE_FILE).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_FIELDS, restval="")
        w.writeheader()
        w.writerows(queue)
    return staging


@pytest.fixture
def env(tmp_path: Path):
    records, queue = _fixture()
    staging_dir = _write_staging(tmp_path, records, queue)
    ledger = qr.DecisionLedger(tmp_path / "review" / "decisions.jsonl")
    return qr.load_staging(staging_dir), ledger, staging_dir, tmp_path


def _digest_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _confirm(staging, ledger, email_id, **kw):
    args = dict(
        decision=qr.DECISION_CONFIRM, operator="op@example.cl",
        opportunity_mode=qr.OPPORTUNITY_EXISTING, opportunity_id=str(uuid.uuid4()),
        quote_number="CN01005", quote_number_basis=qr.BASIS_CONFIRMED_PROPOSAL,
    )
    args.update(kw)
    return qr.record_decision(staging, ledger, email_id, **args)


# --- no automatic case / quote creation ------------------------------------------------


def test_module_imports_no_database_driver_or_repository() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("psycopg", "sqlite3", "sqlalchemy", "origenlab_api.v2.repository",
                 "origenlab_api.v2.command", "origenlab_api.v2.case_command", "googleapiclient", "requests")
    assert not [m for m in imported if m.startswith(forbidden)], imported


def test_loading_and_deciding_writes_only_the_ledger(env) -> None:
    staging, ledger, staging_dir, root = env
    before = _digest_tree(staging_dir)
    _confirm(staging, ledger, 1001)
    qr.record_decision(staging, ledger, 1004, decision=qr.DECISION_REJECT,
                       operator="op@example.cl", reason="supplier RFQ, not a customer quote")
    assert _digest_tree(staging_dir) == before
    files = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    assert files == {"staging/" + qr.QUEUE_FILE, "staging/" + qr.SOURCE_RECORDS_FILE,
                     "review/decisions.jsonl"}
    assert all(entry["applied"] is False for entry in ledger.entries())


def test_items_never_carry_a_suggested_opportunity_or_accepted_number(env) -> None:
    staging, *_ = env
    for item in staging.items:
        d = item.as_dict()
        assert "opportunity_id" not in d and "quote_number" not in d
        assert "opportunity_id" in item.missing


def test_confirm_requires_an_explicit_opportunity(env) -> None:
    staging, ledger, *_ = env
    with pytest.raises(qr.ReviewRefused, match="explicit opportunity"):
        _confirm(staging, ledger, 1001, opportunity_mode=None, opportunity_id=None)
    # An organization name, a domain or a subject is not an opportunity id.
    for guess in ("Cliente Uno", "cliente.example.cl", "Cotización equipo", "CN01005"):
        with pytest.raises(qr.ReviewRefused, match="UUID"):
            _confirm(staging, ledger, 1001, opportunity_id=guess)
    with pytest.raises(qr.ReviewRefused, match="create_new must not"):
        _confirm(staging, ledger, 1001, opportunity_mode=qr.OPPORTUNITY_CREATE_NEW)
    assert ledger.entries() == []


def test_create_new_records_intent_only(env) -> None:
    staging, ledger, *_ = env
    entry = _confirm(staging, ledger, 1001, opportunity_mode=qr.OPPORTUNITY_CREATE_NEW, opportunity_id="")
    assert entry["opportunity"] == {"mode": "create_new", "opportunity_id": None}
    assert entry["applied"] is False


# --- CN is a proposal; ambiguity needs review; duplicates only warn --------------------


def test_ambiguous_cn_requires_review(env) -> None:
    staging, ledger, *_ = env
    item = staging.item(1003)
    assert qr.WARNING_AMBIGUOUS_CN in item.warnings and "quote_number" in item.missing
    with pytest.raises(qr.ReviewRefused, match="no single proposal"):
        _confirm(staging, ledger, 1003, quote_number="CN01010")
    with pytest.raises(qr.ReviewRefused, match="ambiguous CN"):
        _confirm(staging, ledger, 1003, quote_number="CN01010", quote_number_basis=qr.BASIS_OPERATOR_ENTERED)
    entry = _confirm(staging, ledger, 1003, quote_number="cn01010",
                     quote_number_basis=qr.BASIS_OPERATOR_ENTERED, reason="PDF header reads 1010")
    assert entry["quote_number"] == "CN01010"
    assert entry["quote_number_basis"] == "operator_entered"
    assert entry["proposed_quote_numbers"] == ["CN01007", "CN01010"]


def test_zero_cn_also_requires_a_typed_number_and_reason(env) -> None:
    staging, ledger, *_ = env
    item = staging.item(1004)
    assert {qr.WARNING_NO_CN, qr.WARNING_DIRECTION_UNCLEAR, qr.WARNING_QUOTE_DOC_WITHOUT_CN} <= set(item.warnings)
    with pytest.raises(qr.ReviewRefused, match="no single proposal"):
        _confirm(staging, ledger, 1004, quote_number="CN01099")


def test_proposal_is_not_accepted_until_confirmed_or_edited(env) -> None:
    staging, ledger, *_ = env
    with pytest.raises(qr.ReviewRefused, match="quote_number_basis"):
        _confirm(staging, ledger, 1001, quote_number_basis=None)
    with pytest.raises(qr.ReviewRefused, match="not the proposal"):
        _confirm(staging, ledger, 1001, quote_number="CN01006")
    edited = _confirm(staging, ledger, 1001, quote_number="CN01005-A",
                      quote_number_basis=qr.BASIS_OPERATOR_ENTERED)
    assert edited["quote_number"] == "CN01005-A"
    with pytest.raises(qr.ReviewRefused, match="quote_number must be"):
        _confirm(staging, ledger, 1001, quote_number="CN 01005 / rev", quote_number_basis=qr.BASIS_OPERATOR_ENTERED)


def test_duplicate_cn_is_only_a_warning(env) -> None:
    staging, ledger, *_ = env
    one, two = staging.item(1001), staging.item(1002)
    assert qr.WARNING_SHARED_CN in one.warnings and one.same_number_other_emails == (1002,)
    assert qr.WARNING_SHARED_DOCUMENT in one.warnings and one.same_document_other_emails == (1002,)
    first = _confirm(staging, ledger, 1001)
    second = _confirm(staging, ledger, 1002)
    assert first["quote_number"] == second["quote_number"] == "CN01005"
    assert qr.WARNING_SHARED_CN in first["warnings_at_decision"]
    # Nothing was merged: two entries, two distinct Gmail messages.
    assert {e["gmail_message_id"] for e in ledger.entries()} == {"19d0000000000001", "19d0000000000002"}
    assert qr.review_summary(staging, ledger)["by_status"]["confirmed"] == 2


# --- rejections are auditable --------------------------------------------------------


def test_rejected_candidates_remain_auditable(env) -> None:
    staging, ledger, *_ = env
    with pytest.raises(qr.ReviewRefused, match="needs a reason"):
        qr.record_decision(staging, ledger, 1004, decision=qr.DECISION_REJECT, operator="op@example.cl")
    with pytest.raises(qr.ReviewRefused, match="does not take a quote number"):
        qr.record_decision(staging, ledger, 1004, decision=qr.DECISION_REJECT, operator="op@example.cl",
                           reason="x", quote_number="CN01099")
    qr.record_decision(staging, ledger, 1004, decision=qr.DECISION_REJECT,
                       operator="op@example.cl", reason="catalogue, not a quotation")
    assert ledger.states()[1004].status == qr.STATUS_REJECTED
    # Still in the queue, still readable.
    assert staging.item(1004).email_id == 1004
    qr.record_decision(staging, ledger, 1004, decision=qr.DECISION_PENDING, operator="op2@example.cl")
    state = ledger.states()[1004]
    assert state.status == qr.STATUS_PENDING
    assert [h["decision"] for h in state.history] == ["reject_non_quotation", "leave_pending"]
    assert state.history[0]["reason"] == "catalogue, not a quotation"
    assert len(ledger.path.read_text(encoding="utf-8").splitlines()) == 2


def test_every_decision_names_an_operator(env) -> None:
    staging, ledger, *_ = env
    with pytest.raises(qr.ReviewRefused, match="operator"):
        qr.record_decision(staging, ledger, 1001, decision=qr.DECISION_PENDING, operator="  ")


# --- exact identifiers ---------------------------------------------------------------


def test_exact_identifiers_are_preserved(env) -> None:
    staging, ledger, *_ = env
    records, _ = _fixture()
    rec = records[0]
    item = staging.item(1001)
    assert item.rfc822_message_id == RFC_ID
    assert item.gmail_message_id == "19d0000000000001"
    assert item.gmail_thread_id == "t19d0000000000001"
    assert item.dedupe_key == "gmail_message:19d0000000000001"
    assert item.source_uri == "gmail://msg/19d0000000000001"
    assert item.subject_raw == rec["payload"]["subject_raw"]
    assert item.subject == "Cotización equipo"
    assert [d.sha256 for d in item.documents] == [SHA_A]
    assert [d.source_attachment_id for d in item.documents] == [1]
    entry = _confirm(staging, ledger, 1001)
    stored = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
    for key in ("rfc822_message_id", "gmail_message_id", "gmail_thread_id", "raw_sha256",
                "dedupe_key", "source_uri", "document_sha256", "source_record_sha256"):
        assert stored[key] == entry[key]
    assert stored["rfc822_message_id"] == RFC_ID
    assert stored["raw_sha256"] == rec["payload"]["raw_sha256"]


def test_mismatched_identifiers_refuse_to_load(tmp_path: Path) -> None:
    records, queue = _fixture()
    queue[0]["gmail_message_id"] = "19d000000000000X"
    with pytest.raises(qr.StagingInconsistent, match="Gmail id"):
        qr.load_staging(_write_staging(tmp_path, records, queue))


def test_legacy_records_are_historical_only(env) -> None:
    staging, ledger, *_ = env
    assert staging.historical_record_count == 1
    assert 2001 not in {i.email_id for i in staging.items}
    with pytest.raises(qr.ReviewRefused, match="historical evidence only"):
        qr.record_decision(staging, ledger, 2001, decision=qr.DECISION_PENDING, operator="op@example.cl")
    assert ledger.entries() == []


# --- the local view ------------------------------------------------------------------


def _load_script():
    spec = importlib.util.spec_from_file_location("quote_evidence_review_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def server(env):
    staging, ledger, *_ = env
    script = _load_script()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = script.make_handler(staging, ledger, "op@example.cl", port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port, ledger
    httpd.shutdown()
    httpd.server_close()


_SAME = object()


def _req(port: int, method: str, path: str, body: dict | None = None, host: str | None = None,
         origin=_SAME):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if origin is _SAME and method == "POST":
        origin = f"http://127.0.0.1:{port}"  # what a browser sends from the tool's own form
    if origin is not _SAME and origin is not None:
        headers["Origin"] = origin
    data = None
    if body is not None:
        data = urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    return resp.status, resp.read().decode("utf-8")


def test_view_lists_q1_to_q3_with_exact_ids_and_warnings(server) -> None:
    port, _ = server
    status, body = _req(port, "GET", "/")
    assert status == 200
    for gid in ("19d0000000000001", "19d0000000000003", "19d0000000000004"):
        assert gid in body
    assert "2001" not in body.split("<tbody>")[1]
    status, body = _req(port, "GET", "/item/1001")
    assert status == 200
    for needle in ("&lt;CAx+Odd=Chars_1.2@mail.example.cl&gt;", SHA_A, "compras@cliente.example.cl",
                   "CN01005", "El mismo CN aparece en otros correos", "opportunity_id"):
        assert needle in body
    assert _req(port, "GET", "/item/2001")[0] == 404


def test_view_records_decisions_and_refuses_bad_ones(server) -> None:
    port, ledger = server
    status, body = _req(port, "POST", "/item/1003/decide", {
        "decision": "confirm_customer_quotation", "opportunity_mode": "existing",
        "opportunity_id": "Cliente Dos", "quote_number": "CN01010", "quote_number_basis": "operator_entered",
    })
    assert status == 422 and "UUID" in body
    assert ledger.entries() == []
    status, _ = _req(port, "POST", "/item/1004/decide",
                     {"decision": "reject_non_quotation", "reason": "catálogo"})
    assert status == 303
    assert [e["decision"] for e in ledger.entries()] == ["reject_non_quotation"]


def test_view_refuses_a_foreign_host(server) -> None:
    port, ledger = server
    assert _req(port, "GET", "/", host="evil.example")[0] == 403
    assert _req(port, "POST", "/item/1004/decide", {"decision": "leave_pending"}, host="evil.example")[0] == 403
    assert ledger.entries() == []


_REJECT = {"decision": "reject_non_quotation", "reason": "catálogo"}


@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost"])
def test_view_accepts_a_same_origin_browser_post(server, hostname) -> None:
    port, ledger = server
    status, _ = _req(port, "POST", "/item/1004/decide", _REJECT,
                     host=f"{hostname}:{port}", origin=f"http://{hostname}:{port}")
    assert status == 303
    assert [e["decision"] for e in ledger.entries()] == ["reject_non_quotation"]


@pytest.mark.parametrize("origin", [
    "https://evil.example",
    "http://evil.example:{port}",
    "null",  # sandboxed iframes, data: URLs and no-referrer pages send this
    "http://127.0.0.1:{other}",
    "https://127.0.0.1:{port}",
    "http://127.0.0.1.evil.example:{port}",
    "http://localhost:{port}/item/1004",
    "*",
    "",
    None,  # a POST must name its origin
])
def test_view_refuses_a_foreign_origin_and_appends_nothing(server, origin) -> None:
    port, ledger = server
    if origin is not None:
        origin = origin.format(port=port, other=port + 1)
    status, body = _req(port, "POST", "/item/1004/decide", _REJECT, origin=origin)
    assert status == 403 and "forbidden origin" in body
    assert ledger.entries() == []
    assert not ledger.path.exists() or ledger.path.read_bytes() == b""


def test_view_refuses_a_foreign_origin_on_get(server) -> None:
    port, _ = server
    assert _req(port, "GET", "/", origin="https://evil.example")[0] == 403
    assert _req(port, "GET", "/", origin=f"http://localhost:{port}")[0] == 200


def test_view_sends_a_referrer_policy_that_keeps_the_origin(server) -> None:
    # `no-referrer` makes browsers send `Origin: null` on the form POST, which must be refused.
    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    resp.read()
    assert resp.getheader("Referrer-Policy") == "same-origin"


def test_allowed_origins_are_exactly_the_two_loopback_urls() -> None:
    script = _load_script()
    assert script.allowed_origins(8765) == frozenset({"http://127.0.0.1:8765", "http://localhost:8765"})


# --- read-only email chain -----------------------------------------------------------
#
# Shaped like the real 706492 case, with invented data: two messages in one thread carry the
# same CN and bytes (sent out of order in the staging file); a second thread shares only a
# brochure's bytes; a third thread has the same subject, sender and recipient domain as the
# first but nothing exact in common, and must not appear at all.

T_ONE = "19dthreadone0001"
T_TWO = "19dthreadtwo0002"
T_LOOKALIKE = "19dlookalike0003"
SHA_QUOTE = "1" * 64
SHA_BROCHURE = "2" * 64
SHA_OTHER_QUOTE = "3" * 64
SHA_LOOKALIKE = "4" * 64


def _chain_fixture() -> tuple[list[dict], list[dict]]:
    quote = lambda att, em: _doc(att, em, "CN01005-Cliente Uno.pdf", SHA_QUOTE, ["CN01005"])  # noqa: E731
    brochure = lambda att, em: _doc(att, em, "Folleto.PDF", SHA_BROCHURE, [])  # noqa: E731
    other = lambda att, em: _doc(att, em, "Cotización 1006 - Cliente Dos.pdf", SHA_OTHER_QUOTE, [])  # noqa: E731
    records = [
        # Later message first in the file: order must come from sent_at, not file order.
        _gmail(3002, "19e0000000000002", "customer_quote_candidate", ["CN01005"],
               [quote(11, 3002), brochure(12, 3002)], "<b@mail.example.cl>",
               thread=T_ONE, sent_at="2026-03-24T08:42:30-03:00"),
        _gmail(3001, "19e0000000000001", "customer_quote_candidate", ["CN01005"],
               [quote(13, 3001), brochure(14, 3001)], "<a@mail.example.cl>",
               thread=T_ONE, sent_at="2026-03-18T15:56:03-03:00"),
        _gmail(3003, "19e0000000000003", "customer_quote_candidate", [],
               [other(15, 3003), brochure(16, 3003)], "<c@mail.example.cl>",
               thread=T_TWO, sent_at="2026-03-25T16:28:24-03:00"),
        _gmail(3004, "19e0000000000004", "customer_quote_candidate", [],
               [other(17, 3004), brochure(18, 3004)], "<d@mail.example.cl>",
               thread=T_TWO, sent_at="2026-03-25T17:06:45-03:00"),
        # Same subject, same sender, same recipient domain as thread one — and nothing exact.
        _gmail(3005, "19e0000000000005", "customer_quote_candidate", ["CN01099"],
               [_doc(19, 3005, "CN01099-Cliente Uno.pdf", SHA_LOOKALIKE, ["CN01099"])], "<e@mail.example.cl>",
               thread=T_LOOKALIKE, sent_at="2026-03-19T09:00:00-03:00"),
        # Same thread, another offset: 15:00-04:00 is 19:00Z, *after* 3001's 18:56Z, although
        # its string sorts first.
        _gmail(3006, "19e0000000000006", "customer_quote_candidate", [],
               [_doc(20, 3006, "Respuesta.pdf", "5" * 64, [])], "<f@mail.example.cl>",
               thread=T_ONE, sent_at="2026-03-18T15:00:00-04:00"),
    ]
    queue = []
    for rec in records:
        p = rec["payload"]
        cn = " ".join(p["proposed_quote_numbers"])
        queue.append({
            "email_id": str(p["source_email_id"]), "gmail_message_id": p["gmail_message_id"],
            "proposed_quote_numbers": cn, "same_number_other_emails": "",
            "cross_era_number_collision": "False",
            "queue": "Q1_gmail_single_cn" if len(p["proposed_quote_numbers"]) == 1 else "Q2_gmail_zero_or_multiple_cn",
        })
    return records, queue


@pytest.fixture
def chain_env(tmp_path: Path):
    records, queue = _chain_fixture()
    staging_dir = _write_staging(tmp_path, records, queue)
    ledger = qr.DecisionLedger(tmp_path / "review" / "decisions.jsonl")
    return qr.load_staging(staging_dir), ledger, staging_dir, tmp_path


def test_chain_groups_exact_thread_in_chronological_order(chain_env) -> None:
    staging, *_ = chain_env
    chain = qr.email_chain(staging, 3002)
    assert chain.gmail_thread_id == T_ONE
    # 3001 at 15:56-03:00 (18:56Z) precedes 3006 at 15:00-04:00 (19:00Z): instants, not strings.
    assert chain.email_ids == (3001, 3006, 3002)
    assert chain.anchor_email_id == 3002
    # The same chain from any member.
    assert qr.email_chain(staging, 3001).email_ids == chain.email_ids
    # Every message keeps its own exact identifiers.
    assert [m.gmail_message_id for m in chain.messages] == [
        "19e0000000000001", "19e0000000000006", "19e0000000000002"]
    assert [m.rfc822_message_id for m in chain.messages] == [
        "<a@mail.example.cl>", "<f@mail.example.cl>", "<b@mail.example.cl>"]


def test_chain_reports_shared_cn_and_duplicate_bytes_within_the_thread(chain_env) -> None:
    staging, *_ = chain_env
    chain = qr.email_chain(staging, 3001)
    assert chain.shared_quote_numbers == {"CN01005": (3001, 3002)}
    dup = {d.sha256: d.occurrences for d in chain.duplicate_documents}
    assert set(dup) == {SHA_QUOTE, SHA_BROCHURE}
    assert sorted(dup[SHA_QUOTE]) == [(3001, "CN01005-Cliente Uno.pdf"), (3002, "CN01005-Cliente Uno.pdf")]


def test_other_thread_with_exact_shared_bytes_is_related_not_merged(chain_env) -> None:
    staging, *_ = chain_env
    chain = qr.email_chain(staging, 3001)
    assert 3003 not in chain.email_ids and 3004 not in chain.email_ids
    related = {r.item.email_id: r for r in chain.related}
    assert set(related) == {3003, 3004}
    for r in related.values():
        assert r.shared_document_sha256 == (SHA_BROCHURE,)
        assert r.shared_quote_numbers == ()
        assert r.item.gmail_thread_id == T_TWO
    groups = chain.related_by_thread()
    assert [(tid, [r.item.email_id for r in rs]) for tid, rs in groups] == [(T_TWO, [3003, 3004])]
    # Seen from the other side, thread two is its own chain; thread one is only related.
    other = qr.email_chain(staging, 3004)
    assert other.email_ids == (3003, 3004)
    assert {r.item.email_id for r in other.related} == {3001, 3002}


def test_chain_never_groups_by_name_domain_or_subject(chain_env) -> None:
    staging, *_ = chain_env
    lookalike = staging.item(3005)
    anchor = staging.item(3001)
    # Same display subject, sender and recipient — deliberately.
    assert (lookalike.subject, lookalike.sender, lookalike.recipients) == (
        anchor.subject, anchor.sender, anchor.recipients)
    chain = qr.email_chain(staging, 3001)
    assert 3005 not in chain.email_ids
    assert 3005 not in {r.item.email_id for r in chain.related}
    assert qr.email_chain(staging, 3005).email_ids == (3005,)
    assert qr.email_chain(staging, 3005).related == ()


def test_message_without_thread_id_stands_alone(tmp_path: Path) -> None:
    records, queue = _chain_fixture()
    for rec in records:
        if rec["payload"]["source_email_id"] in (3001, 3002):
            rec["payload"]["gmail_thread_id"] = None
    staging = qr.load_staging(_write_staging(tmp_path, records, queue))
    chain = qr.email_chain(staging, 3001)
    assert chain.gmail_thread_id is None
    assert chain.email_ids == (3001,)
    # Its exact twin is still visible — as related evidence, not as the same record.
    assert {r.item.email_id for r in chain.related} >= {3002}


def test_chain_is_read_only_and_decisions_stay_per_message(chain_env) -> None:
    staging, ledger, staging_dir, root = chain_env
    before = _digest_tree(root)
    for it in staging.items:
        qr.email_chain(staging, it.email_id)
    assert _digest_tree(root) == before  # no ledger created, staging untouched
    assert not hasattr(qr.email_chain(staging, 3001), "opportunity_id")

    _confirm(staging, ledger, 3001)
    states = ledger.states()
    # Confirming one message of a thread decides nothing for its siblings or relations.
    assert set(states) == {3001}
    assert [e["email_id"] for e in ledger.entries()] == [3001]
    assert ledger.entries()[0]["gmail_message_id"] == "19e0000000000001"
    assert qr.review_summary(staging, ledger)["by_status"]["confirmed"] == 1


def test_chain_refuses_legacy_and_unknown_ids(env) -> None:
    staging, *_ = env
    with pytest.raises(qr.ReviewRefused, match="historical evidence only"):
        qr.email_chain(staging, 2001)
    with pytest.raises(qr.ReviewRefused, match="not in the Q1–Q3"):
        qr.email_chain(staging, 999999)


@pytest.fixture
def chain_server(chain_env):
    staging, ledger, *_ = chain_env
    script = _load_script()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = script.make_handler(staging, ledger, "op@example.cl", port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port, ledger
    httpd.shutdown()
    httpd.server_close()


def test_chain_view_shows_messages_in_order_and_related_apart(chain_server) -> None:
    port, ledger = chain_server
    status, body = _req(port, "GET", "/item/3002/cadena")
    assert status == 200
    head, _, rest = body.partition("<tbody>")
    main_table, _, related_part = rest.partition("Evidencia relacionada")
    assert T_ONE in head
    # Chronological in the main table, with every exact field.
    positions = [main_table.index(f"/item/{n}'") for n in (3001, 3006, 3002)]
    assert positions == sorted(positions)
    for needle in ("19e0000000000001", "19e0000000000002", "19e0000000000006",
                   SHA_QUOTE, SHA_BROCHURE, "compras@cliente.example.cl",
                   "2026-03-18T15:56:03-03:00", "Cotización equipo"):
        assert needle in main_table
    assert "/item/3003'" not in main_table and "/item/3004'" not in main_table
    # Thread two appears only as related evidence, under its own thread id.
    assert T_TWO in related_part and "/item/3003'" in related_part and "/item/3004'" in related_part
    assert "/item/3005'" not in body
    assert ledger.entries() == []


def test_chain_view_has_no_write_path(chain_server) -> None:
    port, ledger = chain_server
    status, _ = _req(port, "POST", "/item/3001/cadena", {"decision": "leave_pending"})
    assert status == 404
    assert ledger.entries() == []
    status, body = _req(port, "GET", "/item/3001/cadena")
    assert "<form" not in body
