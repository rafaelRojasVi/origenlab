# Contenido pendiente y revisión legal, sitio V2

Status: canonical
Owner: web-maintainers
Last reviewed: 2026-09-06

Lo que el sitio **no** publica porque nadie lo ha confirmado. Ningún elemento de
esta lista puede redactarse desde el repositorio: o lo aporta el negocio
(**CONTENIDO**) o lo confirma un abogado (**LEGAL**).

Mientras un punto siga aquí, el diseño deja el hueco preparado pero no lo enlaza:
un enlace roto o una página legal inventada son peores que su ausencia.

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
proveedores de infraestructura.

El pie del sitio reserva la fila legal (`SiteFooter.astro` lleva el comentario
que marca el lugar) pero **no enlaza** `/privacidad/` ni `/aviso-legal/` hasta
que exista texto revisado.

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
| Equipos asociados a alimentos | CONTENIDO | `/categorias/alimentos/` declara con franqueza que aún no hay familia publicada |
| Términos de garantía, instalación y puesta en marcha más allá de «según fabricante» y «por escrito» | CONTENIDO | Copy de `/servicios/`; se mantiene la redacción acotada actual |
| Expectativa de plazo de respuesta a una cotización | CONTENIDO | No se declara ninguna |
| Imágenes de producto de mayor resolución o de prensa del fabricante | CONTENIDO | Los originales son de ~1.000 px; suficientes hoy, ajustados para una futura vista ampliada |

---

## 4. Prueba comercial

Nada de lo siguiente existe en el repositorio y **el diseño no tiene hueco para
ello** hasta que exista y sea verificable: referencias de clientes, testimonios,
casos, participación en licitaciones, certificaciones, membresías y registros de
proveedor.

| Pendiente | Tipo |
|---|---|
| Historia de la empresa para `/nosotros/` (fundación, equipo, por qué Valdivia) | CONTENIDO |
| Cuentas de Instagram o LinkedIn (`contact.instagramHandle` es `null`) | CONTENIDO |
| Fotografía propia de laboratorio o de equipo instalado, con derechos | CONTENIDO |

---

## 5. Decisiones tomadas en el rediseño que conviene confirmar

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
