# Sistema de diseño, sitio público V2

Status: canonical
Owner: web-maintainers
Last reviewed: 2026-11-08

Este documento describe el sistema visual del sitio público tras el rediseño V2.
Es la referencia para decidir si algo nuevo pertenece al sitio o no. La verdad de
negocio sigue viviendo en `src/data/*`; aquí sólo se define **cómo se presenta**.

El descubrimiento que originó el rediseño está en
[`WEBSITE_V2_DESIGN_BRIEF.md`](WEBSITE_V2_DESIGN_BRIEF.md). Los datos que el
negocio todavía debe aportar están en [`CONTENT_NEEDED.md`](CONTENT_NEEDED.md).

---

## 1. Idea

**Blanco con color: equipo real sobre blanco puro, tarjetas blancas con sombra
teal suave, un solo acento teal.**

El 2026-11-08 la dueña de la empresa vetó el negro y el gris («nada sobre negro
ni gris»); entre tres maquetas claras, los dueños eligieron «Blanco con color».
La página es blanca y los equipos se ven sobre blanco puro, sólo con su sombra
suave: **sin resplandores de color detrás de ningún equipo** y **sin tintes
pastel por familia** (ambos se probaron el 2026-11-08 y el dueño los descartó:
«se ve tacky», «quita el color de fondo»). El color de marca vive en el acento
teal (botones, enlaces, punto final) y en la banda de cierre. La jerarquía la
sostiene la tipografía grande (800, muy apretada, punto final teal).

Consecuencias prácticas:

1. **Ninguna superficie gris ni oscura.** La única excepción es el pie del sitio
   (`--color-footer`, decisión de la dueña del 2026-11-08: «el pie oscuro, como
   el actual»). Las sombras van teñidas de teal, nunca negras.
2. **Pósteres, tiras de giro y fotos de fabricante se funden, sin caja**: están
   sobre blanco y se ponen con `.pz` (`filter: brightness(1.037)` +
   `mix-blend-mode: multiply`), así su blanco se funde con el papel. Nunca dentro de un envoltorio
   transformado o aislado entre la imagen y su fondo.
3. **Una sola tarjeta de modelo**: `components/ui/LitCard.astro`. Nunca
   tarjetas dentro de tarjetas; lo demás se agrupa con filetes y espacio.
4. **Cada mención de un modelo enlaza directo a su ficha** (regla del dueño,
   2026-11-08). Los listados siguen el orden comercial de `src/lib/ranking.ts`.
5. **El aviso comercial se dice una vez por página**, en texto normal.

---

## 2. Tokens

Todos los valores viven en `src/styles/global.css` bajo `@theme`. Un componente
que introduce un color, un radio o una sombra propia está fuera del sistema.

### Color

| Token | Valor | Uso |
|---|---|---|
| `paper` / `raised` | `#FFFFFF` | Página, tarjetas, menús, hoja del mega-menú, panel del formulario |
| `surface` | `#EFFAF8` | Tinte teal pálido: secciones alternas, cuadros de datos |
| `pressed` | `#E2F3EF` | Hover/activo |
| `hairline` / `hairline-strong` | `#E5EEEB` / `#CFDEDA` | Filetes decorativos (**nunca** borde de control) |
| `edge` | `#6D7E7B` | Borde de controles: botón secundario, campos, botón «Pausar» de la franja (≥ 3,5:1 sobre blanco) |
| `ink-950` | `#0D1918` | Titulares (18:1) |
| `ink-800` | `#263432` | Texto corrido (13:1) |
| `ink-600` | `#465755` | Texto secundario (7,6:1) |
| `ink-500` | `#5A6B68` | Texto terciario (5,6:1) |
| `teal-700` / `accent` | `#0F766E` | Botón primario (texto blanco, 5,5:1), etiquetas, anillo de foco |
| `teal-800` / `accent-hover` | `#115E59` | Enlaces (7,6:1), hover del primario |
| `teal-500` | `#14B8A6` | El punto final de los titulares, puntos, LED (nunca texto) |
| `glow-teal` / `glow-aqua` | `#C4F1E6` / `#C9ECFB` | Sólo los radiales de la banda de cierre (`.surface-band`); nunca detrás de un equipo |
| `band` | `#DCF6F0` | Banda de cierre (con radiales aqua y menta, `.surface-band`) |
| `quote` | `#F4F9FE` | Sección de la cotización (`.surface-quote`) |
| `studio` | `#FFFFFF` | Fondo sobre el que se hornean pósteres y tiras (`scene.ts`) |
| `header` | `rgb(255 255 255 / .82)` | Cabecera de vidrio blanco con desenfoque |
| `footer` | `#141617` | El pie: la única superficie oscura |
| `onink-100/300/500` | `#ECEBEB` / `#B9BCBD` / `#8D9295` | Texto del pie (15 / 9,5 / 5,8:1) |
| `shadow-card` / `-lift` / `-btn` / `-sheet` | `rgb(15 90 80 / …)` | Sombras teñidas de teal |

