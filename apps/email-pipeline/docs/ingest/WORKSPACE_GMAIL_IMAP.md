# Google Workspace → SQLite (Gmail IMAP + OAuth2)

**Operational default** for the live **contacto@** mailbox when it lives in **Google Workspace**: mail lands in **Gmail** and rows get **`gmail:...`** `source_file` values. Contacto review/drafting helpers use those Gmail-source rows rather than Titan `imap:...` rows; the former Streamlit surfaces were removed on 2026-06-04.

Use this path when **contacto@origenlab.cl** (or the shared mailbox you ingest) is a **real Workspace user**. Mail is in **Gmail**.

---

## Qué es cada cosa (no mezclar)

| Variable / archivo | Qué es | ¿Dónde lo consigues? |
|--------------------|--------|----------------------|
| **`ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON`** | Archivo **JSON** que descargas **una vez** desde Google Cloud. Identifica tu “app” (cliente OAuth tipo **Escritorio**) e incluye `client_id` y `client_secret`. | [Google Cloud Console](https://console.cloud.google.com/) → tu proyecto → **APIs y servicios** → **Credenciales** → crear **ID de cliente OAuth** → tipo **Aplicación de escritorio** → **Descargar JSON**. |
| **`ORIGENLAB_GMAIL_TOKEN_JSON`** | Archivo que **crea el script Python la primera vez** que inicias sesión en el navegador. Guarda el *refresh token* para no volver a pedir login cada vez. **No lo descargas de ninguna web.** | Primera ejecución de `05_workspace_gmail_imap_to_sqlite.py` (se abre el navegador). Opcionalmente defines la ruta con esta variable; si no, usa por defecto `$ORIGENLAB_DATA_ROOT/secrets/gmail_workspace_token.json`. |
| **`ORIGENLAB_GMAIL_WORKSPACE_USER`** | La dirección del buzón a leer por IMAP, p. ej. `contacto@origenlab.cl`. | Ya la tienes en Admin de Workspace. |

**Resumen:** solo **descargas** el JSON del cliente OAuth. El **token** lo **genera** tu máquina después del primer login.

---

## Pasos en Google Cloud (OAuth client JSON)

1. Abre **[console.cloud.google.com](https://console.cloud.google.com/)** e inicia sesión (puede ser `admin@origenlab.cl` u otra cuenta con permiso de proyecto).

2. Arriba, elige o **crea un proyecto** (ej. `origenlab-email-ingest`).

3. Menú **☰** → **APIs y servicios** → **Pantalla de consentimiento de OAuth**:
   - Tipo de usuario: **Interno** si solo cuentas `@origenlab.cl` en tu Workspace (lo más simple).
   - Rellena nombre de la app, email de contacto, dominio si lo pide.
   - Guarda y continúa hasta cerrar el asistente.

4. **APIs y servicios** → **Credenciales** → **+ Crear credenciales** → **ID de cliente OAuth**:
   - Tipo de aplicación: **Aplicación de escritorio** (en inglés: **Desktop app**).
   - Nombre cualquiera (ej. `origenlab-pipeline-mac`).
   - **Crear** → **Descargar JSON** (icono de flecha abajo).

5. Mueve ese archivo **fuera del repo**, por ejemplo:
   - `~/secrets/origenlab-gmail-oauth-client.json`

6. En **`apps/email-pipeline/.env`** pon la ruta **absoluta**:

   ```bash
   ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON=/home/TU_USUARIO/secrets/origenlab-gmail-oauth-client.json
   ORIGENLAB_GMAIL_WORKSPACE_USER=contacto@origenlab.cl
   ```

   (`ORIGENLAB_GMAIL_TOKEN_JSON` es **opcional** hasta que quieras fijar una ruta concreta para el token.)

---

## Scope (qué permiso pide el script)

El script usa el scope **`https://mail.google.com/`** porque **Gmail IMAP con XOAUTH2** lo exige; el scope “solo lectura” de la API REST **no** sustituye a este para IMAP.

Si la pantalla de consentimiento es **Interna**, suelen poder autorizarlo todos los usuarios del Workspace sin publicar la app.

---

## Instalar dependencias

```bash
cd apps/email-pipeline
uv sync --group ml --group workspace --group dev
```

---

## Primera ejecución (aquí aparece el “token”)

```bash
uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py \
  --folder INBOX \
  --since-days 30 \
  --max-messages 50 \
  --skip-duplicate-message-id
```

1. Se abre el **navegador**.
2. Inicia sesión con **`contacto@origenlab.cl`** (el mismo buzón que `ORIGENLAB_GMAIL_WORKSPACE_USER`).
3. Acepta los permisos.
4. El script **escribe** el archivo de token (ruta por defecto o la de `ORIGENLAB_GMAIL_TOKEN_JSON`).

Las siguientes veces **no** debería abrir el navegador si el refresh token sigue válido.

---

## Carpetas IMAP (Gmail)

Los nombres bajo `[Gmail]/...` dependen del **idioma de la interfaz de Gmail** del buzón (no del cliente IMAP). Si `--folder` falla con *Could not select folder*, no uses el nombre “de manual” en inglés: lista lo que devuelve el servidor y copia la línea tal cual.

```bash
uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --list-folders
```

| Uso | Gmail UI **inglés** (típico) | Gmail UI **español** (típico) |
|-----|------------------------------|--------------------------------|
| Bandeja de entrada | `INBOX` | `INBOX` |
| Enviados | `[Gmail]/Sent Mail` | `[Gmail]/Enviados` |
| Borradores | `[Gmail]/Drafts` | `[Gmail]/Borradores` |
| Destacados | `[Gmail]/Starred` | `[Gmail]/Destacados` |
| Importantes | `[Gmail]/Important` | `[Gmail]/Importantes` |
| Spam | `[Gmail]/Spam` | `[Gmail]/Spam` |
| Papelera | `[Gmail]/Trash` | `[Gmail]/Papelera` |
| Todos | `[Gmail]/All Mail` | `[Gmail]/Todos` |

No uses solo `[Gmail]` como `--folder`: es contenedor; elige una carpeta hoja (p. ej. `[Gmail]/Enviados`). Para el nombre exacto en tu cuenta, prioriza la salida de `--list-folders`.

---

## Misma base SQLite que el resto del pipeline

Las filas llevan `source_file` = `gmail:contacto@origenlab.cl/INBOX`.  
`--replace-source` borra solo ese origen y vuelve a insertar.  
`--skip-duplicate-message-id` evita duplicar si mezclas con datos de PST.

---

## Problemas frecuentes

- **`access_denied` / app en pruebas:** en consentimiento **Externo**, añade `contacto@` como usuario de prueba, o usa **Interno** con cuentas del dominio.
- **`invalid_client`:** el JSON no es el de **Escritorio** o la ruta en `.env` está mal.
- **Admin bloquea apps OAuth:** en Admin de Google → **Seguridad** → **Controles de API** / apps de terceros (nombre exacto según consola) y permite el cliente o el tipo de app.

---

## Titan vs Workspace (documentación)

[`apps/web/docs/email-setup.md`](../../../web/docs/email-setup.md) puede seguir mencionando Titan. Si el correo operativo es **solo** Workspace, conviene alinear ese doc cuando el equipo lo decida.
