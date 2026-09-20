"""V1 → V2 migration evidence extraction (read-only).

Everything in this package reads. Nothing here opens a writable connection, and
no module may import a sender, a Gmail client or a Postgres writer.

``scripts/migrate/`` is the parked V1 → PostgreSQL loader family
(``docs/EXPERIMENTAL_PARKED.md``); this package and ``scripts/migration/`` are
the V2 migration-evidence extractors, which never write to any database.
"""

from __future__ import annotations
