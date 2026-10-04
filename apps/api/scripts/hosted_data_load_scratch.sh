#!/usr/bin/env bash
# mint|clone-cleanroom|drop a disposable origenlab_test_<8 hex> database on the clean-room
# container (origenlab_dev_db). Used by hosted_data_load.py verify and by the e2e test.
# Deliberately not disposable_db.sh: that one targets the Supabase CLI stack, not this container.
set -euo pipefail
cmd="${1:?usage: $0 mint|clone-cleanroom|drop <name>}"; name="${2:-}"
cd "$(dirname "$0")/../../.."
export OL_REPO_ROOT="$PWD"
source supabase/scripts/lib/local_target.sh
new_name() { echo "origenlab_test_$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"; }
case "$cmd" in
  clone-cleanroom)
    # a byte-exact copy of origenlab_clean (ledger, data, sequences); CREATE DATABASE ... TEMPLATE
    # needs no other session on origenlab_clean while it runs
    name="$(new_name)"
    ol_require_dev_database "$OL_REPO_ROOT" >/dev/null
    others="$(ol_psql_dev_maintenance -tAc "select count(*) from pg_stat_activity where datname = 'origenlab_clean' and pid <> pg_backend_pid()")"
    if [[ "$others" != "0" ]]; then
      echo "FAIL: $others other session(s) connected to origenlab_clean; cannot clone it as a template." >&2
      exit 1
    fi
    ol_psql_dev_maintenance -tAc "create database \"$name\" template origenlab_clean" >/dev/null
    echo "$name" ;;
  mint)
    name="$(new_name)"
    ol_require_dev_database "$OL_REPO_ROOT" >/dev/null
    ol_psql_dev_maintenance -tAc "create database \"$name\"" >/dev/null
    ol_require_dev_scratch_database "$name" >/dev/null
    ol_bootstrap_platform_objects_into ol_psql_dev_scratch --with-ledger
    ol_apply_migrations_into ol_psql_dev_scratch >/dev/null
    echo "$name" ;;
  drop)
    ol_require_dev_scratch_database "$name" >/dev/null
    ol_require_dev_database "$OL_REPO_ROOT" >/dev/null
    ol_psql_dev_maintenance -tAc "drop database if exists \"$name\" with (force)" >/dev/null ;;
  *) echo "usage: $0 mint|clone-cleanroom|drop <name>" >&2; exit 2 ;;
esac
