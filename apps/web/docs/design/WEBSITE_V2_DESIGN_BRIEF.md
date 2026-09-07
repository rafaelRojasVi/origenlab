# OrigenLab Website V2 - Design and discovery brief

Status: proposal (design direction only, nothing implemented)
Owner: web-maintainers
Created: 2026-09-05
Baseline audited: `apps/web` at local `main` 66623060, byte-identical to `origin/main` 359ad4b2 for the `apps/web/` path (verified with `git diff --stat HEAD origin/main -- apps/web`, empty)
Scope: `apps/web/` only. No change is proposed to `apps/api`, `apps/dashboard`, `apps/email-pipeline`, CRM data, or marketing-email tooling.

This document is the design artifact for the V2 marketing site. It records what exists, what is wrong with it, and what V2 should be. It does not implement anything. Every business fact in it comes from `src/data/*`, `docs/company-scope.md`, or the built output; anything the redesign wants but the repository cannot prove is marked **CONTENT NEEDED** and listed in sections 26 and 27. Legal statements are framed as items for counsel, not conclusions (section 24).

Design read (declared before any design work): a **redesign-overhaul** of a scientific B2B distributor marketing site for institutional and technical buyers in Chile, in an **editorial-technical, restrained** visual language, built on the existing Astro + Tailwind v4 stack with self-hosted type and no component library. Dials: `DESIGN_VARIANCE 5`, `MOTION_INTENSITY 3`, `VISUAL_DENSITY 4`. The current site reads as roughly `3 / 3 / 5`.

---

## Contents

1. Current-state forensic inventory
2. What should be preserved
3. What should be removed or replaced
4. Main UX and design problems
5. Target audiences
6. Conversion hierarchy
7. Proposed information architecture (with ASCII sitemap)
8. Navigation model
9. Detailed homepage anatomy (with ASCII wireframes)
10. Product page anatomy
11. Category and application page anatomy
12. Brand page anatomy
13. Contact and quotation journey
14. Visual design principles
15. Typography recommendation
16. Color system recommendation
17. Spacing and grid system
18. Numbering and section-label system
19. Component inventory
20. Motion principles
21. Responsive strategy
22. Accessibility requirements
23. SEO requirements
24. Privacy and legal requirements
25. Third-party-script policy
26. Content gaps requiring human truth
27. Asset gaps
28. Proposed implementation slices
29. Test and visual-QA strategy
30. Explicit non-goals

Appendix A. Web Interface Guidelines review (file:line)
Appendix B. Evidence log and commands used

---

## 1. Current-state forensic inventory

### 1.1 Stack and operations

| Item | Value | Source |
|---|---|---|
| Framework | Astro (package range `^7.2.0`, `astro check` reports v7.1.5 installed), static output | `package.json`, build log |
| Styling | Tailwind CSS v4 via `@tailwindcss/vite`; tokens in `@theme` block; no `tailwind.config` | `astro.config.mjs`, `src/styles/global.css` |
| Language | TypeScript strict (`astro/tsconfigs/strict`) | `tsconfig.json` |
| Node | 22.12.0 pinned | `.nvmrc` |
| Hosting | HostGator shared hosting, manual FTP/cPanel upload of `dist/` into `public_html`; no CI/CD | `docs/deployment.md`, `docs/deployment-status.md` |
| Mail | `contacto@origenlab.cl` on Titan (not on the hosting) | `docs/email-setup.md` (referenced, not re-audited) |
| Checks | `npm run check`, `npm run build`, `npm run validate:catalog` | `package.json`, `scripts/validate-catalog.mjs` |
| Tests | None (no unit, e2e, visual, or a11y tests) | repo tree |
| Baseline health on 2026-09-05 | check: 0 errors, 1 hint (`threeBodySim.ts:161` unused param); build: 17 pages in 467 ms; catalog validation OK | this session |

### 1.2 Routes actually built (17)

```
/                                   home
/nosotros/                          about
/productos/                         products hub
/productos/centrifugas/             product family (only family)
/productos/centrifugas/biocen-22/   product detail (x5)
/productos/centrifugas/biocen-22-r/
/productos/centrifugas/digicen-22/
/productos/centrifugas/digicen-22-r/
/productos/centrifugas/consul-22/
/marcas/                            brands hub
/marcas/ortoalresa/                 brand page (hand-written)
/marcas/serva-electrophoresis/      brand page (hand-written)
/categorias/alimentos/              commercial line (x3)
/categorias/control-de-calidad/
/categorias/laboratorio-clinico/
/contacto/                          contact (no form)
/logo-lab/                          internal logo experiments, PUBLIC, not in sitemap, not noindexed
```

Also shipped inside `dist/` because they live under `public/`: 7 HTML files and ~40 assets under `/email/*` (Gmail signature tooling, including logos of IKA, Hielscher, CRTOP and Ollital, brands that do not exist in `src/data/brands.ts`). These are publicly reachable on the production origin.

Missing: `/404` (Astro has no `src/pages/404.astro`, so HostGator serves its default error page), `/privacidad`, any legal page, `/servicios`, `/cotizar`.

### 1.3 Business data layer (`src/data/`)

| File | Content | Notes |
|---|---|---|
| `company.ts` | name, geography Chile, one-liner, hero subtitle, home intro, catalog note, 6 audience segments, 3 value props | `valueProps` and `heroSubtitle` are no longer rendered anywhere |
| `contact.ts` | email, phone `+56 9 6256 7816`, WhatsApp E.164, public location "Valdivia, Chile", hours `09:00–18:00`, street address (internal only), `instagramHandle: null` | address policy: no street on public pages |
| `services.ts` | 5 offerings: Soporte, Asesorías, Garantía (según fabricante), Instalación, Puesta en marcha | not rendered on any page today |
| `categories.ts` | 3 commercial lines: alimentos, control-de-calidad, laboratorio-clinico, each with buyerGuide + 3 bullets | "alimentos" has zero related products |
| `brands.ts` | 2 brands: SERVA Electrophoresis GmbH, Ortoalresa (Álvarez Redondo S.A.) | copy forbids "distribuidor exclusivo / representante oficial" (validator enforces) |
| `products.ts` | 8 products: 5 Ortoalresa centrifuges with full manufacturer specs, images, PDF links; 3 SERVA SKUs without images or detail pages | all Ortoalresa `ctaText` = "Solicitar cotización" |
| `productFamilies.ts` | 1 family: centrifugas; canonical Ortoalresa order | |
| `faq.ts` | 4 questions, derived from company/contact data | |
| `documents.ts` | empty; model for future PDFs | "No incluir entradas reales hasta exista archivo y permiso de publicación" |

`src/config/site.ts` derives the SEO description from the data layer, sets `baseUrl https://origenlab.cl`, OG image path (SVG), and a 4-item nav.

### 1.4 Layout, header, footer

- `src/layouts/Layout.astro`: `lang="es"`, canonical with trailing slash, OG + Twitter tags, `og:image` pointing at an **SVG**, `apple-touch-icon` pointing at an **SVG**, `meta generator` exposed, Google Fonts `preconnect` + render-blocking stylesheet (Plus Jakarta Sans 400/500/600/700 + italic 400), skip link, `<main tabindex="-1">`, and at the end of `<body>` the Tidio loader (`<script is:inline src="//code.tidio.co/fhlfx2hwx24jm3sa2koagvs4vsn0jtbj.js" async>`, line 67).
- `Header.astro`: sticky, `bg-brand-950/95` + `backdrop-blur-md`, animated canvas logo lockup (three-body simulation, 18 s loop at 30 fps, `IntersectionObserver` pause, static SVG under `prefers-reduced-motion`), 4 nav links, white pill "Cotizar" to `/contacto`, mobile menu built with `<details>` (no Escape handling, no focus trap, no outside-click close).
- `Footer.astro`: teal gradient, 7-link catalog nav, static lockup, contact lines, `pb-28` bottom padding hack to keep the copyright above the Tidio bubble, copyright only. No legal entity, no privacy link, no terms.

### 1.5 Design system as it exists

Tokens (`global.css`): 11 teal shades `brand-50` … `brand-950` plus Tailwind slate/emerald defaults; one font; no spacing, radius, or motion tokens. 16 component classes (`btn-primary`, `btn-primary-light`, `btn-outline`, `btn-outline-dark`, `btn-whatsapp`, `card-elevated`, `home-*`, `showcase-*`).

Measured surface vocabulary (source grep, `src/**`):

| Device | Count | Reading |
|---|---:|---|
| `rounded-2xl` / `rounded-xl` / `rounded-lg` / `rounded-3xl` / `rounded-full` | 24 / 29 / 25 / 2 / 8 | five radii in play, no rule |
| `bg-gradient-to-*` | 14 | gradients on hero, panels, cards, CTA band, footer |
| `shadow-sm|md|lg|xl` | 53 | elevation used as default grouping |
| `backdrop-blur` | 3 | header, hero panel, outline button |
| `uppercase tracking-*` micro-labels (eyebrows) | 20 in production pages (+12 in logo-lab only) | eyebrow above almost every block |
| Pills/chips | hero trust chips (4), hero category chips (3), category card chips (9), filter chips (5, non-interactive), application chips (6-8 per product), support-note chips (4), spec chips | the "pill" is the dominant secondary element |

Visual read from screenshots at 1440 and 390 px (captured from the local preview build): dark teal gradient hero with a plus-sign SVG texture and a wave divider; a white glass panel holding product cards inside cards; three equal category cards with a colored left border; a gradient "Centrífugas Ortoalresa" panel with three more cards inside; teal-on-teal header and footer. The page is coherent and fast, but reads as a SaaS template: symmetric card grids, chips as decoration, gradient bands, one accent used as the whole palette.

### 1.6 Client-side JavaScript

| Script | Size (transfer) | Where |
|---|---:|---|
| `OrigenThreeBodyCanvas...js` (logo animation) | ~3 KB | every page (header) |
| Tidio loader → widget-v4 render bundle | 6.5 KB loader, then vendor bundle | every page |
| Everything else | 0 | pages are static HTML + one CSS file (~10 KB) |

### 1.7 Third-party surface (verified)

| Origin | How | Purpose | Consent gate |
|---|---|---|---|
| `fonts.googleapis.com` (+ `fonts.gstatic.com`) | `<link rel="preconnect">` + `<link rel="stylesheet">` in `<head>` | webfont CSS and files | none |
| `code.tidio.co` | protocol-relative inline `<script async>` at end of `<body>` | live chat | none |
| `socket.tidio.co` | referenced by the Tidio bundle (WebSocket) | chat transport | none |
| `cdnjs.cloudflare.com` | referenced inside the Tidio bundle | vendor dependency | none |

No analytics, no tag manager, no pixels, no consent management, no cookie banner, no privacy page (grep for `privacidad|cookie|consent|localStorage|gtag|analytics` across `src/` returns only the Tidio line).

### 1.8 Accessibility and contrast measurements (current tokens)

| Pair (usage) | Ratio | AA text (4.5) | AA large / UI (3) |
|---|---:|---|---|
| white on `emerald-600` (`.btn-whatsapp`, every WhatsApp button) | 3.77 | FAIL | pass |
| white on `emerald-500` (HomeFinalCTA WhatsApp button) | 2.54 | FAIL | FAIL |
| `slate-400` on white (FAQ chevron) | 2.56 | FAIL (decorative, low impact) | FAIL |
| white on `brand-700` (`.btn-primary`) | 5.47 | pass | pass |
| `slate-500` on white (small meta text) | 4.76 | pass | pass |
| `brand-200/300/400` on `brand-900/950` (footer, hero) | 5.09 to 11.48 | pass | pass |