`npm run qa:contrast` recalcula el contraste de cada texto visible del sitio
construido sobre su fondo efectivo (AA 4,5:1), el borde de los controles
secundarios (3:1) y el anillo de foco de cada control.

Errores de formulario (con alcance de `/contacto/`): `--color-error` `#D92D20`
(borde), `--color-error-fg` `#B42318` (texto, 6,5:1), `--color-error-bg`
`#FEF3F2`.

La hoja de la cotización de ejemplo (`components/home/QuoteShowcase.astro`)
replica los colores de impresión del Cotizador (`quotePrint.css`): es un
documento, no una superficie del sitio. Como la cotización real, no lleva
imágenes de equipos.

Reglas: un solo acento en todo el sitio; el color nunca es el único portador de
significado; los filetes no transportan información.

#### Tintas de familia

| Familia | Tinta (`family-*`, ≥ 5,6:1 sobre blanco) |
|---|---|
| Sonicación | `#1D4F7C` |
| Pesaje y humedad | `#7D5310` |
| Centrifugación | `#5B3F96` |
| Dispersión | `#1F5F7A` |
| Osmometría | `#6D5A0E` |
| Electroforesis | `#2F4A8F` |

Las tintas sólo marcan texto y filetes de una familia (numerales, rótulos,
accesorios). **No hay fondos por familia**: los tokens `tint-*` y `familyTint()`
se retiraron el 2026-11-08 y no deben volver.

**No existe el verde WhatsApp.** El canal se identifica por el glifo, no por un
segundo verde que compita con la marca.

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

Roles definidos como clases en `global.css`: `.t-d1`, `.t-d2`, `.t-d3` (display),
`.t-h2`, `.t-h3`, `.t-lead`, `.t-body`, `.t-small`, `.t-label`, `.t-data`.
`.t-display` sigue existiendo como alias de `.t-d3`.

| Rol | Peso | Tamaño (390 a 1280 px) | Interlínea | Uso |
|---|---|---|---|---|
| `.t-d1` | 800, -0,02 em | 48 a 96 px | 0,98 | Titular de la portada |
| `.t-d2` | 800, -0,02 em | 40 a 72 px | 1,0 | Primera pantalla de modelo y de familia |
| `.t-d3` | 800, -0,02 em | 34 a 56 px | 1,04 | Titular de página interior |
| `.t-h2` | 700, -0,02 em | 32 a 40 px | 1,1 | Título de sección |

Reglas: ningún texto por debajo de 13 px; la versalita con tracking sólo existe
en el rol `.t-label`; medidas de 60 a 66 caracteres en texto corrido; sin rayas
(`—`) ni semirrayas en la copia visible; espacio duro entre cifra y unidad.

### Espacio, forma y movimiento

- Base de 8 px. Ritmo de sección: 64 px en móvil, 96 px desde 768 px.
- Ancho de página 80 rem, con 20 / 32 / 48 px de margen exterior (`.shell`).
- **Radios: 10 px controles** (`--radius-btn`), **16 px tarjetas** (`--radius-card`),
  **24 px escena 3D** (`--radius-scene`), **28 px banda de cierre** (`--radius-band`)
  y **2 px** (`--radius-edge`) para etiquetas, tablas, imágenes y campos de
  lectura. Bajo 640 px la banda baja a 20 px y la escena a 16 px.
