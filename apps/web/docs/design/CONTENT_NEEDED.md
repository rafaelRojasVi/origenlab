# Contenido pendiente y revisión legal, sitio V2

Status: canonical
Owner: web-maintainers
Last reviewed: 2026-09-07

Lo que el sitio **no** publica porque nadie lo ha confirmado. Ningún elemento de
esta lista puede redactarse desde el repositorio: o lo aporta el negocio
(**CONTENIDO**) o lo confirma un abogado (**LEGAL**).

Mientras un punto siga aquí, el diseño deja el hueco preparado y no lo rellena.
La única excepción son las dos rutas legales, que existen como borrador visible
y sin indexar para poder revisarlas: un texto que se declara borrador es útil,
uno que finge estar aprobado no.

Las cifras tienen además su propio registro en `src/data/claims.ts`, con fuente,
fecha, aprobación y estado público. Una cifra sin aprobar no se renderiza, y
`validate:dist` lo comprueba sobre el HTML construido.

---

## 1. Identidad legal y privacidad

| Pendiente | Tipo | Bloquea |
|---|---|---|
| Razón social, RUT y comuna del domicilio legal | CONTENIDO | Fila legal del pie, `/aviso-legal/`, campo `legalName` en JSON-LD `Organization` |
| Texto de la política de privacidad, bases de licitud, plazos de conservación y análisis de transferencias | LEGAL | `/privacidad/` |
| Dirección de correo para derechos del titular (por ejemplo un alias `datos@`) y proceso interno de atención | CONTENIDO | Canal de solicitudes en `/privacidad/` y `/contacto/` |
| Si hace falta aviso de cookies con el estado actual del sitio | LEGAL | Nada hoy: el sitio no fija cookies propias ni carga terceros |
| Estado de los acuerdos con encargados de tratamiento (Cloudflare, HostGator, Titan) | CONTENIDO / LEGAL | Inventario de encargados |

**Estado actual verificable del sitio** (base factual para esa futura política,
comprobado por `npm run validate:dist` en cada build): no carga ningún script,
tipografía, imagen ni hoja de estilo de terceros; no fija cookies propias; no
tiene formularios; no usa analítica ni píxeles. Las consultas llegan por correo o
WhatsApp del propio visitante. Cloudflare actúa como proxy del dominio y
HostGator como hosting, de modo que ambos procesan direcciones IP como
proveedores de infraestructura. Ese inventario vive ahora en código, en
`src/data/legal.ts` (`siteBehaviour`), con la comprobación de cada punto al lado.

**Las dos rutas existen como borrador desde la revisión de portada del
2026-09-06.** `/privacidad/` y `/aviso-legal/` se construyen desde
`src/data/legal.ts`, muestran lo verificado y **nombran lo que falta** en vez de
taparlo con lenguaje genérico. Mientras `legalStatus.reviewedBy` sea `null`:

- se sirven con `noindex`, fuera del sitemap, con `Disallow` en `robots.txt` y
  con `X-Robots-Tag` en `.htaccess`;
- el pie las enlaza marcadas como borrador, para poder revisarlas en la vista
  previa de la rama;
- cada una abre con un aviso que dice que no es una política vigente.

**No se despliegan.** El texto final tiene que revisarlo un profesional
habilitado en Chile antes de publicarse, y el negocio tiene que aportar la
identidad legal de la tabla anterior.

La Ley 21.719 entra en vigor el 1 de diciembre de 2026. Qué obligaciones aplican
a OrigenLab como operador B2B pequeño es una pregunta legal, no de ingeniería.

---

## 2. Marcas

**Lista cerrada de seis, fijada por el negocio en la revisión de marca del
2026-09-06:** Hielscher Ultrasonics (sonicación), Ortoalresa (centrifugación),
IKA (dispersión y homogeneización), Adam Equipment (pesaje y análisis de
humedad), Löser Messtechnik (osmometría) y SERVA Electrophoresis
(electroforesis).

Esa revisión resolvió el punto que llevaba abierto desde mayo, «qué fabrica cada
marca sin catálogo»: ahora la correspondencia marca–familia vive en
`src/data/brands.ts` (`familyId`) y el sitio puede nombrar la familia junto al
logotipo. **Ollital y CRTOP salieron del sitio público.**

`npm run validate:brands` comprueba la lista en cuatro capas: los registros de
`brands.ts`, los archivos de `public/brands/`, las filas de
`src/data/sourceRegistry.ts` y el HTML de `dist/`. Una marca retirada que
reaparezca en cualquiera de ellas hace fallar la validación.

