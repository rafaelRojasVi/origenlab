# Claude Code — OrigenLab

**Read [`AGENTS.md`](./AGENTS.md) first** for repo-wide rules, business truth, and when to open `docs/`.

## Router (use before coding)

| Need | Open |
|------|------|
| Business facts, copy, categories, contact | `src/data/*` (`company`, `contact`, `categories`, `services`, `brands`, `faq`, `documents`) |
| Cifras públicas: redacción, fuente, aprobación | `src/data/claims.ts` (usar `publicClaim(id)`, nunca el arreglo) |
| Alcance de equipamiento por familia | `src/data/equipmentScope.ts` |
| Marcas publicadas (lista cerrada de seis) | `src/data/brands.ts` (`APPROVED_BRAND_IDS`) |
| Fuente oficial, PDF, procedencia y permiso de imagen | `src/data/sourceRegistry.ts` |
| Sistema de marca: construcción, colores, animación | `docs/logo-system.md` |
| Asesoría técnica y hechos de la especialista | `src/data/consultation.ts` |
| Estado legal y datos de identidad pendientes | `src/data/legal.ts` |
| Deploy, `dist/`, HostGator | `docs/deployment.md` |
| Security / prior claims decisions | `docs/security-audit-v1.md` |
| Sistema visual, tokens, primitivas | `docs/design/DESIGN_SYSTEM.md` |
| Qué falta confirmar antes de publicar (negocio o abogado) | `docs/design/CONTENT_NEEDED.md` |
| Reusable workflows | `.claude/skills/*/SKILL.md` |
| Collaborator / AI onboarding | `CONTRIBUTING.md` |
| Company scope & quotation prompt | `docs/company-scope.md` |

Stack: Astro + Tailwind v4, static site, Spanish-first. Build: `npm run build` → `dist/`.
Full gate before declaring work done: `npm run validate` (check + build + catalog +
brands + dist + contrast + interaction), then `npm run qa:screens` for the visual
and accessibility pass. `npm run verify:sources` comprueba los enlaces externos
contra la red y por eso queda fuera de `validate`.

Two hard constraints beyond the content rules:

- **No third parties.** The production build must not load a script, stylesheet,
  font or image from any domain other than `origenlab.cl`. `validate:dist`
  enforces it.
- **One design system.** Colors, radii, spacing and type roles come from the
  tokens in `src/styles/global.css`; group with hairlines and space, not cards.
  Read `docs/design/DESIGN_SYSTEM.md` before adding a component.

**Las marcas son una lista cerrada de seis.** `APPROVED_BRAND_IDS` en
`src/data/brands.ts` es la única lista que el sitio público puede mostrar, y
`validate:brands` la comprueba en `brands.ts`, en `public/brands/`, en
`src/data/sourceRegistry.ts` y en el HTML de `dist/`. Añadir o quitar una marca
exige confirmación escrita del negocio.

**Ninguna imagen de fabricante se publica sin procedencia y permiso en
`src/data/sourceRegistry.ts`.** Que una imagen sea visible en público no implica
permiso de reutilización, no se rehospedan PDF del fabricante y jamás se ilustra
una marca con el equipo de otra. Las familias sin fotografía autorizada se
componen con el diagrama de `src/lib/familyMotif.ts`.

Do not invent brands, certifications, specs, lead times, or warranty details not in data or docs.

**Numbers are gated.** Every visible commercial figure lives in `src/data/claims.ts`
with wording, source, measurement date, approver and public/internal status, and
reaches a template only through `publicClaim(id)`. `validate:catalog` checks the
register's shape; `validate:dist` fails if the literal wording of an unapproved
claim appears in the built HTML. CRM contacts, organizations, campaign recipients
and historical email are never clients or sales.

**`/privacidad/` and `/aviso-legal/` are review drafts.** They are `noindex`, out
of the sitemap and must not be deployed until a qualified Chilean professional
reviews the text and the business supplies the legal identity in `src/data/legal.ts`.
