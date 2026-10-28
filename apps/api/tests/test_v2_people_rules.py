"""Who wrote each email (people first, Task 4): pure planning, no database.

Every name, address and institution here is fictitious (`.invalid`, «Universidad Ficticia»).
"""

from __future__ import annotations

from datetime import datetime, timezone

from origenlab_api.v2.people_rules import (
    ContactPointRow,
    OrgRow,
    PeopleSnapshot,
    PersonRow,
    Reading,
    name_norm,
    names_match,
    names_overlap,
    org_key,
    plan_people,
)

UF = "org-uf"          # Universidad Ficticia, domain ficticia.invalid
OTRA = "org-otra"      # Instituto Otro, domain otra.invalid
OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)
NEW = datetime(2026, 6, 1, tzinfo=timezone.utc)

ORGS = {
    UF: OrgRow(id=UF, name="Universidad Ficticia", status="active"),
    OTRA: OrgRow(id=OTRA, name="Instituto Otro", status="active"),
}


def reading(address: str, *, header: str | None = None, sig_name: str | None = None,
            institution: str | None = None, title: str | None = None, co: tuple[str, ...] = (),
            n: int = 1) -> Reading:
    signature = None
    if sig_name or institution or title:
        signature = {"name": sig_name, "title": title, "institution": institution, "phone": None}
    return Reading(
        assertion_id=f"a-{address}-{n}", source_record_id=f"s-{n}", message_participant_ids=(),
        value={"version": 1, "sender_address": address, "header_name": header, "signature": signature,
               "co_addresses": list(co), "signature_raw": ""},
    )


def person(pid: str, name: str, *, confirmation: str = "machine_proposed", created_at=OLD,
           status: str = "active", merged_into: str | None = None) -> PersonRow:
    return PersonRow(id=pid, display_name=name, confirmation=confirmation, status=status,
                     merged_into=merged_into, created_at=created_at)


def cp(cid: str, address: str, person_id: str | None, *, usage: str = "work",
       organization_id: str | None = None, status: str = "active") -> ContactPointRow:
    return ContactPointRow(id=cid, value_norm=address, person_id=person_id,
                           organization_id=organization_id, usage=usage, status=status)


def snapshot(readings, *, persons=(), contact_points=(), affiliations=None, aliases=None,
             answered=None, org_by_domain=None) -> PeopleSnapshot:
    persons_by_id = {p.id: p for p in persons}
    names: dict[str, set[str]] = {}
    for p in persons:
        names.setdefault(name_norm(p.display_name), set()).add(p.id)
    for pid, alias in (aliases or []):
        names.setdefault(name_norm(alias), set()).add(pid)
    return PeopleSnapshot(
        readings=tuple(readings), persons=persons_by_id, names=names,
        contact_points={c.value_norm: c for c in contact_points},
        affiliations=affiliations or {}, organizations=ORGS,
        org_by_name={org_key(o.name): {o.id} for o in ORGS.values()},
        org_by_domain={"ficticia.invalid": UF, "otra.invalid": OTRA} if org_by_domain is None else org_by_domain,
        answered=answered or {},
    )


def rules(actions) -> list[tuple[str, str]]:
    return [(a.rule_id, a.tier) for a in actions]


# --- names --------------------------------------------------------------------------------------

def test_name_norm_folds_and_drops_titles():
    assert name_norm("  Dra.  María   GONZÁLEZ ") == "maria gonzalez"
    assert name_norm("Prof. Ing. Pedro Soto") == "pedro soto"


def test_names_match_rules():
    assert names_match("Pedro Soto", "Juan Pedro Soto Rivas")
    assert names_match("Dr. Pedro Soto", "pedro soto")
    assert not names_match("Pedro", "Pedro Soto")
    assert not names_match("Pedro", "Pedro")            # a single given name never matches
    assert not names_match("Pedro Rivas", "Pedro Soto")
    assert not names_match("Juan Pedro", "Pedro Juan Soto")
    assert not names_match("Juan Carlos", "Juan Carlos Pérez")     # a compound given name alone
    assert not names_match("Pedro Soto", "Pedro Soto Rivas")       # «Soto» may be a given name
    assert names_overlap("Pedro Soto", "Pedro Soto Rivas")
    assert names_overlap("Juan Carlos", "Juan Carlos Pérez")
    assert not names_overlap("Pedro", "Pedro Soto")


# --- strong rules -------------------------------------------------------------------------------

def test_known_address_resolves_and_adds_header_name():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", header="Juan Pedro Soto Rivas")],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("P1b", "strong")]
    assert actions[0].person_id == "p1" and actions[0].fields == {"contact_point_id": "c1"}
    assert actions[1].fields == {"name": "Juan Pedro Soto Rivas", "source": "display_name"}
    assert actions[1].reasons


