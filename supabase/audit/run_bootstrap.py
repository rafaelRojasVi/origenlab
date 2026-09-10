#!/usr/bin/env python3
"""Entry point for the hosted role bootstrap dry run. Invoked through
supabase/scripts/hosted_role_bootstrap.sh.

Standard library only, and no database connection in any mode. This file exists so the package
directory is on sys.path when the tool is run as a script from a clean checkout, with nothing
installed and no packaging step -- the same arrangement as run_audit.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from olaudit.bootstrap_cli import main  # noqa: E402 - the path must be set before the import

if __name__ == "__main__":
    raise SystemExit(main())
