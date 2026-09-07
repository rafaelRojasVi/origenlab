# Contenido pendiente y revisión legal, sitio V2

Status: canonical
Owner: web-maintainers
Last reviewed: 2026-09-06

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

| Pendiente | Tipo | Bloquea |
|---|---|---|
| Alcance comercial formal por marca (distribuidor, revendedor, pedidos gestionados) | CONTENIDO | `commercialNote` en las cuatro marcas sin catálogo; `validate:catalog` impide declararlo sin este dato. Hoy se dice qué fabrica cada una, no en qué condiciones la vende OrigenLab |
| Permiso escrito de reproducción de los seis logotipos y de las imágenes de producto de Ortoalresa | CONTENIDO | TODO abierto desde 2026-05. Registrado por marca en `src/data/sourceRegistry.ts` como `ASSET_PERMISSION_NEEDED` |
| Fotografía de equipo de Hielscher, IKA, Adam Equipment, Löser y SERVA | CONTENIDO | Las cinco familias se componen con un diagrama propio (`src/lib/familyMotif.ts`), no con fotografía. Nunca se ilustra una marca con el equipo de otra |
| Catálogo publicable de las cuatro marcas sin ficha | CONTENIDO | Páginas `/marcas/{slug}/` propias para ellas |
| Logotipo vectorial de Löser Messtechnik | CONTENIDO | El único original disponible es un JPEG de 70 × 70 calado sobre un azulejo naranja. Se normaliza a una tinta y se lee, pero no escala |
| HTTPS en el sitio de Löser Messtechnik | TERCERO | `loeser-osmometer.de` rechaza el saludo TLS: el sitio de 2005 sólo responde por http. El enlace oficial es http y la excepción está declarada en `brands.ts` (`websiteInsecure`). No depende de OrigenLab |
| Regenerar la firma de correo corporativa | CONTENIDO | `public/email/origenlab-contacto-signature.html` sigue mostrando Ollital y CRTOP, y no incluye a Adam Equipment ni a Löser. Dejó de respaldar la cifra «6 marcas con las que trabajamos», que ahora se apoya en `brands.ts` y en `validate:brands` |
| Verificar en navegador la página de dispersores de IKA | CONTENIDO | `ika.com` devuelve el interstitial de Cloudflare (403) a todo cliente automatizado, incluido un navegador headless. El PDF oficial sí responde. La URL de la página no está comprobada por máquina |

---

## 3. Producto y comercial

| Pendiente | Tipo | Bloquea |
|---|---|---|
| PDF de catálogos y fichas con permiso de publicación | CONTENIDO | `documents.ts` está vacío; las páginas de aplicación muestran el texto genérico de `company.catalogNote` |
| Familias de equipo más allá de centrífugas | CONTENIDO | `/productos/` publica hoy una familia y una línea de reactivos; la portada nombra las seis |
| Modelos, especificaciones e imágenes de las cuatro familias por consulta | CONTENIDO | La portada las nombra con su fabricante (`equipmentScope.ts`, nivel `consulta`), pero no puede publicar un solo modelo. `validate:catalog` impide asociarles cifra o fotografía |
| ~~Qué familia fabrica cada marca sin catálogo~~ | **Resuelto 2026-09-06** | Confirmado por el negocio; vive en `brands.ts` (`familyId`) |
| Fotografía de producto de familias distintas de centrifugación | CONTENIDO | Toda la fotografía aprobada del repositorio es de centrífugas Ortoalresa. Las otras cinco familias usan un diagrama propio y la portada no abre con fotografía |
| Equipos asociados a alimentos | CONTENIDO | `/categorias/alimentos/` declara con franqueza que aún no hay familia publicada |
| Términos de garantía, instalación y puesta en marcha más allá de «según fabricante» y «por escrito» | CONTENIDO | Copy de `/servicios/`; se mantiene la redacción acotada actual |
| Expectativa de plazo de respuesta a una cotización | CONTENIDO | Propuesta redactada en `claims.ts` como `respuesta-inicial-un-dia-habil`, en estado `proposed` y sin aprobar: **no se renderiza**, y `validate:dist` comprueba que su texto literal no aparezca en el HTML. El sitio usa mientras tanto la redacción cualitativa aprobada «Respuesta ágil y seguimiento claro». Para aprobarla hace falta que el negocio la asuma por escrito y defina qué cuenta como respuesta inicial |
| Imágenes de producto de mayor resolución o de prensa del fabricante | CONTENIDO | Los originales son de ~1.000 px; suficientes hoy, ajustados para una futura vista ampliada |

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
