# OrigenLab — Auditoría de seguridad y arquitectura v1

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-03-23

Auditoría enfocada para el sitio estático Astro desplegado en HostGator (sin backend).

**Nota de alineación con el código:** Las secciones 2–3 y el resumen final reflejan el estado **actual** del repositorio (recursos de terceros, meta tags, implicaciones para CSP). Las tablas históricas de hallazgos más abajo se conservan con el estado **Hecho / pendiente** actualizado donde correspondía.

---

## 1. Estructura del proyecto

**Estado: adecuada para un sitio brochure/catálogo de pequeña empresa.**

- **config**: `site.ts` centraliza nombre, dominio, email, navegación. Sin complejidad innecesaria.
- **data**: `categories.ts` y `brands.ts` son datos estáticos; fáciles de extender.
- **components**: Header, Footer, Hero, QuoteCTA, PageHeader, Card. Reutilización clara.
- **layouts**: Un solo layout (`Layout.astro`) con slot. Mantenible.
- **pages**: Rutas planas (index, nosotros, productos, marcas, contacto) + `categorias/[slug]`. Coherente con sitio estático.

No se detectó complejidad innecesaria en páginas, componentes ni datos.

---

## 2. Seguridad (despliegue estático)

### Revisado y correcto

- **Sin backend**: No hay API, formularios que envíen a servidor propio ni base de datos en el sitio público. Riesgo de inyección/autenticación en el propio sitio: nulo.
- **Sin secretos en código**: No hay `process.env` / `import.meta.env` en `src/`. `.env` y `.env.production` están en `.gitignore`.
- **Scripts de terceros en el sentido de tracking**: No hay `<script src="http(s)://...">` de analytics, chat ni pixels. No hay llamadas XHR/fetch a terceros en el bundle del sitio con ese fin.
- **Recursos de terceros (tipografía)**: `Layout.astro` enlaza hojas de estilo desde **Google Fonts** (`https://fonts.googleapis.com` y `https://fonts.gstatic.com`, `preconnect` + `stylesheet`) para **Plus Jakarta Sans**. Es una **dependencia operativa** de terceros (disponibilidad y política de privacidad del proveedor). Todo por **HTTPS**; no hay mixed content en esos enlaces.
- **Enlaces en contenido**: En el cuerpo del sitio, los `href` de navegación son relativos (`/`, `/contacto`, etc.) o `mailto:contacto@origenlab.cl`. No hay redirecciones en contenido a dominios ajenos con fines de navegación.
- **Redirect interno**: Solo `Astro.redirect('/productos')` cuando no existe la categoría; destino fijo y controlado.

### Observaciones

- **CSP (Content-Security-Policy)**: Si se añade CSP en `.htaccess`, debe **permitir explícitamente** los orígenes de Google Fonts (`style-src` / `font-src` para `fonts.googleapis.com` y `fonts.gstatic.com`). Un `default-src 'self'` **sin** esas excepciones **rompería** la carga de fuentes. Además, revisar si el build de Astro requiere `'unsafe-inline'` en `style-src` para estilos críticos (comprobar en entorno de prueba antes de producción).

---

## 3. Favicon y meta

- **Favicon**: Presente en `public/favicon.svg` y referenciado en `Layout.astro` como `/favicon.svg`. Consistente en todas las páginas.
- **Meta básica**: Cada página define `title` y `description` vía el layout. `charset` UTF-8 y `viewport` correctos.
- **Canonical**: Implementado en `Layout.astro` con `link rel="canonical"` derivado de `site.baseUrl` y el path actual.
- **Open Graph y Twitter Card**: Implementados en `Layout.astro`: `og:type`, `og:locale`, `og:site_name`, `og:title`, `og:description`, `og:url`; `twitter:card`, `twitter:title`, `twitter:description`.
- **Opcional pendiente**:
  - `og:image` (mejor vista previa al compartir en redes).
  - `theme-color` para la UI del navegador.

---

## 4. Cabeceras de seguridad y HTTPS

- **Antes de la auditoría inicial**: No había orientación en el proyecto para HTTPS ni cabeceras de seguridad en HostGator.
- **Cambios realizados**:
  - Añadido `public/.htaccess` con:
    - Redirección HTTP → HTTPS (301).
    - Cabeceras: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`.
  - Actualizado `docs/deployment.md` con instrucciones para subir `.htaccess` y uso de HTTPS/cabeceras.

El `.htaccess` se copia a la raíz de `dist/` en el build; debe incluirse al subir los archivos al hosting (algunos clientes FTP ocultan archivos que empiezan por punto).

---

## 5. Información expuesta en HTML

- **Generator**: `<meta name="generator" content="…">` (valor de `Astro.generator`) expone la herramienta de build. Riesgo bajo; opcional quitarlo o dejarlo genérico si se quiere reducir fingerprinting.

---

## 6. Enlaces y redirecciones

- Enlaces internos comprobados: `/`, `/nosotros`, `/productos`, `/marcas`, `/contacto`, `/categorias/:slug`. Todas las rutas existen en el build.
- Única redirección: categoría inexistente → `/productos`. Correcta y segura.

---

## Clasificación de hallazgos

### Debe corregirse antes del lanzamiento

| # | Hallazgo | Acción |
|---|----------|--------|
| 1 | No había refuerzo de HTTPS ni cabeceras de seguridad documentadas para HostGator. | **Hecho**: añadido `public/.htaccess` y sección en `docs/deployment.md`. Verificar al desplegar que `.htaccess` sube y que HTTPS está activo en el dominio. |

No quedan ítems obligatorios pendientes para un lanzamiento estático básico.

---

### Conviene corregir pronto

| # | Hallazgo | Recomendación |
|---|----------|----------------|
| 1 | Al subir por FTP/cPanel, `.htaccess` puede no subirse si el cliente oculta archivos que empiezan por punto. | Incluir en checklist de despliegue: “Confirmar que `.htaccess` está en la raíz del sitio y que las visitas por HTTP redirigen a HTTPS”. |
| 2 | Previews sociales sin imagen dedicada. | Añadir `og:image` (URL absoluta bajo `https://origenlab.cl/...`) cuando haya recurso acordado. |

