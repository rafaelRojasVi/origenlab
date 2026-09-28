"""Operator-profile PINs: the pepper, the Argon2id hash, and what counts as a PIN.

A profile PIN proves which person is using a shared Google Workspace sign-in
(`docs/ARCHITECTURE.md` §5.1). It is short by nature, so what protects it is not its length:

* **Argon2id**, memory-hard, from `cryptography` (already a pinned dependency for the Google
  JWKS check; Argon2id is in it since 44). Parameters follow RFC 9106's second recommended
  option — 64 MiB, three passes, four lanes — and are stored in the PHC string, so they can be
  raised later without invalidating existing hashes.
* **A server-side pepper**, passed as Argon2's secret input `K`. It lives only in the API's
  environment (`ORIGENLAB_PROFILE_PIN_PEPPER`), never in the database, so a copy of
  `platform.operator_profile` alone cannot be brute-forced offline. It must differ from the
  session secret: a leak of one must not be a leak of the other.
* **The database throttle** (`profile_auth.py`), which bounds online guessing.

A PIN is never logged, returned, stored or put in an exception message. :class:`PinHasher`
takes it as an argument and keeps nothing; errors name the rule that failed, never the value.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

#: The pepper is at least this long, like the session secret (`auth_session.py`).
MIN_PEPPER_LENGTH = 32
#: ...and not a repetition of a few characters.
MIN_PEPPER_DISTINCT_CHARACTERS = 12

#: A new PIN: digits only, six to twelve of them.
NEW_PIN_RE = re.compile(r"^[0-9]{6,12}$")
#: The longest string ever handed to Argon2 at sign-in; anything longer is refused unhashed.
MAX_SUBMITTED_PIN_LENGTH = 64

_PHC_RE = re.compile(
    r"^\$argon2id\$v=19\$m=(?P<m>[0-9]{1,8}),t=(?P<t>[0-9]{1,3}),p=(?P<p>[0-9]{1,3})"
    r"\$(?P<salt>[A-Za-z0-9+/]{16,})\$(?P<hash>[A-Za-z0-9+/]{32,})$"
)


class PepperRefused(ValueError):
    """The pepper is missing or too weak to start with. Raised at startup, never per request."""


class PinPolicyRefused(ValueError):
    """A new PIN breaks the policy. The message names the rule, never the PIN."""


@dataclass(frozen=True)
class Argon2Parameters:
    memory_kib: int = 64 * 1024
    iterations: int = 3
    lanes: int = 4
    salt_bytes: int = 16
    hash_bytes: int = 32


#: The production parameters. Tests may pass cheaper ones; verification accepts any stored
#: parameters at or above :data:`FLOOR`, so a stored hash can never be weaker than that.
DEFAULT_PARAMETERS = Argon2Parameters()
#: OWASP's minimum for Argon2id (19 MiB, two passes). A stored hash below it is refused.
FLOOR = Argon2Parameters(memory_kib=19 * 1024, iterations=2, lanes=1)


def validate_pepper(pepper: str | None, *, session_secret: str | None) -> str:
    """Return the pepper, or refuse to start.

    The message never quotes the value. It is the same rule inside and outside production:
    there is no development pepper, because a hash made under a weak pepper would carry into
    any database it was provisioned into.
    """
    value = pepper or ""
    if not value.strip():
        raise PepperRefused(
            "ORIGENLAB_PROFILE_PIN_PEPPER is required when ORIGENLAB_PROFILE_LOGIN_ENABLED=true "
            "(generate one with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`)"
        )
    if len(value) < MIN_PEPPER_LENGTH or len(set(value)) < MIN_PEPPER_DISTINCT_CHARACTERS:
        raise PepperRefused(
            f"ORIGENLAB_PROFILE_PIN_PEPPER is too weak: at least {MIN_PEPPER_LENGTH} characters "
            f"with at least {MIN_PEPPER_DISTINCT_CHARACTERS} distinct ones"
        )
    if session_secret and hmac.compare_digest(value.encode(), session_secret.encode()):
        raise PepperRefused(
            "ORIGENLAB_PROFILE_PIN_PEPPER must differ from ORIGENLAB_AUTH_SESSION_SECRET"
        )
    return value


def validate_new_pin(pin: str) -> str:
    """Refuse a PIN that is not six to twelve digits, or that is trivially guessable."""
    if not isinstance(pin, str) or not NEW_PIN_RE.match(pin):
        raise PinPolicyRefused("a PIN is six to twelve digits")
    if len(set(pin)) == 1:
        raise PinPolicyRefused("a PIN may not repeat one digit")
    steps = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if steps in ({1}, {9}):
        raise PinPolicyRefused("a PIN may not be an ascending or descending run")
    return pin


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)


class PinHasher:
    """Hash and verify PINs under one pepper. Holds the pepper, never a PIN."""

    def __init__(self, pepper: str, parameters: Argon2Parameters = DEFAULT_PARAMETERS) -> None:
        if not pepper:
            raise PepperRefused("a PIN hasher needs a pepper")
        if (parameters.memory_kib < FLOOR.memory_kib or parameters.iterations < FLOOR.iterations
                or parameters.lanes < 1):
            raise ValueError("Argon2id parameters below the floor would make unverifiable hashes")
        self._secret = pepper.encode("utf-8")
        self._parameters = parameters
        # A hash of nothing in particular, verified against when there is no real hash to check,
        # so an unknown or locked profile costs the same time as a wrong PIN.
        self._decoy = self.hash(base64.b32encode(os.urandom(10)).decode())

    def __repr__(self) -> str:  # never show the pepper
        return f"PinHasher(m={self._parameters.memory_kib}, t={self._parameters.iterations}, p={self._parameters.lanes})"

    def _derive(self, pin: str, salt: bytes, *, m: int, t: int, p: int, length: int) -> bytes:
        return Argon2id(
            salt=salt, length=length, iterations=t, lanes=p, memory_cost=m, ad=None,
            secret=self._secret,
        ).derive(pin.encode("utf-8"))

    def hash(self, pin: str) -> str:
        """Return the Argon2id PHC string of `pin`. The caller has already applied the policy."""
        prm = self._parameters
        salt = os.urandom(prm.salt_bytes)
        digest = self._derive(pin, salt, m=prm.memory_kib, t=prm.iterations, p=prm.lanes,
                              length=prm.hash_bytes)
        return (f"$argon2id$v=19$m={prm.memory_kib},t={prm.iterations},p={prm.lanes}"
                f"${_b64(salt)}${_b64(digest)}")

    def verify(self, pin: str, encoded: str | None) -> bool:
        """True only when `encoded` is a sound Argon2id hash of `pin` under this pepper.

        Always performs one Argon2 derivation — against the decoy when `encoded` is absent or
        unusable — so the answer takes the same time whatever the reason it is no.
        """
        target = encoded if encoded and _PHC_RE.match(encoded) else None
        if not isinstance(pin, str) or len(pin) > MAX_SUBMITTED_PIN_LENGTH:
            pin, target = "", None
        match = _PHC_RE.match(target or self._decoy)
        assert match is not None  # the decoy is always well-formed
        m, t, p = int(match["m"]), int(match["t"]), int(match["p"])
        if m < FLOOR.memory_kib or t < FLOOR.iterations or p < 1 or m > 1024 * 1024 or t > 64 or p > 64:
            target = None
            match = _PHC_RE.match(self._decoy)
            assert match is not None
            m, t, p = int(match["m"]), int(match["t"]), int(match["p"])
        try:
            salt, expected = _unb64(match["salt"]), _unb64(match["hash"])
        except (binascii.Error, ValueError):
            return False
        actual = self._derive(pin, salt, m=m, t=t, p=p, length=len(expected))
        return target is not None and hmac.compare_digest(actual, expected)

    def dummy_verify(self) -> None:
        """Spend one verification's time and learn nothing (unknown or locked profile)."""
        self.verify("", None)