**Desde el 2026-09-07 las seis tienen página propia.** Lo que antes lo impedía
era una sola bandera, `catalogPublished`, que decidía a la vez si había página y
si se podía describir el alcance comercial. Ahora son dos ejes en `brands.ts`,
`editorialPublished` y `commercialScopeConfirmed`, y sólo el segundo sigue
esperando al negocio. El material editorial de las seis es investigación
verificada: 18 modelos y familias en `src/data/brandModels.ts`, leídos de la
página o del PDF del propio fabricante el 2026-09-07 y con la URL comprobada por
petición real (`npm run verify:sources`, 47 destinos, 0 fallos).

| Pendiente | Tipo | Bloquea |
|---|---|---|
| Alcance comercial formal por marca (distribuidor, revendedor, pedidos gestionados) | CONTENIDO | `commercialNote` en Hielscher, IKA, Adam Equipment y Löser; `validate:catalog` impide declararlo sin `commercialScopeConfirmed`. Sus páginas dicen qué fabrica el fabricante y que OrigenLab cotiza la línea, nunca en qué calidad |
| ~~Permiso de Ortoalresa para las cinco fotografías publicadas~~ | **Resuelto 2026-09-07** | El titular del negocio confirmó que OrigenLab cuenta con autorización previa de los seis fabricantes para publicar su fotografía oficial de producto (`MANUFACTURER_AUTHORIZATION_BASIS` en `src/data/productImages.ts`; registro en `docs/product-assets.md`). No es una licencia pública ni se afirma documento, cláusula, vigencia o exclusividad. El uso del logotipo de Ortoalresa sigue en la fila de logotipos |
| Permiso escrito de reproducción de los seis logotipos | CONTENIDO | TODO abierto desde 2026-05. Registrado por marca en `src/data/sourceRegistry.ts` como `ASSET_PERMISSION_NEEDED`. Adam Equipment publica un Brand Toolkit para distribuidores (adamequipment.com/toolkit) que cede logotipos y banners; confirmar que aplica a OrigenLab |
| ~~Fotografía de equipo de Hielscher, IKA, Adam Equipment, Löser y SERVA~~ | **Resuelto 2026-09-07** | 24 de 24 entradas publicadas con fotografía oficial del fabricante (`VERIFIED` en `src/data/productImages.ts`), con permiso por confirmación del negocio y procedencia por imagen. Queda como mejora, no como bloqueo: pedir originales en mayor resolución a IKA (73 a 153 px en el folleto), Löser (400 × 500 px) y SERVA/LICORbio (BlueMarine 100 de 169 px y BlueSlick de 126 px), que hoy se muestran sin ampliar |
| **Logotipo en color de Adam Equipment** | CONTENIDO | Revelado de color en el riel de marcas. El único original en el repositorio (`public/email/brands/adam-equipment-source.png`, 300 × 125) ya es monocromo: media RGB 90,90,90. No existe versión en color |
| Logotipo vectorial (y en color utilizable) de Löser Messtechnik | CONTENIDO | El único original disponible es un JPEG de 70 × 70 calado sobre un azulejo naranja. Se normaliza a una tinta y se lee, pero no escala y no da un color fiable |
| HTTPS en el sitio de Löser Messtechnik | TERCERO | `loeser-osmometer.de` rechaza el saludo TLS: el sitio de 2005 sólo responde por http. El enlace oficial es http y la excepción está declarada en `brands.ts` (`websiteInsecure`) y en `brandModels.ts` (`officialUrlInsecure`). No depende de OrigenLab |
| PDF descargable de Löser Messtechnik | TERCERO | El fabricante no publica ninguno: los folletos se piden por formulario (`anfrage-eng.html`). Las cuatro fichas de osmometría enlazan página oficial y lo dicen |
| Regenerar la firma de correo corporativa | CONTENIDO | `public/email/origenlab-contacto-signature.html` sigue mostrando Ollital y CRTOP, y no incluye a Adam Equipment ni a Löser. Dejó de respaldar la cifra «6 marcas con las que trabajamos», que ahora se apoya en `brands.ts` y en `validate:brands` |
| Verificar en navegador la página de dispersores de IKA, y la URL de cada modelo | CONTENIDO | `ika.com` devuelve el interstitial de Cloudflare (403) a todo cliente automatizado, incluido un navegador headless. El PDF oficial sí responde y es de donde salen las cifras de T 10 basic, T 18 digital y T 25 digital. Las tres fichas declaran `officialUrlScope: 'familia'` y enlazan la página de dispersores, no la del modelo, porque no hay forma de comprobar por máquina la URL de cada uno |

