# Despliegue — OrigenLab (HostGator)

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-09-21

Sitio estático generado con Astro. El resultado del build son HTML, CSS y assets en la carpeta `dist/`.

## Despliegue automático (GitHub Actions → cPanel por SSH)

El flujo de trabajo [`.github/workflows/web-deploy.yml`](../../../.github/workflows/web-deploy.yml)
construye el sitio y sincroniza `apps/web/dist/` con la carpeta pública de
cPanel. Es el camino previsto; la subida manual de más abajo queda como
respaldo cuando Actions no está disponible.

**Dos trabajos, y la separación es lo que da la garantía.**

| Trabajo | Qué hace | Secretos | Entorno |
|---|---|---|---|
| `build` | `npm ci` → Chromium para las puertas de QA → `npm run validate` → publica el `dist/` validado como artefacto | ninguno | ninguno |
| `deploy` | descarga ese `dist/` → prepara la llave SSH → **ensayo** de rsync → sincronización real | los cinco de cPanel | `production` |

`deploy` sincroniza exactamente los bytes que `build` validó, porque recibe el
artefacto en lugar de reconstruir el sitio.

**Cuándo corre cada uno.**

| Disparador | `build` | `deploy` |
|---|---|---|
| push a `main` bajo `apps/web/**` | sí | **no, nunca** |
| *Run workflow* con `apply` = `false` | sí | sí, y termina en el ensayo |
| *Run workflow* con `apply` = `true` | sí | sí, y sincroniza de verdad |

**Un push a `main` no puede desplegar.** No es que una condición lo impida: el
trabajo que tiene acceso a los secretos **no se crea** para un push, así que no
hay llave SSH ni conexión posible en la ruta de integración continua.

**`deploy` siempre pide aprobación**, con `apply` en `false` o en `true`, porque
declara el entorno `production`. El ensayo ya abre una conexión SSH al servidor,
y ninguna conexión al alojamiento debería ocurrir sin que alguien la apruebe.

**Qué sube.** Sólo el contenido de `apps/web/dist/`, incluido `.htaccess`.
Nunca el repositorio, nunca el directorio personal. `rsync --delete` convierte la carpeta pública en un espejo exacto de `dist/`:
un archivo que esté en el servidor y no en el build **se borra**. Quedan
excluidos —y por tanto protegidos— `cgi-bin/` y `.well-known/`, que crea el
panel. Si hay algo más en la carpeta pública que no venga del build (una
carpeta subida a mano, un archivo del alojamiento), sáquelo de ahí o añádalo a
`--exclude` en el guion **antes** del primer despliegue real.

**Qué no toca.** Cloudflare, Supabase, D1, el Worker del boletín y la
configuración de correo quedan fuera: este flujo sólo escribe archivos en la
carpeta pública.

### Modo ensayo (dry-run)

[`scripts/deploy-cpanel.sh`](../scripts/deploy-cpanel.sh) es **ensayo por
defecto**: sin `--apply` calcula los cambios, imprime cuántos archivos
eliminaría `--delete` y no escribe nada en el servidor. El flujo de trabajo
ejecuta *siempre* el ensayo antes de sincronizar, de modo que el registro de
cada despliegue contiene la lista exacta de altas, cambios y bajas.

La primera prueba se hace a mano y sin escribir:

1. Actions → **web-deploy** → *Run workflow*.
2. Dejar **`apply`** en `false`.
3. Aprobar el entorno `production` cuando lo pida.
4. Leer el paso *Dry run*: debe listar el sitio entero como alta y una cifra de
   eliminaciones que cuadre con lo que hay hoy en `public_html`.

Si la cifra de eliminaciones supera 500, el guion se niega a continuar. Ese
tope existe para que una ruta equivocada no vacíe una carpeta que no era;
súbalo con `MAX_DELETIONS` sólo cuando el ensayo demuestre que la cifra es
correcta.

El mismo ensayo puede correrse desde una máquina local que tenga la llave:

```bash
cd apps/web
npm run build
CPANEL_HOST=... CPANEL_PORT=... CPANEL_USER=... \
CPANEL_WEB_ROOT=/home/<usuario>/public_html \
SSH_KEY_FILE=~/.ssh/origenlab_deploy \
scripts/deploy-cpanel.sh            # ensayo; --apply para sincronizar
```

### Guardas de la ruta pública

