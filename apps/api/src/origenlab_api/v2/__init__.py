"""The V2 durable read boundary.

`apps/api` is V1's operator API: SQLite-first, with a Postgres mirror for reporting and an
allowlisted `/operations/*` command path. This package adds a *separate*, read-only surface
over the **V2** durable core, so the operator dashboard can be moved off the rebuildable
legacy mirrors one card at a time without disturbing anything V1 serves today.

Three properties hold by construction:

* **Read-only.** Every query runs inside `begin read only` as `origenlab_api`, a role with
  no membership in `origenlab_owner`. RLS constrains these reads exactly as it will in
  production rather than being bypassed by a privileged local login.
* **Not mounted unless configured.** With no `ORIGENLAB_V2_DATABASE_URL` the router is
  absent entirely, so an unconfigured deployment gets no `/v2` surface rather than one that
  errors.
* **Portable.** Identity is a port with a JWKS adapter (the target, present and
  unconfigured) and a local development adapter that refuses to load against anything but a
  literal loopback database. Nothing here hardcodes localhost in a way that would block
  later hosted configuration.
"""

from __future__ import annotations

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
    "IdentityMisconfigured",
    "IdentityPort",
    "IdentityRefused",
    "JwksVerifier",
    "LocalDevIdentity",
    "OperatorIdentity",
    "V2Repository",
    "build_identity_port",
    "clamp_limit",
    "is_loopback_dsn",
    "router",
]
