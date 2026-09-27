"""Legacy Drive reconciliation + case-first migration dry run: placement, duplicates, collisions, rehearsal."""

from __future__ import annotations

import hashlib

import pytest

from origenlab_api.v2 import quote_case_archive as qa
from origenlab_api.v2 import quote_drive_legacy_migration as lm

FOLDER = qa.FOLDER_MIME


def pdf(tag: str) -> bytes:
    return b"%PDF-1.4\n" + tag.encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


BYTES = {sha(pdf(t)): pdf(t) for t in ("a1", "a2", "a2b", "b1", "blk", "x", "ev", "cl1", "cl2", "unk", "e1")}
S = {t: sha(pdf(t)) for t in ("a1", "a2", "a2b", "b1", "blk", "x", "ev", "cl1", "cl2", "unk", "e1")}


def f(fid: str, name: str, parent: str, path: str, *, mime: str = "application/pdf", tag: str | None = None,
      props: dict | None = None) -> dict:
    item = {"id": fid, "name": name, "mimeType": mime, "parents": [parent], "path": path, "trashed": False,
            "appProperties": props or {}}
    if tag:
        item |= {"sha256Checksum": S[tag], "size": str(len(pdf(tag)))}
    return item


def inventory(extra_casos: list[dict] | None = None) -> dict:
    env = [
        f("fa", "CN01001 — Ana - Lab A", "ENV", "Cotizaciones/Enviadas/CN01001 — Ana - Lab A", mime=FOLDER),
        f("fa1", "CN01001-Ana - Lab A.pdf", "fa", "…/CN01001-Ana - Lab A.pdf", tag="a1"),
        f("fb", "CN01002 — Ana - Lab A", "ENV", "Cotizaciones/Enviadas/CN01002 — Ana - Lab A", mime=FOLDER),
        f("fb1", "CN01002-Ana - Lab A.pdf", "fb", "…/CN01002-Ana - Lab A.pdf", tag="a2"),
        f("fb2", "CN01002A-Ana - Lab A.pdf", "fb", "…/CN01002A-Ana - Lab A.pdf", tag="a2b"),
        f("fc", "CN01003 — Beto", "ENV", "Cotizaciones/Enviadas/CN01003 — Beto", mime=FOLDER),
        f("fc1", "CN01003-Beto.pdf", "fc", "…/CN01003-Beto.pdf", tag="b1"),
        f("fd", "CN01003 — Beto copia", "ENV", "Cotizaciones/Enviadas/CN01003 — Beto copia", mime=FOLDER),
        f("fd1", "CN01003-Beto.pdf", "fd", "…/copia/CN01003-Beto.pdf", tag="b1"),
        f("fe", "CN01009 — Carla", "ENV", "Cotizaciones/Enviadas/CN01009 — Carla", mime=FOLDER),
        f("fe1", "CN01009-Carla.pdf", "fe", "…/CN01009-Carla.pdf", tag="cl1"),
        f("ff", "CN01009 — Diego", "ENV", "Cotizaciones/Enviadas/CN01009 — Diego", mime=FOLDER),
        f("ff1", "CN01009-Diego.pdf", "ff", "…/CN01009-Diego.pdf", tag="cl2"),
        f("fg", "CN01010 — Otro", "ENV", "Cotizaciones/Enviadas/CN01010 — Otro", mime=FOLDER),
        f("fg1", "CN01010-Otro.pdf", "fg", "…/CN01010-Otro.pdf", tag="blk"),
        f("fg2", "CN01010-Otro borrador.pdf", "fg", "…/CN01010-Otro borrador.pdf", tag="ev"),
        f("fg3", "misterio.pdf", "fg", "…/misterio.pdf", tag="unk"),
        f("fg4", "CN01011-Email.pdf", "fg", "…/CN01011-Email.pdf", tag="e1"),
    ]
    pend = [
        f("pa", "CN01020 — Eva", "PEND", "Cotizaciones/Pendientes/CN01020 — Eva", mime=FOLDER),
        f("pa1", "CN01020-Eva.pdf", "pa", "…/CN01020-Eva.pdf", tag="x"),
        f("pa2", "CN01020-Eva.xlsx", "pa", "…/CN01020-Eva.xlsx",
          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        f("pa3", "brochure.pdf", "pa", "…/brochure.pdf", tag="unk") | {"sha256Checksum": None},
    ]
    casos = extra_casos or []
    return {
        "principal": "contacto@origenlab.cl",
        "cotizaciones": {"id": "COT", "name": "Cotizaciones", "mimeType": FOLDER, "parents": [], "appProperties": {}},
        "cotizaciones_children": [
            {"id": "PEND", "name": "Pendientes", "mimeType": FOLDER, "parents": ["COT"]},
            {"id": "ENV", "name": "Enviadas", "mimeType": FOLDER, "parents": ["COT"]},
            {"id": "CASOS", "name": "Casos", "mimeType": FOLDER, "parents": ["COT"],
             "appProperties": {qa.PROP_KIND: qa.KIND_CASE_ROOT}},
        ],
        "trees": {"Enviadas": env, "Pendientes": pend, "Casos": casos},
    }


def known() -> dict[str, lm.KnownDocument]:
    k = lm.KnownDocument
    docs = [
        k(S["a1"], "confirm_customer_quotation", "case-A", "01001-26", ("g1",), (1,), "CN01001-Ana - Lab A.pdf", "quotation", "01001-26", "Ana - Lab A"),
        k(S["a2"], "confirm_customer_quotation", "case-A", "01002-26", ("g2",), (2,), "CN01002-Ana - Lab A.pdf", "quotation", "01002-26", "Ana - Lab A"),
        k(S["a2b"], "confirm_customer_quotation", "case-A", "01002-26", ("g3",), (3,), "CN01002A-Ana - Lab A.pdf", "quotation", "01002-26", "Ana - Lab A"),
        k(S["b1"], "confirm_customer_quotation", "case-B", "01003-26", ("g4",), (4,), "CN01003-Beto.pdf", "quotation", "01003-26", "Beto"),
        k(S["cl1"], "confirm_customer_quotation", "case-C", "01009-26", (), (5,), "CN01009-Carla.pdf", "quotation", "01009-26", "Carla"),
        k(S["cl2"], "confirm_customer_quotation", "case-D", "01009-26", (), (6,), "CN01009-Diego.pdf", "quotation", "01009-26", "Diego"),
        k(S["blk"], None, None, None, (), (7,), None, "quotation", "01010-26", "Otro", blocked_reasons=("G1_numero_en_otra_oportunidad",)),
        k(S["ev"], None, None, None, (), (), None, "quotation", "01003-26", "Beto"),
        k(S["x"], "confirm_customer_quotation", "case-E", "01020-26", (), (8,), "CN01020-Eva.pdf", "quotation", "01020-26", "Eva"),
        k(S["e1"], None, None, None, ("g9",), (9,), "CN01011-Email.pdf", "quotation", "01011-26", "Email", email_decision="confirm_customer_quotation"),
    ]
    return {d.sha256: d for d in docs}


def plan() -> dict:
    def opp(key, status, addressee, revs):
        quotes = {}
        for q, s, t in revs:
            quotes.setdefault(q, []).append({"document_sha256": s, "sent_at": t})
        return {"planned_opportunity_id": key, "status": status, "printed_addressee": addressee,
                "quotes": [{"quote_number": q, "revisions": r} for q, r in quotes.items()], "reasons": []}
    return {"opportunities": [
        opp("case-A", "ready", "Ana - Lab A", [("01002-26", S["a2b"], "2026-05-03T10:00:00-04:00"),
                                             ("01001-26", S["a1"], "2026-05-01T10:00:00-04:00"),
                                             ("01002-26", S["a2"], "2026-05-02T10:00:00-04:00")]),
        opp("case-B", "needs_organization_confirmation", "Beto", [("01003-26", S["b1"], "2026-05-04T10:00:00-04:00")]),
        opp("case-C", "ready", "Carla", [("01009-26", S["cl1"], "2026-05-05T10:00:00-04:00")]),
        opp("case-D", "ready", "Diego", [("01009-26", S["cl2"], "2026-05-06T10:00:00-04:00")]),
        opp("case-E", "held", "Eva", [("01020-26", S["x"], "2026-05-07T10:00:00-04:00")]),
    ], "blocked_documents": []}


@pytest.fixture
def built() -> lm.MigrationPlan:
    return lm.build_plan(inventory(), known(), lm.plan_cases(plan()))


def row(p: lm.MigrationPlan, drive_id: str) -> lm.LegacyRow:
    return next(r for r in p.rows if r.drive_id == drive_id)


def test_every_legacy_item_is_untouched(built) -> None:
    assert len(built.rows) == 22
    assert {r.legacy_action for r in built.rows} == {"untouched"}
    assert built.summary["legacy_writes"] == 0


def test_several_quotations_and_revisions_share_one_case(built) -> None:
    [a] = [c for c in built.cases if c["case_key"] == "case-A"]
    assert a["folder_name"] == "Caso 01001 — Ana - Lab A" and a["folder_action"] == "create_case_folder"
    assert [(d["quote_number"], d["revision"]) for d in a["documents"]] == [("01001-26", 1), ("01002-26", 1), ("01002-26", 2)]
    assert set(a["flags"]) == {"case_spans_legacy_folders", "case_has_several_quotes", "case_has_revisions"}
    assert row(built, "fb2").target_file == "01002-26 r2 — CN01002A-Ana - Lab A.pdf"


def test_duplicate_pdf_in_two_legacy_folders_is_copied_once(built) -> None:
    assert row(built, "fc1").action == lm.A_COPY and row(built, "fd1").action == lm.A_DUPLICATE
    assert "duplicate_pdf" in row(built, "fc1").flags and "duplicate_pdf" in row(built, "fd1").flags
    [b] = [c for c in built.cases if c["case_key"] == "case-B"]
    assert len(b["documents"]) == 1 and b["legacy_folders"] == ["CN01003 — Beto", "CN01003 — Beto copia"]


def test_one_number_for_two_clients_is_two_cases_and_a_reported_collision(built) -> None:
    assert row(built, "fe1").case_key == "case-C" and row(built, "ff1").case_key == "case-D"
    [col] = [c for c in built.collisions if c["number"] == "1009-26"]
    assert col["kind"] == "several_cases" and col["cases"] == ["case-C", "case-D"]
    assert "number_collision" in row(built, "fe1").flags


def test_a_draft_version_of_a_confirmed_number_is_blocked_not_merged(built) -> None:
    r = row(built, "fg2")
    assert r.classification == lm.C_OTHER_VERSION and r.action == lm.A_BLOCKED and r.case_key is None


def test_blocked_unknown_and_email_only_documents_are_not_copied(built) -> None:
    assert row(built, "fg1").classification == lm.C_BLOCKED and row(built, "fg1").lifecycle == "held"
    assert row(built, "fg3").classification == lm.C_UNKNOWN and row(built, "fg3").action == lm.A_BLOCKED
    assert row(built, "fg4").classification == lm.C_EMAIL_ONLY and row(built, "fg4").action == lm.A_BLOCKED


def test_working_files_and_missing_checksums_are_reported(built) -> None:
    assert row(built, "pa2").classification == lm.C_WORKING and row(built, "pa2").action == lm.A_NONE
    assert row(built, "pa3").classification == lm.C_NO_CHECKSUM and row(built, "pa3").action == lm.A_BLOCKED
    [folder] = [x for x in built.folders if x["drive_id"] == "pa"]
    assert folder["outcome"] == "partially_mapped" and folder["legacy_action"] == "untouched"


def test_pending_and_held_cases_are_archived_with_their_status_beside_them(built) -> None:
    b, e = row(built, "fc1"), row(built, "pa1")
    assert b.action == e.action == lm.A_COPY
    assert b.crm_status == "pending_organization_confirmation" and b.lifecycle == "pending_organization_confirmation"
    assert e.crm_status == "held" and e.lifecycle == "held"
    assert "held" not in e.target_folder and "pend" not in b.target_folder.lower()


def test_an_existing_case_folder_is_reused_and_an_archived_file_linked() -> None:
    casos = [
        f("k1", "Caso 01001 — Ana - Lab A", "CASOS", "Cotizaciones/Casos/Caso 01001 — Ana - Lab A", mime=FOLDER,
          props={qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: "case-A", qa.PROP_OPENING_QUOTE: "01001-26"}),
        f("k1f", "01001-26 r1 — CN01001-Ana - Lab A.pdf", "k1", "…", tag="a1",
          props={qa.PROP_KIND: qa.KIND_REVISION, qa.PROP_CASE_KEY: "case-A", qa.PROP_DOC_SHA: S["a1"]}),
    ]
    p = lm.build_plan(inventory(casos), known(), lm.plan_cases(plan()))
    [a] = [c for c in p.cases if c["case_key"] == "case-A"]
    assert a["folder_action"] == "reuse_case_folder" and a["existing_folder_id"] == "k1"
    assert row(p, "fa1").action == lm.A_LINK and len(a["documents"]) == 2


def test_filename_serial_mismatch_is_flagged() -> None:
    inv = inventory()
    inv["trees"]["Enviadas"][1]["name"] = "CN0101-Ana - Lab A.pdf"
    p = lm.build_plan(inv, known(), lm.plan_cases(plan()))
    assert "filename_number_mismatch" in row(p, "fa1").flags
    assert "filename_number_mismatch" not in row(p, "fb1").flags


def test_number_key_folds_leading_zeros_only() -> None:
    assert lm.number_key("01005-26") == lm.number_key("1005-26") == "1005-26"
    assert lm.number_key("011453AI-26") == "11453AI-26" != lm.number_key("011453AII-26")


def test_rehearsal_first_run_rerun_and_rollback(built) -> None:
    rep = lm.rehearse(inventory(), built, BYTES.get, run_id="t-run")
    assert rep["first_run"]["refused"] == [] and rep["first_run"]["all_verified"]
    assert rep["first_run"]["folders_created"] == 5  # A, B, C, D, E — C and D share a number, not a case
    assert rep["first_run"]["files_uploaded"] == 7
    assert rep["rerun"]["writes"] == 0
    assert rep["rollback"]["casos_restored"] and rep["rollback"]["trashed"] == rep["first_run"]["writes"]
    assert rep["legacy_unchanged"]


def test_rehearsal_reports_documents_without_local_bytes(built) -> None:
    missing = S["b1"]
    rep = lm.rehearse(inventory(), built, lambda s: None if s == missing else BYTES.get(s), run_id="t2")
    assert rep["documents_without_local_bytes"] == [missing]
    assert rep["legacy_unchanged"]


def test_status_change_does_not_change_the_plan_paths() -> None:
    p1 = lm.build_plan(inventory(), known(), lm.plan_cases(plan()))
    flipped = plan()
    for o in flipped["opportunities"]:
        o["status"] = "ready"
    p2 = lm.build_plan(inventory(), known(), lm.plan_cases(flipped))
    paths = lambda p: sorted((r.drive_id, r.target_folder, r.target_file) for r in p.rows)  # noqa: E731
    assert paths(p1) == paths(p2)


def _plan_with_gmail_only() -> tuple[dict, dict]:
    p = plan()
    extra = pdf("gmail-only")
    s = sha(extra)
    p["opportunities"].append({"planned_opportunity_id": "case-G", "status": "needs_organization_confirmation",
                               "printed_addressee": "Gina", "reasons": [],
                               "quotes": [{"quote_number": "01030-26",
                                           "revisions": [{"document_sha256": s, "sent_at": "2026-05-09T10:00:00-04:00"}]}]})
    k = known() | {s: lm.KnownDocument(s, "confirm_customer_quotation", "case-G", "01030-26", ("g30",), (30,),
                                        "CN01030-Gina.pdf", "quotation", "01030-26", "Gina")}
    return p, k


def test_a_plan_document_in_no_drive_folder_is_planned_from_gmail_bytes_apart() -> None:
    p, k = _plan_with_gmail_only()
    built = lm.build_plan(inventory(), k, lm.plan_cases(p))
    [g] = built.gmail_only
    assert g["action"] == "upload_from_gmail_bytes" and g["case_key"] == "case-G"
    assert g["target_folder"] == "Caso 01030 — Gina" and g["target_file"] == "01030-26 r1 — CN01030-Gina.pdf"
    assert built.summary["files_to_upload_from_gmail_bytes"] == 1 and built.summary["cases_only_from_gmail"] == 1
    assert built.summary["files_to_copy"] == 7
    legacy_only = lm.build_plan(inventory(), k, lm.plan_cases(p), include_gmail_only=False)
    assert all(c["case_key"] != "case-G" for c in legacy_only.cases if c["documents"])
    assert legacy_only.gmail_only[0]["action"] == "upload_from_gmail_bytes"  # still reported


def test_a_plan_document_without_a_matching_ledger_row_is_blocked() -> None:
    p, k = _plan_with_gmail_only()
    s = next(iter(set(k) - set(known())))
    k[s] = lm.KnownDocument(s, "confirm_customer_quotation", "some-other-case", "01030-26")
    built = lm.build_plan(inventory(), k, lm.plan_cases(p))
    assert built.gmail_only[0]["action"] == lm.A_BLOCKED and built.summary["gmail_only_blocked"] == 1


def test_a_document_already_in_casos_is_not_planned_again() -> None:
    p, k = _plan_with_gmail_only()
    s = next(iter(set(k) - set(known())))
    casos = [f("k", "Caso 01030 — Gina", "CASOS", "…", mime=FOLDER,
               props={qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: "case-G"}),
             {"id": "kf", "name": "x.pdf", "mimeType": "application/pdf", "parents": ["k"], "path": "…",
              "sha256Checksum": s, "appProperties": {qa.PROP_KIND: qa.KIND_REVISION, qa.PROP_CASE_KEY: "case-G", qa.PROP_DOC_SHA: s}}]
    built = lm.build_plan(inventory(casos), k, lm.plan_cases(p))
    assert built.gmail_only == [] and not any(c["case_key"] == "case-G" and c["documents"] for c in built.cases)
