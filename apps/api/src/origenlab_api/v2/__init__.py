"""The V2 durable core — the read boundary and the human-review command boundary.

`apps/api` is V1's operator API: SQLite-first, with a Postgres mirror for reporting and an
allowlisted `/operations/*` command path. This package adds a *separate*, read-only surface
over the **V2** durable core, so the operator dashboard can be moved off the rebuildable
legacy mirrors one card at a time without disturbing anything V1 serves today.

The package holds **two** surfaces, split so that each one's guarantee is checkable in one
file. `routes.py` + `repository.py` are the read boundary; `command_routes.py` +
`command_repository.py` are the human-review command boundary that records durable decisions
about staged evidence. Four properties hold by construction:

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

from origenlab_api.v2.command_repository import V2CommandRepository
from origenlab_api.v2.command_routes import command_router
from origenlab_api.v2.commands import COMMAND_NAMES, CommandRefused
from origenlab_api.v2.identity import (
    IdentityMisconfigured,
    IdentityPort,
    IdentityRefused,
    JwksVerifier,
    LocalDevIdentity,
    OperatorIdentity,
    build_identity_port,
    is_loopback_dsn,
)
from origenlab_api.v2.repository import V2Repository, clamp_limit
from origenlab_api.v2.routes import router

__all__ = [
    "COMMAND_NAMES",
    "CommandRefused",
    "IdentityMisconfigured",
    "IdentityPort",
    "IdentityRefused",
    "JwksVerifier",
    "LocalDevIdentity",
    "OperatorIdentity",
    "V2CommandRepository",
    "V2Repository",
    "build_identity_port",
    "clamp_limit",
    "command_router",
    "is_loopback_dsn",
    "router",
]
