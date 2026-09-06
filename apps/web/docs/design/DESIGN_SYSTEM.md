# Sistema de diseño, sitio público V2

Status: canonical
Owner: web-maintainers
Last reviewed: 2026-09-06

Este documento describe el sistema visual del sitio público tras el rediseño V2.
Es la referencia para decidir si algo nuevo pertenece al sitio o no. La verdad de
negocio sigue viviendo en `src/data/*`; aquí sólo se define **cómo se presenta**.

El descubrimiento que originó el rediseño está en
[`WEBSITE_V2_DESIGN_BRIEF.md`](WEBSITE_V2_DESIGN_BRIEF.md). Los datos que el
negocio todavía debe aportar están en [`CONTENT_NEEDED.md`](CONTENT_NEEDED.md).

---

## 1. Idea

**Papel, tinta y medición.**

El sitio se lee como un documento técnico bien compuesto de una casa de
instrumentos: fondo papel cálido, texto grafito, filetes de un píxel y un solo
acento teal que marca acción y lugar. La jerarquía la sostienen la tipografía y
el espacio en blanco, no las fichas ni las sombras. La única imagen del sistema
es la fotografía real de equipo del fabricante, siempre sobre papel, a la misma
escala óptica y con pie en monoespaciada.

Tres consecuencias prácticas:

1. **No hay fichas por defecto.** Se agrupa con filetes y espacio. Una caja sólo
   aparece cuando aísla algo que debe leerse aparte.
2. **Una sola superficie invertida por página**: la banda de cierre. Más el pie.
3. **El aviso comercial se dice una vez por página**, en texto normal. Repetirlo
   en cada fila se lee como ansiedad, no como rigor.

---

## 2. Tokens

Todos los valores viven en `src/styles/global.css` bajo `@theme`. Un componente
que introduce un color, un radio o una sombra propia está fuera del sistema.

### Color

| Token | Valor | Uso |
|---|---|---|
| `paper` | `#FAFAF7` | Fondo de página |
| `surface` | `#F2F2EE` | Banda de marcas, estado hover de filas |
| `hairline` | `#E3E3DE` | Filetes de 1 px |
| `ink-950` | `#141617` | Titulares, texto principal, banda de cierre y pie |
| `ink-800` | `#2B2E30` | Texto corrido |
| `ink-600` | `#54595D` | Texto secundario |
| `ink-500` | `#676D71` | Etiquetas mono y pies (color de texto mínimo sobre papel) |
| `teal-700` | `#0F766E` | Botón primario, foco, numeral del índice |
| `teal-800` | `#115E59` | Hover del primario, enlaces sobre papel |
| `teal-500` | `#14B8A6` | Acento sobre superficie oscura únicamente |
| `onink-100/300/500` | `#ECECEB` / `#B9BCBD` / `#8D9295` | Texto sobre la banda oscura |

Contraste verificado (WCAG 2.x): `ink-950` sobre `paper` 17,4:1; `ink-800` 13,1:1;
`ink-600` 6,8:1; `ink-500` 5,0:1; `teal-800` sobre `paper` 7,3:1; blanco sobre
`teal-700` 5,5:1; `teal-500` sobre `ink-950` 7,3:1.

Reglas: un solo acento en todo el sitio; sin degradados; el color nunca es el
único portador de significado; los filetes no transportan información.

**No existe el verde WhatsApp.** El canal se identifica por el glifo, no por un
segundo verde que compita con la marca (el `emerald-600` anterior fallaba AA a
14 px con 3,77:1).

### Tipografía

Auto-hospedada, sin peticiones a terceros. Ambas familias bajo SIL Open Font
License 1.1, que permite el auto-hospedaje. Se sincronizan con
`npm run sync:fonts` desde los paquetes de Fontsource declarados en
`devDependencies`.

- **Plus Jakarta Sans** (variable, 200-800): titulares, interfaz y texto corrido.
  Es la tipografía de marca ya documentada para cotizaciones y PDF en
  `docs/company-scope.md`; cambiarla obligaría a rehacer la papelería.
- **IBM Plex Mono** (400 y 500): medidas, numerales de sección, códigos de ítem,
  pies de imagen y valores de ficha. Cifras tabulares.

Roles definidos como clases en `global.css`: `.t-display`, `.t-h2`, `.t-h3`,
`.t-lead`, `.t-body`, `.t-small`, `.t-label`, `.t-data`.

Reglas: ningún texto por debajo de 13 px; la versalita con tracking sólo existe
en el rol `.t-label`; medidas de 60 a 66 caracteres en texto corrido; sin rayas
(`—`) ni semirrayas en la copia visible; espacio duro entre cifra y unidad.

### Espacio, forma y movimiento

