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

El muro de marcas del sitio se apoya en la firma de correo corporativa
(`public/email/origenlab-contacto-signature.html`), que ya publica el texto
verificado «Marcas con las que trabajamos» junto a los seis logotipos: SERVA,
Ortoalresa, IKA, CRTOP, Ollital y Hielscher.

| Pendiente | Tipo | Bloquea |
|---|---|---|
| Descripción de una línea para IKA, CRTOP, Ollital y Hielscher (qué fabrica cada una) | CONTENIDO | Estas cuatro marcas aparecen sólo como logotipo con enlace al fabricante; `validate:catalog` impide describirlas sin este dato |
| Alcance comercial formal por marca (distribuidor, revendedor, pedidos gestionados) | CONTENIDO | Copy más claro en las páginas de marca; hoy se usa la redacción acotada actual |
| Permiso escrito de reproducción de logotipos e imágenes de producto de Ortoalresa y SERVA | CONTENIDO | TODO abierto desde 2026-05 en `docs/product-assets.md`. Se extiende ahora a IKA, CRTOP, Ollital y Hielscher |
| Un logotipo de CRTOP sin el bloque de razón social | CONTENIDO | La versión oficial disponible es un lockup de 8:1 cuyo texto secundario resulta ilegible en el muro |
| Catálogo publicable de las cuatro marcas sin ficha | CONTENIDO | Páginas `/marcas/{slug}/` propias para ellas |

---

## 3. Producto y comercial

| Pendiente | Tipo | Bloquea |
|---|---|---|
| PDF de catálogos y fichas con permiso de publicación | CONTENIDO | `documents.ts` está vacío; las páginas de aplicación muestran el texto genérico de `company.catalogNote` |
| Familias de equipo más allá de centrífugas | CONTENIDO | `/productos/` publica hoy una familia y una línea de reactivos |
| Modelos, especificaciones e imágenes de sonicación, dispersión y pesaje | CONTENIDO | La portada nombra esas tres familias como alcance de cotización (`equipmentScope.ts`, nivel `consulta`), pero no puede publicar un solo modelo. `validate:catalog` impide asociarles marca, cifra o imagen |
| Qué familia fabrica cada marca sin catálogo | CONTENIDO | Ninguna plantilla puede deducir que Hielscher hace ultrasonido o IKA dispersión. El alcance y las marcas se publican por separado justamente por esto |
| Fotografía de producto de familias distintas de centrifugación | CONTENIDO | Toda la fotografía aprobada del repositorio es de centrífugas Ortoalresa. Por eso el hero es tipográfico y la única fotografía de la portada está atada a esa familia |
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
