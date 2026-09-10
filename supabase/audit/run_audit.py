#!/usr/bin/env python3
"""Entry point for the Slice 0 audit. Invoked through supabase/scripts/slice0_audit.sh.

Standard library only. This file exists so the package directory is on sys.path when the audit is
run as a script from a clean checkout, with nothing installed and no packaging step.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from olaudit.cli import main  # noqa: E402 - the path must be set before the package is imported

if __name__ == "__main__":
    raise SystemExit(main())