Other measured facts: 7 `<img>` tags without `width`/`height` (CLS risk; list in Appendix A); `scroll-behavior: smooth` set globally without a reduced-motion guard; the header logo animation loops indefinitely with no pause control (WCAG 2.2.2 concern, see section 22); mobile tap targets: category chips 24 px tall, footer links 18 to 20 px tall, FAQ summaries 20 px tall; no horizontal overflow at 390 px; one `h1` per page and a sane `h2`/`h3` outline on the home page.

### 1.9 SEO surface (current)

Present: unique titles and descriptions per page, canonical URLs, OG/Twitter tags, `robots.txt`, hand-maintained `sitemap.xml` (16 URLs, no `lastmod`), product `metaTitle`/`metaDescription`.
Absent: any Schema.org structured data, a 404 page, a raster OG image, PNG touch icons, `theme-color`, breadcrumbs, `noindex` on `/logo-lab`, sitemap generation from routes, hreflang (not needed, single locale).

### 1.10 Prior decisions on record (2026-05-16 audit series, `docs/audits/`)

Kept: Tidio as the chat channel (FloatingChat removed), SERVA and Ortoalresa at equal visual level, five Ortoalresa centrifuges as the editorial family, Bioprocen 22 R withdrawn but its image archived, conservative commercial copy (no exclusivity, no stock, no lead times). Deferred: SERVA detail pages, header categories dropdown, PNG OG image, Tidio replacement. Open TODO: written permission to reproduce Ortoalresa and SERVA logos and product images on origenlab.cl (`docs/product-assets.md`).

### 1.11 Artifacts inspected that are NOT repository truth

`docs/origenlab-brochures/catalog-premium/` (untracked, local) contains a brochure prototype that presents six lines (Ortoalresa, IKA, Hielscher, SERVA, KNAUER/Löser, CRTOP/Ollital). `public/email/brands/` ships logos for IKA, CRTOP, Ollital, Hielscher for the Gmail signature. Neither source is in `src/data/`, and `AGENTS.md` forbids inventing brands. V2 must not surface these brands until the data layer records them with confirmed commercial status (section 26).

---

## 2. What should be preserved

- **Business truth discipline.** The data-layer-first model (`src/data/*` as the single source), the "no invented claims" policy, the WhatsApp/mailto prefill helpers in `src/lib/whatsapp.ts`, and the CTA dictionary in `src/lib/ctaLabels.ts`.
- **Conservative commercial copy.** "Cotización y disponibilidad sujetas a confirmación comercial", manufacturer attribution on specs, the clinical disclaimer on the laboratorio-clínico page, the explicit "no implica representación exclusiva" lines.
- **Product data quality for Ortoalresa.** Five complete fichas with manufacturer specs, PDFs, and locally hosted AVIF images. This is the strongest content the site has and V2 should give it more room, not less.
- **URL structure.** Every current slug (`/productos`, `/productos/centrifugas/[slug]`, `/marcas/[slug]`, `/categorias/[slug]`, `/nosotros`, `/contacto`). Redesign must not churn indexed URLs; additions only, plus 301s for anything that moves.
- **The atom mark and wordmark.** The logo system (`src/components/logo/*`, `public/logo/*`, favicon) is a real brand asset with documented rationale (`docs/logo-system.md`). Its treatment changes (section 20), the mark does not.
- **Brand teal as identity.** `#0f766e` (brand-700) stays the accent; it passes AA on white and on the proposed warm white. What changes is how much surface it covers.
- **Static-first architecture.** Astro static output, minimal JS, shared-hosting compatibility, the `.htaccess` HTTPS redirect and security headers.
- **Catalog validator.** `scripts/validate-catalog.mjs` guards real business invariants (removed SKUs, canonical order, safe copy, asset presence). It also hard-codes homepage component names; V2 must update those guards deliberately, not delete the script.
- **Accessibility wins already in place.** Skip link, `focus-visible` rings on every interactive class, `aria-labelledby` on sections, reduced-motion fallback for the logo animation, `<details>`-based FAQ.
- **Existing Spanish copy voice.** Serious, short, Chilean business Spanish. The V2 rewrite is a tightening, not a new voice.

## 3. What should be removed or replaced

| Remove / replace | Why | Replacement |
|---|---|---|
| Teal gradient hero with SVG plus-pattern and wave divider (`HomeHero.astro`, `Hero.astro`) | template signature; decorative texture; wave divider is a 2019 SaaS tell | flat graphite or warm-white hero, hairline rules, one real product photograph |
| Glass panel with nested product cards in the hero (`.home-panel`) | cards inside cards; three CTAs plus 4 chips plus 3 chips in one viewport | single product figure with caption; two CTAs |
| Trust chips, category chips, support-note chips, filter chips that do nothing (`HomeHero`, `HomeCategoryCards`, `HomeProcess`, `centrifugas/index.astro`) | pills as decoration; non-interactive chips look like broken filters | plain text lines, definition lists, real filters or nothing |
| Three equal category cards with letter "icons" (`HomeCategoryCards.astro`) | the canonical AI/template feature row; fake icons | numbered index list (section 9, block 01) |
| `card-elevated` with `border-l-4` colored edge as the default container | elevation used to group everything | hairline dividers and whitespace; cards only where a product figure needs a frame |
| Five radii, 14 gradients, 53 shadows | no rule | one radius scale (section 17), no gradients, shadows only on overlays (menu, dialog) |
| Emerald WhatsApp buttons (`.btn-whatsapp`, HomeFinalCTA) | AA contrast failure; a second brand color competing with the primary CTA | WhatsApp as a secondary graphite action with icon; one filled teal primary per view |
| Three CTAs in the hero, three in the final band, up to four per product card | duplicate intent, decision fatigue | one primary ("Solicitar cotización"), one secondary, channels as text links |
| Header "Cotizar" pill in white | label differs from the site-wide "Solicitar cotización" | same label everywhere; teal primary button |
| Google Fonts `<link>` | third-party request on every page view, render-blocking, privacy exposure | self-hosted WOFF2, `font-display: swap`, preload |
| Tidio loaded unconditionally on all pages | privacy exposure without notice or consent; covers footer; slows first interaction | consent-gated load or removal (section 24, 25); decision needed |
| SVG `og:image`, SVG `apple-touch-icon` | most social scrapers and iOS ignore SVG | 1200×630 PNG/JPG OG, 180×180 PNG touch icon, PNG favicons |
| Hand-maintained `public/sitemap.xml` | drifts from routes | `@astrojs/sitemap` (or a build script) generating from routes |
| `/logo-lab` in production build; `/email/*` under `public/` | internal tooling shipped publicly; ships third-party brand logos not on the site | move to a dev-only route or a non-public tooling folder outside `public/` |
| `<meta name="generator">` | fingerprinting, no benefit | remove |
| Global `scroll-behavior: smooth` | ignores reduced motion | gate behind `prefers-reduced-motion: no-preference` |
| Uppercase eyebrows on nearly every block, availability lines in small caps | noise; the same rhythm on every section | one section-index system (section 18); availability as normal-case body text |
| "Fabricante" / "Visión general" / "Marca" eyebrow labels differing per brand page | inconsistent template | one brand-page template driven by `brands.ts` |
| Footer `pb-28` hack for the chat bubble | layout bent around a third party | remove with the consent-gated chat |
| `company.valueProps`, `heroSubtitle` dead data; `Card.astro`, `Hero.astro`, `HomeBrandsSection.astro` unused | dead code and data | delete after V2 components land (search callers first, per repo rule) |

## 4. Main UX and design problems

1. **Generic visual language.** Gradient hero, glass panel, chips, three equal cards, colored-edge cards, rounded everything. Nothing in the composition says "laboratory equipment"; the only scientific object on the page is the product photo, and it is boxed inside two nested containers.
2. **Teal used as base, not accent.** Header, hero, panel borders, footer, CTA band, chips and links are all teal. The accent has nothing to contrast against, so hierarchy collapses and the brand color stops signalling "action".
3. **CTA chaos.** Home hero: white "Solicitar cotización" (→ /contacto), outline "Ver productos", green "Cotizar por WhatsApp". Header: "Cotizar". Final band: green WhatsApp first, then "Enviar correo", then "Ir a contacto". Product cards: "Ver ficha" + WhatsApp. The primary conversion has four labels and three destinations.
4. **Contact page is a dead end.** A single card with a definition list and two buttons. No form, no guidance on what to send, no reassurance about response, no privacy notice for the data the visitor is about to send by email.
5. **No applications layer.** Categories are "commercial lines" (alimentos, control de calidad, clínico) with buyer guides but almost no products; the only real product family (centrífugas) is attached to two of them and absent from the third. Visitors searching by application (preparación de muestras, electroforesis) have no landing.
6. **Brand pages are two hand-written templates.** Different section labels, different CTA placement, different logo treatment. SERVA has no product images and no detail pages, so the same card component renders two levels of completeness side by side.
7. **Product family page shows fake filters.** Five chips styled as filters that filter nothing.
8. **Spec table is a 13 to 19 row hairline table.** Correct data, poor scanning; no grouping, no tabular numerals, no featured specs.
9. **Motion without purpose.** An 18-second looping physics animation in the header on every page, a fade-up on every main element, hover lifts on every card. None of it communicates hierarchy or state.
10. **Typography does no work.** One weight (700) for every heading, one size step between h2 and h3, body at 14 px in cards. No scale, no measure discipline, no numerals treatment for specs.
11. **Trust is asserted, not shown.** Chips say "Asesoría antes de comprar" and "Disponibilidad sujeta a confirmación". The repo has no customers, testimonials, certifications, team, or history to show, so the current site compensates with disclaimers. V2 must earn trust through precision (real specs, real photos, clear process, legal transparency), not through more claims.
12. **Legal and privacy void.** No privacy policy, no cookie or chat notice, no legal entity in the footer, while a third-party chat script loads on every page.

## 5. Target audiences

From `company.audience` and `categories.ts`; no personas are invented beyond what the data implies.

| Segment (data) | Typical role | What they need from the site | Conversion |
|---|---|---|---|
| Laboratorios de servicios | jefe de laboratorio, analista senior | equipment by application, specs, quick quote for a known model | quotation with model/config |
| Laboratorios de investigación, universidades | investigador/a, encargado/a de compras | technical fichas, manufacturer PDFs, alternatives, purchase via institutional procurement | quotation with institution and application |
| Clínicas, hospitales (laboratorio clínico) | tecnólogo/a médico, jefe de laboratorio clínico, abastecimiento | credible, non-overclaiming information; clarity that OrigenLab sells equipment and does not certify clinical suitability | quotation; often formal RFQ |
| Industrias de I+D, control de calidad (alimentos, QA/QC) | QC lead, ingeniero/a de procesos | application-first navigation, comparison, service and installation scope | quotation |
| Procurement / licitaciones (implied by hospitals, universities) | comprador público o institucional | formal identity (legal name, RUT, address for invoicing), documentation, response channel | quotation via email with formal data |

Cross-cutting: all segments are B2B, Spanish-speaking, mostly desktop at work and mobile for WhatsApp. They judge credibility by precision and restraint, not by marketing energy.

## 6. Conversion hierarchy

1. **Primary: Solicitar cotización.** One label, one destination (`/cotizar`, section 13), one visual treatment (filled teal button). Present in the header, in the hero, at the end of every product, family, brand, and category page, and as the footer's last block. Product context travels with the click (`?producto=biocen-22`, `?marca=ortoalresa`, `?linea=control-de-calidad`).
2. **Supporting channels: correo and WhatsApp.** Text links or secondary buttons that reuse the existing prefilled message builders. They never appear as a second filled colored button next to the primary. WhatsApp keeps its glyph so it is recognizable, in graphite.
3. **Discovery: Ver equipos / Ver ficha / Ver aplicaciones.** Tertiary links (underlined text, arrow) that move the visitor toward a quotation with more context.
4. **Retention: Descargar ficha del fabricante.** External links, marked as such, to manufacturer PDFs already in `products.ts`.
5. **Chat (if kept):** an explicit, consented, secondary channel. It must never be the first visible call to action.

