"""CRM promotion dry run: suggestions, conflicts, blocked steps and idempotency. Invented fixtures."""

from __future__ import annotations

import copy

from origenlab_api.v2 import quote_crm_promotion as qc

SHA_A = "a" * 64
SHA_B = "b" * 64
OPP_A = "00000000-0000-4000-8000-00000000000a"
OPP_B = "00000000-0000-4000-8000-00000000000b"


def _row(sha=SHA_A, decision_id="d-1", opp=OPP_A, number="1101-26", email_id=1, mode="create_new",
         applied=False, operator="Operadora Prueba", extra_occurrences=()):
    occurrences = [{"email_id": email_id, "gmail_message_id": f"g{email_id}", "gmail_thread_id": "t1",
                    "sent_at": "2026-05-01T10:00:00-04:00", "source_attachment_id": 10 + email_id,
                    "rfc822_message_id": f"<m{email_id}@example.invalid>"}]
    occurrences += [{"email_id": e, "gmail_message_id": f"g{e}", "gmail_thread_id": "t1",
                     "sent_at": f"2026-05-0{e}T10:00:00-04:00"} for e in extra_occurrences]
    return {
        "decision": "confirm_customer_quotation", "decision_id": decision_id, "document_sha256": sha,
        "canonical_email_id": email_id, "occurrences": occurrences, "applied": applied,
        "operator": operator, "quote_number": number, "printed_quote_numbers": ["0" + number],
        "filenames": [f"CN0{number}.pdf"], "client_evidence_lines": ["At. Instituto Ficticio"],
        "opportunity": {"mode": mode, "opportunity_id": None, "planned_opportunity_id": opp},
        "recorded_at": "2026-09-25T00:00:00+00:00",
    }


def _doc(org="Instituto Ficticio", contact=None, addressee=None, serial=1101, rut=None):
    return {
        "quote_number": {"serial": serial, "evidence": {"line": f"COTIZACIÓN N°0{serial}-26"}},
        "clients": [{"organization": org, "contact": contact, "addressee": {"value": addressee or org},
                     "rut": {"value": rut} if rut else None, "address": None}],
        "document_date": {"value": "2026-05-01"}, "products": [{"value": "Equipo X"}],
    }


def _staged(email_id=1, recipients='"a" <persona@instituto-ficticio.example>'):
    return {email_id: {"dedupe_key": f"gmail_message:g{email_id}", "payload": {"recipients": recipients}}}


def _snapshot(*, loaded=False, orgs=(), operator="Operadora Prueba"):
    snap = qc.CrmSnapshot(
        organizations=[qc.CrmOrganization(f"org-{i}", n, None, "unknown", c) for i, (n, c) in enumerate(orgs)],
        operators=[operator],
    )
    if loaded:
        snap.source_records = {"gmail_message:g1": "sr-1", "gmail_message:g2": "sr-2", "gmail_message:g3": "sr-3"}
        snap.assertions = {("gmail_message:g1", "organization_name"): ["as-1"],
                           ("gmail_message:g1", "contact_address"): ["as-2"]}
    return snap


def _plan(rows=None, docs=None, staged=None, snap=None, selected=None, seed=1235):
    rows = rows if rows is not None else [_row()]
    return qc.plan_promotion(
        selected if selected is not None else rows, rows, docs or {SHA_A: _doc()},
        staged if staged is not None else _staged(), snap if snap is not None else _snapshot(),
        approved_seed_next_serial=seed, provisional_seed_next_serial=1243,
    )


def _codes(item):
    return {c.code for c in item.conflicts}


def _blocking(item):
    return {c.code for c in item.blocking}


def test_every_item_requires_manual_approval_and_stays_unapplied():
    [item] = _plan()
    d = item.as_dict()
    assert d["requiere_aprobacion_manual"] is True
    assert d["applied"] is False
    assert any("requesting_institution" in m for m in d["aprobaciones_manuales"])


def test_unloaded_source_blocks_the_case_and_the_org_command():
    [item] = _plan()
    assert qc.C_SOURCE_NOT_LOADED in _blocking(item)
    assert qc.C_NO_ORG_ASSERTION in _blocking(item)
    opened = next(s for s in item.steps if s.command == qc.CMD_OPEN_CASE)
    assert opened.status == qc.STEP_BLOCKED
    assert not item.promotable_now


