#!/usr/bin/env bash
# Genera config/odoo.conf (no versionado) a partir de la plantilla y .env.
#
# La master password vive solo en .env, que está en .gitignore. El repo es
# público: nunca escribas el secreto en odoo.conf.example ni en el README.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template="$here/config/odoo.conf.example"
target="$here/config/odoo.conf"
envfile="$here/.env"

if [[ ! -f "$envfile" ]]; then
    echo "Falta $envfile. Copia .env.example y rellena los valores." >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a && source "$envfile" && set +a

if [[ -z "${ODOO_MASTER_PASSWORD:-}" ]]; then
    echo "Falta ODOO_MASTER_PASSWORD en $envfile." >&2
    exit 1
fi

if [[ -f "$target" ]]; then
    cp "$target" "$target.bak"
    echo "Respaldo previo en $target.bak"
fi

sed "s|__ODOO_MASTER_PASSWORD__|${ODOO_MASTER_PASSWORD}|" "$template" > "$target"

# El usuario odoo del contenedor (uid 100) debe poder leerlo; tu uid es 1000.
chmod 0644 "$target"
echo "Escrito $target"
