# Product assets — provenance

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-09-06

Assets for public product/brand pages are stored under `public/` (not hotlinked in production).

## Ortoalresa — active catalog (2026-05-16)

| Product | Local image | Source image | PDF |
|---------|-------------|--------------|-----|
| Biocen 22 | `public/products/ortoalresa/biocen-22.avif` | https://ortoalresa.com/imagen_producto/Biocen_22.avif | https://ortoalresa.com/catalogo_producto/Catalogo_Biocen_22_ESP.pdf |
| Biocen 22 R | `public/products/ortoalresa/biocen-22-r.avif` | https://ortoalresa.com/imagen_producto/Biocen_22_R.avif | https://ortoalresa.com/catalogo_producto/Catalogo_Biocen_22_R_ESP.pdf |
| Consul 22 | `public/products/ortoalresa/consul-22.avif` | https://ortoalresa.com/imagen_producto/Consul_22.avif | https://ortoalresa.com/catalogo_producto/Catalogo_serie_Consul_22_ESP.pdf |
| Digicen 22 | `public/products/ortoalresa/digicen-22.avif` | https://ortoalresa.com/imagen_producto/Digicen_22.avif | https://ortoalresa.com/catalogo_producto/Catalogo_serie_Digicen_22_ESP.pdf |
| Digicen 22 R | `public/products/ortoalresa/digicen-22-r.avif` | https://ortoalresa.com/imagen_producto/Digicen_22_R.avif | Mismo PDF de serie Digicen 22 (fabricante) |

**Nota:** Digicen 22 y Digicen 22 R comparten el catálogo PDF de la serie Digicen 22 según documentación del fabricante.

### Derivados de producto (V2)

`npm run build:product-images` genera, desde cada AVIF original, los tamaños que
sirve el sitio: `{slug}-480.avif`, `{slug}-960.avif` y sus equivalentes WebP.

El proceso convierte el blanco de estudio en transparencia mediante un relleno
por inundación **desde los bordes** del lienzo, no por umbral global: así el gris
claro de las carcasas y los reflejos internos, que no tocan el borde, quedan
intactos. Después recorta el aire sobrante para que todos los equipos ocupen la
misma superficie óptica. **No se retoca, recorta ni recompone el equipo
fotografiado.** Los originales se conservan sin modificar.

## Logotipos de marca (V2)

`npm run build-brand-logos` normaliza los seis logotipos a `public/brands/*.png`
a 3x, a una sola tinta (`ink-800`), con alfa derivado de la luminancia. Es el
mismo criterio ya aplicado en la tira de marcas de la firma de correo
(`public/email/brands/README.md`), que también las normaliza a escala de grises.

Fuentes oficiales y procedencia: `public/email/brands/README.md`.

| Marca | Salida web | Fuente |
|-------|-----------|--------|
| Ortoalresa | `public/brands/ortoalresa-logo.png` | `ortoalresa-source.svg` |
| SERVA | `public/brands/serva-logo.png` | `serva-source.png` |
| IKA | `public/brands/ika-logo.png` | `ika-source.png` |
| Hielscher | `public/brands/hielscher-logo.png` | `hielscher-source.svg` |
| Ollital | `public/brands/ollital-logo.png` | `ollital-source.jpeg` |
| CRTOP | `public/brands/crtop-logo.png` | `crtop-source.jpg` |

**TODO (abierto):** confirmar con OrigenLab el permiso de reproducción de los
logotipos de las seis marcas y de las imágenes de producto de Ortoalresa en
origenlab.cl. Registrado también en
[`design/CONTENT_NEEDED.md`](design/CONTENT_NEEDED.md).

`public/brands/serva-wordmark.svg` y los antiguos `ortoalresa-logo.svg` /
`serva-logo.png` fueron reemplazados por la salida normalizada del script.

## Open Graph e iconos (sitio)

| Asset | Notes |
|-------|--------|
| `public/og/origenlab-og.png` | Previsualización social 1200x630 (`og:image` / `twitter:image`). Generada por `npm run build:social`. Las redes no renderizan SVG en las previsualizaciones. |
| `public/og/origenlab-og.svg` | Fuente vectorial de la anterior. No se referencia desde el HTML. |
| `public/apple-touch-icon.png` | 180x180. iOS ignora un `apple-touch-icon` en SVG. |
| `public/favicon.svg`, `public/favicon.ico` | Ambos enlazados desde `Seo.astro`. |

## Tipografías

`public/fonts/` contiene los woff2 auto-hospedados de Plus Jakarta Sans (variable)
e IBM Plex Mono (400 y 500), ambas bajo SIL Open Font License 1.1. Se sincronizan
con `npm run sync:fonts` desde los paquetes de Fontsource declarados en
`devDependencies`. El sitio no carga tipografías de Google.

## Archived / unused on site

| Asset | Notes |
|-------|--------|
| `public/products/ortoalresa/bioprocen-22-r.avif` | **Archived — do not delete** without explicit approval. Removed from public catalog (product Bioprocen 22 R). Kept on disk for reference; must not appear in `products.ts`, routes, or sitemap. Validated by `npm run validate:catalog`. |

## Commercial copy

Public pages must **not** state exclusive distribution or official representation. WhatsApp CTAs use prefilled quote messages via `src/lib/whatsapp.ts` (`https://wa.me/56962567816?text=...`).
