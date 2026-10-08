#!/usr/bin/env bash
# One-time local, interactive GitHub production environment setup.
# Secret values are read from the terminal or a local PEM file, never committed.
set -euo pipefail
umask 077

repo="rafaelRojasVi/origenlab"
env_name="origenlab-release"
for binary in gh age-keygen openssl; do
  command -v "$binary" >/dev/null 2>&1 || {
    echo "Missing $binary; install it locally before setup. Nothing changed." >&2
    exit 2
  }
done
gh auth status >/dev/null
# The GitHub repository variable selects the release policy; do not trust a
# shell-only override in the setup helper.
# Older gh CLIs (including the operator's installed version) have no
# "gh variable get" subcommand. The REST endpoint is compatible with gh api.
# Fail closed if the configured GitHub policy cannot be read.
if ! solo_mode="$(gh api "repos/$repo/actions/variables/OL_RELEASE_SOLO_MODE" --jq .value)"; then
  echo "Cannot read the OL_RELEASE_SOLO_MODE repository variable; no secrets changed." >&2
  exit 1
fi
if [[ "$solo_mode" != "true" && "$solo_mode" != "false" ]]; then
  echo "Invalid OL_RELEASE_SOLO_MODE value; expected true or false." >&2
  exit 1
fi
# No production secrets are provisioned unless main has strict CI and a PR
# policy compatible with the configured owner/collaborator approval mode.
OL_RELEASE_SOLO_MODE="$solo_mode" python3 "$(dirname "${BASH_SOURCE[0]}")/check_branch_protection.py" --local
# Dedicated unattended release environment; do not weaken the manually-approved
# production environment used by the public website.
# Fail closed if branch policy cannot be established; main itself must be protected.
printf '%s' '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}' |
  gh api -X PUT "repos/$repo/environments/$env_name" --input - >/dev/null
policy_rows="$(gh api --paginate "repos/$repo/environments/$env_name/deployment-branch-policies" \
  --jq '.branch_policies[] | [.name, .type] | @tsv')"
if [ -n "$policy_rows" ] && [ "$policy_rows" != $'main\tbranch' ]; then
  echo "Release environment has other branch/tag policies; review them before installing secrets." >&2
  exit 1
fi
if [ -z "$policy_rows" ]; then
  printf '%s' '{"name":"main","type":"branch"}' |
    gh api -X POST "repos/$repo/environments/$env_name/deployment-branch-policies" --input - >/dev/null
fi
unset policy_rows
echo "Configuring protected GitHub Environment $env_name for $repo."
echo "No service deploy or database write is performed by this script."

cert="$HOME/.config/origenlab-v2/prod-ca-2021.crt"
test -r "$cert" || { echo "Verified CA certificate not found at $cert" >&2; exit 1; }
openssl x509 -in "$cert" -noout >/dev/null || { echo "Invalid CA PEM" >&2; exit 1; }

identity="$HOME/.config/origenlab-v2/production-backup-age-identity.txt"
if [ ! -e "$identity" ]; then
  mkdir -p "$HOME/.config/origenlab-v2"
  chmod 700 "$HOME/.config/origenlab-v2"
  age-keygen -o "$identity" >/dev/null
fi
chmod 600 "$identity"
recipient="$(age-keygen -y "$identity")"
[[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || { echo "Invalid age recipient" >&2; exit 1; }

default_host="${PGHOST:-}"
read -r -p "Supabase session pooler host [${default_host:-required}]: " host
host="${host:-$default_host}"
[[ "$host" =~ ^aws-[0-9]{1,2}-sa-east-1\.pooler\.supabase\.com$ ]] || {
  echo "Expected exact production session pooler hostname from Supabase Connect" >&2
  exit 1
}
echo "The Supabase migrator password must be rotated if previously shared in chat."
read -r -s -p "Rotated origenlab_migrator password (not shown): " migrator_pass
echo
test -n "$migrator_pass" || { echo "Migrator password missing" >&2; exit 1; }
read -r -s -p "Render API key (not shown): " render_key
echo
test -n "$render_key" || { echo "Render API key missing" >&2; exit 1; }

# Pipe each private value via STDIN; never pass credentials in argv or print them.
printf '%s' "$host" | gh secret set OL_PROD_POOLER_HOST -e "$env_name" -R "$repo"
printf '%s' "txgsamojgvkymitcdcpo" | gh secret set OL_PROD_PROJECT_REF -e "$env_name" -R "$repo"
gh secret set OL_PROD_CA_PEM -e "$env_name" -R "$repo" < "$cert"
printf '%s' "$migrator_pass" | gh secret set OL_PROD_MIGRATOR_PASSWORD -e "$env_name" -R "$repo"
printf '%s' "$render_key" | gh secret set RENDER_API_KEY -e "$env_name" -R "$repo"
unset migrator_pass
gh variable set OL_PROD_BACKUP_AGE_RECIPIENT -e "$env_name" -R "$repo" --body "$recipient"
echo
echo "To allow migration-first releases, Render must NOT deploy directly on main."
read -r -p "Type DISABLE to switch all four OrigenLab Render services to auto-deploy OFF (no restart): " answer
if [[ "$answer" == "DISABLE" ]]; then
  RENDER_API_KEY="$render_key" python3 "$(dirname "${BASH_SOURCE[0]}")/set_render_autodeploy_off.py" --disable-autodeploy
else
  echo "Render auto-deploy settings unchanged. Release CI will refuse to deploy until all four are OFF."
fi
unset render_key answer
echo
echo "Production environment secrets and public backup recipient configured."
echo "OFFLINE RECOVERY KEY: $identity"
echo "Back up this PRIVATE file to a second safe offline location and test decrypting a backup."
echo "Do not share, upload to GitHub, or remove the private key."
echo "Next: after merging reviewed PR, run the GitHub Actions read-only plan and encrypted-backup restore drill."
