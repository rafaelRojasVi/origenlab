# Sistema de marca OrigenLab

Status: canonical
Owner: web-maintainers
Last reviewed: 2026-09-06

La marca vigente es **Órbita**: la lemniscata de la coreografía en ocho de los
tres cuerpos, con los tres cuerpos macizos sobre ella. Sustituye al átomo de
2026-05, que se retiró porque no se veía.

La página interna [`/logo-lab/`](../src/pages/logo-lab.astro) muestra el sistema
aplicado y es la referencia visual; este documento es la referencia escrita.

---

## Por qué se rediseñó

El átomo anterior era invisible en la práctica, y no por gusto sino por
aritmética:

| Elemento | Valor anterior | A 32 px |
|---|---|---|
| Radio de los nodos | 0,1 sobre un viewBox de 8,5 | **0,38 px de radio** |
| Trazo de las órbitas | 0,09 sobre 8,5 | 0,34 px |
| Opacidad de las órbitas | 0,24 | — |
| Halo de los nodos | opacidad 0,32 | — |

Tres puntos de menos de un píxel de diámetro, dibujados al 24 % de opacidad, no
se ven en ninguna pantalla. La marca nueva no usa ninguna opacidad menor que 1.

---

## Construcción

Lienzo de **48 × 48**, centro en (24, 24). 48 es divisible por 16, 24 y 48, así
que los tres tamaños críticos caen sobre la rejilla de píxeles sin medio punto.

La trayectoria es una **lemniscata de Bernoulli** estirada 1,95 en vertical y
girada −30°. Es la forma que describen tres cuerpos de igual masa en la
coreografía en ocho de Moore y Chenciner. El estiramiento lleva la caja de
2,83:1 a ~1,5:1, que llena el cuadrado; el giro convierte el eje en una diagonal
ascendente, que es la que lee como recorrido de la muestra al resultado.

Los **tres nodos** se reparten por longitud de arco (fase 0,17), no por
parámetro: por parámetro se agrupan dos junto al cruce y la marca se lee como
una oruga. Su peso crece de izquierda a derecha (0,86 / 1 / 1,16), asignado por
posición y no por índice de recorrido, de modo que el gradiente sobrevive a
cualquier ajuste de fase.

| Medida | Valor | A 16 px |
|---|---|---|
| Grosor de traza | 3,6 / 48 | 1,2 px |
| Radio de nodo | 4,8 / 48 | 1,6 px (3,2 px de diámetro) |
| Refuerzo ≤ 24 px | traza ×1,25, nodos ×1,12 | 1,5 px de traza |

**Fuente única:** [`scripts/lib/mark-geometry.mjs`](../scripts/lib/mark-geometry.mjs).
De ahí salen tanto los SVG de `public/logo/` como las constantes de
`src/lib/logo/generatedMark.ts` que dibujan los componentes Astro. Nada se
traza dos veces, así que la marca del encabezado y el archivo que se entrega a
un proveedor no pueden divergir.

Regenerar todo: `npm run build:brand`.

---

## Área de respeto y tamaño mínimo

- **Área de respeto:** un cuarto del lado de la marca, libre por los cuatro
  costados. No entra ahí ni texto, ni filete, ni borde de imagen.
- **Tamaño mínimo: 16 px.** Por debajo los tres nodos dejan de contarse.
- **De 16 a 24 px** se usa la variante reforzada (traza y nodos más gruesos). Es
  la misma marca con más cuerpo, no un dibujo distinto.

---

## Colores permitidos

Dos parejas y una tinta única. Sin degradados, sin sombras y **sin ninguna
aplicación con opacidad reducida**: si la marca no cabe a plena tinta, se usa a
menor tamaño, no más tenue.

| Uso | Token | Valor | Contraste con su fondo |
|---|---|---|---|
| Traza sobre papel | `ink-950` | `#141617` | 17,4:1 |
| Nodos sobre papel | `teal-700` | `#0f766e` | 5,2:1 |
| Traza sobre tinta | `paper` | `#fafaf7` | 17,4:1 |
| Nodos sobre tinta | `teal-500` | `#14b8a6` | 7,3:1 |
| Nodos sobre teal-800 | `teal-500` | `#14b8a6` | 3,1:1 |