### Por qué el riel de marcas es gris entero

Cuatro de los seis originales tienen color de fabricante utilizable (Hielscher,
Ortoalresa, IKA y SERVA). Adam Equipment no tiene ninguno y el de Löser es un
JPEG de 70 × 70. Revelar color al pasar el cursor dejaría cuatro marcas en color
y dos en gris: una jerarquía involuntaria, y justo entre las cinco familias de
maquinaria que la revisión acaba de igualar. El riel se queda gris entero, y lo
que revela el cursor o el foco es tinta plena y la familia en acento, igual para
las seis. **Con los dos activos que faltan se puede reconsiderar; con uno solo,
no.**

---

## 3. Producto y comercial

| Pendiente | Tipo | Bloquea |
|---|---|---|
| PDF de catálogos y fichas con permiso de publicación | CONTENIDO | `documents.ts` está vacío; las páginas de aplicación muestran el texto genérico de `company.catalogNote`. Los PDF del fabricante se enlazan, nunca se rehospedan |
| ~~Familias de equipo más allá de centrífugas~~ | **Resuelto 2026-09-07** | `/productos/` publica las seis familias en un orden que deja centrifugación en el centro |
| ~~Modelos y especificaciones de las cuatro familias por consulta~~ | **Resuelto 2026-09-07** | 18 modelos y familias en `brandModels.ts`, leídos de la fuente oficial. El nivel `consulta` pasó a `documentada`: puede nombrar modelos, y sigue sin poder declarar fotografía ni cifra sin aprobar |
| Identidad exacta y alcance de las referencias TEMED y REPEL-SILANE de SERVA | CONTENIDO | Se retiraron del catálogo el 2026-09-07: no fue posible verificarlas en el catálogo del fabricante, y una referencia de reactivo mal identificada es peor que ninguna. Están en `REMOVED_SLUGS` de `validate-catalog.mjs` para que no vuelvan sin la verificación |
| Rangos de las fuentes BluePower 300 BLOT, 3000 HPE y 6000 IPG | CONTENIDO | De las tres se publica el código de catálogo y para qué está pensada cada una. Sólo la 600 PRIME tiene rangos en su ficha del fabricante |
| ~~Qué familia fabrica cada marca sin catálogo~~ | **Resuelto 2026-09-06** | Confirmado por el negocio; vive en `brands.ts` (`familyId`) |
| ~~Fotografía de producto de familias distintas de centrifugación~~ | **Resuelto 2026-09-07** | Las seis familias publican fotografía oficial del fabricante en `/productos/`, `/marcas/` y cada página de marca (24 filas `VERIFIED` en `productImages.ts`). El diagrama propio se conserva como capa conceptual. La portada no cambió |
| Equipos asociados a alimentos | CONTENIDO | `/categorias/alimentos/` declara con franqueza que aún no hay familia publicada |
| Términos de garantía, instalación y puesta en marcha más allá de «según fabricante» y «por escrito» | CONTENIDO | Copy de `/servicios/`; se mantiene la redacción acotada actual |
| Expectativa de plazo de respuesta a una cotización | CONTENIDO | Propuesta redactada en `claims.ts` como `respuesta-inicial-un-dia-habil`, en estado `proposed` y sin aprobar: **no se renderiza**, y `validate:dist` comprueba que su texto literal no aparezca en el HTML. El sitio usa mientras tanto la redacción cualitativa aprobada «Respuesta ágil y seguimiento claro». Para aprobarla hace falta que el negocio la asuma por escrito y defina qué cuenta como respuesta inicial |
| Imágenes de producto de mayor resolución o de prensa del fabricante | CONTENIDO | Ortoalresa, Hielscher y Adam publican originales de ~1.000 px, suficientes hoy. IKA (73 a 153 px), Löser (400 px) y SERVA (126 a 500 px) publican originales pequeños que el sitio muestra a tamaño real; pedir archivos en alta resolución a cada fabricante mejoraría las fichas |

---

## 4. Asesoría técnica

La portada publica de Tatiana Vivanco exactamente tres hechos confirmados por
ella y por el negocio el 2026-09-06: nombre, profesión (bioquímica) y cargo
(gerente de ventas de OrigenLab). Viven en `src/data/consultation.ts` y
`validate:catalog` bloquea que se añada cualquier otro campo personal.

