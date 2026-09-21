# Despliegue — OrigenLab (HostGator)

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-09-21

Sitio estático generado con Astro. El resultado del build son HTML, CSS y assets en la carpeta `dist/`.

## Qué se despliega y qué no

Se despliega **el contenido de `dist/`**, y sólo eso.

`dist-preview/` es la compilación de revisión del boletín, la que dibuja el
formulario de suscripción. **No se despliega nunca.** Se genera con
`npm run build:preview` y existe para poder ver y probar el formulario mientras
las ocho puertas de `src/data/newsletter.ts` siguen cerradas. Si alguna vez se
subiera por error, el sitio ofrecería una suscripción que ningún endpoint
atiende: `npm run validate` falla ante un `dist/` con formulario justamente para
que eso no llegue a ocurrir.

Las rutas `/privacidad/`, `/aviso-legal/` y `/newsletter/` se
despliegan sin indexar (meta `noindex`, fuera del sitemap, `Disallow` en
`robots.txt` y `X-Robots-Tag` en `.htaccess`). Salen de ese estado cuando el
texto legal esté revisado y, en el caso del boletín, cuando el servicio esté
activo.

## Checklist antes del lanzamiento

- [ ] Ejecutar `npm run validate` (incluye `build` y `validate:dist`) y revisar que no haya errores.
- [ ] Comprobar que **no se está subiendo `dist-preview/`**.
- [ ] Subir **todo** el contenido de `dist/` al directorio público (p. ej. `public_html`), incluyendo **`.htaccess`** (en FTP/cPanel, activar “mostrar archivos ocultos” si no lo ve).
- [ ] Comprobar que la raíz del sitio contiene `index.html` y `.htaccess`.
- [ ] Abrir el sitio por **HTTP** (ej. `http://origenlab.cl`) y verificar que redirige a **HTTPS**.
- [ ] Revisar en el navegador: Inicio, Nosotros, Productos, Marcas, Contacto; una categoría (ej. alimentos); opcional `robots.txt` y `sitemap.xml` en la raíz del sitio.
- [ ] Probar el enlace “Enviar correo” en Contacto (debe abrir el cliente de correo con contacto@origenlab.cl).
- [ ] Verificar que **contacto@origenlab.cl** recibe y envía según **[docs/email-setup.md](email-setup.md)** (buzón principal en **Titan**; IMAP/SMTP y DNS/MX como allí se documentan). No asumir que el buzón se “crea solo” en cPanel: el sitio y el DNS pueden estar en HostGator mientras el correo operativo está en Titan.

## Pasos

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
| Subir archivos| FTP o cPanel → directorio público     |
| Dominio       | DNS → hosting (p. ej. HostGator) según estado actual |
| Correo contacto@ | Ver [email-setup.md](email-setup.md) (Titan; no usar solo cPanel como referencia del buzón) |
| Seguridad     | Subir `.htaccess`; HTTPS y cabeceras según archivo en raíz |

No se requiere Node.js en el servidor; solo se sirven archivos estáticos. Antes de dar por cerrado el lanzamiento, usar el **Checklist antes del lanzamiento** de esta página.
