#!/usr/bin/env bash
# One-time local, interactive GitHub production environment setup.
# Secret values are read from the terminal or a local PEM file, never committed.
set -euo pipefail
umask 077

repo="rafaelRojasVi/origenlab"
env_name="production"
for binary in gh age-keygen openssl; do
  command -v "$binary" >/dev/null 2>&1 || {
    echo "Missing $binary; install it locally before setup. Nothing changed." >&2
    exit 2
  }
done
gh auth status >/dev/null
echo "Configuring protected GitHub Environment $env_name for $repo."
echo "This does NOT deploy services, change databases, or disable Render auto-deploy."

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
unset migrator_pass render_key
gh variable set OL_PROD_BACKUP_AGE_RECIPIENT -e "$env_name" -R "$repo" --body "$recipient"
echo
echo "Production environment secrets and public backup recipient configured."
echo "OFFLINE RECOVERY KEY: $identity"
echo "Back up this PRIVATE file to a second safe offline location and test decrypting a backup."
echo "Do not share, upload to GitHub, or remove the private key."
echo "Next: turn OFF Render independent auto-deploy for all 4 services, then run a GitHub Actions read-only plan."
