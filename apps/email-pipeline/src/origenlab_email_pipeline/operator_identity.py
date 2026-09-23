"""Who the operator is, as configuration rather than as a literal in a public tree.

``github.com/rafaelRojasVi/origenlab`` is public, and two of the operator's own mailboxes are
personal Gmail addresses. They are also **business-rule keys**: the warm-case classifier
decides "internal admin note" versus "client thread" by comparing a sender against them
exactly (:func:`~origenlab_email_pipeline.warm_case_sender_rules.looks_like_internal_admin_thread`).
That is why they could not simply be deleted -- removing them changes what the pipeline
classifies, which is a business change, not a privacy edit.

So the rule keeps its shape and loses the literal. The classifier branches on a **role**
(``payments_operator``, ``commercial_operator``); this module answers which address currently
holds that role. The answer comes from a JSON file that lives outside Git, next to the other
inputs the repository deliberately does not carry (the Wave bundles, the Gmail manifests).

**The shipped default is fictitious.** Nothing here has a real address baked in, and a
checkout with no configuration classifies against ``@example.invalid`` mailboxes -- reserved
by RFC 2606 and undeliverable. That makes the failure mode visible rather than silent: an
unconfigured run simply never matches those two rules, and :attr:`OperatorIdentity.is_configured`
says so out loud.

**Absence and malformation are different.** No file at all is the documented default and is
accepted. A file that was *named* and is missing, or is present and unreadable, is refused --
a typo in a path must not look like a working configuration.

Configure it by copying ``config/operator_identity.example.json`` to
``~/data/origenlab-v2-local/operator_identity.json`` and filling in the real addresses, or by
pointing ``ORIGENLAB_OPERATOR_IDENTITY_FILE`` at a file elsewhere.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

#: Environment variable naming the identity file. Set it and the default path is not consulted.
OPERATOR_IDENTITY_ENV_VAR = "ORIGENLAB_OPERATOR_IDENTITY_FILE"

#: Where the file lives when the environment does not say otherwise. Outside the working tree
#: by design, beside the other inputs Git does not carry.
DEFAULT_OPERATOR_IDENTITY_PATH = (
    Path.home() / "data" / "origenlab-v2-local" / "operator_identity.json"
)

#: The closed set of roles a rule may branch on. A file naming anything else is refused rather
#: than ignored: an unknown key is far more likely to be a typo in a real deployment than a
#: deliberate extension, and silently dropping it would disable a rule without saying so.
OPERATOR_ROLES: tuple[str, ...] = ("payments_operator", "commercial_operator")

# Deliberately loose: this validates that a configured value is address-shaped, not that it is
# deliverable. Refusing a legitimate address would be worse than accepting an odd one.
_ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OperatorIdentityError(RuntimeError):
    """The identity file was named but could not be used. Never raised for "no file"."""


@dataclass(frozen=True)
class OperatorIdentity:
    """The addresses behind the operator roles, plus whether any of them are real.

    ``role_addresses`` is a read-only mapping so that a caller cannot reach in and change what
    a rule matches at runtime: identity is loaded once and then is a fact, not a setting.
    """

    role_addresses: Mapping[str, str]
    #: ``False`` when every address is still the shipped fictitious one. Callers that care
    #: whether the two exact-value rules can fire at all read this rather than guessing.
    is_configured: bool

    def address_for(self, role: str) -> str:
        return self.role_addresses[role]

    @property
    def personal_addresses(self) -> frozenset[str]:
        """Every role address, as the classifier's membership test wants it."""
        return frozenset(self.role_addresses.values())


def _fictitious() -> OperatorIdentity:
    return OperatorIdentity(
        role_addresses=MappingProxyType(
            {
                "payments_operator": "operador.pagos@example.invalid",
                "commercial_operator": "operadora.comercial@example.invalid",
            }
        ),
        is_configured=False,
    )


#: The default every unconfigured checkout, test run and CI job uses.
FICTITIOUS_OPERATOR_IDENTITY: OperatorIdentity = _fictitious()


def _read(path: Path) -> object:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OperatorIdentityError(
            f"no se pudo leer el archivo de identidad del operador ({path}): {exc}"
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OperatorIdentityError(
            f"el archivo de identidad del operador ({path}) no es JSON válido: {exc}"
        ) from exc


def _roles_of(document: object, path: Path) -> Mapping[str, str]:
    if not isinstance(document, dict):
        raise OperatorIdentityError(
            f"el archivo de identidad del operador ({path}) debe ser un objeto JSON."
        )
    roles = document.get("roles")
    if not isinstance(roles, dict):
        raise OperatorIdentityError(
            f"el archivo de identidad del operador ({path}) debe tener un objeto «roles»."
        )
    for role, address in roles.items():
        if role not in OPERATOR_ROLES:
            raise OperatorIdentityError(
                f"«{role}» no es un rol de operador conocido; los roles son "
                f"{', '.join(OPERATOR_ROLES)}."
            )
        if not isinstance(address, str) or not _ADDRESS_RE.match(address.strip()):
            raise OperatorIdentityError(
                f"la dirección del rol «{role}» en {path} no tiene forma de correo."
            )
    return roles


def load_operator_identity(*, path: Path | None | object = ...) -> OperatorIdentity:
    """Resolve the operator identity.

    ``path`` defaults to "look at the environment, then the default location". Pass ``None``
    to force the fictitious identity, or an explicit :class:`~pathlib.Path` to read one file
    and refuse if it is not there -- which is what the tests and the diagnostics use.
    """
    explicit = path is not ...
    if explicit and path is None:
        return FICTITIOUS_OPERATOR_IDENTITY

    if explicit:
        assert isinstance(path, Path)
        target, named = path, True
    else:
        configured = os.environ.get(OPERATOR_IDENTITY_ENV_VAR, "").strip()
        if configured:
            target, named = Path(configured), True
        else:
            target, named = DEFAULT_OPERATOR_IDENTITY_PATH, False

    if not target.is_file():
        if named:
            raise OperatorIdentityError(
                f"el archivo de identidad del operador no existe: {target}"
            )
        # The documented default: no file, fictitious identity, nothing said.
        return FICTITIOUS_OPERATOR_IDENTITY

    roles = _roles_of(_read(target), target)
    # A file that names one role configures that one. The other keeps its fictitious address,
    # so a half-filled file disables exactly the rule it leaves out and no more.
    resolved = dict(FICTITIOUS_OPERATOR_IDENTITY.role_addresses)
    resolved.update({role: address.strip().lower() for role, address in roles.items()})
    return OperatorIdentity(
        role_addresses=MappingProxyType(resolved),
        is_configured=bool(roles),
    )
