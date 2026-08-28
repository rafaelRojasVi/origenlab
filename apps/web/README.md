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
| `npm run lint` | Mismo que `check` |

## Despliegue (HostGator)

1. `npm run check` y `npm run build`.
2. Subir **todo el contenido** de `dist/` a `public_html` (no una carpeta `dist` dentro de public_html).
3. Incluir el archivo `.htaccess` (HTTPS y cabeceras de seguridad).

Checklist completo y pasos: [docs/deployment.md](docs/deployment.md). Estado actual: [docs/deployment-status.md](docs/deployment-status.md).

## Estructura del proyecto

- `src/config/site.ts` — Configuración central (nombre, dominio, email, baseUrl, nav).
- `src/layouts/Layout.astro` — Layout principal (español, meta, canonical, Header/Footer).
- `src/pages/` — Inicio, nosotros, productos, marcas, contacto; categorías en `categorias/[slug].astro`.
- `src/components/` — Header, Footer, Hero, QuoteCTA, PageHeader, Card.
- `src/data/` — Categorías y marcas (datos estáticos).
- `src/styles/global.css` — Estilos globales y Tailwind.
- `public/.htaccess` — Se copia a `dist/`; forzar HTTPS y cabeceras básicas en el servidor.

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
| [docs/security-audit-v1.md](docs/security-audit-v1.md) | Auditoría de seguridad y arquitectura v1 |
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