- **Altura de botón**: 48 px (`--btn-h`) y 40 px el pequeño (`--btn-h-sm`), que
  **bajo 1024 px sube a 44 px**: ningún objetivo táctil queda por debajo de 44 px.
  Relleno horizontal 20 y 16 px.
- **Transición de interfaz**: `--ease-ui` = 150 ms ease-out, para color, borde y
  flecha. Cabecera y marca tienen sus propios tiempos (abajo).
- **Sin sombras.** La cabecera fija se separa con un filete, no con desenfoque.
- Movimiento: respuesta a hover y foco, la flecha del botón (4 px) y el escalado
  .98 al pulsar, la cabecera que se encoge, la marca, el revelado `.rv` y los dos
  bucles acotados de más abajo. Nada de parallax, ninguna librería de animación. Todo
  lo que se mueve está bajo `prefers-reduced-motion`.

**Cabecera y marca.** La cabecera pasa de 72 a 56 px ligada al desplazamiento
(`animation-timeline: scroll(root)`, CSS puro); sin soporte, un observador marca
`data-scrolled` y la transición es de 200 ms ease-out. La marca de tres cuerpos se
dibuja **una vez al cargar y otra en cada `pointerenter`**, 1,2 s
(`SiteLogo.astro`, clase `.is-playing`); no es un bucle. Bajo
`prefers-reduced-motion: reduce` la marca queda quieta y dibujada, y la cabecera
cambia de tamaño de golpe, sin transición.

**`.rv`, revelado ligado al desplazamiento** (`global.css`): CSS puro con
`animation-timeline: view()`, rango `entry 0% a 35%`, de opacidad .15 y 20 px a
su sitio. Sin soporte de `animation-timeline`, con movimiento reducido o en
impresión no hace nada y el contenido está siempre pintado. Nada de la primera
pantalla lleva `.rv`. Es el mecanismo de las páginas de la 1b; `[data-reveal]`
(abajo) es el heredado.

**Movimiento reducido, política única.** Una regla global pone a cero toda
transición (`transition-duration: 0s !important`); el desplazamiento de la flecha
y el escalado del botón sólo existen bajo `no-preference`; `.rv`, el bucle de
la marca del hero y el del riel no se declaran con `reduce`. `qa:interaction`
comprueba que con `reduce` no queda ninguna animación activa.

**Presupuestos** (`validate:dist`): JavaScript de página < 15 kB (la medición de
`visita` va en línea y cuenta), CSS < 80 kB, la sala 3D bajo demanda < 900 kB
aparte. Con la 1b: JS 13,8 kB, CSS 74,5 kB. Una pieza nueva de CSS borra primero
lo que reemplaza.

**Los dos únicos bucles del sitio, los dos en la portada.** No hay más, y añadir
un tercero exige la misma justificación que estos dos:

1. **La órbita de la marca del hero.** Tres cuerpos recorriendo la lemniscata
   por `offset-path`, 34 s, sin JavaScript. No se anima la marca de la cabecera
   ni la del pie: un bucle permanente en el shell del sitio, en toda página, no
   tendría cómo pararse.
2. **El riel de marcas.** `transform` sobre una pista duplicada, 52 s. Es el
   único bucle con control de pausa visible, y lo tiene porque WCAG 2.2.2 lo
   exige para contenido en movimiento de más de 5 s. El control lo instala el
   script, y el movimiento no existe hasta que el script marca el riel: sin
   script no hay control y por tanto tampoco movimiento.

**La sala 3D del hero** (2026-11-02) es la única escena con movimiento continuo
y es mejora progresiva: el HTML trae la fotografía oficial del equipo y
three.js se pide con `import()` sólo con WebGL, sin
`prefers-reduced-motion: reduce` y cuando la figura se acerca a la pantalla. El
bucle se detiene fuera de pantalla y con la pestaña oculta. Fuera del lienzo
todo son botones normales con `aria-pressed` y `aria-expanded`. El fragmento se
mide aparte del presupuesto de 15 kB (`validate:dist`), porque no forma parte
de la carga de ninguna página.

