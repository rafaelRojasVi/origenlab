"""The V2 durable core — the read boundary and the human-review command boundary.

`apps/api` is V1's operator API: SQLite-first, with a Postgres mirror for reporting and an
allowlisted `/operations/*` command path. This package adds a *separate*, read-only surface
over the **V2** durable core, so the operator dashboard can be moved off the rebuildable
legacy mirrors one card at a time without disturbing anything V1 serves today.

The package holds **three** surfaces, split so that each one's guarantee is checkable in one
file. `routes.py` + `repository.py` are the read boundary; `command_routes.py` +
`command_repository.py` are the human-review command boundary that records durable decisions
about staged evidence; `case_command_routes.py` + `case_command_repository.py` are the
commercial-case command boundary, which runs the case that follows one. The two write
boundaries share `command_core.py` — one transaction, one receipt, one event stream, one
dispatch — rather than a copy of it. Four properties hold by construction:

* **The read boundary cannot write.** Every query in `repository.py` runs inside `begin read
  only` as `origenlab_api`, a role with no membership in `origenlab_owner`, and a test over
  the read router's own methods asserts it exposes no POST, PATCH or DELETE.
* **The command boundary writes only in one transaction, only with a receipt.** Every
  durable change carries the evidence record it is about, the verified operator, a mandatory
  reason, an idempotency key and an append-only `crm.domain_event`. A refusal rolls back
  everything including the receipt.
* **Not mounted unless configured.** With no `ORIGENLAB_V2_DATABASE_URL` the router is
  absent entirely, so an unconfigured deployment gets no `/v2` surface rather than one that
  errors.
* **Portable.** Identity is a port with a JWKS adapter (the target, present and
  unconfigured) and a local development adapter that refuses to load against anything but a
  literal loopback database. Nothing here hardcodes localhost in a way that would block
  later hosted configuration.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

#: Each name the package offers and the submodule it lives in. Loaded on first use (PEP 562), not
#: on import: a submodule import such as `origenlab_api.v2.quote_case_archive` runs this file, and
#: importing the routers here pulled FastAPI, every route and the email pipeline (~1,000 modules,
#: ~70 MB) into each Render worker process that only needed one helper (2026-10-21).
_LAZY: dict[str, str] = {
    "V2CaseCommandRepository": "case_command_repository",
    "case_command_router": "case_command_routes",
    "CASE_COMMAND_NAMES": "case_commands",
    "CommandTransaction": "command_core",
    "V2CommandRepository": "command_repository",
    "command_router": "command_routes",
    "COMMAND_NAMES": "commands",
    "CommandRefused": "commands",
    "IdentityMisconfigured": "identity",
    "IdentityPort": "identity",
    "IdentityRefused": "identity",
    "JwksVerifier": "identity",
    "LocalDevIdentity": "identity",
    "OperatorIdentity": "identity",
    "build_identity_port": "identity",
    "is_loopback_dsn": "identity",
    "V2Repository": "repository",
    "clamp_limit": "repository",
    "router": "routes",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


__all__ = [
    "CASE_COMMAND_NAMES",
    "COMMAND_NAMES",
    "CommandRefused",
    "CommandTransaction",
    "IdentityMisconfigured",
    "IdentityPort",
    "IdentityRefused",
    "JwksVerifier",
    "LocalDevIdentity",
    "OperatorIdentity",
    "V2CaseCommandRepository",
    "V2CommandRepository",
    "V2Repository",
    "build_identity_port",
    "case_command_router",
    "clamp_limit",
    "command_router",
    "is_loopback_dsn",
    "router",
]