def test_known_name_is_not_added_twice():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", header="Pedro Soto", sig_name="Dr. Pedro Soto")],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}},
    )
    assert rules(plan_people(snap)) == [("P1", "strong")]


def test_person_without_a_real_name_takes_the_first_one():
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", header="Pedro Soto")],
        persons=[person("p1", "pjsoto@hotmail.invalid")],
        contact_points=[cp("c1", "pjsoto@hotmail.invalid", "p1", usage="personal")],
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("P1b", "strong")]
    assert actions[1].fields == {"name": "Pedro Soto", "source": "display_name"}


def test_unrelated_name_is_never_added():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", header="Ana Díaz", sig_name="Pedro")],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}},
    )
    assert rules(plan_people(snap)) == [("P1", "strong")]


def test_new_hotmail_attaches_when_signature_names_person_and_institution():
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", header="Pedro Soto", sig_name="Juan Pedro Soto Rivas",
                 institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto")],
        affiliations={"p1": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P2", "strong")]
    assert actions[0].person_id == "p1"
    assert actions[0].fields == {"person_id": "p1", "usage": "personal", "organization_id": None}
    assert actions[0].address == "pjsoto@hotmail.invalid"


def test_new_person_from_signature():
    snap = snapshot(
        [reading("pedro.soto@ficticia.invalid", sig_name="Pedro Soto", title="Jefe de laboratorio",
                 institution="Universidad Ficticia")],
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P3", "strong")]
    assert actions[0].person_id is None
    assert actions[0].fields == {"display_name": "Pedro Soto", "organization_id": UF,
                                 "title": "Jefe de laboratorio", "usage": "work"}


def test_new_person_is_created_once_per_run():
    snap = snapshot(
        [reading("pedro.soto@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia", n=1),
         reading("pedro.soto@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia", n=2)],
    )
    assert rules(plan_people(snap)) == [("P3", "strong")]


def test_new_person_needs_two_tokens_and_one_institution():
    snap = snapshot(
        [reading("pedro@ficticia.invalid", sig_name="Pedro", institution="Universidad Ficticia", n=1),
         reading("ana.diaz@ficticia.invalid", sig_name="Ana Díaz", institution="Instituto Otro", n=2)],
    )
    # «Pedro» is one token; «Instituto Otro» by name and ficticia.invalid by domain are two orgs.
    assert plan_people(snap) == []


def test_affiliation_from_signature():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("P4", "strong")]
    assert actions[1].person_id == "p1" and actions[1].fields == {"organization_id": UF}


def test_merge_two_proposed_persons():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Juan Pedro Soto Rivas", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto", created_at=NEW), person("p2", "Juan Pedro Soto Rivas", created_at=OLD)],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}, "p2": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("P5", "strong")]
    assert actions[1].fields == {"winner_person_id": "p2", "loser_person_id": "p1"}


def test_merge_keeps_the_confirmed_person():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Juan Pedro Soto Rivas", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto", confirmation="confirmed", created_at=NEW),
                 person("p2", "Juan Pedro Soto Rivas", created_at=OLD)],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}, "p2": {UF}},
    )
    merge = [a for a in plan_people(snap) if a.rule_id == "P5"]
    assert merge[0].fields == {"winner_person_id": "p1", "loser_person_id": "p2"}


# --- weak guesses -------------------------------------------------------------------------------

def test_two_confirmed_persons_only_guess():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Juan Pedro Soto Rivas", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto", confirmation="confirmed"),
                 person("p2", "Juan Pedro Soto Rivas", confirmation="confirmed")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}, "p2": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("W1", "weak")]
    assert actions[1].person_id == "p2" and actions[1].fields == {"person_id": "p2"}
    assert any("confirmadas" in r for r in actions[1].reasons)


def test_first_name_only_is_a_guess():
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", header="Pedro")],
        persons=[person("p1", "Pedro Soto")],
        affiliations={"p1": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("W1", "weak")]
    assert actions[0].person_id == "p1"
    assert any("nombre de pila" in r for r in actions[0].reasons)