Medido en la revisión del 2026-09-07: CLS 0,0000 en las seis rutas principales a
390, 768 y 1440 px, y ni un píxel de desplazamiento horizontal. Todo lo que se
mueve compone (`transform` y `opacity`) y la caja de la sala 3D reserva su alto
con `aspect-ratio`.

**Revelado al desplazarse** (`[data-reveal]`, definido en `global.css`): una
transición de opacidad y 12 px de desplazamiento vertical cuando el elemento
entra en pantalla. Condiciones, todas obligatorias:

1. Es mejora progresiva estricta. El estado inicial oculto sólo existe mientras
   el documento lleva `data-reveal-ready`, que pone el script de la portada. Sin
   JavaScript no se oculta nada nunca.
2. Todo el bloque vive bajo `prefers-reduced-motion: no-preference`, y un cambio
   de preferencia con la página abierta descarta el estado oculto.
3. Ningún elemento de la primera pantalla lo lleva: allí el script llegaría
   después del primer pintado y se vería el parpadeo.
4. `@media print` fuerza la visibilidad: en papel no hay desplazamiento que
   dispare nada.
5. Es una transición, no una animación: `qa:interaction` comprueba que con
   movimiento reducido no queda ninguna animación activa.
6. Ningún contenido depende del movimiento para entenderse, y no hay secuestro
   del desplazamiento.

---

## 3. Numeración de secciones

Cada sección de primer nivel de una **página interior** lleva un índice: numeral
mono, filete y nombre en lenguaje llano. Funciona como el índice de capítulos de
un manual técnico.

```text
01 ──────── EQUIPOS
Centrífugas Ortoalresa
```

Reglas:

- Lo genera `SectionIndex.astro` a partir de un `index`; el numeral no se escribe
  a mano.
- Una vez por sección de primer nivel. Nunca dentro de una fila, una ficha o el
  hero.
- En una página interior, la banda de cotización lleva el último número.
- Es un índice, no una etiqueta decorativa: si una sección no es un capítulo del
  documento, no lleva numeral.

**La portada no se numera.** Encadenar `01` a `05` en la página de inicio la
convertía en un documento formateado y no en un primer encuentro comercial: el
numeral prometía un manual que la portada no es. Allí la identidad de cada
sección la da la etiqueta mono y, sobre todo, el cambio de fondo, de escala y de
densidad. `ClosingQuoteBand` acepta `index` opcional justamente para eso.

---

## 4. Primitivas

Pocas y mejores. Antes de crear un componente nuevo, comprobar que ninguna de
estas resuelve el problema.