def test_with_evidence_loaded_only_the_missing_quote_command_blocks():
    [item] = _plan(snap=_snapshot(loaded=True, orgs=[("Instituto Ficticio", "confirmed")]))
    assert _blocking(item) == {qc.C_NO_QUOTE_COMMAND}
    statuses = {s.command: s.status for s in item.steps}
    assert statuses[qc.CMD_OPEN_CASE] == qc.STEP_OPERATOR
    assert statuses[qc.CMD_CONFIRM_ORG] == qc.STEP_OPERATOR
    assert statuses[qc.WRITE_QUOTE_NO_COMMAND] == qc.STEP_BLOCKED


def test_exact_match_to_machine_proposed_org_is_a_warning_not_a_link():
    [item] = _plan(snap=_snapshot(orgs=[("instituto  ficticio", "machine_proposed")]))
    org = item.as_dict()["organizacion_sugerida"]
    assert org["base_coincidencia"] == qc.ORG_MATCH_EXACT
    assert org["accion_sugerida"] == qc.CMD_CONFIRM_ORG
    assert qc.C_ORG_MACHINE in _codes(item) - _blocking(item)


def test_accent_folded_match_is_reported_separately():
    [item] = _plan(snap=_snapshot(orgs=[("Instituto Fícticio", "machine_proposed")]))
    assert item.organization["base_coincidencia"] == qc.ORG_MATCH_ACCENT_FOLDED


def test_no_match_suggests_create_organization():
    [item] = _plan(snap=_snapshot(orgs=[("Otro Lugar", "confirmed")]))
    assert item.organization["accion_sugerida"] == qc.CMD_CREATE_ORG
    assert qc.C_ORG_NEW in _codes(item)
    assert any(s.command == qc.CMD_CREATE_ORG for s in item.steps)


def test_two_exact_matches_are_ambiguous_and_block():
    [item] = _plan(snap=_snapshot(loaded=True, orgs=[("Instituto Ficticio", "confirmed")] * 2))
    assert qc.C_ORG_AMBIGUOUS in _blocking(item)


def test_client_without_institution_blocks_requesting_but_not_qualifying():
    docs = {SHA_A: _doc(org=None, addressee="Persona Inventada")}
    [item] = _plan(docs=docs, snap=_snapshot(loaded=True))
    assert qc.C_ORG_NOT_PRINTED in _blocking(item)
    statuses = {(s.command, s.body.get("to")): s.status for s in item.steps}
    assert statuses[(qc.CMD_ADD_CASE_ORG, None)] == qc.STEP_BLOCKED
    assert statuses[(qc.CMD_ADVANCE_STAGE, "qualifying")] == qc.STEP_OPERATOR
    assert statuses[(qc.CMD_ADVANCE_STAGE, "qualified")] == qc.STEP_BLOCKED
    assert not any(s.command in (qc.CMD_CREATE_ORG, qc.CMD_CONFIRM_ORG) for s in item.steps)


def test_rut_makes_a_dashless_addressee_an_organization_candidate():
    docs = {SHA_A: _doc(org=None, addressee="Empresa Inventada", rut="11.111.111-1")}
    [item] = _plan(docs=docs)
    assert item.organization["base_coincidencia"] == qc.ORG_MATCH_NONE
    assert qc.C_ORG_NOT_PRINTED not in _codes(item)


def test_quote_number_in_crm_or_shared_in_ledger_blocks():
    snap = _snapshot()
    snap.quote_numbers = {"1101-26": "opp-x"}
    [item] = _plan(snap=snap)
    assert qc.C_QUOTE_NUMBER_TAKEN in _blocking(item)

    rows = [_row(), _row(sha=SHA_B, decision_id="d-2", opp=OPP_B, email_id=2)]
    items = _plan(rows=rows, docs={SHA_A: _doc(), SHA_B: _doc()}, staged={**_staged(), **_staged(2)})
    assert all(qc.C_QUOTE_NUMBER_SHARED in _blocking(i) for i in items)


