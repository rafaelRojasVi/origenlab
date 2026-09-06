# OrigenLab — Sitio web

Este directorio (**`apps/web/`**) es la aplicación **marketing site** dentro del [monorepo raíz](../../README.md). El otro paquete principal es [`apps/email-pipeline/`](../email-pipeline/) ([README](../email-pipeline/README.md)). Desarrollo del sitio: siempre con cwd en **`apps/web/`** (o rutas equivalentes) para `npm`.

Sitio estático para **OrigenLab**, empresa de equipamiento y soluciones para laboratorio (Valdivia, Chile).  
**Stack:** Astro + Tailwind CSS ([`package.json`](package.json)). Contenido en español. Despliegue manual a HostGator (public_html).

**Alcance del negocio:** venta de equipos para laboratorios de servicio e investigación en Chile; líneas en alimentos, control de calidad y laboratorio clínico. Audiencia: laboratorios, universidades, clínicas, hospitales, I+D. Datos completos (contacto, servicios, tono, prompt para cotizaciones): [docs/company-scope.md](docs/company-scope.md). Código fuente de verdad: `src/data/*`.

**Monorepo / GitHub:** Política de seguridad, licencia y contribución del repositorio están en la raíz del clone: [`SECURITY.md`](../../SECURITY.md), [`CONTRIBUTING.md`](../../CONTRIBUTING.md), [`LICENSE`](../../LICENSE). La plantilla de **pull requests** por defecto es [`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md).

**Privacidad y datos:** El sitio es estático (sin backend en este repo). Los archivos de correo, bases SQLite, exportes JSONL e informes del pipeline viven en el otro paquete ([`apps/email-pipeline/`](../email-pipeline/)) y, en producción local, **fuera de git**; no subas datos operativos al árbol de `apps/web/`.

## Comandos

| Comando | Descripción |
|---------|-------------|
| `npm install` | Instalar dependencias |
| `npm run dev` | Servidor de desarrollo en `http://localhost:4321` |
| `npm run build` | Build de producción → carpeta `dist/` |
| `npm run preview` | Vista previa del build local |
| `npm run check` | Verificación de tipos y contenido (Astro) |
| `npm run validate` | **Puerta completa:** check + build + validate:catalog + validate:dist |
| `npm run validate:catalog` | Verdad de catálogo: marcas, productos, activos, especificaciones agrupadas, copy comercial |
| `npm run validate:dist` | Invariantes del HTML construido: cero terceros, encabezados, imágenes, sitemap, presupuesto de peso |
| `npm run qa:screens` | Recorrido en Chromium a 375 / 768 / 1440 px con capturas e informe en `.qa/` |

### Regeneración de activos

Estos comandos sólo se ejecutan cuando cambian los archivos de origen; su salida
se versiona.

| Comando | Salida |
|---------|--------|
| `npm run sync:fonts` | `public/fonts/*.woff2` desde los paquetes de Fontsource |
| `npm run build:brand-logos` | `public/brands/*.png` a tinta única desde `public/email/brands/*-source.*` |
| `npm run build:product-images` | Derivados 480/960 px en AVIF y WebP, con el fondo blanco recortado |
| `npm run build:social` | `public/og/origenlab-og.png` y `public/apple-touch-icon.png` |

## Despliegue (HostGator)

1. `npm run check` y `npm run build`.
2. Subir **todo el contenido** de `dist/` a `public_html` (no una carpeta `dist` dentro de public_html).
3. Incluir el archivo `.htaccess` (HTTPS y cabeceras de seguridad).

Checklist completo y pasos: [docs/deployment.md](docs/deployment.md). Estado actual: [docs/deployment-status.md](docs/deployment-status.md).

## Estructura del proyecto

- `src/config/site.ts` — Configuración central (nombre, dominio, navegación, destino del CTA).
- `src/layouts/Layout.astro` — Documento base; la cabecera del documento vive en `components/Seo.astro`.
- `src/pages/` — Inicio, productos, familia y ficha de centrífugas, aplicaciones,
  categorías (`categorias/[slug].astro`), marcas (`marcas/[slug].astro`),
  servicios, nosotros, contacto, 404 y la página interna `logo-lab`.
- `src/components/ui/` — Primitivas del sistema de diseño (ver
  [docs/design/DESIGN_SYSTEM.md](docs/design/DESIGN_SYSTEM.md)).
- `src/components/home/` — Hero y muro de marcas de la portada.
- `src/data/` — Verdad de negocio: empresa, contacto, servicios, categorías,
  marcas, productos, familias, agrupación de especificaciones, FAQ, documentos.
- `src/styles/global.css` — Tokens del sistema (`@theme`) y primitivas de CSS.
- `public/.htaccess` — Se copia a `dist/`: HTTPS, redirección de `www`, 404, CSP y cabeceras.

## Sistema de diseño

El sitio público se rediseñó por completo en la V2. Antes de añadir un color, un
radio, una sombra o un componente, leer
[docs/design/DESIGN_SYSTEM.md](docs/design/DESIGN_SYSTEM.md): define los tokens,
las primitivas y lo que el sistema no admite. Lo que el negocio todavía debe
aportar está en [docs/design/CONTENT_NEEDED.md](docs/design/CONTENT_NEEDED.md).

**El sitio no carga nada de terceros.** Ni scripts, ni tipografías, ni imágenes,
ni hojas de estilo. `npm run validate:dist` falla si alguno se cuela.

## Documentación

Índice principal: [docs/README.md](docs/README.md)
Inicio rápido para agentes: [docs/APP_CONTEXT.md](docs/APP_CONTEXT.md)

| Documento | Contenido |
|-----------|-----------|
| [docs/README.md](docs/README.md) | Índice canónico (gobernanza, arquitectura, operaciones, features, histórico) |
| [docs/deployment.md](docs/deployment.md) | Pasos de despliegue y checklist antes del lanzamiento |
| [docs/deployment-status.md](docs/deployment-status.md) | Estado actual, hosting, DNS, advertencias |
| [docs/email-setup.md](docs/email-setup.md) | Email contacto@origenlab.cl (Titan, IMAP/SMTP, DKIM) |
| [docs/company-scope.md](docs/company-scope.md) | Alcance, contacto, servicios, tono y prompt para redactar cotizaciones |
| [docs/security-audit-v1.md](docs/security-audit-v1.md) | Auditoría de seguridad y arquitectura v1, con la actualización del rediseño V2 |
| [docs/design/DESIGN_SYSTEM.md](docs/design/DESIGN_SYSTEM.md) | Sistema visual V2: tokens, primitivas, reglas |
| [docs/design/CONTENT_NEEDED.md](docs/design/CONTENT_NEEDED.md) | Contenido y revisión legal pendientes |
| [docs/design/WEBSITE_V2_DESIGN_BRIEF.md](docs/design/WEBSITE_V2_DESIGN_BRIEF.md) | Descubrimiento y brief que originó el rediseño |
| [docs/product-assets.md](docs/product-assets.md) | Procedencia de imágenes, logotipos, activos sociales y tipografías |
| [CLAUDE.md](CLAUDE.md) | Instrucciones para asistencia con IA |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Guía para colaboradores y uso con Claude/Cursor (reglas, skills, alcance) |

## Repo y ramas

- **Git:** el remoto es el **monorepo** (raíz del clone); este sitio vive bajo `apps/web/`.
- **Ramas:** suele usarse `main` y `dev` a nivel monorepo; desarrollo del sitio en la rama acordada del equipo.
- **Alcance de esta carpeta:** solo la app Astro; el monorepo incluye además `apps/email-pipeline/`, `apps/api/`, `apps/dashboard/`, y documentación de arquitectura en la raíz ([`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../../docs/architecture/CURRENT_SYSTEM_TRUTH.md)).

**Colaboradores y uso con Claude/Cursor:** ver [CONTRIBUTING.md](CONTRIBUTING.md) (estructura, dónde está cada cosa, reglas en `.cursor/rules/`, skills en `.claude/skills/`, alcance en `docs/company-scope.md`).

## Licencia

Licencia **MIT** del monorepo: [raíz `LICENSE`](../../LICENSE). También puede existir [LICENSE](LICENSE) en esta carpeta por el historial del subtree.  
**Contacto del sitio:** contacto@origenlab.cl