| Componente | Papel |
|---|---|
| `ui/SectionIndex` | Índice numerado de sección |
| `ui/PageIntro` | Migas, H1 y frase de contexto de toda página interior |
| `ui/Breadcrumbs` | Ruta + `BreadcrumbList` en JSON-LD |
| `ui/ApplicationList` | Las tres líneas comerciales como lista numerada |
| `ui/ModelPhoto` | Fotografía de un modelo o familia desde `productImages.ts`; sólo dibuja filas `VERIFIED`, `object-fit: contain`, rótulo con modelo y fabricante, y rotula las imágenes de familia como representativas |
| `ui/SpecGroups` | Ficha técnica agrupada en listas de definición |
| `ui/CompareTable` | Comparativa compacta de una familia bajo sus tarjetas: una fila por modelo (orden comercial), 3–4 cifras del fabricante; bloques por modelo bajo 768 px |
| `ui/BrandLogo` | Logotipo a tinta única con altura óptica por marca. `loading="eager"` sólo en el riel |
| `ui/ClosingQuoteBand` | Banda de cierre invertida, única por página; `primary="whatsapp"` pone WhatsApp en el botón y el formulario entre los canales |
| `ui/Notice` | Aviso comercial, uno por página |
| `ui/ExternalLink` | Enlace externo, anunciado y marcado |
| `ui/Icon` | Trazos de Tabler Icons (MIT), 24x24, stroke 1,5 |
| `ui/LegalDraftNotice` | Estado de borrador de las rutas legales |
| `ui/PendingFactsTable` | Datos legales que faltan, nombrados uno a uno |
| `ui/Button` | Botón del sistema, `<a>` con `href` y `<button>` sin él (ver más abajo) |
| `home/HomeHero` | Portada A: titular B en `.t-d1`, dos acciones y la sala 3D |
| `home/Showroom` | El equipo en 3D (Hielscher UP400St, medidas del plano oficial) sobre la fotografía oficial como respaldo; se gira, se enciende y se recorre por vistas |
| `home/HomeBrands` | Las siete marcas, en orden `rankBrands`, una altura |
| `home/HomeFamilies` | Las seis familias, desde `homeFamilyCards` |
| `home/FamilyMotif` | Diagrama de qué le hace el equipo a la muestra, para las familias sin fotografía |
| `home/HomeSteps` | Cómo comprar en pasos (`buyingSteps()`); la reusan la portada y `/como-comprar/` |
| `home/HomeNotes` | Notas de aplicación; sólo se dibuja si existen (hoy ninguna) |
| `product/FamilyModels` | Rejilla de `LitCard` del catálogo (`/productos/`, familias, centrífugas, `/categorias/`), orden `rankModels`, 2/3/4 columnas; fotos de fondo gris sobre panel blanco |
| `product/SpecTable` | Ficha técnica de modelo en tabla que se desplaza dentro de su caja |
| `product/ModelHero`, `ModelHeroCopy` | Primera pantalla de modelo: 3D (`[data-first-screen]`) y copia; barra fija `[data-sticky-cta]` tras ella |
| `product/TrustStrip`, `ModelFaq`, `AccessoryCards` | Secciones de la ficha (las relacionadas son `LitCard`) |
| `product/FamilyConsult` | «¿No sabe cuál?»: la `ClosingQuoteBand` de las páginas de familia |
| `ui/LitCard` | La única tarjeta de modelo: blanca, filete `hairline`, `shadow-card`; póster o tira de giro (`Turntable`) o foto de fabricante; nombre, un dato, «Ver ficha» y «Cotizar» |
| `ui/RegisteredStrip` | «Proveedor inscrito en»: nombres y logos aprobados en `claims.ts`, marquesina CSS con botón «Pausar»; quieta con movimiento reducido |
| `ui/IndustryTiles` | Las industrias en tejas de texto con «Ver equipos →»; portada y `/aplicaciones/` |
| `ui/StepList` | Procedimiento vertical con filete continuo; por defecto los cuatro pasos de compra |

Utilidades globales en `global.css`: `.shell` (ancho y márgenes de página),
`.section` (ritmo vertical), `.rule-top` (filete de separación), `.link-row`
(fila de enlaces con flecha) y los roles tipográficos.

Acciones: se escriben con `ui/Button.astro` y no con clases a mano.

| `variant` | Aspecto | Uso |
|---|---|---|
| `primary` | Píldora teal (`accent`), texto blanco, sombra teal, flecha | La acción de la página; una por pantalla |
| `secondary` | Píldora con contorno 1,5 px `edge`, fondo transparente, flecha | Acción alterna |
| `tertiary` | Texto teal, el subrayado crece al pasar, flecha | Enlace de acción; nunca compite con el primario |
| `whatsapp` | Contorno, glifo de WhatsApp | Canal directo |
| `icon` | Cuadrado con glifo; `label` obligatorio (nombre accesible) | Menú, cerrar, pausa |

Tamaños `md` (50 px) y `sm` (40 px; 44 px bajo 1024 px), siempre píldora. Un
solo aspecto en todo el sitio (`tone` se acepta por compatibilidad y no cambia
nada). Primario: hover `accent-hover`, foco anillo teal separado 3 px.
Secundario, WhatsApp e icono: sin relleno, borde `edge`, hover borde
`ink-950`. Deshabilitado con `aria-disabled`.
La flecha avanza 4 px al hover. Los `data-*` y `aria-*` extra se reenvían;
las clases `.btn-*` existen pero no se escriben a mano. Una intención,
una etiqueta, y el sitio tiene dos:

