# Product assets — provenance

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-09-06

Assets for public product/brand pages are stored under `public/` (not hotlinked in production).

> **La fuente única de procedencia y permisos es ahora
> [`src/data/sourceRegistry.ts`](../src/data/sourceRegistry.ts).** Tiene una fila
> por marca publicada con la página oficial, el PDF oficial, el origen de la
> imagen, la ruta local, la base de permiso, la fecha de verificación y el
> estado (`VERIFIED` / `PDF_NOT_FOUND` / `ASSET_PERMISSION_NEEDED` /
> `CONTENT_NEEDED`). `npm run verify:sources` comprueba con peticiones reales
> que todos esos destinos responden; `npm run validate:brands` comprueba que
> exista una fila por marca. Este documento conserva el detalle por modelo de
> Ortoalresa, que es más fino que una fila por marca.

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

`npm run build:brand-logos` normaliza los seis logotipos aprobados a
`public/brands/*.png` a 3x y a una sola tinta (`ink-800`). Es el mismo criterio
aplicado en la tira de marcas de la firma de correo.

El script trata tres tipos de original, porque los seis no llegan igual:

| `mode` | Original | Tratamiento |
|---|---|---|
| `ink` | trazo oscuro sobre blanco | alfa desde la luminancia invertida |
| `alpha` | trazo claro sobre transparente | se conserva el alfa del original; aplanarlo sobre blanco lo borraría |
| `reversed` | trazo claro calado sobre color macizo | umbral que aísla lo casi blanco, con recorte previo del borde del azulejo |

| Marca | Salida web | Fuente | `mode` |
|-------|-----------|--------|--------|
| Hielscher Ultrasonics | `public/brands/hielscher-logo.png` | `hielscher-source.svg` | ink |
| Ortoalresa | `public/brands/ortoalresa-logo.png` | `ortoalresa-source.svg` | ink |
| IKA | `public/brands/ika-logo.png` | `ika-source.png` | ink |
| Adam Equipment | `public/brands/adam-equipment-logo.png` | `adam-equipment-source.png` | alpha |
| Löser Messtechnik | `public/brands/loeser-logo.png` | `loeser-source.jpg` | reversed |
| SERVA Electrophoresis | `public/brands/serva-logo.png` | `serva-source.png` | ink |

Ollital y CRTOP salieron del sitio público en la revisión de marca del
2026-09-06. Sus `*-source.*` siguen en `public/email/brands/` porque la firma de
correo aún los usa; sus salidas web (`public/brands/ollital-logo.png`,
`crtop-logo.png`) se borraron y `validate:brands` falla si reaparecen.

**TODO (abierto):** confirmar con OrigenLab el permiso escrito de reproducción
de los logotipos de las seis marcas y de las imágenes de producto de Ortoalresa
en origenlab.cl. Registrado en `src/data/sourceRegistry.ts`
(`ASSET_PERMISSION_NEEDED`) y en [`design/CONTENT_NEEDED.md`](design/CONTENT_NEEDED.md).

### Color: qué original sirve y cuál no (medido el 2026-09-07)

El riel de marcas de la portada podría revelar el color del fabricante al pasar
el cursor. No lo hace, porque el conjunto no está completo:

| Marca | Original | Color aprovechable |
|---|---|---|
| Hielscher Ultrasonics | `hielscher-source.svg`, 146 × 67 | sí, azul marino |
| Ortoalresa | `ortoalresa-source.svg`, 132 × 44 | sí, rojo |
| IKA | `ika-source.png`, 400 × 161 | sí, azul marino |
| Adam Equipment | `adam-equipment-source.png`, 300 × 125 | **no**: el original ya es monocromo, media RGB 90,90,90 |
| Löser Messtechnik | `loeser-source.jpg`, 70 × 70 | **no utilizable**: tiene color, no resolución |
| SERVA Electrophoresis | `serva-source.png`, 267 × 80 | sí, azul |

Cuatro de seis. Con eso, el revelado dejaría cuatro marcas en color y dos en
gris, que es una jerarquía involuntaria justo entre las cinco familias de
maquinaria que la revisión igualó. **El riel se queda gris entero** y lo que
revela el cursor o el foco es tinta plena, igual para las seis. Los dos activos
que faltan están en
[`design/CONTENT_NEEDED.md`](design/CONTENT_NEEDED.md) como CONTENIDO.

Los seis logotipos web salen además a la misma tinta (`#2b2e30`), lo que permite
igualarlos en el riel con una sola opacidad de reposo: ninguna marca queda más
clara que otra por su propio color.

## Open Graph e iconos (sitio)

| Asset | Notes |
|-------|--------|
| `public/og/origenlab-og.png` | Previsualización social 1200x630 (`og:image` / `twitter:image`). Generada por `npm run build:brand`. Las redes no renderizan SVG en las previsualizaciones. |
| `public/og/origenlab-og.svg` | Fuente vectorial de la anterior. No se referencia desde el HTML. |
| `public/apple-touch-icon.png` | 180x180, generado por `npm run build:brand`. iOS ignora un `apple-touch-icon` en SVG. |
| `public/favicon.svg`, `public/favicon.ico` | Ambos enlazados desde `Seo.astro`. Generados por `npm run build:brand` desde la misma geometría que la marca del sitio. |

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
