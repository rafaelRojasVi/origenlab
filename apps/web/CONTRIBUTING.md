# Guía para colaboradores (y uso con Claude / Cursor)

Estás en **`apps/web/`**: la app **sitio Astro** de OrigenLab (Chile). El **repositorio Git** es un **monorepo**: la raíz del clone contiene también **`apps/email-pipeline/`**, **`apps/api/`**, **`apps/dashboard/`**, y otros archivos compartidos — ver [README del monorepo](../../README.md) y [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../../docs/architecture/CURRENT_SYSTEM_TRUTH.md). Todo el código **del sitio** vive bajo `apps/web/`; la documentación y reglas de esta carpeta aplican a esa app.

---

## Por dónde empezar

| Si quieres… | Abre |
|-------------|------|
| **Entender el proyecto y correr el sitio** | [README.md](README.md) |
| **Reglas de negocio, datos y qué no inventar** | [AGENTS.md](AGENTS.md) |
| **Rutas rápidas para IA (qué archivo leer según la tarea)** | [CLAUDE.md](CLAUDE.md) |
| **Alcance de la empresa, contacto, tono y prompt para cotizaciones** | [docs/company-scope.md](docs/company-scope.md) |
| **Desplegar a HostGator** | [docs/deployment.md](docs/deployment.md) |

---

## Estructura de `apps/web/` (esta app)

Ruta desde la raíz del monorepo: **`apps/web/`**. Árbol principal:

```
apps/web/
├── src/                    # Código del sitio Astro
│   ├── data/               # Fuente de verdad: company, contact, categories, services, etc.
│   ├── components/
│   ├── layouts/
│   ├── pages/
│   ├── config/
│   └── styles/
├── public/                 # Assets estáticos (favicon, .htaccess, robots, sitemap)
├── docs/                   # Documentación (deploy, seguridad, alcance, email)
├── .github/                # Plantilla de PR para revisión (esta app)
├── .cursor/rules/          # Reglas Cursor para el sitio
├── .claude/skills/         # Skills Claude (deploy, copy, etc.)
├── AGENTS.md               # Instrucciones para agentes IA (reglas, datos, tono)
├── CLAUDE.md               # Router rápido para Claude
├── CONTRIBUTING.md         # Este archivo
├── LICENSE                 # MIT — ver README
├── .editorconfig
├── .gitattributes
└── .nvmrc                  # Node 20 (opcional: nvm use)
```

Los datos de negocio (empresa, contacto, categorías, servicios) están en **`src/data/*.ts`**. No duplicar en el código; usar esos módulos. Para copy y cotizaciones, el resumen está en **`docs/company-scope.md`**.

---

## Uso con Claude (Cursor / API)

- **CLAUDE.md** indica qué archivo leer según la necesidad (datos, deploy, seguridad).
- **AGENTS.md** es la referencia completa: negocio, contacto, reglas de contenido, tono, despliegue.
- **Skills** en `.claude/skills/` se usan para tareas concretas:
  - `astro-hostgator-deploy`: revisar build y subida a HostGator.
  - `brand-copy`: redacción de páginas y CTAs alineada a la marca.
- **docs/company-scope.md** incluye el prompt listo para reescribir cotizaciones con otra IA (p. ej. ChatGPT).

No inventar marcas, certificaciones, plazos ni garantías no documentadas en `src/data/` o en `docs/`.

---

## Uso con Cursor

Las reglas en **`.cursor/rules/`** se aplican automáticamente en este workspace:

- **project.mdc** — Reglas de negocio, tono, datos y calidad de código (siempre activas).
- **astro-deploy.mdc** — Build, `dist/` y despliegue estático (se activa con archivos de Astro/public).
- **marketing-pages.mdc** — Páginas de contenido y copy (se activa con las rutas correspondientes).

Al abrir **`apps/web/`** (o el monorepo con reglas que apunten aquí) en Cursor, esas reglas orientan las respuestas hacia `src/data/` y `docs/` de **esta** app.

---

## Resumen para revisión de código

- **Datos:** Cambios a texto de negocio/contacto/categorías → hacerlos en `src/data/*`, no hardcodear en componentes.
- **Copy:** Tono profesional, español Chile, sin hype; CTAs tipo «Solicitar cotización», «Contactar por WhatsApp».
- **Deploy:** Build con `npm run build`; subir el **contenido** de `dist/` (no la carpeta `dist`) al directorio público en HostGator; incluir `.htaccess`.
- **Seguridad/claims:** Antes de añadir enlaces o afirmaciones de confianza, revisar [docs/security-audit-v1.md](docs/security-audit-v1.md).

Si algo no está documentado aquí, **AGENTS.md** y **docs/company-scope.md** son la siguiente parada.