| Pendiente | Tipo | Bloquea |
|---|---|---|
| Autorización escrita para publicar un retrato, y el archivo con derechos | CONTENIDO | La sección de asesoría se compone hoy sin fotografía de persona |
| Titulación concreta, casa de estudios y trayectoria | CONTENIDO | Cualquier frase de credibilidad más allá de «bioquímica» |
| Años de experiencia | CONTENIDO | Registrado en `claims.ts` como `anos-de-experiencia`, sin fuente |
| Si se publica un canal de contacto propio o sólo los corporativos | CONTENIDO | Hoy se publican sólo `contacto@origenlab.cl` y el WhatsApp corporativo |

---

## 5. Prueba comercial

Nada de lo siguiente existe en el repositorio y **el diseño no tiene hueco para
ello** hasta que exista y sea verificable: referencias de clientes, testimonios,
casos, participación en licitaciones, certificaciones, membresías y registros de
proveedor.

Las cifras correspondientes están registradas en `src/data/claims.ts` con
`status: 'unavailable'` para que la decisión quede documentada y nadie las
vuelva a proponer sin evidencia: `clientes-atendidos`, `ventas-cerradas`,
`cotizaciones-emitidas`, `proyectos-realizados`, `anos-de-experiencia` e
`historial-de-tiempo-de-respuesta`.

**Los contactos, organizaciones, destinatarios de campaña y mensajes históricos
de correo del CRM no son clientes ni ventas.** No pueden convertirse en una
cifra pública por agregación.

La portada resuelve la falta de prueba social con las preguntas reales que traen
los laboratorios, no con testimonios inventados.

| Pendiente | Tipo |
|---|---|
| Historia de la empresa para `/nosotros/` (fundación, equipo, por qué Valdivia) | CONTENIDO |
| Cuentas de Instagram o LinkedIn (`contact.instagramHandle` es `null`) | CONTENIDO |
| Fotografía propia de laboratorio o de equipo instalado, con derechos | CONTENIDO |

---

## 6. Decisiones tomadas en el rediseño que conviene confirmar

1. **Se retiró Tidio.** El chat cargaba en cada página, incluidas las internas,
   antes de cualquier aviso. No se sustituyó por otro chat.
2. **Se ampliaron las marcas visibles de dos a seis**, con el respaldo de la
   firma corporativa y sin describir las cuatro nuevas. Si el negocio prefiere
   mostrar sólo las dos con catálogo, basta con quitarlas de `brands.ts`.
3. **Los logotipos de marca se muestran a una sola tinta**, siguiendo el criterio
   ya usado en la firma de correo.
4. **No se creó `/cotizar/` ni un formulario.** No existe endpoint aprobado, y
   `/contacto/` cumple ese papel con los canales reales prellenados.
5. **No se migró ninguna URL.** La navegación dice «Aplicaciones» mientras las
   rutas siguen siendo `/categorias/*`.

### Revisión de portada, 2026-09-06

6. **El alcance de cotización se amplió a cinco familias.** Centrifugación y
   electroforesis con ficha publicada; sonicación y procesamiento ultrasónico,
   dispersión y homogeneización, y pesaje y análisis de humedad por consulta
   técnica. La procedencia del segundo nivel es la confirmación del negocio en
   esta revisión, y está escrita en la cabecera de `equipmentScope.ts`. Si el
   negocio prefiere no anunciar una familia hasta tener catálogo, basta con
   quitarla de ese archivo.
7. **La portada dejó de numerar sus secciones** y perdió el acordeón de
   preguntas frecuentes, que se movió a `/contacto/` junto con su JSON-LD.
8. **«6 marcas con las que trabajamos» se declara una sola vez**, en el índice
   de alcance del hero, y sale del registro de afirmaciones.
9. **Se añadió una segunda etiqueta de acción**, «Cuéntenos su aplicación», para
   la intención de asesoría previa. Convive con «Solicitar cotización», que
   sigue siendo la de la propuesta formal.
10. **Se corrigió un fallo del arnés de QA**: `scroll-behavior: smooth` hacía
    que el recorrido de `qa:screens` nunca llegara al pie de la página, de modo
    que el contenido diferido de la mitad inferior no se comprobaba.

### Revisión de marca y portada, 2026-09-06 (rediseño V2)

11. **Se cerró la lista de marcas en seis** y se retiraron Ollital y CRTOP del
    sitio público. La lista es ahora una constante (`APPROVED_BRAND_IDS`) con
    una puerta automática, `npm run validate:brands`, que la comprueba también
    sobre el HTML construido. Sus activos de correo se conservan.
12. **Se rediseñó la marca.** El átomo anterior dibujaba los nodos con 0,38 px
    de radio a 32 px y las órbitas al 24 % de opacidad: era invisible. La marca
    nueva («Órbita») no usa ninguna opacidad menor que 1. Las tres exploraciones
    y la hoja de comparación están en `design/logo-explorations/`, y el sistema
    completo en `docs/logo-system.md`.