---

### Mejoras posteriores (nice to have)

| # | Hallazgo | Sugerencia |
|---|----------|------------|
| 1 | El meta `generator` expone detalle del generador. | Quitar la etiqueta o usar un valor genérico si se quiere reducir fingerprinting. |
| 2 | Política de contenido (CSP). | Diseñar CSP en staging: incluir Google Fonts y comprobar estilos del build de Astro antes de activar en producción. |
| 3 | Favicon solo en SVG. | Añadir `favicon.ico` en `public/` y enlazarlo en el layout como fallback para navegadores antiguos. |

---

## Resumen

- **Arquitectura**: Adecuada y mantenible para un sitio brochure/catálogo estático; sin complejidad innecesaria.
- **Seguridad estática**: Sin secretos en código del sitio; sin scripts de analytics/chat de terceros; enlaces de navegación y `mailto` controlados; **Google Fonts** como dependencia declarada de tipografía (HTTPS).
- **Meta y SEO social**: Canonical y Open Graph / Twitter básicos implementados en `Layout.astro`; `og:image` opcional pendiente.
- **Mejoras aplicadas**: `.htaccess` con HTTPS y cabeceras básicas; documentación de despliegue actualizada.
- **Próximos pasos recomendados**: Validar en producción HTTPS y `.htaccess`; valorar `og:image` y CSP con pruebas reales del build.

---

## Actualización 2026-09-06 (rediseño V2 del sitio público)

Esta sección corrige dos afirmaciones que quedaron obsoletas en el cuerpo del
documento y registra lo que cambió con el rediseño.

### Corrección de afirmaciones previas

- **Chat de terceros.** Este documento afirmaba que el sitio no cargaba scripts
  de chat de terceros. Era **incorrecto** desde antes de esta revisión:
  `Layout.astro` inyectaba el widget de Tidio (`code.tidio.co`) en todas las
  páginas, incluida `/logo-lab/`, de forma asíncrona y **antes de cualquier
  aviso o consentimiento**. Tidio declara en su política pública corresponsabilidad
  del tratamiento, procesamiento de IP y datos de dispositivo, y transferencia a
  Estados Unidos.
- **Cloudflare.** El sitio en producción responde con `server: cloudflare`, es
  decir, hay un proxy delante del origen HostGator que procesa las IP de los
  visitantes. No estaba documentado ni aquí ni en `deployment-status.md`.

### Estado tras el rediseño

| Elemento | Antes | Ahora |
|---|---|---|
| Tidio | Cargado en todas las páginas | **Eliminado.** Sin sustituto y sin otro chat |
| Google Fonts | `preconnect` + hoja remota en cada página | **Auto-hospedadas** en `public/fonts/` (SIL OFL 1.1), precargadas |
| Peticiones externas al cargar | 3 dominios de terceros | **Ninguna.** Verificado en cada build por `validate:dist` y en el navegador por `qa:screens` |
| CSP | Imposible sin romper Google Fonts | **Activa** en `public/.htaccess`, restringida a `'self'` |
| `meta generator` | Exponía la versión de Astro | Eliminado |
| Superficies internas | `/logo-lab/` y `/email/*.html` indexables | `noindex`, fuera del sitemap, `Disallow` en robots y `X-Robots-Tag` en `.htaccess` |
| JavaScript en producción | ~7,8 kB (canvas del logotipo animado) | ~8 kB, sólo mejora progresiva del menú móvil |
| Cookies propias | Ninguna | Ninguna (sin cambio) |
| Formularios | Ninguno | Ninguno. No se creó endpoint: la cotización sigue por correo y WhatsApp |

### Política de terceros

El sitio de producción no debe cargar ningún recurso desde un dominio distinto de
`origenlab.cl`. `npm run validate:dist` recorre el HTML construido y falla si
aparece un `<script src>` o un `<link rel="stylesheet">` externo, o cualquier
rastro de Tidio, Google Fonts, Google Analytics, Tag Manager, Hotjar o Facebook.

Incorporar un tercero exige, en este orden: decisión del negocio, revisión legal
(base de licitud y, si aplica, acuerdo de encargo), carga diferida tras una
acción explícita del visitante que nombre al proveedor, entrada en el inventario
de encargados y actualización de la CSP.

### Pendientes de revisión legal

La política de privacidad, el aviso legal y el canal de derechos del titular
siguen sin publicarse porque no existe texto revisado. El pie reserva el lugar
pero no enlaza páginas inexistentes. Detalle en
[`design/CONTENT_NEEDED.md`](design/CONTENT_NEEDED.md).
