"""Who wrote each email: plan identity actions from `people:v1` readings (people first, Task 4).

Pure planning — no database, no clock. The caller builds a `PeopleSnapshot` from the readings the
worker wrote and the current CRM identity rows; `plan_people` returns what to do:

- **strong** actions the system applies by itself (logged, undoable with «Quitar»): P1 resolve a
  known address, P1b add a name, P2 attach a new address, P3 create a person, P4 add an
  affiliation, P5 merge two persons;
- **weak** actions shown as a «¿Es …?» guess with Sí/No (W1).

Rules are evaluated per reading in that order and the first branch that applies wins: a known
address takes the P1 branch (P1, then P1b/P4/P5 or W1 as they apply); an unknown address tries P2,
then P3, then W1. Shared mailboxes, own domains and readings without a name get nothing.

Everything doubtful is weak or nothing: a strong action needs the name *and* the institution to
agree, and never comes from a free-mail domain, a shared mailbox, or an address that signs with
two different names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from origenlab_api.v2.mail_rules import FREE_MAIL_DOMAINS, fold
from origenlab_api.v2.person_suggestions import GENERIC_LOCAL_PARTS, OWN_DOMAINS
from origenlab_api.v2.person_suggestions import fold as fold_compact

#: Courtesy titles dropped from a name before comparing («Dra. María González» = «María González»).
TITLES = frozenset({"dr", "dra", "prof", "sr", "sra", "ing"})

#: Free-mail providers recognised by their first label on any top-level domain («hotmail.cl»,
#: «hotmail.co.uk»), on top of the exact `FREE_MAIL_DOMAINS`.
FREE_MAIL_PROVIDERS = frozenset({
    "gmail", "googlemail", "hotmail", "outlook", "live", "msn", "yahoo", "icloud", "aol", "proton",
    "protonmail", "gmx", "zoho", "yandex",
})

#: Contact-point usages that never resolve to one person by address alone.
SHARED_USAGES = frozenset({"shared_mailbox", "individual_owner_unknown"})

RULE_IDS = ("P1", "P1b", "P2", "P3", "P4", "P5", "W1")

Tier = Literal["strong", "weak"]


@dataclass(frozen=True)
class Reading:
    assertion_id: str
    source_record_id: str
    message_participant_ids: tuple[str, ...]
    value: dict[str, Any]


@dataclass(frozen=True)
class PersonRow:
    id: str
    display_name: str
    confirmation: str            # machine_proposed | confirmed
    status: str                  # active | archived
    merged_into: str | None
    created_at: datetime | None = None   # decides the older person in a merge


@dataclass(frozen=True)
class ContactPointRow:
    id: str
    value_norm: str
    person_id: str | None
    organization_id: str | None
    usage: str
    status: str


@dataclass(frozen=True)
class OrgRow:
    id: str
    name: str
    status: str


@dataclass(frozen=True)
class PeopleSnapshot:
    readings: tuple[Reading, ...]
    persons: dict[str, PersonRow]
    names: dict[str, set[str]]                    # name_norm -> person ids (display names and aliases)
    contact_points: dict[str, ContactPointRow]    # value_norm -> row
    affiliations: dict[str, set[str]]             # person id -> org ids (open)
    organizations: dict[str, OrgRow]
    org_by_name: dict[str, set[str]]              # org_key(name) -> org ids
    org_by_domain: dict[str, str]                 # exclusive, non-free domain -> org id
    answered: dict[tuple[str, str], bool]         # (address, person id) -> Sí/No


@dataclass(frozen=True)
class PeopleAction:
    rule_id: str
    tier: Tier
    assertion_id: str
    address: str
    person_id: str | None
    fields: dict[str, Any]
    reasons: tuple[str, ...]


# --- names --------------------------------------------------------------------------------------

def name_norm(name: str) -> str:
    """Folded, punctuation as spaces, spaces collapsed, courtesy titles dropped."""
    words = re.sub(r"[^a-z0-9]+", " ", fold(name)).split()
    return " ".join(w for w in words if w not in TITLES)


def org_key(name: str) -> str:
    """The `org_by_name` key of an institution name (folded, spaces collapsed)."""
    return fold(name)


def _surname_tokens(tokens: list[str]) -> set[str]:
    """Tokens that may be a surname: the last two, never the first (a given name)."""
    return set(tokens[1:][-2:])


def names_match(a: str, b: str) -> bool:
    """Whether two names are the same person's: equal, or one's tokens inside the other's.

    At least two shared tokens and one of them a surname of the longer name («Pedro Soto» ⊆ «Juan
    Pedro Soto Rivas»). A single shared given name never matches.
    """
    ta, tb = name_norm(a).split(), name_norm(b).split()
    if len(ta) < 2 or len(tb) < 2:
        return False
    if ta == tb or set(ta) == set(tb):
        return True
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if not set(short) <= set(long_):
        return False
    return short[-1] in _surname_tokens(long_)


# --- addresses and institutions ----------------------------------------------------------------

def _domain(address: str) -> str:
    return address.rpartition("@")[2]


def is_free_mail(domain: str) -> bool:
    labels = domain.split(".")
    if domain in FREE_MAIL_DOMAINS:
        return True
    if labels[0] not in FREE_MAIL_PROVIDERS:
        return False
    return len(labels) == 2 or (len(labels) == 3 and labels[1] in {"com", "co"})


def _is_own(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in OWN_DOMAINS)


def _is_generic_mailbox(address: str) -> bool:
    local = address.partition("@")[0].split("+")[0]
    return fold_compact(local) in GENERIC_LOCAL_PARTS


# --- the planner --------------------------------------------------------------------------------

class _Planner:
    def __init__(self, snap: PeopleSnapshot) -> None:
        self.snap = snap
        self.actions: list[PeopleAction] = []
        self.person_names: dict[str, set[str]] = {}
        for norm, ids in snap.names.items():
            for pid in ids:
                self.person_names.setdefault(pid, set()).add(norm)
        self.names_by_address = self._names_by_address()
        # Once per run: an address gets one attach/create, a person one affiliation, a name one alias.
        self.planned_addresses: set[str] = set()
        self.planned_new_names: set[str] = set()
        self.planned_aliases: set[tuple[str, str]] = set()
        self.planned_affiliations: set[str] = set()
        self.planned_merges: set[frozenset[str]] = set()
        self.planned_guesses: set[tuple[str, str]] = set()

    # helpers ---------------------------------------------------------------------------------
    def _names_by_address(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.snap.readings:
            address = (r.value.get("sender_address") or "").strip().lower()
            for name in (r.value.get("header_name"), (r.value.get("signature") or {}).get("name")):
                if name and name_norm(name):
                    out.setdefault(address, []).append(name)
        return out

    def two_names(self, address: str) -> bool:
        """Whether the address signs with two names that are not the same person's."""
        names = self.names_by_address.get(address, [])
        return any(name_norm(a) != name_norm(b) and not names_match(a, b)
                   for i, a in enumerate(names) for b in names[i + 1:])

    def live(self, pid: str) -> PersonRow | None:
        p = self.snap.persons.get(pid)
        if p is None or p.status != "active" or p.merged_into is not None:
            return None
        return p

    def names_of(self, pid: str) -> set[str]:
        p = self.snap.persons[pid]
        return self.person_names.get(pid, set()) | {name_norm(p.display_name)}

    def matching_persons(self, name: str) -> list[str]:
        return sorted(pid for pid in self.snap.persons if self.live(pid)
                      and any(names_match(name, n) for n in self.names_of(pid)))

    def first_name_persons(self, name: str) -> list[str]:
        tokens = name_norm(name).split()
        if len(tokens) != 1:
            return []
        return sorted(pid for pid in self.snap.persons if self.live(pid)
                      and any(n.split()[:1] == tokens for n in self.names_of(pid)))

    def institution_orgs(self, address: str, institution: str | None) -> set[str]:
        """Orgs the signature institution resolves to: exact name, or the exclusive domain."""
        if not institution or not institution.strip():
            return set()
        orgs = set(self.snap.org_by_name.get(org_key(institution), set()))
        domain = _domain(address)
        if not is_free_mail(domain) and domain in self.snap.org_by_domain:
            orgs.add(self.snap.org_by_domain[domain])
        return {o for o in orgs if (row := self.snap.organizations.get(o)) is not None and row.status == "active"}

    def affiliated(self, pid: str) -> set[str]:
        return set(self.snap.affiliations.get(pid, set()))

    def emit(self, rule_id: str, tier: Tier, r: Reading, address: str, person_id: str | None,
             fields: dict[str, Any], *reasons: str) -> None:
        self.actions.append(PeopleAction(rule_id=rule_id, tier=tier, assertion_id=r.assertion_id,
                                         address=address, person_id=person_id, fields=fields,
                                         reasons=tuple(reasons)))

    def guess(self, r: Reading, address: str, pid: str, *reasons: str) -> None:
        if (address, pid) in self.snap.answered or (address, pid) in self.planned_guesses:
            return
        self.planned_guesses.add((address, pid))
        co = {(a or "").strip().lower() for a in r.value.get("co_addresses") or []}
        if any((c := self.snap.contact_points.get(a)) is not None and c.person_id == pid for a in co):
            reasons = (*reasons, "esa persona venía en copia del mismo correo")
        self.emit("W1", "weak", r, address, pid, {"person_id": pid}, *reasons)

    # one reading -----------------------------------------------------------------------------
    def plan(self, r: Reading) -> None:
        value = r.value
        address = (value.get("sender_address") or "").strip().lower()
        if not address or "@" not in address or _is_own(_domain(address)):
            return
        signature = value.get("signature") or {}
        header_name = (value.get("header_name") or "").strip() or None
        sig_name = (signature.get("name") or "").strip() or None
        if not (header_name and name_norm(header_name)) and not (sig_name and name_norm(sig_name)):
            return
        institution = (signature.get("institution") or "").strip() or None
        cp = self.snap.contact_points.get(address)
        if cp is not None:
            if cp.usage in SHARED_USAGES:
                return
            if cp.person_id is not None and cp.status == "active" and self.live(cp.person_id):
                self.known(r, address, cp, header_name, sig_name, institution)
            return  # an inactive, orphan or merged row: no strong rule; leave it to an operator
        if _is_generic_mailbox(address):
            return
        self.unknown(r, address, header_name, sig_name, signature, institution)

    def known(self, r: Reading, address: str, cp: ContactPointRow, header_name: str | None,
              sig_name: str | None, institution: str | None) -> None:
        pid = cp.person_id
        assert pid is not None
        self.emit("P1", "strong", r, address, pid, {"contact_point_id": cp.id},
                  "esta dirección ya es de esta persona")
        orgs = self.institution_orgs(address, institution)

        # P5 / W1: the signature names another live person.
        merging = False
        if sig_name:
            others = [o for o in self.matching_persons(sig_name) if o != pid]
            if len(others) == 1:
                merging = self.other_person(r, address, pid, others[0], sig_name, orgs)

        # P1b: names this person is not yet known by.
        if not merging:
            for name, source in ((header_name, "display_name"), (sig_name, "signature")):
                if name:
                    self.add_name(r, address, pid, name, source)

        # P4: a person with no open affiliation, and a signature naming exactly one institution.
        if not self.affiliated(pid) and len(orgs) == 1 and pid not in self.planned_affiliations:
            self.planned_affiliations.add(pid)
            org = next(iter(orgs))
            self.emit("P4", "strong", r, address, pid, {"organization_id": org},
                      f"la firma dice «{institution}» y la persona no tenía institución")

    def add_name(self, r: Reading, address: str, pid: str, name: str, source: str) -> None:
        norm = name_norm(name)
        if len(norm.split()) < 2 or norm in self.names_of(pid) or (pid, norm) in self.planned_aliases:
            return
        if any(o != pid for o in self.matching_persons(name)):
            return  # the name is also another person's: never an alias by itself (P5 or W1 decide)
        p = self.snap.persons[pid]
        if any(names_match(name, n) for n in self.names_of(pid)):
            reason = f"«{name}» es otra forma del nombre «{p.display_name}»"
        elif (not self.person_names.get(pid, set()) - {name_norm(p.display_name)}
              and ("@" in p.display_name or len(name_norm(p.display_name).split()) < 2)):
            reason = f"la persona aún no tiene nombre (solo «{p.display_name}»)"
        else:
            return
        self.planned_aliases.add((pid, norm))
        where = "el remitente del correo" if source == "display_name" else "la firma"
        self.emit("P1b", "strong", r, address, pid, {"name": name, "source": source}, reason,
                  f"el nombre viene de {where}")

    def other_person(self, r: Reading, address: str, pid: str, other: str, sig_name: str,
                     orgs: set[str]) -> bool:
        """P5 when the signature names another person of the same institution; W1 otherwise."""
        if (address, other) in self.snap.answered and self.snap.answered[(address, other)] is False:
            return False
        p, q = self.snap.persons[pid], self.snap.persons[other]
        reasons: list[str] = []
        if not any(names_match(sig_name, n) for n in self.names_of(pid)):
            reasons.append(f"la dirección ya es de {p.display_name}, pero la firma dice «{sig_name}»")
        shared = self.affiliated(pid) & self.affiliated(other)
        if not orgs:
            reasons.append("la firma no dice una institución que esté en el CRM")
        elif len(orgs) > 1:
            reasons.append("la institución de la firma coincide con más de una institución")
        elif not (orgs & shared):
            reasons.append("las dos personas no son de la institución de la firma")
        if p.confirmation == "confirmed" and q.confirmation == "confirmed":
            reasons.append("las dos personas están confirmadas; solo una persona puede unirlas")
        if reasons:
            self.guess(r, address, other, *reasons)
            return False
        pair = frozenset({pid, other})
        if pair in self.planned_merges:
            return True
        self.planned_merges.add(pair)
        winner, loser = _winner(p, q)
        self.emit("P5", "strong", r, address, winner.id,
                  {"winner_person_id": winner.id, "loser_person_id": loser.id},
                  f"la firma «{sig_name}» y la dirección de {p.display_name} son de la misma persona",
                  "las dos están en la misma institución",
                  f"se conserva {winner.display_name} ({'confirmada' if winner.confirmation == 'confirmed' else 'la más antigua'})")
        return True

    def unknown(self, r: Reading, address: str, header_name: str | None, sig_name: str | None,
                signature: dict[str, Any], institution: str | None) -> None:
        if address in self.planned_addresses:
            return
        free = is_free_mail(_domain(address))
        usage = "personal" if free else "work"
        two_names = self.two_names(address)
        orgs = self.institution_orgs(address, institution)
        name = sig_name or header_name
        assert name is not None
        matches = self.matching_persons(name)

        # P2: the signature names exactly one person of the institution it gives.
        if sig_name and len(matches) == 1 and not two_names and len(orgs) == 1:
            pid = matches[0]
            if orgs <= self.affiliated(pid) and self.snap.answered.get((address, pid)) is not False:
                self.planned_addresses.add(address)
                self.emit("P2", "strong", r, address, pid, {"person_id": pid, "usage": usage},
                          f"la firma dice «{sig_name}» de «{institution}»",
                          f"{self.snap.persons[pid].display_name} ya es de esa institución")
                return

        # P3: a full name nobody has, and an institution that is exactly one org.
        norm = name_norm(sig_name or "")
        if (sig_name and len(norm.split()) >= 2 and not matches and not two_names and len(orgs) == 1
                and norm not in self.planned_new_names):
            self.planned_addresses.add(address)
            self.planned_new_names.add(norm)
            org = next(iter(orgs))
            self.emit("P3", "strong", r, address, None,
                      {"display_name": sig_name, "organization_id": org,
                       "title": (signature.get("title") or "").strip() or None, "usage": usage},
                      f"la firma dice «{sig_name}» de «{institution}»",
                      "no hay otra persona con ese nombre")
            return

        # W1: a name points to exactly one person, but something strong is missing.
        reasons: list[str] = []
        if len(matches) == 1:
            pid = matches[0]
        else:
            loose = self.first_name_persons(name) if not matches else []
            if len(loose) != 1:
                return
            pid = loose[0]
            reasons.append(f"solo coincide el nombre de pila «{name}»")
        if not sig_name:
            reasons.append("el correo no trae firma con nombre; solo el nombre del remitente")
        if two_names:
            reasons.append("esta dirección firma con dos nombres distintos")
        if not institution:
            if sig_name:
                reasons.append("la firma no dice la institución")
        elif not orgs:
            reasons.append(f"la institución «{institution}» no está en el CRM")
        elif len(orgs) > 1:
            reasons.append(f"«{institution}» coincide con más de una institución")
        elif not orgs <= self.affiliated(pid):
            reasons.append(f"la persona no es de «{institution}»; es otra institución")
        if not reasons:
            reasons.append("la coincidencia no basta para asignarla sola")
        self.guess(r, address, pid, *reasons)


def _winner(p: PersonRow, q: PersonRow) -> tuple[PersonRow, PersonRow]:
    """(winner, loser): the confirmed one, else the older one (ties: the address owner `p`)."""
    if p.confirmation == "confirmed" and q.confirmation != "confirmed":
        return p, q
    if q.confirmation == "confirmed" and p.confirmation != "confirmed":
        return q, p
    if p.created_at is not None and q.created_at is not None and q.created_at < p.created_at:
        return q, p
    return p, q


def plan_people(snapshot: PeopleSnapshot) -> list[PeopleAction]:
    """The identity actions for every reading in the snapshot, in reading order."""
    planner = _Planner(snapshot)
    for r in snapshot.readings:
        planner.plan(r)
    return planner.actions


__all__ = ["ContactPointRow", "FREE_MAIL_PROVIDERS", "OrgRow", "PeopleAction", "PeopleSnapshot",
           "PersonRow", "RULE_IDS", "Reading", "SHARED_USAGES", "TITLES", "is_free_mail", "name_norm",
           "names_match", "org_key", "plan_people"]
