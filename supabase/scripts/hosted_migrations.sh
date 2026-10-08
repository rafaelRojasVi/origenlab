#!/usr/bin/env bash
# Compatibility wrapper; no separate ledger login, no split transactions.
set -euo pipefail
if [[ "$#" -ne 1 ]]; then echo 'usage: hosted_migrations.sh --plan|--apply' >&2; exit 2; fi
case "$1" in --plan) mode=plan;; --apply) mode=apply;; *) exit 2;; esac
exec python3 "$(dirname "${BASH_SOURCE[0]}")/hosted_migrations.py" "$mode"