Rule: exactly one filled button per viewport region; duplicate intent shares one label (`ctaLabels.solicitarCotizacion`).

## 7. Proposed information architecture

Slug policy: keep every existing URL; add new ones; 301 anything that moves. `/categorias/*` is kept as the canonical URL for the three commercial lines (renaming to `/aplicaciones/*` would churn indexed URLs for no user benefit; the nav label can still read "Aplicaciones").

```
origenlab.cl
│
├── /                                    Inicio
│
├── /productos/                          Equipos e insumos (hub: familias + marcas + líneas)
│   ├── /productos/centrifugas/          Familia: Centrífugas (Ortoalresa)
│   │   ├── /productos/centrifugas/biocen-22/
│   │   ├── /productos/centrifugas/biocen-22-r/
│   │   ├── /productos/centrifugas/digicen-22/
│   │   ├── /productos/centrifugas/digicen-22-r/
│   │   └── /productos/centrifugas/consul-22/
│   └── /productos/electroforesis/       Familia: Reactivos e insumos de electroforesis (SERVA)   [NEW, needs SERVA SKU data review]
│       ├── /productos/electroforesis/blueslick-42500/                                             [NEW, CONTENT NEEDED: image, description]
│       ├── /productos/electroforesis/temed-25ml/                                                  [NEW]
│       └── /productos/electroforesis/repel-silane-ge17-1332-01/                                   [NEW]
│
├── /categorias/                         Aplicaciones y líneas (index)                              [NEW index page]
│   ├── /categorias/alimentos/
│   ├── /categorias/control-de-calidad/
│   └── /categorias/laboratorio-clinico/
│
├── /marcas/                             Marcas
│   ├── /marcas/ortoalresa/              (one data-driven template for all brands)
│   └── /marcas/serva-electrophoresis/
│
├── /servicios/                          Servicios y asesoría técnica (from services.ts)          [NEW]
│
├── /nosotros/                           OrigenLab (about)
│
├── /cotizar/                            Solicitar cotización (structured request)                [NEW, primary conversion]
├── /contacto/                           Contacto (channels, hours, location, legal identity)
│
├── /privacidad/                         Política de privacidad (versioned)                        [NEW]
├── /privacidad/solicitudes/             Ejercicio de derechos / data-subject requests             [NEW, may fold into /privacidad]
├── /404                                 Página no encontrada                                      [NEW]
│
├── sitemap-index.xml, robots.txt        generated
│
└── NOT PUBLIC in V2: /logo-lab (dev-only route or removed from build), /email/* (moved out of public/)
```

Relationship rules (drive internal linking and structured data):

- Product → belongs to exactly one family, one brand, one or more categories.
- Family → lists its products, links its brand(s) and categories.
- Brand → lists its families and products, links manufacturer site.
- Category (aplicación / línea) → lists related families and products, plus buyer guide; if it has no products (alimentos today) it shows the guide and the quotation path only, and says so honestly.
- Every leaf ends with the quotation block carrying its context.

## 8. Navigation model

Primary navigation (desktop, one line, ≤ 72 px tall):

```
[ atom  OrigenLab ]        Productos   Aplicaciones   Marcas   Servicios   Nosotros     [ Solicitar cotización ]
```

- "Productos" → `/productos/`. Optional second-level on hover/focus later (families + brands); V2 ships without a mega menu.
- "Aplicaciones" → `/categorias/` index (label in Spanish is "Aplicaciones"; URL stays `/categorias/`).
- "Servicios" → `/servicios/`.
- "Nosotros" → `/nosotros/`.
- "Contacto" leaves the primary bar (it is in the footer, in the CTA block, and one click from `/cotizar`). If the business insists on "Contacto" in the header, drop "Nosotros" to keep five items; do not run six plus a button.
- Header CTA: filled teal "Solicitar cotización" → `/cotizar/`.

Mobile: logo left, "Menú" button right (a real `<button aria-expanded>` controlling a panel, replacing `<details>`), panel lists the five items plus contact channels plus the primary CTA; closes on Escape and on route change; focus returns to the button.

Secondary / footer navigation (four columns on desktop, stacked on mobile):

1. Equipos: Centrífugas, Electroforesis (when live), Ver todos los productos.
2. Aplicaciones: Alimentos, Control de calidad, Laboratorio clínico.
3. Marcas y empresa: Ortoalresa, SERVA, Servicios, Nosotros.
4. Contacto: email, teléfono, WhatsApp, horario, "Valdivia, Chile · atención en todo Chile".

Legal line: © year OrigenLab · **CONTENT NEEDED: razón social y RUT** · Política de privacidad · Solicitudes sobre datos personales · (version tag of the policy).

Breadcrumbs on every page below the home: `Inicio / Productos / Centrífugas / Biocen 22`, rendered as a `<nav aria-label="Ruta">` list and mirrored in `BreadcrumbList` JSON-LD.

## 9. Detailed homepage anatomy

Section order and job. Numbers are the visible section index (section 18). The hero carries no number.

| # | Block | Job | Content source | Layout family |
|---|---|---|---|---|
| – | Hero | state what OrigenLab does and for whom; one product figure; one primary CTA | `company.oneLiner`, product image from `products.ts` | asymmetric split (text 7 cols / figure 5 cols) |
| 01 | Líneas de trabajo | orient by application: alimentos, control de calidad, laboratorio clínico | `categories.ts` | numbered index list with hairlines (no cards) |
| 02 | Equipos | show the real catalog: 5 centrifuges with photo, type, two key specs | `products.ts` (Ortoalresa), `getShowcaseSpecChips` | product grid 3 + 2, figures on warm-white, mono spec line |
| 03 | Reactivos e insumos | SERVA line: 3 references as a compact table-list, link to brand | `products.ts` (SERVA), `brands.ts` | two-column list, no images (none exist) |
| 04 | Marcas | two manufacturer logos with one truthful sentence each | `brands.ts` | hairline strip, logos at equal height |
| 05 | Cómo cotizamos | four steps; sets expectations without promising times | `HomeProcess` steps (move to data) | horizontal numbered timeline |
| 06 | Servicios | soporte, asesorías, garantía según fabricante, instalación, puesta en marcha | `services.ts` | definition list, two columns |
| 07 | Preguntas frecuentes | the 4 existing questions | `faq.ts` | `<details>` list with hairlines |
| 08 | Solicitar cotización | closing conversion block | `ctaLabels`, `contact.ts` | full-width graphite band, one primary button, channels as text |
| – | Footer | secondary nav, contact, legal | `site.ts`, `contact.ts` | four columns |

Rules applied: eight sections use six distinct layout families; no two adjacent sections share a family; one filled button per section; no chips; no gradients; the only images are real product photographs and the two brand logos.

### 9.1 Desktop wireframe (1280 to 1440 px)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ ⚛ OrigenLab        Productos   Aplicaciones   Marcas   Servicios   Nosotros  [Solicitar cotización] │  ← 64-72px, warm white, hairline below
├──────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│  Equipos para laboratorio,                        ┌──────────────────────────────┐       │
│  cotizados con criterio técnico                   │                              │       │
│  (h1, 2 lines max, 56-64px)                       │      [ product photograph ]  │       │
│                                                   │        Biocen 22 (AVIF)      │       │
│  Venta de equipos para laboratorios de servicio   │                              │       │
│  e investigación, con atención en todo Chile.     └──────────────────────────────┘       │
│  (≤ 20 words, from company.oneLiner)              Ortoalresa · Microcentrífuga ventilada  │  ← caption, mono, ink-muted
│                                                                                          │
│  [ Solicitar cotización ]   Ver equipos →                                                │
│                                                                                          │
├────────────────────────────────────────────── hairline ──────────────────────────────────┤
│ 01  Líneas de trabajo                                                                    │
│ ───────────────────────────────────────────────────────────────────────────────────────  │
│     Alimentos                Equipamiento para análisis y control en entornos …       →  │
│ ───────────────────────────────────────────────────────────────────────────────────────  │
│     Control de calidad       Instrumentación y equipos orientados a control …         →  │
│ ───────────────────────────────────────────────────────────────────────────────────────  │
│     Laboratorio clínico      Líneas de equipamiento para laboratorio clínico …        →  │
│ ───────────────────────────────────────────────────────────────────────────────────────  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 02  Equipos                                                        Ver todos los equipos → │
│                                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                                  │
│  │   [photo]    │   │   [photo]    │   │   [photo]    │                                  │
│  │              │   │              │   │              │                                  │
│  └──────────────┘   └──────────────┘   └──────────────┘                                  │
│  Biocen 22          Biocen 22 R        Digicen 22                                        │
│  Microcentrífuga    Microcentrífuga    Centrífuga universal                              │
│  ventilada          refrigerada                                                          │
│  15.000 rpm · 24×2 ml   18.100 rpm · 8×15 ml   16.500 rpm · 4×100 ml   ← mono, tabular   │
│                                                                                          │
│           ┌──────────────┐   ┌──────────────┐                                            │
│           │   [photo]    │   │   [photo]    │                                            │
│           └──────────────┘   └──────────────┘                                            │
│           Digicen 22 R       Consul 22                                                   │
│           Universal refrig.  Gran capacidad                                              │
│           16.500 rpm · …     14.300 rpm · 4×400 ml                                       │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 03  Reactivos e insumos                         SERVA Electrophoresis          Ver línea → │
│     BlueSlick™                 Ítem 42500        Reactivo para tratamiento de placas …    │
│     TEMED …, 25 ml             —                 Reactivo de uso frecuente …              │
│     REPEL-SILANE               Ítem GE17-1332-01 Insumo para preparación …                │
│     Pedidos gestionados por OrigenLab según confirmación comercial.                      │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 04  Marcas                                                                               │
│     [ SERVA logo ]   Reactivos e insumos para electroforesis …                           │
│     [ ortoalresa ]   Fabricante europeo de centrífugas de laboratorio …                  │
│     Información de producto según documentación pública del fabricante.                  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 05  Cómo cotizamos                                                                       │
│     1 ─────────────── 2 ─────────────── 3 ─────────────── 4                              │
│     Cuéntenos su      Revisamos          Enviamos           Coordinamos                  │
│     necesidad         alternativas       cotización         condiciones                  │
│     (one sentence)    (one sentence)     (one sentence)     (one sentence)               │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 06  Servicios                                                                            │
│     Soporte            Respuesta a dudas de uso y coordinación …                         │
│     Asesorías          Ayuda para acotar opciones antes de comprar …                     │
│     Garantía           Según fabricante y condiciones del equipo; detalle en cotización. │
│     Instalación        Cuando el equipo lo exige; alcance acordado por escrito.          │
│     Puesta en marcha   Equipos más complejos …                                           │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 07  Preguntas frecuentes                                                                 │
│     ▸ ¿Cómo solicito una cotización?                                                     │
│     ▸ ¿Qué tipo de clientes atienden?                                                    │
│     ▸ ¿Tienen catálogos o fichas técnicas?                                               │
│     ▸ ¿Cómo los contacto y en qué horario?                                               │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 08  Solicitar cotización                                          (graphite band)        │
│     Indique laboratorio, aplicación o equipo de interés.                                 │
│     [ Solicitar cotización ]     contacto@origenlab.cl · WhatsApp +56 9 6256 7816        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Equipos        Aplicaciones        Marcas y empresa        Contacto                      │
│ Centrífugas    Alimentos           Ortoalresa              contacto@origenlab.cl         │
│ Electroforesis Control de calidad  SERVA                   +56 9 6256 7816 · WhatsApp    │
│ Ver todos      Laboratorio clínico Servicios · Nosotros    Lun–vie 09:00–18:00           │
│                                                            Valdivia, Chile · todo Chile  │
│ © 2026 OrigenLab · [razón social · RUT: CONTENT NEEDED] · Privacidad · Datos personales  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Mobile wireframe (390 px)