def test_same_name_other_institution_never_merges():
    # A new address signs as a María González of another institution: a guess, not an attach.
    snap = snapshot(
        [reading("mgonzalez@otra.invalid", sig_name="María González", institution="Instituto Otro")],
        persons=[person("p1", "María González")],
        affiliations={"p1": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("W1", "weak")]
    assert any("institución" in r for r in actions[0].reasons)

    # Two María González, one per institution: the known address resolves, nothing merges.
    snap = snapshot(
        [reading("mgonzalez@ficticia.invalid", sig_name="María González", institution="Universidad Ficticia")],
        persons=[person("p1", "María González"), person("p2", "María González", created_at=NEW)],
        contact_points=[cp("c1", "mgonzalez@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}, "p2": {OTRA}},
    )
    actions = plan_people(snap)
    assert "P5" not in [a.rule_id for a in actions]
    assert actions[0].rule_id == "P1"


def test_one_address_two_signature_names_never_attaches():
    snap = snapshot(
        [reading("lab.ficticia@hotmail.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia", n=1),
         reading("lab.ficticia@hotmail.invalid", sig_name="Ana Díaz", institution="Universidad Ficticia", n=2)],
        persons=[person("p1", "Pedro Soto")],
        affiliations={"p1": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("W1", "weak")]
    assert actions[0].person_id == "p1"
    assert any("dos nombres" in r for r in actions[0].reasons)


def test_guess_from_header_name_without_signature():
    snap = snapshot(
        [reading("pedro.soto@ficticia.invalid", header="Pedro Soto")],
        persons=[person("p1", "Pedro Soto")],
        affiliations={"p1": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("W1", "weak")]
    assert any("firma" in r for r in actions[0].reasons)


# --- no action ----------------------------------------------------------------------------------

def test_shared_mailbox_never_resolves():
    snap = snapshot(
        [reading("contacto@ficticia.invalid", header="Pedro Soto", sig_name="Pedro Soto",
                 institution="Universidad Ficticia", n=1),
         reading("compras@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia", n=2),
         reading("caja@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia", n=3)],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "contacto@ficticia.invalid", None, usage="shared_mailbox", organization_id=UF),
                        cp("c2", "caja@ficticia.invalid", "p1", usage="individual_owner_unknown")],
        affiliations={"p1": {UF}},
    )
    assert plan_people(snap) == []


def test_own_domain_and_no_name_do_nothing():
    snap = snapshot(
        [reading("ventas@origenlab.cl", sig_name="Pedro Soto", institution="Universidad Ficticia", n=1),
         reading("x@labdelivery.cl", sig_name="Pedro Soto", institution="Universidad Ficticia", n=2),
         reading("pedro.soto@ficticia.invalid", institution="Universidad Ficticia", n=3)],
        persons=[person("p1", "Pedro Soto")],
        affiliations={"p1": {UF}},
    )
    assert plan_people(snap) == []


def test_an_address_only_reading_plans_nothing_even_on_a_known_address():
    # `people-backfill` keeps a reading with only the address when the model found nothing (R12).
    snap = snapshot(
        [reading("pedro.soto@ficticia.invalid", n=1), reading("nuevo@hotmail.invalid", n=2)],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "pedro.soto@ficticia.invalid", "p1", organization_id=UF)],
        affiliations={"p1": {UF}},
    )
    assert plan_people(snap) == []


def test_free_mail_domain_never_names_institution():
    # Even a bad exclusive-domain row for a free-mail domain never gives the institution.
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", sig_name="Pedro Soto"),
         reading("ana.diaz@gmail.com", sig_name="Ana Díaz", n=2)],
        org_by_domain={"hotmail.invalid": UF, "gmail.com": UF},
    )
    assert plan_people(snap) == []

    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", sig_name="Pedro Soto")],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "pjsoto@hotmail.invalid", "p1", usage="personal")],
        org_by_domain={"hotmail.invalid": UF},
    )
    assert rules(plan_people(snap)) == [("P1", "strong")]   # no P4 from the domain


def test_answered_no_is_not_asked_again():
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", header="Pedro")],
        persons=[person("p1", "Pedro Soto")],
        affiliations={"p1": {UF}},
        answered={("pjsoto@hotmail.invalid", "p1"): False},
    )
    assert plan_people(snap) == []

    # A «No» also stops the strong attach for that address and person.
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto")],
        affiliations={"p1": {UF}},
        answered={("pjsoto@hotmail.invalid", "p1"): False},
    )
    assert plan_people(snap) == []


def test_merged_or_archived_persons_are_not_candidates():
    snap = snapshot(
        [reading("pedro.soto@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto", merged_into="p9"), person("p2", "Pedro Soto", status="archived")],
    )
    assert rules(plan_people(snap)) == [("P3", "strong")]


# --- review probes (A–F): the known-address branch and partial names -----------------------------