- Base de 8 px. Ritmo de sección: 64 px en móvil, 96 px desde 768 px.
- Ancho de página 80 rem, con 20 / 32 / 48 px de margen exterior (`.shell`).
- **Un solo radio: 2 px** (`--radius-edge`), en botones, imágenes y campos.
- **Sin sombras.** La cabecera fija se separa con un filete, no con desenfoque.
- Movimiento: sólo respuesta a hover y foco, más el desplazamiento de 2 px de la
  flecha en los enlaces. Nada en bucle, nada de parallax, ninguna librería de
  animación. Todo lo que se mueve está bajo `prefers-reduced-motion`.

---

## 3. Numeración de secciones

Cada sección de primer nivel lleva un índice: numeral mono, filete y nombre en
lenguaje llano. Funciona como el índice de capítulos de un manual técnico.

```text
01 ──────── EQUIPOS
Centrífugas Ortoalresa
```

Reglas:

- Lo genera `SectionIndex.astro` a partir de un `index`; el numeral no se escribe
  a mano.
- Una vez por sección de primer nivel. Nunca dentro de una fila, una ficha o el
  hero.
- La banda de cotización siempre lleva el último número de la página.
- Es un índice, no una etiqueta decorativa: si una sección no es un capítulo del
  documento, no lleva numeral.

---

## 4. Primitivas

Pocas y mejores. Antes de crear un componente nuevo, comprobar que ninguna de
estas resuelve el problema.

| Componente | Papel |
|---|---|
| `ui/SectionIndex` | Índice numerado de sección |
| `ui/PageIntro` | Migas, H1 y frase de contexto de toda página interior |
| `ui/Breadcrumbs` | Ruta + `BreadcrumbList` en JSON-LD |
| `ui/ModelRow` | Fila de modelo: fotografía, nombre, tipo y cifras. Sustituye a las fichas de producto |
| `ui/ModelList` | Contenedor de filas de modelo (filete superior y reinicio de lista) |
| `ui/ApplicationList` | Las tres líneas comerciales como lista numerada |
| `ui/SkuList` | Referencias sin ficha propia: nombre, número de ítem y una línea |
| `ui/ProductFigure` | Fotografía de equipo en caja cuadrada sobre papel, con `<picture>` AVIF/WebP |
| `ui/KeyFacts` | Tres o cuatro cifras de cabecera en mono |
| `ui/SpecGroups` | Ficha técnica agrupada en listas de definición |
| `ui/CompareTable` | Comparativa: tabla con región desplazable en escritorio, listas apiladas bajo 768 px |
| `ui/BrandLogo` | Logotipo a tinta única con altura óptica por marca |
| `ui/ClosingQuoteBand` | Banda de cierre invertida, única por página |
| `ui/Notice` | Aviso comercial, uno por página |
| `ui/ExternalLink` | Enlace externo, anunciado y marcado |
| `ui/Icon` | Trazos de Tabler Icons (MIT), 24x24, stroke 1,5 |

Utilidades globales en `global.css`: `.shell` (ancho y márgenes de página),
`.section` (ritmo vertical), `.rule-top` (filete de separación), `.link-row`
(fila de enlaces con flecha) y los roles tipográficos.

Acciones: `.btn-primary` (relleno teal), `.btn-secondary` (contorno tinta),
`.btn-invert` (sólo sobre la banda) y `.link-arrow` (texto y flecha, nunca
compite con el primario). Una intención, una etiqueta:
**«Solicitar cotización»**, con la forma corta «Cotizar» sólo en la cabecera
estrecha, donde el nombre accesible sigue siendo el completo.

---

## 5. Imágenes

Las fotografías de producto son recortes del fabricante sobre blanco de estudio.
`scripts/build-product-images.mjs` convierte ese blanco en transparencia mediante
un relleno por inundación desde los bordes (no por umbral global, que se comería
el gris claro de las carcasas), recorta el aire sobrante y deriva 480 y 960 px en
AVIF y WebP. Sobre el fondo papel el equipo queda apoyado en la página en lugar
de flotar dentro de un rectángulo blanco, que era el efecto de ficha del sitio
anterior.

Los logotipos de marca pasan por `scripts/build-brand-logos.mjs`: recorte, alfa
derivado de la luminancia y una sola tinta (`ink-800`). La altura óptica de cada
uno vive en `brands.ts`, porque depende del lockup del fabricante y no del
contexto de uso. Es el mismo criterio ya aplicado en la firma de correo
corporativa.

Toda `<img>` declara `width` y `height`. La imagen del hero y la de la ficha de
producto cargan con prioridad; el resto es diferido.

---

## 6. Lo que el sistema no admite

- Fichas dentro de fichas, o una rejilla de tres fichas iguales.
- Un segundo verde, un degradado, una sombra o un segundo radio.
- Versalitas con tracking fuera del rol `.t-label`.
- Repetir el aviso de disponibilidad en cada fila.
- Cualquier script, tipografía o imagen servida desde un tercero.
- Analítica, píxeles, gestores de etiquetas o chat externo.
- Movimiento en bucle, parallax o coreografía de scroll.
- Afirmar representación oficial, distribución exclusiva o certificación.