```
┌────────────────────────────┐
│ ⚛ OrigenLab        [Menú]  │  ← 56px, hairline
├────────────────────────────┤
│ Equipos para               │
│ laboratorio, cotizados     │
│ con criterio técnico       │  ← h1 32-36px, 3 lines max
│                            │
│ Venta de equipos para      │
│ laboratorios de servicio e │
│ investigación, con         │
│ atención en todo Chile.    │
│                            │
│ [ Solicitar cotización  ]  │  ← full width, 48px
│ Ver equipos →              │
│                            │
│ ┌────────────────────────┐ │
│ │    [product photo]     │ │  ← 4:3, warm-white frame
│ └────────────────────────┘ │
│ Ortoalresa · Microcentrí-  │
│ fuga ventilada             │
├────────────────────────────┤
│ 01  Líneas de trabajo      │
│ ────────────────────────── │
│ Alimentos               →  │  ← rows ≥ 48px tall
│ ────────────────────────── │
│ Control de calidad      →  │
│ ────────────────────────── │
│ Laboratorio clínico     →  │
├────────────────────────────┤
│ 02  Equipos                │
│ ┌──────────┐ ┌──────────┐  │
│ │ [photo]  │ │ [photo]  │  │  ← 2-col grid
│ └──────────┘ └──────────┘  │
│ Biocen 22    Biocen 22 R   │
│ 15.000 rpm   18.100 rpm    │
│ ┌──────────┐ ┌──────────┐  │
│ │ [photo]  │ │ [photo]  │  │
│ └──────────┘ └──────────┘  │
│ Digicen 22   Digicen 22 R  │
│ ┌──────────┐               │
│ │ [photo]  │  Consul 22    │
│ └──────────┘  14.300 rpm   │
│ Ver todos los equipos →    │
├────────────────────────────┤
│ 03  Reactivos e insumos    │
│ BlueSlick™ · Ítem 42500    │
│ TEMED …, 25 ml             │
│ REPEL-SILANE · GE17-1332-01│
│ Ver línea SERVA →          │
├────────────────────────────┤
│ 04  Marcas                 │
│ [SERVA]      [ortoalresa]  │
├────────────────────────────┤
│ 05  Cómo cotizamos         │
│ 1 Cuéntenos su necesidad   │  ← vertical timeline
│ 2 Revisamos alternativas   │
│ 3 Enviamos cotización      │
│ 4 Coordinamos condiciones  │
├────────────────────────────┤
│ 06  Servicios              │
│ Soporte                    │
│   Respuesta a dudas …      │
│ Asesorías                  │
│   Ayuda para acotar …      │
│ …                          │
├────────────────────────────┤
│ 07  Preguntas frecuentes   │
│ ▸ ¿Cómo solicito una …     │
│ ▸ ¿Qué tipo de clientes …  │
├────────────────────────────┤
│ 08  Solicitar cotización   │  ← graphite band
│ [ Solicitar cotización  ]  │
│ contacto@origenlab.cl      │
│ WhatsApp +56 9 6256 7816   │
├────────────────────────────┤
│ footer columns stacked     │
│ legal line                 │
└────────────────────────────┘
```

Hero discipline: headline ≤ 2 lines desktop / 3 lines mobile, one sentence ≤ 20 words, one primary button, one text link, no chips, no trust strip, no version or tagline. The product figure is a real AVIF from `public/products/ortoalresa/`, `fetchpriority="high"`, explicit dimensions, no frame-inside-frame.

## 10. Product page anatomy

Route: `/productos/[familia]/[slug]/`. Template driven entirely by `products.ts` + `brands.ts` + `productFamilies.ts`.

```
Breadcrumb: Inicio / Productos / Centrífugas / Biocen 22

┌─ header ──────────────────────────────────────────────────────────────┐
│ Ortoalresa · Microcentrífuga ventilada          (brand + equipmentType, mono, ink-muted)
│ Biocen 22                                        (h1)
│ Microcentrífuga ventilada para microtubos …     (summary, ≤ 25 words)
└───────────────────────────────────────────────────────────────────────┘

┌─ figure (6 cols) ───────┐  ┌─ quote panel (6 cols) ───────────────────┐
│ [product photograph]    │  │ [ Solicitar cotización ]  (→ /cotizar?producto=biocen-22)
│ warm-white, 4:3, no     │  │ Solicitar por correo · Cotizar por WhatsApp (text links, prefilled)
│ shadow, hairline frame  │  │ Cotización y disponibilidad sujetas a confirmación comercial.
└─────────────────────────┘  │ Ficha del fabricante (PDF) ↗   Sitio del fabricante ↗
                             └──────────────────────────────────────────┘

01  Descripción            product.description (prose, max-width 65ch)

02  Especificaciones       Featured 3-4 specs as large mono figures:
                           15.000 rpm    21.885 ×g    24 × 2 ml    Ventilada
                           then grouped definition list:
                           Rendimiento   Velocidad máxima · Capacidad máxima · Versión
                           Construcción  Cámara · Motor · Nivel de ruido · Control · Seguridad
                           Dimensiones   Dimensiones · Peso neto
                           Alimentación  CE 146 · CE 147
                           "Especificaciones según documentación del fabricante Ortoalresa."

03  Aplicaciones           product.applications as a plain comma list or two-column list (not pills)

04  Modelos relacionados   other products in the family, compact rows (photo 64px, name, type, key spec), link

05  Condiciones comerciales product.commercialNote + family note (existing copy)

08  Solicitar cotización   shared closing block with product context
```

Spec grouping is editorial metadata to add to `products.ts` (`specGroup` per spec, or a group order per family), not new facts. Featured specs derive from existing labels (`Velocidad máxima`, `Capacidad máxima`, `Versión`, `Temperatura`). Numbers use `font-variant-numeric: tabular-nums` and non-breaking spaces before units. JSON-LD `Product` (name, brand, image, description, url, category; no `offers`, no price, no availability) and `BreadcrumbList`.

SERVA SKUs: until images and descriptions exist (CONTENT NEEDED), they do not get detail pages; the family page lists them as rows with the quotation CTA. The IA reserves `/productos/electroforesis/` for when the data is complete.

## 11. Category and application page anatomy

Route: `/categorias/[slug]/` plus a new `/categorias/` index.

```
Breadcrumb: Inicio / Aplicaciones / Control de calidad

Equipos para control de calidad                (h1 from categories.name)
Instrumentación y equipos orientados a …       (description)

01  Cómo orientamos esta línea    category.buyerGuide as prose

02  Antes de cotizar              category.buyerBullets as a numbered list (1, 2, 3), not a boxed tip

03  Equipos relacionados          products where categorySlugs includes this slug, as product rows
                                  (photo, name, type, key spec, Ver ficha). If none: an honest line
                                  "Esta línea se cotiza por requerimiento; no publicamos listado cerrado."
                                  plus link to /productos/. (Applies to alimentos today.)

04  Familias y marcas             families/brands linked to this category (data-driven)

Clinical-only callout             existing disclaimer text, rendered as a bordered note, unchanged copy

08  Solicitar cotización          with ?linea=control-de-calidad
```

The `/categorias/` index lists the three lines as the same numbered index used on the home page, with one paragraph each and their related product count (real count from data). No invented sub-applications: `productFamilies.ts` and `products.ts` decide what appears.

## 12. Brand page anatomy

Route: `/marcas/[slug]/`, one data-driven template replacing the two hand-written pages (SEO: URLs unchanged).

```
Breadcrumb: Inicio / Marcas / Ortoalresa

[ brand logo, max-height 40px ]   Ortoalresa                       (h1)
                                  Álvarez Redondo S.A. · Fabricante europeo de centrífugas …  (legalName + summary)
                                  Información de producto según documentación pública del fabricante. OrigenLab no fabrica estos equipos.
                                  Sitio del fabricante ↗

01  Líneas                        families for this brand (Centrífugas), with product count

02  Productos                     grouped by productGroup (Microcentrífugas / Universales y gran capacidad)
                                  as product rows or the 3+2 grid; SERVA: SKU rows (no images)

03  Áreas de aplicación           brand.applicationAreas as a plain list

04  Condiciones comerciales       brand.commercialNote (SERVA prepago line stays)

08  Solicitar cotización          with ?marca=ortoalresa
```

Labels are identical for every brand. No "Fabricante" vs "Visión general" divergence. The brand hub `/marcas/` is a hairline strip of logos with one sentence each, not cards.

## 13. Contact and quotation journey

Primary path: any CTA → `/cotizar/` with context in the query string → structured request → confirmation.

```
/cotizar/

Solicitar cotización                                     (h1)
Indique lo que necesita; respondemos con orientación técnica y comercial según disponibilidad.

01  Sobre su laboratorio
    Institución o empresa*            [ text, autocomplete=organization ]
    Nombre y cargo*                    [ text, autocomplete=name ]
    Correo*                            [ email, inputmode=email, autocomplete=email, spellcheck=false ]
    Teléfono                           [ tel, inputmode=tel, autocomplete=tel ]
    Ciudad / región*                   [ text, autocomplete=address-level2 ]

02  Sobre el equipo
    Línea o aplicación                 [ select: alimentos / control de calidad / laboratorio clínico / otra ]
    Equipo o modelo de referencia      [ text, prefilled from ?producto= ]   e.g. "Biocen 22"
    Cantidad                           [ number, min 1 ]
    Requisitos técnicos o mensaje      [ textarea ]  helper: capacidad, rango, norma o método, accesorios
    Plazo estimado                     [ text or select: sin urgencia / 1-3 meses / antes de … ]

03  Datos personales
    ☐ He leído la Política de privacidad (v YYYY-MM-DD) y acepto que OrigenLab use estos datos para responder mi solicitud.*
    Short notice inline: responsable, finalidad (responder la cotización), conservación, canal de derechos. Link to /privacidad/.

[ Enviar solicitud ]     o escríbanos: contacto@origenlab.cl · WhatsApp +56 9 6256 7816

Confirmation state: "Recibimos su solicitud. Le responderemos por correo." No response-time promise.
Error state: inline messages under each field, focus moved to the first error, summary with links.
```

Field list mirrors the operational intake checklist already in `docs/company-scope.md` ("Datos a solicitar"). Nothing beyond that list is collected (data minimization).

Transport decision (business + legal, see section 26): the site is static on shared hosting with no backend in this repo. Options, in order of preference:

1. **Phase 1 (no personal data processed by the site):** the form composes a structured `mailto:` body and a prefilled WhatsApp message from the same fields (progressive enhancement over the current helpers). Data goes straight from the visitor's mail client to `contacto@origenlab.cl`. The privacy notice covers the email channel.
2. **Phase 2 (server-side submission):** a minimal endpoint (a Cloudflare Worker in a new, separate app, or a vetted form service under a data-processing agreement) that forwards to `contacto@origenlab.cl` and, later, could create a lead through the durable CRM command boundary described in the monorepo docs. This requires a privacy notice update, a processor agreement, retention rules, and spam protection without third-party CAPTCHA scripts (honeypot + time-to-submit + rate limit). Out of scope for V2 slices unless approved.