def test_probe_a_compound_given_name_never_merges():
    snap = snapshot(
        [reading("jcperez@ficticia.invalid", sig_name="Juan Carlos", institution="Universidad Ficticia")],
        persons=[person("p1", "Juan Carlos Pérez"), person("p2", "Juan Carlos Rojas", created_at=NEW)],
        contact_points=[cp("c1", "jcperez@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}, "p2": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("W1", "weak")]
    assert actions[1].person_id == "p2"


def test_probe_b_two_maternal_surnames_never_merge():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto Muñoz"), person("p2", "Pedro Soto Rivas", created_at=NEW)],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}, "p2": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("W1", "weak")]
    assert any("misma persona" in r for r in actions[1].reasons)


def test_probe_c_two_names_on_a_known_address_add_no_alias():
    snap = snapshot(
        [reading("lab.ficticia@hotmail.invalid", header="Pedro Soto", n=1),
         reading("lab.ficticia@hotmail.invalid", header="Ana Díaz", n=2)],
        persons=[person("p1", "lab.ficticia@hotmail.invalid")],
        contact_points=[cp("c1", "lab.ficticia@hotmail.invalid", "p1", usage="personal")],
    )
    assert rules(plan_people(snap)) == [("P1", "strong"), ("P1", "strong")]


def test_probe_c_no_name_yet_takes_one_alias_per_run():
    # One person without a real name owns two addresses, each signing one name: the two-names
    # rule does not see it (one name per address), yet only the first name becomes an alias.
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", header="Pedro Soto", n=1),
         reading("otra@hotmail.invalid", header="Ana Díaz", n=2)],
        persons=[person("p1", "pjsoto@hotmail.invalid")],
        contact_points=[cp("c1", "pjsoto@hotmail.invalid", "p1", usage="personal"),
                        cp("c2", "otra@hotmail.invalid", "p1", usage="personal")],
    )
    actions = plan_people(snap)
    assert [a.fields.get("name") for a in actions if a.rule_id == "P1b"] == ["Pedro Soto"]


def test_one_token_display_name_takes_only_a_matching_first_name():
    snap = snapshot(
        [reading("pedro@ficticia.invalid", header="Ana Díaz", n=1)],
        persons=[person("p1", "Pedro")],
        contact_points=[cp("c1", "pedro@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}},
    )
    assert rules(plan_people(snap)) == [("P1", "strong")]


def test_probe_d_two_names_on_a_known_address_never_merge():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Juan Pedro Soto Rivas", institution="Universidad Ficticia", n=1),
         reading("psoto@ficticia.invalid", sig_name="Ana Díaz", institution="Universidad Ficticia", n=2)],
        persons=[person("p1", "Pedro Soto", created_at=NEW), person("p2", "Juan Pedro Soto Rivas")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
        affiliations={"p1": {UF}, "p2": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("P1", "strong"), ("W1", "weak"), ("P1", "strong")]
    assert any("dos nombres" in r for r in actions[1].reasons)


def test_probe_e_someone_elses_signature_gives_no_affiliation():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Ana Díaz", institution="Instituto Otro")],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
    )
    assert rules(plan_people(snap)) == [("P1", "strong")]


def test_probe_e_two_signers_give_no_affiliation():
    snap = snapshot(
        [reading("psoto@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia", n=1),
         reading("psoto@ficticia.invalid", sig_name="Ana Díaz", n=2)],
        persons=[person("p1", "Pedro Soto")],
        contact_points=[cp("c1", "psoto@ficticia.invalid", "p1")],
    )
    assert "P4" not in [a.rule_id for a in plan_people(snap)]


def test_probe_f_compound_given_name_only_guesses():
    snap = snapshot(
        [reading("jcarlos@hotmail.invalid", sig_name="Juan Carlos", institution="Universidad Ficticia")],
        persons=[person("p1", "Juan Carlos Pérez")],
        affiliations={"p1": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("W1", "weak")]
    assert actions[0].person_id == "p1"
    assert any("solo en parte" in r for r in actions[0].reasons)


def test_partial_name_blocks_a_new_person():
    # «Pedro Soto» may be «Pedro Soto Rivas»: no new person, only a guess.
    snap = snapshot(
        [reading("pedro.soto@ficticia.invalid", sig_name="Pedro Soto", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto Rivas")],
        affiliations={"p1": {UF}},
    )
    assert rules(plan_people(snap)) == [("W1", "weak")]


def test_attach_needs_one_person_even_when_another_is_partly_named():
    snap = snapshot(
        [reading("pjsoto@hotmail.invalid", sig_name="Pedro Soto Rivas", institution="Universidad Ficticia")],
        persons=[person("p1", "Pedro Soto Rivas"), person("p2", "Pedro Soto")],
        affiliations={"p1": {UF}, "p2": {UF}},
    )
    actions = plan_people(snap)
    assert rules(actions) == [("W1", "weak")]
    assert actions[0].person_id == "p1"
    assert any("parecido" in r for r in actions[0].reasons)