13. **Se retiró el sistema de logotipo anterior entero**: cinco componentes,
    ocho módulos de `src/lib/logo/`, cinco SVG y tres scripts. Con ello el sitio
    pasó de un canvas animado con simulación en el hilo principal a **0 kB de
    JavaScript**. La animación del hero es `offset-path` en CSS.
14. **La portada dejó de abrir con una sola familia.** El hero nombra las seis
    con su fabricante en la primera pantalla, y centrifugación (la única con
    fotografía) aparece la tercera de cuatro composiciones distintas.
15. **Las cinco familias sin fotografía llevan un diagrama propio** de lo que el
    equipo le hace a la muestra (`src/lib/familyMotif.ts`), no una fotografía de
    archivo ni el equipo de otro fabricante.
16. **Se creó `src/data/sourceRegistry.ts`**, con página oficial, PDF oficial,
    origen de imagen, base de permiso, fecha de verificación y estado por marca.
    `npm run verify:sources` comprueba con peticiones reales que los 25 destinos
    externos publicados responden.
17. **El número de teléfono aparece una sola vez por bloque y en un solo
    formato.** Antes se repetía dentro de la etiqueta de WhatsApp y como enlace
    `tel:` en la misma sección.

### Revisión de arquitectura de información, 2026-09-07

18. **La página de marca se separó del alcance comercial.** Dos ejes en
    `brands.ts` en vez de una bandera: `editorialPublished` habilita
    `/marcas/{slug}/` y lo tienen las seis; `commercialScopeConfirmed` habilita
    `commercialNote` y lo tienen dos. Antes cuatro familias de equipo enteras no
    tenían ninguna página, y lo único que faltaba de ellas era la confirmación
    comercial.
19. **Las seis páginas de marca comparten el estándar y ninguna la
    disposición.** `brand.layout` da a cada una la suya: escalera de potencia en
    Hielscher, fotografía en Ortoalresa, diagrama del mecanismo en IKA, rejilla
    de legibilidad en Adam, cuadro común en Löser, sistema en SERVA. Seis
    páginas idénticas con el nombre cambiado se leen como un formulario.
20. **Se creó `src/data/brandModels.ts`.** 18 modelos y familias con qué hace el
    equipo, usos típicos, criterios de elección y la fuente oficial. Ninguna
    entrada puede declarar precio, plazo, stock, garantía ni imagen, y
    `validate:catalog` lo comprueba.
21. **`/aplicaciones/` pasó a ser por tarea.** Seis tareas de laboratorio, cada
    una con ancla propia, el equipo que la resuelve y lo que conviene traer a la
    conversación. Los tres mercados quedan debajo con su nombre real, «Por tipo
    de laboratorio». **No se migró ninguna ruta:** siguen siendo
    `/categorias/*`.
22. **`/productos/` se rehizo sobre las seis familias**, en un orden que deja
    centrifugación en el centro porque es la única con fotografía y puesta
    primero se come la página.
23. **El muro de marcas pasó a riel.** Una sola altura óptica, orden de la lista
    cerrada, gris entero por la razón de la sección 2, y con control de pausa
    visible. La copia del bucle lleva `aria-hidden` y no contiene nada
    enfocable.
24. **El hero es interactivo.** La marca es el ancla y seis nodos de capacidad
    la rodean, unidos al índice por el numeral: al pasar cursor o foco por una
    fila se enciende su nodo, y al apuntar un nodo se enciende su fila. Las
    filas del índice son ahora enlaces; antes nombraban seis familias y no
    llevaban a ninguna. Se añadieron seis tintas de familia, medidas y usadas
    sólo ahí.
25. **La puerta de la lista cerrada pasó de cuatro capas a seis.**
    `validate:brands` comprueba además `brandModels.ts`, `applications.ts`, que
    haya una página por marca aprobada y ninguna más, las seis rutas del
    sitemap, que todo destino externo del HTML esté en la lista de los seis
    fabricantes, y que los datos estructurados no nombren a una marca retirada.
26. **Tres fallos del arnés, los tres encontrados por este cambio.** El
    comprobador de enlaces resolvía `/aplicaciones/#osmolalidad` como un archivo
    llamado `#osmolalidad`; `qa:screens` daba por desborde horizontal cualquier
    elemento fuera del ancho del documento aunque un ancestro lo recortara; y
    los logotipos del riel iban diferidos, de modo que uno sin cargar aparecía
    en blanco al entrar en cuadro por el propio movimiento.
