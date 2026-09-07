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
`ProductFigure` o `ModelRow`; `npm run validate:images` comprueba el registro
contra el catálogo, los archivos y el HTML construido.

Estado tras la investigación de imagen del 2026-09-07 (seis dominios oficiales,
sin descargar nada, sin extraer de PDF):

| Fabricante | Modelos publicados | Con fotografía | Base | Qué dice el fabricante |
|---|---|---|---|---|
| Ortoalresa | 5 | 5 (`VERIFIED`) | Activo de OrigenLab con procedencia por modelo; publicación asumida por el negocio | El Aviso Legal reserva la reproducción a su autorización expresa. **Permiso escrito pendiente** |
| Hielscher Ultrasonics | 4 | 0 | `ASSET_PERMISSION_NEEDED` | Imprint & Copyright: las imágenes no pueden copiarse ni mostrarse en otros sitios sin consentimiento |
| IKA | 3 | 0 | `ASSET_PERMISSION_NEEDED` | Sitio inaccesible por máquina (403); el folleto oficial no autoriza reutilización |
| Adam Equipment | 3 familias | 0 | `ASSET_PERMISSION_NEEDED` | Brand Toolkit para distribuidores cede logotipos y banners, no fotografías |
| Löser Messtechnik | 4 | 0 | `ASSET_PERMISSION_NEEDED` | Impressum: prohibida la reproducción sin acuerdo de Löser |
| SERVA Electrophoresis | 5 | 0 | `ASSET_PERMISSION_NEEDED` | Sin términos de reutilización publicados; imágenes de 126 a 500 px |

Ninguna de las 19 fotografías identificadas se descargó ni se publica. Cada
fila del registro nombra el archivo exacto del fabricante para que la petición
sea concreta.

### Peticiones de permiso pendientes de enviar

Redactadas para el negocio; el texto de cada una puede enviarse tal cual.

**Ortoalresa** (marketing@ortoalresa.com, cc sales@ y info@; Álvarez Redondo,
S.A., Daganzo). Pedir autorización previa, expresa y por escrito, según los
apartados 2 y 5 de su Aviso Legal y la cláusula 3 de sus Condiciones Generales
de Venta, para reproducir en origenlab.cl y en material comercial las
fotografías oficiales de Biocen 22, Biocen 22 R, Digicen 22, Digicen 22 R y
Consul 22 (`imagen_producto/Biocen_22.avif` y siguientes), sin alteración salvo
redimensionado y conversión de formato, y para usar la marca y el logotipo
Ortoalresa junto a esos productos, con crédito «Imágenes: © Ortoalresa /
Álvarez Redondo, S.A.». Pedir también, si existe, el kit de imágenes en alta
resolución para distribuidores.

**Hielscher Ultrasonics GmbH** (formulario hielscher.com/email.htm; Teltow).
Pedir, según su Imprint & Copyright (copy_1.htm), permiso escrito para
reproducir en origenlab.cl: UP100H (`up100h_02_p0500.jpg`,
`up100h_05_p1000.jpg`), UP200St (`UP200St_silver_cut.png`,
`up200st-s26d2-vial-p300-opt.jpg`), UP400St
(`Ultrasonic_Homogenizer_UP400St_S24d22D-05-p1000.jpg`) y UIP2000hdT
(`UIP2000hdT-sonicator-transducer-generator-HielscherUltrasonics.jpg`).
Pedir que confirmen que Hielscher tiene los derechos (su aviso advierte de
fotografías de terceros), el crédito exigido, las condiciones de recorte y
redimensionado, y el consentimiento para enlazar sus páginas, que el mismo
aviso también exige.

**IKA-Werke GmbH & Co. KG** (sales@ika.de, cc service@ika.com; formulario
ika.com/owa/ika/content.contact_form; Staufen). Pedir los archivos oficiales
en alta resolución y el permiso escrito para T 10 basic ULTRA-TURRAX
(0003737000), T 18 digital ULTRA-TURRAX (0003720000) y T 25 digital
ULTRA-TURRAX (0003725000), con el crédito y las condiciones de uso de la marca
ULTRA-TURRAX®, y preguntar si existe una oficina para Latinoamérica que
gestione material de distribuidores.

**Adam Equipment** (marketing@adamequipment.com, cc sales@adamequipment.com).
Pedir permiso escrito, o el alta en la Dealer Zone, para reproducir las
fotografías oficiales de las familias PMB (53, 163, 202), Solis (SAB 124e a
514i) y Highland (HCB 123 a 6001), los originales en alta resolución (el sitio
sólo sirve 1.100 px con protección de enlace directo) y las condiciones de
atribución. Confirmar de paso si el Brand Toolkit de logotipos y banners aplica
a OrigenLab.

**Löser Messtechnik** (info@loeser-osmometer.de, Axel Löser, Berlín). Pedir,
según su Impressum, permiso escrito para publicar `Tp7E.jpg` (Osmometer basic),
`Tp7iE.jpg` (i Osmometer basic), `Tp16E-New.jpg` (i Osmometer) y
`Tp21E-New.jpg` (i Cryometer), con crédito «© Löser Messtechnik, Berlin», y
originales de mayor resolución que los 400 × 500 px publicados. Su lista de
distribuidores no tiene entrada para Chile.

**SERVA Electrophoresis GmbH / LICORbio** (info@licorbio.com; Heidelberg).
Pedir permiso escrito y originales en alta resolución de BlueVertical PRiME
(`BV-104-s.jpg`), HPE BlueHorizon (`HPE-BH-s.jpg`), BlueMarine 100
(`BM-100-s.jpg`), las cuatro fuentes BluePower (`BP-600-PRI-s.jpg`,
`BP-300-BLO-s.jpg`, `BP-3000-HPE-s.jpg`, `BP-6000-IPG-s.jpg`) y BlueSlick
(`42500-s.jpg`), con el crédito y el uso de sus marcas registradas. Su lista de
distribuidores para Chile nombra a LabDelivery, no a OrigenLab: conviene
explicar la relación de suministro.

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
