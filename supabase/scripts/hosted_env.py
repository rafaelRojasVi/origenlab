"""Production-only Supabase session-pooler target. Refuse weaker TLS and mismatched identities."""
from __future__ import annotations

import os
import re
from pathlib import Path

EXPECTED_HOST = re.compile(r"^aws-[0-9]{1,2}-sa-east-1\.pooler\.supabase\.com$")
EXPECTED_REF = re.compile(r"^[a-z]{20}$")


class Refused(RuntimeError):
    pass


def database_env(cert_directory: Path) -> dict[str, str]:
    host = os.environ.get("OL_PROD_POOLER_HOST", "")
    ref = os.environ.get("OL_PROD_PROJECT_REF", "")
    password = os.environ.get("OL_PROD_MIGRATOR_PASSWORD", "")
    pem = os.environ.get("OL_PROD_CA_PEM", "")
    if not EXPECTED_HOST.fullmatch(host):
        raise Refused("Expected the reviewed sa-east-1 Supavisor SESSION pooler host")
    if ref != "txgsamojgvkymitcdcpo":
        raise Refused("Invalid production project reference")
    if not password or not pem.startswith("-----BEGIN CERTIFICATE-----"):
        raise Refused("Missing migrator password or verified CA PEM")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise Refused("Hosted production actions may run only inside GitHub Actions")
    cert = cert_directory / "supabase-production-ca.pem"
    cert.write_text(pem + ("\n" if not pem.endswith("\n") else ""), encoding="ascii")
    cert.chmod(0o600)
    # No inherited libpq target, service file or stale PGHOSTADDR may reroute this connection.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")
           and not k.startswith("OL_PROD_")}
    env.update(PGHOST=host, PGPORT="5432", PGDATABASE="postgres",
               PGUSER=f"origenlab_migrator.{ref}", PGPASSWORD=password,
               PGSSLMODE="verify-full", PGSSLROOTCERT=str(cert),
               PGCONNECT_TIMEOUT="15", PGGSSENCMODE="disable")
    return env