`/contacto/` stays as the channels page: email, phone, WhatsApp, hours, public location, legal identity (CONTENT NEEDED), and a link to `/cotizar/`.

## 14. Visual design principles

1. **Paper and ink, not panels.** Warm-white ground, graphite text, hairline rules. Surfaces are flat; grouping is done with rules and whitespace. Cards exist only to frame a product photograph.
2. **Teal is a signal.** It marks the one primary action, links, section numbers, and the mark. It never fills a background larger than a button, except the logo.
3. **Typography carries hierarchy.** A real type scale (section 15), weight contrast (500 vs 700), measure discipline (65ch prose, 45ch captions), tabular numerals for every specification.
4. **Measurement as motif, sparingly.** The scientific character comes from precise alignment, section indices, monospaced figures with units, and hairline grids that organize real content (spec groups, comparison tables). No molecule illustrations, no lab-glassware icons, no plus-sign textures.
5. **Real objects only.** Product photographs from the manufacturer set already in the repo, on plain warm-white, consistent scale, generous margin. No stock photos of scientists, no generated imagery, no decorative SVG.
6. **Honesty as design.** Commercial notes are set in normal body text where the eye expects them (under the CTA, under the spec table), not in small caps. What the business cannot prove is not on the page.
7. **One radius, one shadow rule, zero gradients.** Radius 4 px on inputs and buttons, 8 px on image frames, none on sections. Shadow only on floating layers (mobile menu, future dialogs).
8. **Quiet motion.** Fades and one settle animation for the mark. Nothing loops (section 20).
9. **Every page ends the same way.** The numbered "Solicitar cotización" block, so the site has one exit and it is the business's.
10. **Density suited to buyers.** Visual density 4: enough information per viewport to compare, enough air to read. No art-gallery emptiness, no dashboard packing.

## 15. Typography recommendation

Constraints: Spanish text with accents, static hosting, privacy (self-hosting mandatory), existing brand documents specify Plus Jakarta Sans (`docs/company-scope.md`, "Identidad visual"), scientific register wants tabular numerals and a monospaced companion.

**Recommendation (Option A, brand-continuous):**

| Role | Face | Weights | Notes |
|---|---|---|---|
| Display and headings | Plus Jakarta Sans (self-hosted WOFF2, latin subset) | 500, 600 | tighter tracking at display sizes (-0.02em), weight 700 retired except the wordmark |
| Body and UI | Plus Jakarta Sans | 400, 500 | 16-17 px base, 1.55 line height |
| Numbers, section indices, spec values, captions, SKU | IBM Plex Mono (SIL OFL, self-hosted) | 400, 500 | tabular by design; used for the 01/02/03 system, spec figures, units, breadcrumbs metadata |

Why: keeps the typeface already used in quotations, PDFs and the email signature (one brand voice across channels), removes the Google Fonts dependency, and adds the technical register through a mono companion rather than a new sans. Plex Mono pairs well with a geometric humanist sans and has full Spanish coverage.

**Option B (sharper editorial, requires a brand decision):** Geist Sans + Geist Mono (SIL OFL). Better display presence, tighter rhythm, but changes the brand typeface across documents. Only if the business wants a typographic reset. Not recommended by default.

Not recommended: serif display (no editorial or heritage justification for a distributor), Inter (neutral to the point of anonymity), any face that cannot be self-hosted.

Type scale (desktop / mobile, rem):

```
display  3.5 / 2.25     h1 pages    2.5 / 1.875     h2 sections   1.75 / 1.5
h3       1.25 / 1.125   body        1.0625 / 1.0    small         0.9375
mono-index 0.875 (section numbers)   mono-figure 1.5-2.0 (featured specs)   mono-caption 0.8125
```

Line lengths: prose 60-70ch; card and list text ≤ 45ch. `text-wrap: balance` on headings, `text-wrap: pretty` on body. Preload the two WOFF2 files used above the fold; `font-display: swap`; size-adjusted fallback (`system-ui`) to limit CLS.

## 16. Color system recommendation

Semantic tokens (CSS variables in `@theme`), with measured contrast on the proposed pairings:

| Token | Hex | Role | Contrast on `paper` |
|---|---|---|---|
| `--color-paper` | `#f7f6f2` | page background (warm white) | – |
| `--color-paper-2` | `#efeee9` | alternate surface, image frames | – |
| `--color-ink` | `#14181a` | primary text, graphite dark bands | 16.52 : 1 |
| `--color-ink-soft` | `#3a4144` | secondary text | 9.62 : 1 |
| `--color-ink-muted` | `#5c656a` | metadata, captions, mono labels | 5.51 : 1 (5.13 on paper-2) |
| `--color-hairline` | `#d8d6cf` | decorative rules (not a text or control boundary) | 1.34 : 1 (decorative only) |
| `--color-control-border` | `#5c656a` or darker | input borders, focus-adjacent boundaries (must be ≥ 3:1) | 5.51 : 1 |
| `--color-accent` | `#0f766e` (brand-700) | primary button fill, links, section index, active states | 5.06 : 1 as text; paper text on it 5.06 : 1 |
| `--color-accent-strong` | `#0b5d57` | hover/pressed | paper on it 7.15 : 1 |
| `--color-accent-on-dark` | `#5eead4` (brand-300) | links and indices on graphite | 12.08 : 1 on ink |
| `--color-surface-dark` | `#0f1416` | closing CTA band, mobile menu panel | paper on it 17.16 : 1 |
| `--color-focus` | `#0f766e` with 2 px offset ring | focus-visible | – |
| `--color-error` | a red with ≥ 4.5:1 on paper (pick at implementation, e.g. `#a3261e`) | form errors | verify |

Removed from the palette: `brand-50` … `brand-200` as backgrounds (mint tints), `brand-800/900/950` as header/footer fills (the header becomes paper with a hairline; the footer becomes paper or ink), `emerald-*` (WhatsApp stops being a colored button), all gradients.

Dark mode: not proposed for V2. One light theme, locked at page level. Revisit only if analytics (consented) ever justify it.

WhatsApp: if the business wants a recognizable green somewhere, use it only as the glyph color on a graphite outline button; never as a fill with white text below 4.5:1.

## 17. Spacing and grid system

- Base unit 4 px; spacing scale 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96 · 128.
- Container: `max-width 80rem` (1280 px) for content, `88rem` for the product grid if needed; gutters 24 px (mobile), 32 px (≥ 768), 48 px (≥ 1280).
- Grid: 12 columns, 24/32 px gaps. Hero 7/5, product page 6/6, spec groups 4/8 (label/value), footer 4 × 3.
- Vertical rhythm: section padding 96 px desktop / 64 px mobile; section index row 32 px below the top rule; between blocks inside a section 32 to 48 px.
- Hairlines: 1 px `--color-hairline` above each numbered section (full container width); between list rows.
- Radii: 4 px (buttons, inputs, small tags for real states), 8 px (image frames), 0 elsewhere. Documented rule, applied everywhere.
- Product figures: fixed aspect 4:3, object-fit contain, 24 px inner padding, `paper-2` background.
- Touch targets: minimum 44 × 44 px for every link and control on mobile (rows ≥ 48 px, footer links padded).

## 18. Numbering and section-label system

Purpose: give the site an editorial spine and a scientific register without decorating every heading. This is a deliberate choice that overrides the generic advice against numbered eyebrows; it is acceptable only because it is one system applied with rules.

Rules:

1. Numbers mark **top-level sections of a page** only. Never on cards, list items, footers, or the hero.
2. Format: two digits, mono, `ink-muted` (or `accent` on the current section when a page has an in-page index), followed by the section title in the sans: `01  Líneas de trabajo`.
3. One number per section, always at the same position (top-left, on the section's top hairline), same size (`mono-index`). No other uppercase micro-labels on the page.
4. Numbering restarts on every page. The closing quotation block is always the last number on the page.
5. Numbers are also the anchor ids (`#01-lineas`, `#02-equipos`) so deep links and the optional in-page index reuse them.
6. Process steps use single digits in the same mono (`1 2 3 4`), visually smaller, to keep the two systems distinct.
7. Spec figures share the mono face but never carry an index.

Examples of the language (Spanish, ready to use):

```
01  Líneas de trabajo            Alimentos · Control de calidad · Laboratorio clínico
02  Equipos                      Centrífugas Ortoalresa
03  Reactivos e insumos          SERVA Electrophoresis
04  Marcas
05  Cómo cotizamos
06  Servicios
07  Preguntas frecuentes
08  Solicitar cotización

Product page:
01  Descripción
02  Especificaciones
03  Aplicaciones
04  Modelos relacionados
05  Condiciones comerciales
06  Solicitar cotización

Brand page:
01  Líneas
02  Productos
03  Áreas de aplicación
04  Condiciones comerciales
05  Solicitar cotización

Featured spec figures (mono, no index):
15.000 rpm      21.885 ×g      24 × 2 ml      < 60 dB
```

Copy register for section titles: noun phrases, sentence case, no verbs, no adjectives ("Equipos", not "Nuestros equipos de alta calidad").

## 19. Component inventory

Astro components, no client framework. Names are proposals.

| Component | Replaces | Notes |
|---|---|---|
| `SiteHeader` | `Header.astro` | paper background, hairline, static or settle-once mark, 5 links + primary CTA, `MobileMenu` |
| `MobileMenu` | `<details>` menu | `<button aria-expanded aria-controls>`, panel, Escape closes, focus return, `inert` on page while open |
| `SiteFooter` | `Footer.astro` | four columns, legal line, policy version |
| `Breadcrumbs` | – | `<nav aria-label="Ruta">`, JSON-LD emitted alongside |
| `SectionIndex` | eyebrows | number + title + optional right-aligned link; emits the top hairline and anchor id |
| `IndexList` | `HomeCategoryCards`, `Card` | numbered or plain rows with hairlines, arrow, ≥ 48 px |
| `ProductFigure` | image blocks | AVIF with `width/height`, 4:3, `loading`/`fetchpriority` props, caption slot |
| `ProductGrid` | `ProductShowcaseGrid`, `HomeProductImagePreview` | 3+2 desktop, 2-col mobile, mono spec line |
| `ProductRow` | `ProductPreviewCard` (list mode), `BrandSkuCard` | 64 px figure or none, name, type, key spec, link, optional quote link |
| `SpecFigures` | `showcase-spec-chip` | 3-4 featured figures, mono, units with `&nbsp;` |
| `SpecList` | `ProductSpecTable` | grouped `<dl>`; optional table variant for comparison with `tabular-nums` and `<caption>` |
| `ComparisonTable` | `ProductComparisonStrip` | table on ≥ md, stacked `<dl>` below; `scope` on headers |
| `BrandStrip` | brand cards on `/marcas`, home | logos at equal cap height, one sentence each |
| `StepList` | `HomeProcess` | horizontal timeline / vertical on mobile, single-digit mono |
| `DefinitionList` | – | services, contact details |
| `Faq` | inline `<details>` | same semantics, hairlines, `name` grouping kept |
| `QuoteBlock` | `QuoteCTA`, `HomeFinalCTA` | the numbered closing block; props: context (`producto`, `marca`, `linea`) |
| `QuoteActions` | `ProductQuoteActions` | primary button + email/WhatsApp text links |
| `Button` | `.btn-*` classes | variants: primary (accent), secondary (ink outline), link; sizes md/lg; icon slot |
| `TextLink` | ad-hoc links | underline offset, arrow or external glyph, `rel` handling |
| `Note` | availability / commercial notes | plain bordered paragraph, normal case |
| `Prose` | – | measure and vertical rhythm for description and about copy |
| `Field`, `Select`, `Textarea`, `Checkbox`, `FieldError`, `FormSummary` | – | label above, helper, error below, `aria-describedby`, `autocomplete`, `inputmode` |
| `ConsentGate` | inline Tidio script | renders the chat entry point and loads the vendor script only after explicit opt-in (section 25) |
| `PolicyVersion` | – | renders version, effective date, and change log for `/privacidad/` |
| `Seo` (head partial) | inline head in `Layout` | title template, description, canonical, OG (PNG), Twitter, `theme-color`, JSON-LD slot |
| `NotFound` page | – | `src/pages/404.astro` |

Data additions (editorial, not facts): `specGroup` per spec, `featuredSpecLabels` per family, `applicationLine` moved from `productShowcase.ts` into `products.ts`, `steps` and `quoteFields` as data files, `legal.ts` (razón social, RUT, policy version) once provided.

## 20. Motion principles

- **Budget:** `MOTION_INTENSITY 3`. Transitions on `transform` and `opacity` only, 150 to 250 ms, one easing (`cubic-bezier(0.2, 0, 0, 1)`). No parallax, no scroll-jacking, no marquees, no infinite loops.
- **Header mark:** the three-body canvas is the brand's one signature. To satisfy WCAG 2.2.2 (moving content longer than 5 s needs pause/stop/hide) without adding chrome, play a **single settle animation ≤ 4 s on page load, then hold the static mark**. Under `prefers-reduced-motion`, static immediately (already implemented). The full loop remains available on the (non-public) logo lab. Decision for the business: if the continuous loop is preferred, a visible pause control is required.
- **Entrance:** one fade/translate (8 px, 300 ms) on the hero text and figure only, gated by `prefers-reduced-motion: no-preference`. No per-section fade-ups.
- **Feedback:** buttons darken on hover, translate 1 px on active; links change underline color. No card lift.
- **Menus:** the mobile panel fades in 150 ms; no slide.
- **Smooth scrolling:** only under `no-preference`.
- **Every animation must answer "what does this communicate"** in one sentence in a code comment; otherwise it is not added.

## 21. Responsive strategy

- Breakpoints: 640 / 768 / 1024 / 1280 (Tailwind defaults). Design at 390, 768, 1280, 1440.
- Mobile-first CSS; every multi-column layout declares its `< 768px` collapse in the same component.
- Header: full nav ≥ 1024 (one line, checked at 1024 with Spanish labels); `MobileMenu` below.
- Hero: stacked below 1024 (text, CTA, figure). Figure keeps 4:3 and never exceeds 60vh.
- Product grid: 1 col < 480, 2 cols < 1024, 3+2 ≥ 1024.
- Spec list: `<dl>` single column on mobile; two columns (label / value) ≥ 640; comparison switches to a table ≥ 768 with `overflow-x: auto` inside its own container and a visible scroll affordance.
- Index lists: rows ≥ 48 px tall, arrow stays right-aligned, text wraps.
- Footer: 1 col < 640, 2 cols < 1024, 4 cols ≥ 1024.
- Type: fluid `clamp()` for display and h1 only; body fixed 16/17 px.
- Images: `srcset` from Astro's image pipeline where a raster is served; AVIF with a JPEG fallback via `<picture>` for older Safari; explicit `width`/`height` everywhere.
- Safe areas: `env(safe-area-inset-*)` on the sticky header and the mobile menu panel; `overflow-x: hidden` only on the table container, never on `body`.
- No horizontal scroll at 320 px.

## 22. Accessibility requirements (WCAG 2.2 AA target)

Structure and semantics
- One `h1` per page; sections numbered visually but headings remain `h2`; the index number is `aria-hidden` and the section keeps `aria-labelledby`.
- Landmarks: `header`, `nav` (labelled), `main`, `footer`, `aside` only where used.
- Breadcrumbs as an ordered list inside `<nav aria-label="Ruta">`, current page with `aria-current="page"`.
- Lists are lists; tables are tables with `<caption>`, `scope`, and header cells; definition lists for spec groups and services.
- External links carry a visible glyph and `rel="noopener noreferrer"`; opening in a new tab is announced ("abre en una pestaña nueva") in visually hidden text.

Keyboard and focus
- Every interactive element reachable and operable by keyboard; visible `:focus-visible` ring (2 px accent, 2 px offset) on all controls, including logo link and menu button.
- Mobile menu: Escape closes, focus returns to trigger, page content `inert` while open, no focus trap bugs.
- Skip link retained and styled to the new palette; `main` keeps `tabindex="-1"`.
- Sticky header never covers a focused element: `scroll-padding-top` equal to header height; `scroll-margin-top` on anchored headings.
- Focus is not obscured (2.4.11), target size ≥ 24 × 24 CSS px everywhere and ≥ 44 px on mobile primary controls (2.5.8, exceeds minimum).

Contrast and color
- Text ≥ 4.5:1, large text and UI boundaries ≥ 3:1, verified with the token table in section 16 and re-run by script in CI for every foreground/background pair actually used.
- Color is never the only carrier of meaning (links underlined, errors have icon + text, required fields marked with text).

Motion
- All animation behind `prefers-reduced-motion: no-preference`; no content moves longer than 5 s without a pause control (section 20).

Images and media
- Meaningful images have alt text that names brand and model (`imageAlt` already exists); logos: "Logo Ortoalresa"; decorative marks `aria-hidden` or `alt=""`.
- Explicit dimensions on every `<img>` (CLS and layout stability).

Forms (`/cotizar/`)
- Visible `<label for>` above every control; helper text and errors linked with `aria-describedby`; `required` plus visible text "obligatorio"; correct `type`, `inputmode`, `autocomplete`; `spellcheck="false"` on email.
- Errors inline below the field and summarized at top with links; focus moves to the first invalid field; `aria-live="polite"` for the summary; submit button stays enabled until submission starts.
- Consent checkbox and label share one hit target; the policy link opens in the same tab.
- Language of the page `lang="es"`; consider `es-CL` if the business confirms (screen readers treat both as Spanish).

Content
- Reading level: short sentences, one idea each; abbreviations expanded once (RPM, ×g explained in a footnote on spec lists).
- Link text meaningful out of context ("Ver ficha de Biocen 22", not "Ver ficha" repeated five times without context; implement with visually hidden model name).

## 23. SEO requirements

Technical
- Canonical with trailing slash (as today) on every page; internal links use the trailing-slash form consistently; `.htaccess` redirects non-slash → slash and `www` → apex (or the reverse, once DNS truth is confirmed) with 301.
- `src/pages/404.astro` with search-less recovery (links to Productos, Aplicaciones, Cotizar); HostGator `ErrorDocument 404 /404.html` in `.htaccess`.
- Sitemap generated from routes (`@astrojs/sitemap`), excluding `/logo-lab` and any tooling; `robots.txt` references the generated sitemap.
- `/logo-lab` excluded from production output (dev-only or removed); `/email/*` moved out of `public/`; both return 404 or 410 after launch.
- Remove `generator` meta; add `theme-color` matching `paper`; PNG favicons (32, 180) and a `site.webmanifest` (minimal).
- Raster `og:image` 1200 × 630 (default site image plus per-product images generated at build from the product AVIF on a paper background); `twitter:card summary_large_image`.
- Fonts self-hosted and preloaded; CSS remains a single file; no render-blocking third-party requests.

Content and structure
- Title template: `{Page} | OrigenLab` (product: `{Model} | {Tipo} | OrigenLab`, as today). Descriptions ≤ 158 characters, unique, written for buyers (model, type, application, "cotización en Chile").
- One `h1`, descriptive `h2`s that include the natural terms (centrífuga refrigerada, microcentrífuga, electroforesis, laboratorio clínico, control de calidad, alimentos).
- Category pages must not be thin: each needs at least the buyer guide, bullets, related products or an honest statement, and internal links to families and brands. The `alimentos` page is the thin-content risk today (CONTENT NEEDED: at least one real product line or an honest scope paragraph approved by the business).
- No duplicate content between family and brand pages: the family page carries specs and comparison; the brand page carries manufacturer context and the product list; product pages carry the full ficha.
- Internal linking rules from section 7; every product links to its family, brand, and categories; every category links back to its products and families; breadcrumbs everywhere.
- Image `alt` with brand and model; file names already descriptive (`biocen-22.avif`).

Structured data (JSON-LD, emitted by the `Seo` partial)
- `Organization` on every page: `name`, `url`, `logo`, `email`, `telephone`, `address` (`addressLocality` Valdivia, `addressCountry` CL; no street unless the business publishes it), `areaServed` CL, `contactPoint` (sales, Spanish). `legalName` and `taxID` only when provided (CONTENT NEEDED).
- `WebSite` on the home page (no `SearchAction`, there is no search).
- `BreadcrumbList` on every page below the home.
- `Product` on product pages: `name`, `brand` (`Brand` with `name`), `image`, `description`, `url`, `category`, `additionalProperty` for the featured specs. **No `offers`, no price, no `availability`**, consistent with the no-stock, no-price policy.
- `ItemList` on family and category pages listing the product URLs.
- `FAQPage` on the home page for the four questions (only while they remain visible on the page).

Performance (Core Web Vitals)
- LCP: hero product image preloaded, `fetchpriority="high"`, AVIF ≤ 30 KB (current images are 20 to 28 KB), fonts preloaded; target < 1.5 s on 4G.
- CLS: dimensions on all images, `font-display: swap` with size-adjusted fallback, no late-injected banners above content (the consent control must reserve its space or sit at the bottom); target < 0.05.
- INP: no client JS on the critical path besides the ≤ 4 s mark settle; consent-gated chat loads only after a user gesture.
- Third-party impact: zero third-party requests before consent. The current Google Fonts CSS request and Tidio loader are the only external cost and both disappear or move behind consent.

## 24. Privacy and legal requirements

### 24.1 What happens today (documented, not judged)

1. On every one of the 17 public pages, `Layout.astro` line 67 injects `<script is:inline src="//code.tidio.co/fhlfx2hwx24jm3sa2koagvs4vsn0jtbj.js" async>`. The browser requests the loader from `code.tidio.co` at page load, before any interaction and without any notice. The public key `fhlfx2hwx24jm3sa2koagvs4vsn0jtbj` is committed in source.
2. The loader (6.5 KB, served via Cloudflare, HTTP 302 to `https://code.tidio.co/widget-v4/<version>/static/js/render.<hash>.js`) contains Sentry debug identifiers (vendor error telemetry) and references `https://socket.tidio.co` (WebSocket transport) and `https://cdnjs.cloudflare.com`. The vendor's public materials describe widget state stored primarily in `localStorage`, with a `tidio_state_*` cookie, and chat history kept in the visitor's browser storage. The vendor privacy policy states it processes data mainly in the EEA with transfers to the United States under Standard Contractual Clauses and the EU-US Data Privacy Framework, and lists device, connection, IP address, and usage/log data among collected categories.
3. **Runtime verification is pending.** In this session's sandboxed browser the Tidio bundle did not initialize (blocked outbound), so cookies and storage keys were not observed empirically. Appendix B gives the exact commands to run against production to record what is set.
4. `Layout.astro` lines 49 to 53 request the Plus Jakarta Sans stylesheet from `fonts.googleapis.com` (font files from `fonts.gstatic.com`) on every page view. This discloses the visitor's IP address and request metadata to Google before any consent.
5. There is no privacy policy, cookie or storage notice, consent mechanism, legal identity, terms page, or data-subject request channel anywhere on the site. The footer shows only a copyright line.
6. The site itself sets no cookies, uses no storage, has no forms, and no analytics. Personal data reaches OrigenLab today only when the visitor chooses to email, call, or message on WhatsApp.
7. The footer's bottom padding (`pb-28`) exists to keep the copyright above the Tidio chat bubble.

### 24.2 Regulatory horizon (facts to verify with counsel)

Chile's Ley 21.719 (personal data protection) was published in the Diario Oficial on 13 December 2024 and, per the sources consulted, enters into force on 1 December 2026, creating the Agencia de Protección de Datos Personales with supervisory and sanctioning powers. Public summaries describe strengthened data-subject rights (acceso, rectificación, supresión, oposición, portabilidad, bloqueo), information duties toward data subjects, and administrative fines. **None of this is a legal conclusion for OrigenLab.** Counsel must confirm: applicability to a small B2B company, which legal basis covers quotation handling, information-notice contents, retention periods, response deadlines, whether website third-party scripts and IP disclosure to foreign processors require consent, cross-border transfer requirements for Tidio (US entity) and Google Fonts, and whether an internal data-protection contact must be named.

### 24.3 Privacy-safe architecture for V2 (design proposal)

1. **Default state: zero third-party requests.** Self-hosted fonts; no chat, analytics, or embeds before an explicit user action.
2. **Consent gate for chat (if Tidio is kept).** A small, non-blocking control ("Chat en vivo") in the page corner or footer. The first click shows a two-line notice (provider, what it stores, link to the policy) and an "Iniciar chat" button; only then is the vendor script injected. The choice is stored in `localStorage` under a first-party key, with an expiry, and can be withdrawn on `/privacidad/`. No cookie banner, because the site sets no cookies by itself; the notice is contextual to the chat.
3. **Alternative: remove Tidio.** WhatsApp and email already carry the quotation journey; the 2026-05-16 audit kept Tidio by decision, not by evidence of use. **Business decision needed** (section 26): usage data from the Tidio dashboard should inform it.
4. **Permanently accessible, versioned policy.** `/privacidad/` linked from every footer and every form, with: responsable (razón social, RUT, domicilio, correo), datos tratados por canal (correo, WhatsApp, formulario, chat), finalidades, base de licitud (to be defined by counsel), plazos de conservación, destinatarios y encargados (hosting HostGator, correo Titan, Tidio si aplica, WhatsApp/Meta), transferencias internacionales, derechos y cómo ejercerlos, versión y fecha de vigencia, historial de cambios. Policy text is stored as a versioned content file (`src/content/legal/privacidad-YYYY-MM-DD.md`) so every published version remains retrievable at a stable URL (`/privacidad/v/2026-12-01/`).
5. **Data-subject request channel.** A dedicated section or page (`/privacidad/solicitudes/`) stating the channel (a dedicated mailbox or `contacto@origenlab.cl` with a required subject line), the information the requester must provide, and the response period once counsel defines it. The form on `/cotizar/` links to it.
6. **Quotation form (Phase 1) processes nothing server-side**; the notice explains that data travels by the visitor's own email client. Phase 2 (server endpoint) requires the processor agreement, retention rule, and a policy version bump before release.
7. **Storage inventory as a maintained document.** A table in `/privacidad/` (and in `docs/`) listing every first-party and third-party storage key and cookie, its purpose and duration, verified after each release with the commands in Appendix B.
8. **Security headers** in `.htaccess`: keep the current three, add `Strict-Transport-Security`, `Permissions-Policy` (deny camera, microphone, geolocation), and a `Content-Security-Policy` that allows only `'self'` plus the chat origins when consent is given (CSP must be tested against Astro's inline styles before production, as the existing audit already warns).
9. **Footer legal line**: legal name, RUT, policy link, requests link, policy version. **CONTENT NEEDED** for the identity fields.

### 24.4 Items that require legal review (not decided here)

- Applicability and obligations of Ley 21.719 for OrigenLab's website, quotation intake, and WhatsApp use.
- Whether loading Tidio and Google Fonts before consent is permissible, and what notice/consent the chat requires.
- Cross-border transfer basis for Tidio (US) and, if used, any form or email processor.
- Contents, language, and retention statements of the privacy policy; whether a separate cookies/storage notice is required.
- Response deadlines and the procedure for data-subject requests; whether a named contact is required.
- Whether product images and manufacturer logos may be reproduced (open TODO in `docs/product-assets.md`; trademark, not privacy, but the same review cycle).
- Whether the public site must display legal identity (razón social, RUT) and where.

## 25. Third-party-script policy

1. **Allowlist, not blocklist.** A third-party origin may be requested only if it appears in a table in this repository (`docs/` privacy inventory) with: purpose, data it can receive, storage it sets, consent requirement, owner, and review date.
2. **Nothing loads before consent** except first-party assets. Fonts, images, CSS, and JS are served from `origenlab.cl`.
3. **Consent is specific and revocable.** One control per third party (today: at most the chat). No bundled "accept all".
4. **Injection is code, not markup.** Third-party loaders are added by a single `ConsentGate` component that reads the stored choice; no inline `<script src>` tags in layouts. The validator (`validate-catalog.mjs` or a new `validate-privacy.mjs`) fails the build if a `<script src="http` to a non-first-party origin appears outside the gate.
5. **Vendor keys are configuration.** Public widget keys live in `src/config/thirdParty.ts`, documented, so rotation is one change.
6. **No analytics in V2.** If measurement is later required, prefer a first-party, cookieless, aggregate solution and add it through the same gate and policy update. Any analytics decision goes through legal review first.
7. **CSP enforces the allowlist.** The header names the same origins as the table; a mismatch is a deploy blocker.
8. **Quarterly review** (or on every vendor change): re-run the storage inventory commands (Appendix B), diff against the policy table, bump the policy version if anything changed.
9. **Removal path documented** for every vendor: what to delete, what storage to clear, what to update in the policy.

## 26. Content gaps requiring human truth

Everything below is **CONTENT NEEDED** or **DECISION NEEDED**. The design does not assume any of it.

| # | Gap | Why it matters | Who |
|---|---|---|---|
| C-01 | Razón social, RUT, domicilio legal (public or not) for footer, policy, and structured data | legal identity, procurement buyers, `Organization` schema | business + counsel |
| C-02 | Privacy policy text, legal bases, retention periods, requests procedure and deadline, named contact | `/privacidad/`, `/cotizar/` consent notice | counsel |
| C-03 | Keep, gate, or remove Tidio; if kept, its usage data and contract/DPA status | consent architecture, footer, performance | business |
| C-04 | Quotation form transport: Phase 1 mailto only, or Phase 2 endpoint (which provider or worker, who operates it) | section 13 | business + engineering |
| C-05 | Commercial status of brands appearing in the brochure prototype and email signature (IKA, Hielscher, CRTOP, Ollital, KNAUER/Löser) | they must not appear on the site until confirmed and added to `brands.ts` | business |
| C-06 | Written permission to reproduce Ortoalresa and SERVA logos and product images (open TODO since 2026-05-16) | launch blocker for the visual direction that relies on real product photography | business + manufacturers |
| C-07 | SERVA SKU descriptions and images, or a decision to keep SERVA as text-only rows | `/productos/electroforesis/` | business + SERVA materials |
| C-08 | Content for the `alimentos` line: at least one real product line, or an approved scope paragraph | thin-content risk, honest category page | business |
| C-09 | About-page truth: founding, people, why Valdivia, what "asesoría técnica" concretely covers | `/nosotros/`, `/servicios/` cannot be written beyond `services.ts` today | business |
| C-10 | Service scope statements: geographic coverage for installation, what "soporte" includes, warranty handling process | `/servicios/` copy; current data is deliberately vague | business |
| C-11 | Customer references, testimonials, sectors served with permission | none exist; the design has no logo wall and will not add one without written permission | business + customers |
| C-12 | Certifications or memberships (if any) | none in repo; do not display | business |
| C-13 | Social handles (`instagramHandle: null`), LinkedIn | footer | business |
| C-14 | Response-time expectations | policy is not to promise; confirm the copy "le responderemos por correo" is acceptable | business |
| C-15 | Header mark behavior: settle-once (recommended) vs continuous loop with a pause control | section 20 | business |
| C-16 | Typeface decision: Option A (Plus Jakarta Sans + Plex Mono) or Option B (Geist family) | section 15 | business |
| C-17 | `www` vs apex canonical host and current DNS/hosting facts (not provable from git) | redirects | operations |
| C-18 | Whether `es` or `es-CL` should be declared | minor | business |

## 27. Asset gaps

| # | Asset | Current state | Needed for V2 |
|---|---|---|---|
| A-01 | Product photography | 5 Ortoalresa AVIF (manufacturer, ~20-28 KB each), 1 archived | keep; add JPEG fallbacks generated at build; confirm reproduction rights (C-06) |
| A-02 | SERVA product images | none | CONTENT NEEDED or text-only rows |
| A-03 | Brand logos | SERVA PNG (123 KB, oversized), Ortoalresa SVG | optimized SVG/PNG at display size, monochrome variant for the hairline strip if the manufacturers permit |
| A-04 | OG image | SVG (unsupported by major scrapers) | 1200 × 630 PNG default + per-product renders |
| A-05 | Favicons / touch icons | SVG favicon + tiny ICO; touch icon points to SVG | PNG 32/180/512, `site.webmanifest` |
| A-06 | Webfonts | Google Fonts | self-hosted WOFF2 (latin subsets) for the chosen faces |
| A-07 | Hero and about imagery | none beyond product photos | none required by the design; if the business wants a Valdivia or workshop photograph, it must be original and licensed (CONTENT NEEDED) |
| A-08 | Icons | none used except text arrows and a unicode chevron | a small set (arrow, external, WhatsApp glyph, mail, phone, menu, close) from one open icon family, inlined as SVG sprites; no hand-drawn decoration |
| A-09 | Legal documents | none | privacy policy versions as content files |
| A-10 | Manufacturer PDFs | linked externally to ortoalresa.com | stays external unless permission to mirror is granted (`documents.ts` model exists) |

## 28. Proposed implementation slices

Each slice is independently shippable, keeps all URLs, and ends with `npm run check`, `npm run build`, `npm run validate:catalog` (updated), and the QA steps in section 29. Nothing here is started by this brief.

| Slice | Scope | Exit criteria |
|---|---|---|
| S0 Foundations | tokens (section 16), type (15) self-hosted, spacing/radius rules (17), `Button`, `TextLink`, `SectionIndex`, `Breadcrumbs`, `Seo` partial, `404.astro`, sitemap integration, remove `generator`, `theme-color`, PNG icons and OG default, move `/email` tooling out of `public/`, exclude `/logo-lab` from production, reduced-motion gate for smooth scroll | build passes; zero third-party requests on any page except the Tidio line (handled in S1); Lighthouse a11y ≥ 95 on home |
| S1 Privacy and legal scaffolding | `ConsentGate` (chat behind opt-in or removal per C-03), `/privacidad/` with versioning and requests section (placeholder text clearly marked pending counsel), footer legal line, `.htaccess` headers, `validate-privacy.mjs` | no external request before consent; policy reachable from every page; validator blocks stray third-party scripts |
| S2 Header, footer, home | `SiteHeader`, `MobileMenu`, `SiteFooter`, all eight home sections per section 9, updated `validate-catalog.mjs` guards | screenshots at 390/768/1280/1440 approved; contrast script passes; keyboard walkthrough passes |
| S3 Product system | `ProductFigure`, `ProductGrid`, `ProductRow`, `SpecFigures`, `SpecList` with groups, `ComparisonTable`, product template, family template, `Product` + `BreadcrumbList` JSON-LD, editorial spec metadata in `products.ts` | all 5 product pages and the family page re-rendered; Rich Results test passes; no fabricated specs (diff against manufacturer values unchanged) |
| S4 Brands, applications, services, about | one brand template, `/marcas/` strip, `/categorias/` index and pages, `/servicios/` from `services.ts`, `/nosotros/` restructured with existing copy only | pages render from data; CONTENT NEEDED placeholders visibly marked in dev, absent in production |
| S5 Quotation journey | `/cotizar/` Phase 1 (structured mailto + WhatsApp), `QuoteBlock` everywhere with context, `/contacto/` restructured | form usable with keyboard and screen reader; errors inline; no server processing; consent notice present |
| S6 Cleanup | delete superseded components, dead data, old CSS classes after caller search; docs updates (`README`, `ARCHITECTURE`, `logo-system`, `security-audit`) | no unused components; docs describe V2 |
| S7 Launch QA | full visual regression, a11y audit, CWV budget, redirect checks, social preview checks, storage inventory on production, policy version stamped | launch checklist signed |

Dependencies: S1 depends on C-02/C-03 for final text but can ship with clearly marked pending copy; S5 Phase 2 (server endpoint) is a separate future project; S3 SERVA detail pages depend on C-07.

## 29. Test and visual-QA strategy

Static and build
- `npm run check` (0 errors), `npm run build`, `npm run validate:catalog` with updated homepage guards, new `validate-privacy.mjs` (no third-party `<script src>` outside `ConsentGate`, policy page present, footer legal links present).
- Link check over `dist/` (internal 404s, trailing-slash consistency, external links reachable).
- HTML validation of `dist/**/index.html` (Nu validator, CI or local).

Design-system checks (scripted)
- Contrast: extend the Node snippet used in this audit into `scripts/check-contrast.mjs` that reads the token file and a list of used pairs; fails under 4.5:1 for text and 3:1 for UI.
- Vocabulary caps: grep-based checks that fail on `bg-gradient`, on more than one radius outside the rule, on `uppercase tracking` outside `SectionIndex`, and on `#` colors outside the token file.
- Em-dash and straight-quote lint on visible strings (editorial consistency; Spanish typography uses « » or “ ”).

Browser QA (Playwright, `playwright-cli --browser=chromium`, already available locally)
- Screenshot matrix: home, productos, centrifugas, one product, one brand, one category, servicios, nosotros, cotizar, contacto, privacidad, 404 at 390, 768, 1280, 1440; light theme; stored under `apps/web/tests/visual/__snapshots__` (new) and compared on each slice.
- Reduced-motion run (`emulateMedia reducedMotion`): no animation frames, static mark.
- Keyboard walkthrough script: Tab order through header, menu, hero CTA, first product, footer; assert focus ring visible via computed styles; Escape closes menu.
- Accessibility: `@axe-core/playwright` on every route; zero serious/critical.
- Tap-target audit (the same eval used in this session) fails on any control under 44 px at 390 px width.
- Storage and network inventory: after load and after consent, list `cookie-list`, `localstorage-list`, `sessionstorage-list`, and resource hosts; diff against the policy table.
- Third-party check: assert the only hosts before consent are `origenlab.cl`.

Performance
- Lighthouse (mobile preset) budgets: LCP < 1.5 s, CLS < 0.05, TBT < 100 ms, total transfer < 250 KB on home; run on preview and on production after deploy.

Content QA
- Every visible string re-read against `AGENTS.md` rules (no brands, certifications, stock, lead times, warranties, exclusivity); a grep for banned phrases (`distribuidor exclusivo`, `representante oficial`, `stock`, `garantizado`, `líder`) extended in the validator.
- CONTENT NEEDED placeholders must be absent from production HTML (validator greps `dist/` for the marker).

Release
- `docs/deployment.md` checklist extended with: `.htaccess` headers present, `/404` served, `/logo-lab` absent, `/email/` absent, OG PNG resolves, policy version visible, storage inventory recorded.

## 30. Explicit non-goals

- No CMS, database, backend runtime, or server framework inside `apps/web`.
- No changes to `apps/api`, `apps/dashboard`, `apps/dashboard-proxy`, `apps/email-pipeline`, CRM schemas, or the marketing-email tooling (the `/email` tooling is relocated, not modified).
- No pricing, stock, lead times, warranties, delivery promises, or availability on the public site.
- No new brands, products, specifications, customers, testimonials, certifications, or awards without confirmed data in `src/data/`.
- No copying of manufacturer website layouts or copy; manufacturer data is quoted with attribution as today.
- No analytics, tag manager, pixels, A/B testing, or heatmaps in V2.
- No dark mode, no multi-language, no search, no ecommerce, no user accounts.
- No URL renames of indexed pages; no removal of the existing product, brand, or category routes.
- No decorative illustration, stock photography, generated imagery, molecule or DNA motifs, gradients, or glass effects.
- No microservices, event bus, or workflow engine (monorepo rule).
- No implementation as part of this brief.

---

## Appendix A. Web Interface Guidelines review (current code, `file:line`)

Applied from the fetched guideline set to the files read in this audit. Terse by design.

```
## src/layouts/Layout.astro
src/layouts/Layout.astro:32   apple-touch-icon points to SVG (iOS ignores) → PNG 180×180
src/layouts/Layout.astro:34   meta generator exposed → remove
src/layouts/Layout.astro:43   og:image is SVG → raster 1200×630
src/layouts/Layout.astro:49   third-party font stylesheet, render-blocking, no consent → self-host + preload
src/layouts/Layout.astro:67   third-party script on every page before consent; protocol-relative URL → ConsentGate, https
src/layouts/Layout.astro:—    missing <meta name="theme-color">

## src/styles/global.css
src/styles/global.css:20      scroll-behavior: smooth without prefers-reduced-motion guard
src/styles/global.css:35      fade-up on every page's first main child (motion without purpose; gated correctly)
src/styles/global.css:81      .btn-whatsapp white on emerald-600 = 3.77:1 → fails AA text

## src/components/Header.astro
src/components/Header.astro:41   "Cotizar" label differs from site-wide "Solicitar cotización" (duplicate intent)
src/components/Header.astro:50   <details> as menu: no Escape, no focus return, no outside-click close, no aria-expanded
src/components/Header.astro:8    backdrop-blur on sticky header (cost, template tell)

## src/components/Footer.astro
src/components/Footer.astro:55   pb-28/pb-24 padding to dodge chat bubble
src/components/Footer.astro:—    links ~18-20px tall on mobile (< 24px minimum target)
src/components/Footer.astro:—    no legal identity, no privacy link

## src/components/HomeHero.astro
src/components/HomeHero.astro:17-22   4 trust chips + 3 category chips in hero (hero stack > 4 text elements)
src/components/HomeHero.astro:68-81   3 CTAs with overlapping intent; WhatsApp button white on emerald-600/90
src/components/HomeHero.astro:36-45   decorative SVG pattern + radial gradients
src/components/HomeHero.astro:171     category chips 24px tall (touch target)

## src/components/HomeFinalCTA.astro
src/components/HomeFinalCTA.astro:27   white on emerald-500 = 2.54:1 → fails AA
src/components/HomeFinalCTA.astro:25-45 three CTAs, duplicate intent

## src/components/HomeCategoryCards.astro
src/components/HomeCategoryCards.astro:44   three equal cards with letter "icons" (aria-hidden ok) and chips
src/components/HomeCategoryCards.astro:63   "Ver categoría →" 20px tall

## src/components/Card.astro
src/components/Card.astro:19   hover-only "Ver categoría →" (opacity-0) → invisible on touch

## src/components/ProductPreviewCard.astro
src/components/ProductPreviewCard.astro:42   <img> without width/height (CLS)
src/components/ProductPreviewCard.astro:66   availability line in uppercase small caps (noise)

## src/components/HomeBrandsSection.astro
src/components/HomeBrandsSection.astro:28   <img> without width/height (component unused; delete after caller search)

## src/pages/marcas.astro
src/pages/marcas.astro:45   <img> without width/height

## src/pages/marcas/ortoalresa.astro
src/pages/marcas/ortoalresa.astro:45   <img> without width/height
src/pages/marcas/ortoalresa.astro:35   eyebrow "Fabricante" vs SERVA "Visión general" (inconsistent template)

## src/pages/marcas/serva-electrophoresis.astro
src/pages/marcas/serva-electrophoresis.astro:34   <img> without width/height

## src/pages/productos/centrifugas/[slug].astro
src/pages/productos/centrifugas/[slug].astro:42   hero product <img> lazy-loaded and without dimensions → eager, fetchpriority, width/height
src/pages/productos/centrifugas/[slug].astro:55   brand <img> without width/height

## src/pages/productos/centrifugas/index.astro
src/pages/productos/centrifugas/index.astro:40-47   non-interactive "filter" chips
src/pages/productos/centrifugas/index.astro:66-73   tag chips at 0.65rem (~10px) text

## src/components/ProductSpecTable.astro
src/components/ProductSpecTable.astro:15   no <caption>; no tabular-nums; 13-19 undifferentiated rows

## src/pages/index.astro
src/pages/index.astro:47   FAQ chevron slate-400 on white = 2.56:1 (decorative; low impact)

## src/pages/logo-lab.astro
src/pages/logo-lab.astro:—   public route, not in sitemap, no noindex, English copy

## public/
public/og/origenlab-og.svg        SVG social image
public/sitemap.xml                hand-maintained, no lastmod
public/.htaccess                  no HSTS, CSP, Permissions-Policy, ErrorDocument 404
public/email/**                   internal tooling shipped publicly (7 HTML + ~40 assets)

## Passing (keep)
skip link, focus-visible rings, aria-labelledby on sections, one h1 per page, alt text with brand+model,
rel="noopener noreferrer" on external links, reduced-motion fallback for the logo canvas,
IntersectionObserver pause on the canvas, no horizontal overflow at 390px, lang="es", canonical tags.
```

## Appendix B. Evidence log and commands used

Baseline verification
```
git fetch origin main
git rev-list --left-right --count HEAD...origin/main        # 0 ahead, 57 behind
git diff --stat HEAD origin/main -- apps/web                # empty: apps/web identical
```

Health
```
cd apps/web && npm run check && npm run build && npm run validate:catalog
# check: 0 errors, 1 hint · build: 17 pages · catalog: OK
```

Third-party and storage inventory (run against production; sandbox blocked the vendor bundle)
```
playwright-cli open --browser=chromium https://origenlab.cl/
playwright-cli --raw eval "JSON.stringify([...new Set(performance.getEntriesByType('resource').map(e=>new URL(e.name).host))])"
playwright-cli --raw cookie-list
playwright-cli --raw localstorage-list
playwright-cli --raw sessionstorage-list
# then interact with the chat bubble and repeat the three list commands; record results in the policy table
playwright-cli close
```

Observed in this session (preview build, sandbox): resource hosts `fonts.googleapis.com`, `code.tidio.co`, first-party. No cookies or storage before the vendor bundle executed. Tidio loader: 6.5 KB, 302 → `widget-v4/.../render.*.js`, references `socket.tidio.co`, `cdnjs.cloudflare.com`, Sentry debug ids.

Contrast (current and proposed tokens) computed with a WCAG relative-luminance script; results in sections 1.8 and 16.

Screenshots captured at 1440×900 and 390×844 for `/`, `/productos/`, `/productos/centrifugas/`, `/productos/centrifugas/biocen-22/`, `/marcas/`, `/contacto/`, `/categorias/control-de-calidad/`, `/nosotros/` (session scratch, not committed).

Sources consulted for section 24.2 (public summaries; counsel to confirm): Chilean government and legal-commentary pages describing Ley 21.719 (publication 13 Dec 2024, entry into force 1 Dec 2026, creation of the Agencia de Protección de Datos Personales, ARCO+ rights, information duties). Tidio's public privacy policy (data categories, EEA processing with US transfers) and vendor help-center summaries (localStorage-based state, `tidio_state_*`).