`CPANEL_WEB_ROOT` tiene que ser explícito. El guion rechaza, antes de abrir la
conexión: una ruta relativa, una que termine en `/`, una que contenga `..`,
`/`, `/home`, `/root`, cualquier ruta de menos de tres segmentos (es decir, el
directorio personal) y cualquiera que no contenga `public_html`. También
comprueba que la carpeta **ya exista** en el servidor: rsync no la crea, porque
que hubiera que crearla significaría que la ruta está equivocada.

### Las tres rutas legales sí se despliegan

`/privacidad/`, `/cookies/` y `/aviso-legal/` **se publican** con el resto del
sitio: el titular del negocio lo decidió el 2026-09-21 y el guion no las
excluye. Lo que no cambia es su visibilidad: siguen `noindex`, fuera del
sitemap y `Disallow` en `robots.txt` hasta que un profesional chileno revise el
texto y `legalStatus.reviewedBy` lo registre. `validate:dist` comprueba que las
cuatro capas sigan de acuerdo, así que un despliegue no puede indexarlas por
descuido.

Nada de esto llega por un push: el único camino a `--apply` es lanzar el
workflow a mano con `apply` en `true` y aprobar `production`.

### Secretos (GitHub → Settings → Environments → production)

| Secreto | Qué es | Ejemplo |
|---|---|---|
| `CPANEL_HOST` | anfitrión SSH de cPanel | `origenlab.cl` o el nombre que dé HostGator |
| `CPANEL_PORT` | puerto SSH | HostGator suele usar `2222`, no `22` |
| `CPANEL_USER` | usuario SSH de cPanel | el usuario de la cuenta |
| `CPANEL_SSH_KEY` | llave **privada** OpenSSH, exclusiva del despliegue | contenido completo de `origenlab_deploy` |
| `CPANEL_WEB_ROOT` | ruta absoluta de la carpeta pública | `/home/<usuario>/public_html` |
| `CPANEL_SSH_KNOWN_HOSTS` | *(opcional, recomendado)* llave pública del servidor | salida de `ssh-keyscan -p 2222 <host>` |

Sin `CPANEL_SSH_KNOWN_HOSTS` el primer contacto confía en la llave que presente
el servidor y lo avisa en el registro. Con el secreto puesto, la verificación
es estricta.

Ningún secreto se escribe en el repositorio ni se imprime: la llave privada se
vuelca a un archivo temporal del ejecutor con permisos 0600 y se borra al
terminar, pase lo que pase.

### Lo que hay que hacer una vez en cPanel

1. **Crear una llave SSH exclusiva para el despliegue** (no reutilizar una
   personal). En la máquina local:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/origenlab_deploy -C "github-actions-web-deploy" -N ""
   ```

   Deja `origenlab_deploy` (privada) y `origenlab_deploy.pub` (pública).

2. **Autorizar la llave pública en cPanel**: cPanel → *SSH Access* → *Manage
   SSH Keys* → *Import Key*, pegar el contenido de `origenlab_deploy.pub`, y
   después **Manage → Authorize**. Una llave importada y no autorizada no
   entra.

3. **Confirmar el puerto SSH y el anfitrión** en la misma pantalla de cPanel.
   En HostGator suele ser `2222`.

4. **Confirmar la ruta exacta del sitio**. Conectando por SSH:

   ```bash
   ssh -p <puerto> <usuario>@<host> 'pwd; ls -d ~/public_html; ls ~/public_html | head'
   ```

   Si `origenlab.cl` es el dominio principal, la ruta es `/home/<usuario>/public_html`.
   Si estuviera como *addon domain*, es la carpeta que cPanel → *Domains*
   muestre como *Document Root* de `origenlab.cl`, y hay que usar esa.

5. **Cargar la llave privada como `CPANEL_SSH_KEY`** en el entorno
   `production` y borrar cualquier copia que quede fuera de `~/.ssh`.

6. *(Opcional y recomendado)* fijar `CPANEL_SSH_KNOWN_HOSTS` con la salida de
   `ssh-keyscan -p <puerto> <host>`.

### Si hay que volver atrás

El despliegue es un espejo de un commit. Para revertir: revertir el cambio en
`main` (o lanzar *Run workflow* desde el commit bueno) y dejar que el flujo
vuelva a sincronizar. No hay estado en el servidor que recuperar aparte de los
archivos.

---

## Checklist antes del lanzamiento

- [ ] Ejecutar `npm run build` y revisar que no haya errores.
- [ ] Subir **todo** el contenido de `dist/` al directorio público (p. ej. `public_html`), incluyendo **`.htaccess`** (en FTP/cPanel, activar “mostrar archivos ocultos” si no lo ve).
- [ ] Comprobar que la raíz del sitio contiene `index.html` y `.htaccess`.
- [ ] Abrir el sitio por **HTTP** (ej. `http://origenlab.cl`) y verificar que redirige a **HTTPS**.
- [ ] Revisar en el navegador: Inicio, Nosotros, Productos, Marcas, Contacto; una categoría (ej. alimentos); opcional `robots.txt` y `sitemap.xml` en la raíz del sitio.
- [ ] Probar el enlace “Enviar correo” en Contacto (debe abrir el cliente de correo con contacto@origenlab.cl).
- [ ] Verificar que **contacto@origenlab.cl** recibe y envía según **[docs/email-setup.md](email-setup.md)** (buzón principal en **Titan**; IMAP/SMTP y DNS/MX como allí se documentan). No asumir que el buzón se “crea solo” en cPanel: el sitio y el DNS pueden estar en HostGator mientras el correo operativo está en Titan.

