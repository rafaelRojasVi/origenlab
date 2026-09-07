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

`npm run qa:contrast` recalcula el contraste de cada texto visible del sitio
construido sobre su fondo efectivo. El par más ajustado hoy es `ink-500` sobre
`surface` con 4,67:1, en las etiquetas mono del riel de marcas.

Reglas: un solo acento en todo el sitio; sin degradados; el color nunca es el
único portador de significado; los filetes no transportan información.

#### Las seis tintas de familia

Añadidas en la revisión del 2026-09-07 y **sólo** para la constelación del hero
y la fila del índice que le corresponde. No aparecen en ningún otro sitio.

| Token | Valor | Familia | Sobre `paper` |
|---|---|---|---|
| `family-sonicacion` | `#1D4F7C` | Sonicación y procesamiento ultrasónico | 8,16:1 |
| `family-dispersion` | `#6B3F96` | Dispersión y homogeneización | 7,23:1 |
| `family-centrifugacion` | `#115E59` | Centrifugación y separación | 7,25:1 |
| `family-pesaje` | `#7D5310` | Pesaje y análisis de humedad | 6,44:1 |
| `family-osmometria` | `#15697F` | Osmometría | 5,90:1 |
| `family-electroforesis` | `#8F2F4F` | Electroforesis | 7,47:1 |

No son un segundo acento: son una escala de identificación. El sistema sigue
teniendo un acento, el teal, y centrifugación lo reutiliza en vez de inventar un
séptimo verde. Existen porque el hero tiene que decir de un vistazo que hay seis
capacidades distintas, y seis puntos del mismo color dicen lo contrario.

Las seis pasan AA sobre papel y sobre superficie, de modo que pueden teñir texto
(el numeral de la fila activa) y no sólo un punto. Y no son el único portador de
significado: el numeral que las acompaña es el mismo en el diagrama y en la
tabla, y funciona sin color.

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
- Movimiento: respuesta a hover y foco, el desplazamiento de 2 px de la flecha
  en los enlaces, el revelado al desplazarse de la portada y los dos bucles
  acotados de más abajo. Nada de parallax, ninguna librería de animación. Todo
  lo que se mueve está bajo `prefers-reduced-motion`.

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

**La constelación del hero no es un bucle.** Todo su movimiento es respuesta a
una acción: `transform: scale` y `opacity` sobre el nodo y su conector. Con
`prefers-reduced-motion: reduce` desaparecen las transiciones, el estado cambia
de golpe y la relación entre el diagrama y el índice sigue funcionando.

Medido en la revisión del 2026-09-07: CLS 0,0000 en las seis rutas principales a
390, 768 y 1440 px, y ni un píxel de desplazamiento horizontal. Todo lo que se
mueve compone (`transform` y `opacity`) y la caja de la constelación reserva su
alto con `aspect-ratio`.

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
| `ui/ModelRow` | Fila de modelo: fotografía, nombre, tipo y cifras. Sustituye a las fichas de producto |
| `ui/ModelList` | Contenedor de filas de modelo (filete superior y reinicio de lista) |
| `ui/ApplicationList` | Las tres líneas comerciales como lista numerada |
| `ui/SkuList` | Referencias sin ficha propia: nombre, número de ítem y una línea |
| `ui/ProductFigure` | Fotografía de equipo en caja cuadrada sobre papel, con `<picture>` AVIF/WebP |
| `ui/ModelPhoto` | Fotografía de un modelo o familia desde `productImages.ts`; sólo dibuja filas `VERIFIED`, `object-fit: contain`, rótulo con modelo y fabricante, y rotula las imágenes de familia como representativas |
| `ui/KeyFacts` | Tres o cuatro cifras de cabecera en mono |
| `ui/SpecGroups` | Ficha técnica agrupada en listas de definición |
| `ui/CompareTable` | Comparativa: tabla con región desplazable en escritorio, listas apiladas bajo 768 px |
| `ui/BrandLogo` | Logotipo a tinta única con altura óptica por marca. `loading="eager"` sólo en el riel |
| `ui/ModelSheet` | Ficha de un modelo que el sitio describe y enlaza pero no aloja: qué hace, usos, criterios y fuente |
| `ui/ClosingQuoteBand` | Banda de cierre invertida, única por página |
| `ui/Notice` | Aviso comercial, uno por página |
| `ui/ExternalLink` | Enlace externo, anunciado y marcado |
| `ui/Icon` | Trazos de Tabler Icons (MIT), 24x24, stroke 1,5 |
| `ui/LegalDraftNotice` | Estado de borrador de las rutas legales |
| `ui/PendingFactsTable` | Datos legales que faltan, nombrados uno a uno |
| `home/Hero` | Portada: titular a escala propia, constelación e índice de alcance enlazado |
| `home/SampleConstellation` | La marca como ancla y seis nodos de capacidad, unidos al índice del hero |
| `home/BrandRail` | Riel horizontal de las seis marcas, una sola altura, gris, con control de pausa |
| `home/EquipmentFamilies` | Las seis familias, en cuatro composiciones distintas |
| `home/FamilyMotif` | Diagrama de qué le hace el equipo a la muestra, para las familias sin fotografía |
| `home/ConsultationSection` | Asesoría técnica y la especialista |
| `home/ProcessSection` | De la consulta a la cotización, en cinco pasos |
| `home/AudienceSection` | Sectores atendidos y las tres líneas comerciales |

Utilidades globales en `global.css`: `.shell` (ancho y márgenes de página),
`.section` (ritmo vertical), `.rule-top` (filete de separación), `.link-row`
(fila de enlaces con flecha) y los roles tipográficos.

Acciones: `.btn-primary` (relleno teal), `.btn-secondary` (contorno tinta),
`.btn-invert` (sólo sobre la banda) y `.link-arrow` (texto y flecha, nunca
compite con el primario). Una intención, una etiqueta, y el sitio tiene dos:

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

- Fichas dentro de fichas, o una rejilla de tres fichas iguales.
- Un segundo verde, un degradado, una sombra o un segundo radio. Las seis tintas
  de familia no son un segundo acento: viven sólo en la constelación del hero y
  en su índice, y centrifugación reutiliza el teal del sistema.
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