- **«Solicitar cotización»** pide una propuesta formal. Es la acción de la
  cabecera, de la banda de cierre y de las páginas de producto. La forma corta
  «Cotizar» existe sólo en la cabecera estrecha, donde el nombre accesible sigue
  siendo el completo.
- **«Cuéntenos su aplicación»** abre la conversación técnica previa. Es la
  acción de la portada y de la sección de asesoría, porque quien todavía no sabe
  qué equipo necesita no está pidiendo un precio.

Ambas llegan a `/contacto/`. Las etiquetas viven en `lib/ctaLabels.ts` y no se
escriben a mano en una plantilla.

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

Toda `<img>` declara `width` y `height`. La ficha de producto y los seis
logotipos del riel cargan con prioridad; el resto es diferido. El riel es la
excepción por una razón concreta: sus logotipos entran en cuadro por el propio
movimiento, sin que el visitante desplace la página, y uno diferido aparecería
en blanco al llegar.

---

## 6. Lo que el sistema no admite

- Tarjetas fuera de la de modelo (`LitCard`): en
  todo lo demás se agrupa con filetes y espacio. Nunca fichas dentro de fichas ni
  una rejilla de tres tarjetas iguales.
- Un segundo verde, un degradado, una sombra o un segundo radio. Las tintas de
  familia no son un segundo acento ni un fondo.
- Fondos de color por familia o resplandores detrás de un equipo (retirados por
  el dueño el 2026-11-08).
- Versalitas con tracking fuera del rol `.t-label`.
- Repetir el aviso de disponibilidad en cada fila.
- Cualquier script, tipografía o imagen servida desde un tercero.
- Analítica, píxeles, gestores de etiquetas o chat externo.
- Parallax y secuestro del desplazamiento, sin excepción.
- Un tercer bucle. Los dos que hay están en la sección 2 con su justificación, y
  el del riel con su control de pausa. El revelado de `[data-reveal]` es una
  transición, no un bucle, y sólo con las seis condiciones de la sección 2.
- Afirmar representación oficial, distribución exclusiva o certificación.

---

## 7. Cifras visibles

Ninguna cifra comercial se escribe en una plantilla. Toda afirmación numérica
pública vive en `src/data/claims.ts` con redacción exacta, fuente, fecha de
medición, quién la aprobó, cuándo y si es pública, y llega a la página por
`publicClaim(id)`, que devuelve `undefined` si falta cualquiera de esos campos.
Cuando devuelve `undefined` la plantilla omite el elemento entero: no hay texto
de reserva que pueda confundirse con la cifra no aprobada.

Dos guardas lo sostienen. `validate:catalog` comprueba la forma de cada registro
y que una plantilla no importe el arreglo `claims` para saltarse la puerta.
`validate:dist` busca la redacción literal de toda afirmación no aprobada dentro
del HTML construido y falla si aparece.

El registro también guarda, con `status: 'unavailable'`, las cifras que se
evaluaron y se descartaron: clientes, ventas, cotizaciones, proyectos, años de
experiencia e historial de tiempos de respuesta. Están ahí para que nadie las
vuelva a proponer sin la evidencia que falta. **Los contactos, organizaciones y
destinatarios de campaña del CRM no son clientes.**

## 8. Rutas legales en borrador

`/privacidad/` y `/aviso-legal/` se construyen desde `src/data/legal.ts` y son
borradores de revisión, no política vigente. Mientras `isLegalTextApproved()`
sea falso:

- ambas se sirven con `noindex`, fuera del sitemap, con `Disallow` en
  `robots.txt` y con `X-Robots-Tag` en `.htaccess`;
- el pie las enlaza marcadas como borrador, porque un enlace honesto a un
  borrador es mejor que un enlace ausente;
- cada página abre con `LegalDraftNotice`, que dice en su primera línea que no
  es asesoría legal;
- lo que falta se nombra con `PendingFactsTable` en vez de disolverse en
  lenguaje jurídico genérico.

Ningún `value` de `legal.ts` puede rellenarse desde el repositorio, y
`validate:catalog` lo comprueba. El título es «Aviso legal y condiciones de uso»
y no «Condiciones de venta»: el sitio no tiene carrito, formulario ni pasarela,
y lo acordado en una operación vive en la cotización.
