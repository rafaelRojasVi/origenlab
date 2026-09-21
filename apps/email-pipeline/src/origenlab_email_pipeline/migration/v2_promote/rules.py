"""The conservative identity defaults, as pure functions.

Nothing here touches a database, a file or a clock. Every decision the promotion makes
about a real person or organization is taken by a function in this module, so the whole
policy can be read in one place and tested without a database.

The defaults are the operator's, recorded verbatim:

* a named personal address with direct commercial evidence is a **person**, and a public
  domain implies **no organization**;
* a role address — ``ventas@``, ``contacto@``, ``compras@``, ``info@`` and their kin — is an
  **organization or shared mailbox**, never a person;
* multiple addresses, or similar names, are **never merged automatically**;
* **unknown remains unknown**;
* **ambiguity goes to review**;
* **email presence is never marketing permission.**

Two findings from the evidence constrain what those defaults can actually produce, and both
are load-bearing:

1. **No person name exists in the migration evidence.** The Wave 1A/1B recipient rows carry
   ``email``, ``email_norm`` and ``institution_name`` and nothing else. ``crm.person``
   requires a ``display_name``, so creating a person would mean deriving a real human's name
   from an address local part. That is an identity inference, not evidence, so this module
   creates **no person at all** and says so in the reason it records.

2. **A domain is never an identity key on its own** (``docs/DOMAIN.md`` §2.2). So a role
   address is never attached to an organization because it shares that organization's email
   domain. It is promoted as a shared mailbox with no owner, and the owner is an operator
   decision.

What is promoted is therefore the **channel, and only the channel** (``docs/DOMAIN.md``
§2.5) — plus organizations, whose names *are* recorded observations.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

#: Local parts that denote a role or shared mailbox rather than a natural person.
#:
#: A closed, reviewed list rather than a pattern. A heuristic like "contains no dot, so it is
#: a role address" would classify a real person's ``jrojas@`` as a shared mailbox and a role
#: ``ventas.equipos@`` as a person — both of them identity errors, in opposite directions.
#: An exact match against a list somebody read is wrong less often, and when it is wrong it
#: is wrong in the safe direction: an unrecognised local part becomes `unattributed`, which
#: claims nothing about anybody.
#:
#: Adding a local part here is a business decision and belongs in review, not in a regex.
ROLE_LOCAL_PARTS: Final[frozenset[str]] = frozenset(
    {
        # named explicitly by the operator
        "ventas",
        "contacto",
        "compras",
        "info",
        # commercial
        "comercial",
        "sales",
        "contact",
        "cotizaciones",
        "cotizacion",
        "presupuestos",
        "licitaciones",
        "adquisiciones",
        "abastecimiento",
        "proveedores",
        "postventa",
        "repuestos",
        # administrative
        "administracion",
        "admin",
        "gerencia",
        "secretaria",
        "recepcion",
        "oficina",
        "office",
        "contabilidad",
        "finanzas",
        "facturacion",
        "pagos",
        "tesoreria",
        "cobranzas",
        # operational
        "logistica",
        "bodega",
        "despacho",
        "importaciones",
        "operaciones",
        "mantencion",
        "mantenimiento",
        "servicio",
        "servicioalcliente",
        "atencionclientes",
        "soporte",
        "support",
        "calidad",
        "proyectos",
        "ingenieria",
        # generic
        "hello",
        "hola",
        "consultas",
        "reclamos",
        "webmaster",
        "postmaster",
        "noreply",
        "no-reply",
        "donotreply",
        "mailer",
        "mail",
        "correo",
    }
)

#: Public mail domains. A named personal address on one of these implies **no organization** —
#: the operator's rule. This module never attaches an organization to any address, so the set
#: is used to *record* that fact in the promotion reason rather than to change the outcome.
#: It matters for review: a personal address on a corporate domain is a different question
#: for an operator than the same address on gmail.com.
PUBLIC_MAIL_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "hotmail.cl",
        "hotmail.es",
        "outlook.com",
        "outlook.cl",
        "outlook.es",
        "live.cl",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.es",
        "yahoo.cl",
        "icloud.com",
        "me.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "zoho.com",
        "gmx.com",
        "mail.com",
        "vtr.net",
        "entelchile.net",
        "movistar.cl",
    }
)

#: Legal-form suffixes stripped when testing whether two organization names are the *same*
#: name written differently. Stripping them never merges anything — it only decides which
#: names are similar enough that a human must look.
_LEGAL_SUFFIXES: Final[tuple[str, ...]] = (
    "ltda",
    "limitada",
    "sa",
    "s a",
    "spa",
    "eirl",
    "e i r l",
    "inc",
    "corp",
    "srl",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class PromotionRefused(Exception):
    """A value cannot be classified at all, and must not be guessed at."""


@dataclass(frozen=True)
class AddressDecision:
    """What promotion does with one contacted address."""

    #: `crm.contact_point.usage` — `shared_mailbox` or `unattributed`. Never `personal` or
    #: `work`: both require a `person_id`, and no person can be created from this evidence.
    usage: str
    #: True when the local part is a recognised role mailbox.
    is_role_mailbox: bool
    #: True when the domain is a public mail provider.
    is_public_domain: bool
    #: True when the local part looks like a human name (`given.family`). Recorded so review
    #: can be prioritised; it never creates a person.
    looks_personal: bool
    #: Why this decision was taken, in one sentence, stored on the assertion.
    reason: str


def split_address(value_norm: str) -> tuple[str, str]:
    """Split a normalized address into (local part, domain).

    Raises:
        PromotionRefused: the value is not a single ``local@domain`` pair. The import has
            already normalized and shape-checked these, so anything failing here is a real
            anomaly and is sent to review rather than parsed harder.
    """
    parts = value_norm.split("@")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise PromotionRefused(f"not a single local@domain pair: {len(parts) - 1} '@' found")
    return parts[0], parts[1]


def looks_like_person_name(local_part: str) -> bool:
    """True when a local part has the shape of a human name.

    ``given.family``, ``g.family``, ``given_family`` and ``given-family`` all qualify. This
    is a *review-prioritisation* signal only: a true result never creates a person, and a
    false result never rules one out.
    """
    if any(ch.isdigit() for ch in local_part):
        return False
    return bool(re.fullmatch(r"[a-z]+[._-][a-z]+(?:[._-][a-z]+)?", local_part))


def decide_address(value_norm: str) -> AddressDecision:
    """Apply the conservative defaults to one contacted address.

    The outcome is always a contact point and never a person, for the reason given in the
    module docstring. The two shapes differ in what they *claim*:

    * ``shared_mailbox`` says "this channel belongs to an organization, not a person" —
      which a role local part is direct evidence of;
    * ``unattributed`` says "this channel exists and we do not know whose it is" — which is
      the honest reading of everything else.
    """
    local, domain = split_address(value_norm)
    is_role = local in ROLE_LOCAL_PARTS
    is_public = domain in PUBLIC_MAIL_DOMAINS
    personal_shape = looks_like_person_name(local)

    if is_role:
        if is_public:
            # A role local part on a public provider is a shared mailbox that tells us
            # nothing about an organization — exactly the case the operator's rule covers by
            # saying a public domain implies no organization.
            reason = (
                "role local part on a public mail domain: a shared mailbox, and the domain "
                "implies no organization"
            )
        else:
            reason = (
                "role local part: an organization or shared mailbox, never a person; no "
                "organization is attached because a domain is never an identity key "
                "(DOMAIN.md 2.2)"
            )
        return AddressDecision(
            usage="shared_mailbox",
            is_role_mailbox=True,
            is_public_domain=is_public,
            looks_personal=False,
            reason=reason,
        )

    if personal_shape:
        reason = (
            "personal-shaped local part, but the migration evidence records no display name "
            "for this person; a person is not created from an address, so the channel is "
            "recorded unattributed and the identity goes to review"
        )
    else:
        reason = (
            "local part matches no known role mailbox and does not have the shape of a "
            "name: unknown remains unknown, so the channel is recorded unattributed"
        )
    return AddressDecision(
        usage="unattributed",
        is_role_mailbox=False,
        is_public_domain=is_public,
        looks_personal=personal_shape,
        reason=reason,
    )


def organization_similarity_key(name: str) -> str:
    """Fold an organization name to the key used to detect *similar* names.

    Case, accents, punctuation, spacing and a trailing legal form are all removed. Two names
    sharing a key are **never merged** — they are routed to review together, which is the
    whole purpose of computing it.

    Raises:
        PromotionRefused: the name folds away to nothing.
    """
    folded = unicodedata.normalize("NFKD", name.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _NON_ALNUM.sub(" ", folded).strip()
    for suffix in _LEGAL_SUFFIXES:
        if folded.endswith(" " + suffix):
            folded = folded[: -(len(suffix) + 1)].strip()
            break
    folded = " ".join(folded.split())
    if not folded:
        raise PromotionRefused("organization name folds away to an empty key")
    return folded


#: The `kind` recorded for an organization promoted from a bare observed name.
#:
#: `docs/DOMAIN.md` §2.1 leaves the closed list of organization kinds **[OPEN]**, and the
#: evidence carries no kind at all — only a name somebody typed into a V1 campaign. `unknown`
#: is an explicit absence marker, not a guess: it says the kind has not been determined,
#: which is true, and it leaves every row visible to the operator who will decide. It is a
#: deliberate, reviewed choice and is recorded as such in docs/STATUS.md.
UNKNOWN_ORGANIZATION_KIND: Final[str] = "unknown"