## Pasos (subida manual — respaldo)

Este es el camino de respaldo. El habitual es el despliegue automático de más arriba.


1. **Build local**
   ```bash
   npm run build
   ```
   La salida queda en `dist/`.

2. **Subir a HostGator**
   - Conectar por **FTP** o usar el **Administrador de archivos** en cPanel.
   - Subir **todo el contenido** de `dist/` al directorio público del dominio:
     - Si el sitio es la cuenta principal: suele ser `public_html`.
     - Si es un addon domain para `origenlab.cl`: la carpeta asignada a ese dominio (p. ej. `public_html/origenlab` o la raíz que indique HostGator).
   - La raíz del sitio debe contener `index.html` (página de inicio).

3. **URLs y carpetas**
   - Astro genera rutas como `productos/index.html`, `nosotros/index.html`, etc.
   - En HostGator, normalmente solicitar `/productos` sirve `productos/index.html`. Si no, puede ser necesario configurar reglas de reescritura (p. ej. `.htaccess`) para URLs limpias; en la mayoría de planes compartidos la configuración por defecto ya lo permite.

4. **Seguridad (HTTPS y cabeceras)**
   - En la raíz de `dist/` (o en `public/` antes del build) se incluye un `.htaccess` de ejemplo que:
     - Redirige HTTP a HTTPS (301).
     - Añade cabeceras básicas: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`.
   - Al subir los archivos, asegurarse de subir también `.htaccess` (en algunos clientes FTP los archivos que empiezan por punto están ocultos).
   - Si HostGator ya fuerza HTTPS desde cPanel, la redirección en `.htaccess` refuerza el comportamiento.

5. **Dominio y correo**
   - Asegurar que el dominio **origenlab.cl** apunte al **sitio web** en el hosting (DNS / nameservers según tu setup actual; ver [deployment-status.md](deployment-status.md)).
   - El buzón **contacto@origenlab.cl** **no** se configura en el build del sitio. La fuente de verdad operativa del correo es **[docs/email-setup.md](email-setup.md)** (proveedor **Titan**, servidores IMAP/SMTP, DKIM). HostGator/cPanel puede seguir siendo relevante para **DNS del dominio**, pero **no** sustituye la guía de Titan para conectar clientes de correo ni para saber dónde está el buzón real.

## Resumen

| Acción        | Dónde / Cómo                          |
|---------------|----------------------------------------|
| Build         | `npm run build` → `dist/`             |
| Desplegar     | GitHub Actions `web-deploy` → SSH + rsync a la carpeta pública (entorno `production`, aprobación manual) |
| Subir archivos (respaldo) | FTP o cPanel → directorio público     |
| Dominio       | DNS → hosting (p. ej. HostGator) según estado actual |
| Correo contacto@ | Ver [email-setup.md](email-setup.md) (Titan; no usar solo cPanel como referencia del buzón) |
| Seguridad     | Subir `.htaccess`; HTTPS y cabeceras según archivo en raíz |

No se requiere Node.js en el servidor; solo se sirven archivos estáticos. Antes de dar por cerrado el lanzamiento, usar el **Checklist antes del lanzamiento** de esta página.
