#!/usr/bin/env bash
#
# Sincroniza el sitio construido (`apps/web/dist/`) con la carpeta pública de
# cPanel por SSH + rsync.
#
# El guion es *dry-run por defecto*: sin `--apply` calcula y muestra los cambios
# —incluidas las eliminaciones que provocaría `--delete`— y no escribe nada en
# el servidor. Esa es la forma de probarlo la primera vez.
#
#   apps/web/scripts/deploy-cpanel.sh             # ensayo, no toca el servidor
#   apps/web/scripts/deploy-cpanel.sh --apply     # sincroniza de verdad
#
# Entradas, todas por entorno (nunca por argumento, para que no queden en el
# historial del intérprete de órdenes ni en el registro de la ejecución):
#
#   CPANEL_HOST        anfitrión SSH de cPanel
#   CPANEL_PORT        puerto SSH (cPanel rara vez usa el 22)
#   CPANEL_USER        usuario SSH de cPanel
#   CPANEL_WEB_ROOT    ruta absoluta de la carpeta pública (p. ej.
#                      /home/<usuario>/public_html). Debe ser explícita: ver
#                      `assert_web_root` más abajo.
#   SSH_KEY_FILE       ruta al archivo de llave privada, con permisos 0600
#   SSH_KNOWN_HOSTS    (opcional) archivo known_hosts; si falta, se acepta la
#                      llave que presente el servidor y se imprime su huella
#   DIST_DIR           (opcional) carpeta a subir; por defecto apps/web/dist
#   MAX_DELETIONS      (opcional) tope de archivos que el ensayo puede marcar
#                      para borrar antes de negarse; por defecto 500
#
# Lo que este guion no hace, a propósito: no crea la carpeta pública, no
# sincroniza el directorio personal, no usa FTP ni contraseña, y no imprime
# ningún valor secreto.
set -euo pipefail

apply=0
case "${1:-}" in
  --apply) apply=1 ;;
  --dry-run|'') apply=0 ;;
  *) echo "uso: $0 [--dry-run|--apply]" >&2; exit 2 ;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dist="${DIST_DIR:-$here/dist}"
max_deletions="${MAX_DELETIONS:-500}"

fail() { echo "deploy-cpanel: $*" >&2; exit 1; }

require_env() {
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || fail "falta la variable $name"
  done
}

# La ruta pública tiene que ser explícita y profunda. Un despliegue con
# `--delete` contra `/`, `/home` o el directorio personal del usuario borraría
# correo, copias de seguridad y llaves; ninguna comprobación posterior lo
# repararía, así que se comprueba antes de abrir la conexión.
assert_web_root() {
  local root="$1"
  [[ "$root" == /* ]] || fail "CPANEL_WEB_ROOT debe ser una ruta absoluta"
  [[ "$root" != */ ]] || fail "CPANEL_WEB_ROOT no debe terminar en /"
  [[ "$root" != *".."* ]] || fail "CPANEL_WEB_ROOT no puede contener .."
  local depth
  depth="$(awk -F/ '{print NF-1}' <<<"$root")"
  (( depth >= 3 )) || fail "CPANEL_WEB_ROOT demasiado alto ($root): se exige algo como /home/<usuario>/public_html"
  case "$root" in
    /|/home|/root|/root/*) fail "CPANEL_WEB_ROOT apunta fuera de una carpeta pública: $root" ;;
  esac
  grep -q '/public_html' <<<"$root" || fail \
    "CPANEL_WEB_ROOT no contiene public_html ($root). Si el dominio sirve desde otra raíz, ajuste esta comprobación a conciencia en $0"
}

require_env CPANEL_HOST CPANEL_PORT CPANEL_USER CPANEL_WEB_ROOT SSH_KEY_FILE
assert_web_root "$CPANEL_WEB_ROOT"

[[ "$CPANEL_PORT" =~ ^[0-9]+$ ]] || fail "CPANEL_PORT no es un número"
[[ -f "$SSH_KEY_FILE" ]] || fail "SSH_KEY_FILE no existe"
[[ "$(stat -c '%a' "$SSH_KEY_FILE")" == "600" ]] || fail "SSH_KEY_FILE debe tener permisos 0600"

# --- Lo que se sube: el contenido de dist/, entero y sólo eso --------------
[[ -d "$dist" ]] || fail "no existe $dist: ejecute npm run build primero"
[[ -f "$dist/index.html" ]] || fail "$dist no contiene index.html"
[[ -f "$dist/.htaccess" ]] || fail "$dist no contiene .htaccess (va en public/ y debe llegar a la raíz pública)"

ssh_opts=(-p "$CPANEL_PORT" -i "$SSH_KEY_FILE" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)
if [[ -n "${SSH_KNOWN_HOSTS:-}" ]]; then
  ssh_opts+=(-o StrictHostKeyChecking=yes -o UserKnownHostsFile="$SSH_KNOWN_HOSTS")
else
  echo "aviso: sin SSH_KNOWN_HOSTS se confía en la llave que presente el servidor en esta conexión."
  echo "       Fije el secreto CPANEL_SSH_KNOWN_HOSTS para anclarla."
  ssh_opts+=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
fi

target="$CPANEL_USER@$CPANEL_HOST:$CPANEL_WEB_ROOT/"

# La carpeta pública tiene que existir ya. Si rsync la creara, sería señal de
# que la ruta está equivocada y el sitio quedaría publicado en el sitio que no es.
ssh "${ssh_opts[@]}" "$CPANEL_USER@$CPANEL_HOST" "test -d '$CPANEL_WEB_ROOT'" \
  || fail "la carpeta $CPANEL_WEB_ROOT no existe en el servidor (o la conexión SSH falló)"

# -r recursivo, -l enlaces, -p permisos, -t marcas de tiempo, -z compresión.
# Se omiten -o/-g: en alojamiento compartido no se puede fijar propietario.
# --chmod deja 755/644, lo que Apache exige en cPanel.
# --delete es lo que hace que la carpeta pública sea un espejo de dist/.
# Lo excluido también queda protegido del borrado: `cgi-bin/` lo crea cPanel y
# `.well-known/` es donde se validan los certificados. Nada de eso sale del
# build y borrarlo rompería el servidor.
rsync_opts=(
  -rlptz --delete --chmod=D755,F644
  --itemize-changes --human-readable
  --exclude '.DS_Store'
  --exclude '/cgi-bin/'
  --exclude '/.well-known/'
  -e "ssh ${ssh_opts[*]}"
)

plan="$(mktemp)"
trap 'rm -f "$plan"' EXIT

echo "== ensayo (dry-run): $dist/ -> $target"
rsync "${rsync_opts[@]}" --dry-run "$dist/" "$target" | tee "$plan"

deletions="$(grep -c '^\*deleting' "$plan" || true)"
echo
echo "== resumen del ensayo: $deletions archivo(s) se eliminarían en $CPANEL_WEB_ROOT"

if (( deletions > max_deletions )); then
  fail "el ensayo eliminaría $deletions archivos (tope $max_deletions). Revise CPANEL_WEB_ROOT antes de continuar; suba MAX_DELETIONS a conciencia si la cifra es correcta."
fi

if (( apply == 0 )); then
  echo "== ensayo terminado; no se ha escrito nada en el servidor. Repita con --apply para sincronizar."
  exit 0
fi

echo
echo "== sincronizando de verdad"
rsync "${rsync_opts[@]}" "$dist/" "$target"
echo "== listo: $CPANEL_WEB_ROOT es ahora un espejo de $dist"