Todos superan el 3:1 que WCAG 1.4.11 exige a los gráficos no textuales. Los
mide `scripts/lib/mark-render.mjs` y los imprime
`npm run design:logo-explorations`.

En papel y en tinta los nodos van directamente sobre la traza, sin recorte: el
color ya los separa (teal-700 sobre ink-950 da 3,3:1). Sólo la versión **a una
tinta** lleva un recorte del fondo alrededor de cada nodo, porque ahí nodo y
traza comparten color. Ese recorte se controla con `--mark-void`.

---

## Archivos

Todos en `public/logo/`, generados por `npm run build:brand`. **No se editan a
mano:** un archivo retocado deja de coincidir con la marca del sitio.

| Archivo | Uso |
|---|---|
| `origenlab-mark-light.svg` | Marca sobre papel |
| `origenlab-mark-dark.svg` | Marca sobre tinta o color de marca |
| `origenlab-mark-mono.svg` | Una tinta; hereda `currentColor` |
| `origenlab-mark-favicon.svg` | Marca reforzada para 16–24 px |
| `origenlab-lockup-light.svg` | Lockup horizontal sobre papel |
| `origenlab-lockup-dark.svg` | Lockup horizontal sobre tinta |
| `origenlab-lockup-stacked-light.svg` | Lockup apilado sobre papel |
| `origenlab-lockup-stacked-dark.svg` | Lockup apilado sobre tinta |

Y fuera de `public/logo/`, del mismo generador:

| Archivo | Uso |
|---|---|
| `public/favicon.svg`, `public/favicon.ico` | Pestaña del navegador (tinta con esquina redondeada) |
| `public/apple-touch-icon.png` | 180 × 180; iOS ignora un `apple-touch-icon` en SVG |
| `public/og/origenlab-og.png` y `.svg` | Previsualización social 1200 × 630; las redes no renderizan SVG |

En el sitio, el lockup se compone con **HTML**: la palabra «OrigenLab» es texto
real, seleccionable y traducible. Los SVG de lockup llevan el texto dentro sólo
porque se entregan a terceros.

---

## Aplicación

| Superficie | Componente | Fondo |
|---|---|---|
| Cabecera | `SiteLogo` → `OrigenMark` (estática, 30 px) | papel |
| Pie | `SiteLogo tone="paper"` (estática, 26 px) | tinta |
| Hero de la portada | `OrigenMarkAnimated` (180 px) | papel |
| Páginas legales | La marca del pie, sin variante propia | tinta |
| Sistema de marca | `/logo-lab/` (`noindex`, fuera del sitemap) | papel y tinta |

---

## Animación

**La marca es estática en todas partes menos en el hero de la portada.**

Allí los tres cuerpos recorren la propia trayectoria, desfasados un tercio de
vuelta, en un bucle de 34 s. Es `offset-path` sobre la misma curva que dibuja la
marca: sólo compone, no toca el layout, no provoca reflow y **no lleva
JavaScript**.

Reglas:

- No se anima en cabecera ni en pie. Un bucle permanente en el shell del sitio
  no tendría control de pausa, y eso incumpliría WCAG 2.2.2.
- Con `prefers-reduced-motion: reduce` la animación no llega a declararse: el
  bloque entero vive bajo `no-preference`. Los cuerpos se quedan exactamente
  donde los deja la marca estática, no en una posición degradada.
- Ninguna otra superficie puede animar la marca sin revisar esas dos reglas.

---

## Exploraciones

Las tres exploraciones y la hoja de comparación con la que se eligió Órbita
están en [`design/logo-explorations/`](../design/logo-explorations/), fuera de
`public/`:

| Concepto | Resultado |
|---|---|
| **Órbita** | **Elegido.** Distintivo, tres nodos siempre legibles, la lemniscata dice tres cuerpos sin explicarlo |
| Núcleo | Descartado: legible, pero es el átomo de stock que el encargo pedía evitar |
| Muestra | Descartado: elegante y el mejor a tamaño pequeño, pero se lee como hoja u ojo y pierde la lectura atómica |

Regenerar: `npm run design:logo-explorations`, y la hoja a PNG con
`node scripts/shoot.mjs design/logo-explorations/comparison-sheet.html design/logo-explorations/comparison-sheet.png 1180`.