def test_superseded_and_already_applied_rows_block():
    first = _row()
    later = _row(decision_id="d-2")
    [item] = _plan(rows=[first, later], selected=[first])
    assert qc.C_SUPERSEDED in _blocking(item)

    [item] = _plan(rows=[_row(applied=True)])
    assert qc.C_ALREADY_APPLIED in _blocking(item)


def test_unknown_operator_blocks():
    [item] = _plan(snap=_snapshot(operator="Alguien Más"))
    assert qc.C_OPERATOR_UNKNOWN in _blocking(item)


def test_serial_at_or_beyond_approved_seed_warns():
    [item] = _plan(docs={SHA_A: _doc(serial=1240)}, seed=1235)
    assert qc.C_SEED_PROVISIONAL in _codes(item) - _blocking(item)
    assert any("semilla" in m for m in item.manual_approval)


def test_recipients_are_parsed_and_free_mail_flagged():
    staged = _staged(recipients='"X" <Uno@GMAIL.com>, dos@instituto-ficticio.example')
    [item] = _plan(staged=staged)
    rows = item.contact["destinatarios"]
    assert [r["direccion"] for r in rows] == ["dos@instituto-ficticio.example", "uno@gmail.com"]
    assert {qc.C_RECIPIENT_FREE_MAIL, qc.C_RECIPIENT_SEVERAL, qc.C_RECIPIENT_NOT_IN_CRM} <= _codes(item)


def test_duplicates_become_mentions_links():
    [item] = _plan(rows=[_row(extra_occurrences=(2,))])
    links = [s for s in item.steps if s.command == qc.CMD_LINK_EVIDENCE]
    assert [s.body["source_record_dedupe_key"] for s in links] == ["gmail_message:g2"]
    assert links[0].body["relation"] == "mentions"


def test_plan_is_deterministic_and_pure():
    rows, docs, staged, snap = [_row()], {SHA_A: _doc()}, _staged(), _snapshot()
    frozen = copy.deepcopy((rows, docs, staged, snap))
    a = _plan(rows=rows, docs=docs, staged=staged, snap=snap)
    b = _plan(rows=rows, docs=docs, staged=staged, snap=snap)
    assert qc.plan_digest(a) == qc.plan_digest(b)
    assert (rows, docs, staged, snap) == frozen


def test_replay_after_hypothetical_apply_writes_nothing():
    snap = _snapshot(loaded=True)
    items = _plan(snap=snap)
    after = qc.simulate_apply(items, snap)
    assert snap.receipts == {} and snap.quote_numbers == {}  # the original is untouched
    replan = _plan(snap=after)
    assert all(s.status == qc.STEP_DONE for i in replan for s in i.steps)
    report = qc.idempotency_report(items, replan)
    assert report["repeticion_sin_escrituras"] is True
    assert report["escrituras_en_repeticion_tras_aplicar"] == 0
    assert report["numeros_que_se_escribirian_dos_veces"] == []
    assert report["claves_con_comandos_distintos"] == {}


def test_two_documents_of_one_planned_opportunity_share_its_opening_key():
    rows = [_row(), _row(sha=SHA_B, decision_id="d-2", number="1102-26", email_id=2, mode="planned")]
    items = _plan(rows=rows, docs={SHA_A: _doc(), SHA_B: _doc(serial=1102)}, staged={**_staged(), **_staged(2)})
    report = qc.idempotency_report(items, items)
    assert qc.idempotency_key("open", OPP_A) in report["claves_compartidas_misma_escritura"]
    assert report["claves_con_comandos_distintos"] == {}
    assert all(qc.C_OPP_SHARED in _codes(i) for i in items)


def test_existing_receipt_marks_the_step_done():
    snap = _snapshot()
    snap.receipts = {qc.idempotency_key("open", OPP_A): qc.CMD_OPEN_CASE}
    [item] = _plan(snap=snap)
    assert next(s for s in item.steps if s.command == qc.CMD_OPEN_CASE).status == qc.STEP_DONE


def test_long_idempotency_keys_fit_the_receipt_column():
    key = qc.idempotency_key("contact", "x" * 300)
    assert len(key) <= 200 and key.startswith("qpromo:h:")
    assert key == qc.idempotency_key("contact", "x" * 300)
