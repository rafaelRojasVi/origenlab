# Product assets — provenance

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-09-07

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

## Registro por fotografía (V2, 2026-09-07)

`src/data/productImages.ts` tiene una fila por fotografía de producto, publicada
o candidata: marca, modelo o familia exacta, página oficial, origen de la
imagen, tipo de fuente, base de permiso, prueba, original local, dimensiones,
derivados, fecha de comprobación, clasificación (`modelo-exacto` o
`familia-representativa`), alt en español y estado (`VERIFIED`,
`ASSET_PERMISSION_NEEDED`, `CONTENT_NEEDED`, `REJECTED`). Sólo las filas
`VERIFIED` llegan a una plantilla, siempre a través de `ModelPhoto`,
`ProductFigure`, `ModelRow` o `SkuList`; `npm run validate:images` comprueba el
registro contra el catálogo, los archivos y el HTML construido.

### Base de permiso de la fotografía de producto (2026-09-07)

> OrigenLab business-owner confirmation of pre-existing manufacturer
> authorization for publication of official product photography on
> origenlab.cl, confirmed 2026-09-07.

El titular del negocio confirmó directamente, en la revisión de catálogo del
2026-09-07 (fase 3.1), que OrigenLab ya cuenta con autorización previa de los
seis fabricantes para publicar su fotografía oficial de producto en
origenlab.cl. Esa confirmación es la base de permiso de las 24 filas `VERIFIED`
y se cita literal en cada una (`MANUFACTURER_AUTHORIZATION_BASIS`).

Lo que este registro **no** afirma, porque el negocio no lo comunicó: que la
autorización sea una licencia pública, que exista un documento escrito o una
cláusula contractual concreta, que tenga fecha de vencimiento ni que sea
exclusiva. La investigación pública de derechos de autor del 2026-09-07 (los
avisos legales de los seis fabricantes no publican la autorización) se conserva
en el historial de Git y no bloquea la publicación: la autorización es
preexistente y privada, y el negocio responde por ella. Sí queda como mejora
pedir a cada fabricante originales en mayor resolución donde el publicado es
pequeño (IKA, Löser, SERVA), con el detalle en la `note` de cada fila.

Permiso y procedencia van separados. La procedencia es por imagen, en
`imageSourceUrl`: la página oficial del modelo, el folleto o ficha técnica
oficial (PDF) o el activo oficial exacto del que salió la fotografía, siempre en
un dominio del fabricante. No se admite fotografía de revendedores ni
marketplaces, catálogos raspados, fotografía de archivo, imagen generada, otro
modelo presentado como el exacto ni equipo de otro fabricante. Una imagen de
familia sólo ilustra una entrada de familia y se rotula «Imagen representativa
de la familia». Los PDF del fabricante se leen para extraer la imagen incrustada
cuando es la única fuente oficial accesible por máquina (IKA, dos fichas de
Adam); no se rehospedan.

### Cobertura (2026-09-07): 24 de 24 entradas publicadas

| Fabricante | Entradas | Clasificación | Origen |
|---|---|---|---|
| Hielscher Ultrasonics | UP100H, UP200St, UP400St, UIP2000hdT | modelo exacto | página oficial de cada modelo |
| Ortoalresa | Biocen 22, Biocen 22 R, Digicen 22, Digicen 22 R, Consul 22 | modelo exacto | `imagen_producto/` del fabricante (activo desde 2026-05) |
| IKA | T 10 basic, T 18 digital, T 25 digital ULTRA-TURRAX | modelo exacto | folleto oficial de dispersores (PDF), páginas 2 y 3; el sitio HTML devuelve 403 a máquinas |
| Adam Equipment | PMB, Solis, Highland | familia representativa | fichas técnicas PDF oficiales (PMB, HCB) y página oficial de la familia Solis (SAB 225i) |
| Löser Messtechnik | Osmometer basic, i Osmometer basic, i Osmometer, i Cryometer | modelo exacto | página oficial de cada modelo (400 × 500 px) |
| SERVA Electrophoresis | BlueVertical PRiME, HPE BlueHorizon, BlueMarine 100, BlueSlick 42500.01 | modelo exacto | página oficial de cada producto (`imgProd/190`) |
| SERVA Electrophoresis | BluePower (fotografía de la 600 PRIME) | familia representativa | página oficial de fuentes de alimentación |

Los originales del fabricante se conservan sin modificar en
`public/products/<marca>/` con nombre de modelo (`up100h.jpg`,
`t-25-digital.png`, `highland.png`, `i-cryometer.jpg`, `bluemarine-100.jpg`),
salvo las dos imágenes de ficha PDF de Adam, que se extrajeron con su máscara de
recorte y se compusieron sobre blanco. `ModelPhoto` nunca muestra una fotografía
por encima del tamaño real de su derivado mayor: los originales pequeños (IKA
de 73 a 153 px de ancho, BlueMarine 100 de 169 px, BlueSlick de 126 px) se ven
pequeños y nítidos, no grandes y borrosos.

Bloqueos técnicos registrados (ninguno impidió publicar): `ika.com` (HTML) y el
CDN `adamequipment.sirv.com` rechazan a clientes automatizados y no se
eludieron; en ambos casos el fabricante publica la misma fotografía en un PDF
oficial o en su propio dominio, y de ahí salió.

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

`npm run build:product-images` genera, desde cada original (AVIF, JPEG, PNG o
WebP) en `public/products/<marca>/`, los tamaños que sirve el sitio:
`{slug}-480.avif`, `{slug}-960.avif` y sus equivalentes WebP, e imprime las
dimensiones de cada derivado para copiarlas a `productImages.ts`.

Si el borde del lienzo es blanco de estudio (se mide, no se decide a mano), el
proceso convierte ese blanco en transparencia mediante un relleno por inundación
**desde los bordes**, no por umbral global: así el gris claro de las carcasas y
los reflejos internos, que no tocan el borde, quedan intactos, y después recorta
el aire sobrante para que todos los equipos ocupen la misma superficie óptica.
Un original con fondo ambientado o de color se deja tal cual. **No se retoca,
recorta ni recompone el equipo fotografiado.** Los originales se conservan sin
modificar y son el maestro de alta calidad.

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
de los logotipos de las seis marcas en origenlab.cl. Registrado en
`src/data/sourceRegistry.ts` (`ASSET_PERMISSION_NEEDED`) y en
[`design/CONTENT_NEEDED.md`](design/CONTENT_NEEDED.md). La fotografía de
producto ya tiene base de permiso (sección anterior).

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
