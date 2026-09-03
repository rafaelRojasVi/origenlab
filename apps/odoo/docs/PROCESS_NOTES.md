# OrigenLab — proceso comercial en Odoo (notas de implementación)

Running notes dictated by the operator, step by step, plus what was implemented
for each. **This file is the source of truth for the process**; the Odoo config
and `addons/origenlab_import/` must not contradict it without a note here.

Policy source of truth for quote content remains
`~/origenlab/docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md` (§2.2 gates:
every commercial term is either confirmed or explicitly marked unconfirmed).

El stack vive en `apps/odoo/` dentro del monorepo. **El repositorio es público:**
los secretos (master password, contraseña de Postgres) viven solo en `.env` y en
`config/odoo.conf`, ambos ignorados por git. Ver `README.md`.

---

## Objetivo general

- **CRM** sirve bien para leads → se mantiene.
- La **cotización** debe concentrar *toda* la información del negocio: la venta
  al cliente **y** la compra del equipo al proveedor.
- Hoy Odoo reparte eso en 4 registros enlazados (SO, PO, costos en destino,
  plan de pagos). Cerrar esa brecha es el trabajo.

---

## Paso 1 — Entrada del requerimiento y precio del proveedor

**Dictado por el operador (2026-08-07):**

> Un cliente contacta con un requerimiento de varios equipos de laboratorio,
> normalmente **una sola máquina grande** (el caso por defecto). Ingresamos el
> precio que da el proveedor, habitualmente en **USD o EUR, pero puede ser en
> cualquier divisa**. El precio del proveedor es **ad-hoc**, pero debemos
> mantener una **lista del inventario y su precio posible** para ventas futuras.

**Lectura del requisito:**

| # | Requisito | Mecanismo en Odoo |
|---|---|---|
| 1.1 | Cotización con varias líneas; por defecto 1 equipo | Nativo (`sale.order.line`) |
| 1.2 | Precio del proveedor en cualquier divisa | Multi-divisa + divisas activas + tipo de cambio |
| 1.3 | Precio del proveedor es ad-hoc (se negocia por negocio) | Precio editable en la línea; no un precio de lista fijo |
| 1.4 | Mantener catálogo de equipos + precio de referencia para ventas futuras | `product.template` + `product.supplierinfo` (pestaña *Proveedores*) |

**Distinción importante (1.3 vs 1.4):** el precio ad-hoc es el de *este* negocio;
el de referencia es lo que sirve para cotizar *el próximo*. Odoo los separa:
`supplierinfo` guarda la referencia, la línea de la orden guarda el ad-hoc.
Odoo **no** escribe el precio negociado de vuelta al histórico por sí solo
— ver pregunta abierta P2.

### Estado encontrado (baseline, antes de tocar nada)

| Elemento | Estado |
|---|---|
| Multi-divisa | activada |
| Divisa de la compañía | CLP |
| Divisas activas | CLP, UF, UTM, OTR, USD |
| **EUR** | **existe pero inactiva** ← bloqueaba 1.2 |
| Productos | 8 |
| **Registros de precio de proveedor (`supplierinfo`)** | **0** ← 1.4 sin implementar |
| Leads / SO / PO | 1 / 0 / 0 |
| Datos demo | no (base limpia) |
| Nombre de la compañía | "My Company" (sin renombrar) |

### Implementado

**1.2 — Divisas del proveedor** (`scripts/step1_currencies.py`, idempotente)

- **EUR activada.** Existía en la base pero estaba inactiva, así que no se podía
  cotizar una compra a Ortoalresa en euros.
- **Tipo de cambio fijado contra CLP** con el observado del Banco Central
  (mindicador.cl), fecha 2026-08-07: **1 USD = 913,86 CLP**, **1 EUR = 1.053,08 CLP**.
- Verificado: 1.000 USD → 913.860 CLP; 1.000 EUR → 1.053.080 CLP.

> **Trampa documentada:** en Odoo 19 los campos `rate` y `company_rate` son
> *unidades de la divisa por 1 CLP*. El campo que representa "cuántos CLP vale
> 1 EUR" es **`inverse_company_rate`**. Escribir `company_rate` invierte la tasa
> y el error es silencioso: los importes se ven plausibles y el margen queda mal.
> El primer intento cayó justo ahí (1.000 USD daba 1 CLP).

Divisas activas ahora: CLP (compañía), USD, EUR, UF, UTM, OTR.

**Pendiente en 1.2:** una divisa sin tasa vigente se calcula a 1,0 — silenciosamente.
Si aparece un proveedor en otra divisa hay que activarla **y** cargarle tasa antes
de cotizar. Ver P1 sobre cómo mantener esto.

**1.1, 1.3, 1.4:** sin cambios todavía — dependen de P2 y P3.

---

## Paso 2 — Estimación del flete (DHL)

**Dictado por el operador (2026-08-07):**

> El envío se hace con **DHL**. Ellos tienen una tabla según varios factores.
> La idea es **estimar** el precio del envío, pero el precio final **puede
> variar** por varias razones: el proveedor manda cosas extra que ocupan más
> espacio o pesan más, o hay casos especiales como **reactivos**, considerados
> peligrosos, que encarecen el envío.

**Consecuencia de diseño:** el flete es un **estimado con desviación esperada**,
no un dato firme. Cualquier cosa que construyamos debe distinguir
*estimado* de *real facturado por DHL*, y dejar ver la diferencia — si no, el
margen del negocio miente. Esto encaja con la regla §2.2 del doc de negocio
(confirmado vs explícitamente no confirmado).

### Tabla obtenida

Guía oficial descargada y parseada a datos estructurados — no transcrita a mano:

| Archivo | Contenido |
|---|---|
| `data/dhl/dhl_cl_2026.pdf` | Guía de Servicios y Tarifas DHL Express 2026: Chile (fuente) |
| `data/dhl/dhl_cl_2026_rates.json` | Zonas, tarifas de importación, incrementos y recargos |
| `scripts/parse_dhl_rate_guide.py` | Parser reejecutable si sale la guía 2027 |

Fuente: <https://mydhl.express.dhl/content/dam/downloads/cl/es/rate-guide/service_and_rate_guide_cl_es_2026.pdf.coredownload.pdf>

**Lo que contiene la tabla (producto: DHL Express Worldwide Import, en USD):**

- **232 países → zona (1–6).** *España = zona 4* (Ortoalresa). Alemania e Italia
  también 4; EE.UU. 3; China 5.
- **44 tramos de peso** para no-documentos, de 0,5 kg a 70 kg, por zona.
- **Incrementos sobre 10 kg**: por 0,5 kg hasta 30 kg, por 1 kg de 30 a 3.000 kg.
- Ejemplos zona 4: 25 kg = **1.043,48 USD**; 70 kg = **2.754,98 USD**;
  sobre 70 kg, **+39,22 USD por kg**.

**Las tres reglas que explican por qué el precio final se desvía** (justo lo que
describe el operador):

1. **Peso volumétrico.** Se cobra el mayor entre peso real y
   `largo × ancho × alto (cm) / 5000`, por pieza. Si el proveedor manda embalaje
   extra o cosas adicionales, sube el volumen y sube el cobro aunque no pese más.
2. **Redondeo del peso facturable.** Al 0,5 kg hasta 30 kg, al kilo sobre 30 kg;
   cada pieza se redondea al 0,5 kg.
3. **Recargos por envío** — aquí caen los *reactivos*:

| Recargo | USD |
|---|---|
| **Mercancías peligrosas completamente reguladas** | **133,00** |
| Mercancías peligrosas en cantidades exentas | 17,50 |
| Cantidades limitadas | 42,00 |
| Artículos de consumo ID8000 | 32,00 |
| Hielo seco UN1845 | 26,50 |
| Pieza con sobrepeso (real o volumétrico > 70 kg) | 126,00 |
| Pieza excedida de tamaño (lado > 100 cm, o 2º lado > 80 cm) | 23,00 |
| Pieza de procesamiento manual | 23,00 |
| Palet no apilable | 340,00 |
| Liberación formal de aduana | 185,00 |
| Proceso de liberación | 34,00 |
| Entrega en área remota | 0,80/kg, mínimo 40,00 |

**Los reactivos no son un solo recargo:** según cómo se declaren caen en
*completamente reguladas* (133 USD), *cantidades exentas* (17,50) o *cantidades
limitadas* (42). La diferencia es 7×, y la decide el proveedor al declarar la
mercancía — no nosotros. Por eso el estimado debe llevar el supuesto explícito.

**Lo que la tarifa NO incluye** (y por tanto falta para el costo real de
internación): impuestos y aranceles, despacho de aduana, IVA, y recargos.

### Faltante conocido: recargo por combustible

**No viene en la guía.** DHL lo fija mensualmente sobre transporte *y* recargos,
y pesa del orden de **20–30%**. Sin él, todo estimado queda corto en esa
proporción. Queda como parámetro (`fuel_surcharge_pct: null` en el JSON) y hay
que cargarlo. Intenté leerlo de dhl.com y la página no respondió a tiempo.

---

## Paso 3 — Entrega final y confidencialidad del precio de compra

**Dictado por el operador (2026-08-07):**

> Se puede dar la dirección del cliente o la propia, pero hay que pedirle a
> alguien de DHL que **quite las etiquetas con los precios y costos** y que mande
> el equipo sin esas cosas a la dirección del cliente. Eso lo hacen **en aduana**.

Esto responde la pregunta que quedó abierta en el paso 1: el flujo físico es
**variable**, y el caso directo-al-cliente es real.

### Por qué importa más de lo que parece

Es un control **irreversible**: si nadie pide el retiro de etiquetas, el cliente
ve lo que pagamos al proveedor. No hay forma de deshacerlo, y el daño es al
margen de ese negocio y a los siguientes con el mismo cliente. Es exactamente el
tipo de dato que no puede quedar en blanco por descuido.

### Implementado

En `addons/origenlab_import/` (pestaña *Importación* de la orden de compra):

| Campo | Para qué |
|---|---|
| **Entrega final** | `A nuestra dirección` / `Directo al cliente` |
| **Dirección de entrega final** | Obligatoria si va directo al cliente |
| **Retiro de etiquetas de precio** | `Pendiente` / `Solicitado a DHL` / `Confirmado por DHL` / `No aplica` |
| **Contacto en DHL** | Quién tomó la solicitud |
| **Fecha de solicitud** | Cuándo se pidió |

**El control:** si la entrega va directo al cliente, **no se puede registrar la
fecha de nacionalización** mientras el retiro de etiquetas no esté en
*Confirmado por DHL* o marcado *No aplica*. Se puede seguir adelante, pero
solo como decisión explícita — nunca por omisión (§2.2 del doc de negocio).
Además aparece un aviso en el formulario mientras esté sin resolver.

Verificado en la base: bloquea al nacionalizar sin resolver, deja pasar con
confirmación, y no estorba cuando la entrega es a dirección propia.

### Consecuencia pendiente: los costos de internación

Si el equipo va **directo al cliente**, no hay recepción en Odoo — y los
**costos en destino** (aduana, flete, seguro) se aplican sobre una recepción.
Sin ella, esos costos no se pueden repartir sobre la unidad y el margen por
equipo queda aproximado. Ya estaba anotado como límite conocido en el README.

Salida recomendada, **a decidir**: registrar igual la recepción en Odoo aunque
DHL entregue directo (recepción "de papel" contra una ubicación de tránsito) y
después el despacho al cliente. La nacionalización ocurre a nombre de OrigenLab
de todos modos, así que el hecho económico existe aunque el camión no pase por
Valdivia. Eso mantiene vivos los costos en destino y el margen real por equipo.

---

## Paso 4 — Formas de pago del cliente

**Dictado por el operador (2026-08-07):**

> Por lo general les pedimos un **pago inicial del 50%** y el otro **50% contra
> entrega**, pero podrían terminar siendo **N pagos distintos**. Para **cada pago
> hay que generar una factura**.

### La frase que decide el diseño

"Para cada pago hay que generar una factura" **descarta los términos de pago de
Odoo**, que era el mecanismo que la guía anterior recomendaba. Un término de pago
produce **una factura con varios vencimientos**, no varias facturas. Sirve para
"te doy 30 días", no para "te facturo el anticipo ahora y el saldo después".

| Mecanismo | Facturas que emite | ¿Sirve aquí? |
|---|---|---|
| Términos de pago | **1**, con N vencimientos | **No** |
| Pagos parciales | **1**, con N pagos registrados | **No** |
| Anticipos (*down payment*) | **N**, una por anticipo | **Sí** |

**Corrección a la guía anterior:** el README afirmaba que estaba creado el término
*"50% anticipo / 50% a 30 días"*. **No existe en la base** — solo están los de
fábrica de Odoo. Y aunque existiera, sería la herramienta equivocada.

### El hueco que quedaba

Los anticipos emiten una factura por pago, pero **no dejan registrado el plan
acordado**: no hay dónde ver *qué se pactó* frente a *qué se facturó*. Con 50/50
se lleva de memoria; con N pagos, no.

### Implementado

Módulo nuevo **`origenlab_commercial`** (instalado y verificado), separado de
`origenlab_import` porque es la parte de venta, no de importación.

Pestaña **Plan de pagos** en la cotización, con N hitos:

| Columna | Qué guarda |
|---|---|
| Concepto | Cómo se le nombra al cliente («Anticipo», «Contra entrega») |
| % del total | Porcentaje de la cotización que cubre ese pago |
| Se cobra | A la firma / al embarque / contra entrega / a N días de la factura |
| Monto planificado | Calculado sobre el total, en la divisa de la cotización |
| Factura | La factura que cubrió ese hito |
| Estado | Por facturar / Borrador / Facturado / Pagado |

- **Validación: los hitos deben sumar 100%.** Un plan que suma 80% deja saldo sin
  facturar y nadie lo nota hasta que falta la plata. Con tolerancia de 0,05 para
  que 3 pagos de 33,33% sean válidos.
- Aviso visible en el formulario mientras el plan no cuadre.

### Refinamiento pedido (2026-08-07)

> El plan por defecto deben ser los **dos pagos 50% y 50%**, luego de haber
> ingresado el **precio final total hacia el cliente**, seteado manualmente.
> Se deberán **ingresar las fechas** correspondientes a cada pago.

**Implementado:**

- **El plan 50/50 ya no se pide con un botón: aparece solo** en cuanto la
  cotización tiene precio final. Antes de eso no aparece nada — un plan sobre
  total cero no significa nada.
- **Un plan editado a mano nunca se pisa.** Si el negocio se pactó en 40/30/30 y
  después cambia el precio, los porcentajes quedan como los dejaste y solo se
  recalculan los montos. Si borras el plan entero, vuelve el 50/50.
- **Fecha de pago por hito**, y **no se puede confirmar el pedido con fechas en
  blanco**. Se exige al confirmar y no al cotizar: en borrador puede no conocerse
  aún la fecha de entrega, pero un pedido confirmado sin fechas de cobro no se
  puede seguir.

> **Supuesto a confirmar:** "precio final total hacia el cliente" se interpretó
> como el **total de la cotización** — el que resulta de teclear el precio en las
> líneas, que es donde en Odoo se fija manualmente. Si se quería un campo aparte
> de *precio final acordado*, distinto de la suma de líneas, hay que cambiarlo.

Verificado en la base: sin precio no hay plan; con 8.000.000 CLP aparecen
4.000.000 + 4.000.000; al subir a 10.000.000 pasan a 5.000.000 + 5.000.000; un
plan manual 40/30/30 sobrevive a un cambio de precio; confirmar sin fechas se
bloquea y con fechas pasa. Rechaza un plan que suma 80% y acepta 3×33,33%.

### Pendiente

- **El enlace hito → factura es manual.** Emites el anticipo con *Crear factura →
  Anticipo* y enlazas la factura en el hito. Automatizarlo (un botón «Facturar
  este hito» que cree el anticipo por el % del hito y lo enlace solo) es el paso
  natural siguiente, pero quería el modelo confirmado antes de construirlo.
- **DTE: esto multiplica el problema ya conocido.** Cada pago es una factura y en
  Chile cada factura es un DTE. Con 3 pagos son 3 documentos electrónicos. La
  emisión certificada (`l10n_cl_edi`) es **Odoo Enterprise**; esta instalación es
  Community. No es un problema nuevo, pero el plan de N pagos lo agranda: hay que
  decidir el proveedor de DTE antes de operar en real.

---

## Paso 5 — Helper de costeo en la cotización

**Dictado por el operador (2026-08-07):**

> En la vista de cotización debe haber un **helper para calcular el total** usando
> el **monto que da el proveedor** y el **flete** (habrá más cosas en el futuro).

### El requisito que manda es "habrá más cosas"

Con dos campos fijos (proveedor + flete), cada componente nuevo — garantía
extendida, instalación, certificación — obliga a tocar código. Así que el costeo
se modeló como **líneas**, no como campos.

### Implementado

Pestaña **Costeo** en la cotización, antes de *Plan de pagos*:

| Columna | Qué guarda |
|---|---|
| Tipo | Equipo (proveedor) / Flete / Aduana / Seguro / Transporte nacional / Otro |
| Concepto | Descripción libre |
| Monto + Divisa | **En la divisa en que lo cotizaron** (EUR, USD, CLP…) |
| Monto en CLP | Convertido con el tipo de cambio de la fecha de la cotización |
| Supuesto | De dónde sale el número — un estimado sin supuesto no se puede revisar |

Y el cálculo: **Costo total** → **Margen objetivo (%)**, por defecto 30 →
**Precio sugerido**, con botón *Aplicar a la línea*.

Al lado, el contraste que importa: **precio actual (neto)** y **margen real**
con el precio que hoy está en las líneas. Si el precio queda bajo el costo,
sale un aviso de que el negocio pierde plata.

**Agregar un componente nuevo** es sumar una opción a `kind` en
`models/quote_cost.py`. La conversión de divisa, el total y el margen no cambian.

**El sugerido es sugerencia, no imposición.** El precio al cliente se fija a mano
(paso 4). El botón solo lo aplica cuando hay **una** línea — que es el caso por
defecto del negocio, un equipo grande. Con varias avisa en vez de repartir según
un criterio inventado.

Verificado en la base con un caso real:

| Componente | Monto | En CLP |
|---|---|---|
| Digicen 22 R (Ortoalresa) | 7.400,00 EUR | 7.792.792 |
| DHL Express Import zona 4, 25 kg | 1.043,48 USD | 953.595 |
| Agente de aduana | 350.000 CLP | 350.000 |
| **Costo total** | | **9.096.387** |
| Precio sugerido (margen 30%) | | **11.825.303** |

Al aplicarlo: margen real 30,0% y el plan de pagos se sembró solo en
5.912.652 + 5.912.652. Es decir, los pasos 4 y 5 encajan: cargas costos, aplicas
precio, y el plan de pagos aparece con los montos correctos.

### Ajuste pedido (2026-08-07)

> **Costeo** debe ser la **primera** pestaña, después las líneas del pedido y
> después el plan de pagos. Y **equipo y envío** deben estar ahí por defecto.

**Implementado:**

- Orden de pestañas: **Costeo → Líneas del pedido → Plan de pagos** → (resto).
  Refleja el orden real del trabajo: se arma el precio, se escribe en las
  líneas, y el plan de pagos sale del total ya fijado.
- Toda cotización nueva arranca con dos líneas de costo: **Equipo (proveedor)**
  y **Envío**, en 0 y a completar. Aparecen para que no se olviden, no porque
  valgan cero: una cotización sin flete cargado infla el margen.

Verificado: el orden de pestañas queda
`origenlab_costing, order_lines, origenlab_payment_plan, …` y una cotización
nueva trae las dos líneas.

**Resuelto (2026-08-08): el equipo del proveedor parte en EUR.**

Era el riesgo silencioso: con CLP por defecto, teclear 7400 sin cambiar la
divisa dejaba el costo 1.000× abajo y el margen mentía sin avisar — el mismo
tipo de error que el tipo de cambio invertido del paso 1.

- La línea sembrada **Equipo (proveedor)** nace en **EUR**.
- Marcar cualquier línea como *equipo* propone EUR, **salvo que ya tenga una
  divisa elegida a mano** — una elección explícita nunca se pisa.
- Si EUR estuviera inactiva o sin tipo de cambio, cae a la divisa de la
  compañía: convertir 1:1 en silencio es peor que quedarse en CLP.
- Se cambia editando `SUPPLIER_DEFAULT_CURRENCY` en `models/quote_cost.py`.

Verificado: cotización nueva trae `Equipo (proveedor) → EUR`; pasar una línea a
*equipo* la lleva a EUR; una línea puesta a mano en USD sigue en USD;
7.400 EUR → 7.792.792 CLP.

> **Sigue en CLP la línea de Envío.** Las tarifas DHL de la guía están **en
> USD** (paso 2), así que el mismo error es posible ahí. No se cambió porque no
> se pidió: decidir si el flete parte en USD.

### Enlace costo ↔ línea del pedido (2026-08-07)

> Todo **equipo o ítem del proveedor** debe crearse también en las **líneas del
> pedido**, y quedar **enlazado** para tener todo mejor organizado.

**Lo que esto obligó a agregar:** una línea de pedido necesita un **producto**,
no un texto libre. Así que la línea de costo de tipo *equipo* ahora lleva
**producto** y **cantidad** — que es, además, el catálogo que pedía el paso 1
(punto 1.4). Los dos requisitos convergen en el mismo campo.

**Implementado:**

- Al indicar el producto en un costo de tipo **Equipo (proveedor)**, se crea
  sola su **línea del pedido** y ambas quedan enlazadas. El nombre del costo se
  completa con el del producto si aún tenía el texto por defecto.
- **Producto y cantidad se mantienen sincronizados**: cambiar la cantidad en el
  costo la cambia en la línea del pedido.
- Desde la línea del pedido se ven los **costos asociados** y su total en CLP,
  o sea el margen por ítem, no solo el del negocio completo.
- Flete, aduana y seguro **no** generan línea de pedido: son costos del negocio,
  no artículos que el cliente compra. El campo producto queda bloqueado para
  esos tipos.

**Lo que deliberadamente NO hace:**

- **No toca el precio de venta.** El costo manda sobre producto y cantidad; el
  precio se fija con el sugerido (paso 5) o a mano. Sobrescribirlo desde el
  costo borraría una decisión comercial.
- **Borrar un costo no borra la línea del pedido.** Un borrado en cascada sobre
  algo que el cliente ya vio es demasiado destructivo para hacerlo solo.

Verificado en la base: dos equipos (1 y 2 unidades) generan sus dos líneas de
pedido enlazadas; el flete no genera ninguna; subir la cantidad a 3 la propaga a
la línea; desde la línea del Digicen se ve su costo de 7.792.792 CLP.

### Validación del margen aplicado (2026-08-08)

> *Aplicar a la línea* debe estar **validado y resaltado** en algún lado si el
> precio no quedó con el margen esperado.

**Implementado:** un **estado del margen** calculado, comparando el margen real
de las líneas contra el objetivo, con tolerancia de 0,5 puntos porcentuales
(absorbe el redondeo del precio sin dar falsas alarmas).

| Estado | Cuándo | Cómo se ve |
|---|---|---|
| Sin costear | no hay costos cargados | gris |
| Sin precio | hay costos, las líneas van en 0 | gris |
| **Bajo el costo** | margen real negativo | **rojo** |
| **Bajo el objetivo** | por debajo del margen objetivo | **amarillo**, con el desvío en puntos |
| En el objetivo | dentro de la tolerancia | verde |
| Sobre el objetivo | por encima | amarillo — puede ser deliberado, o faltar un costo |

Se muestra en **tres lugares**:

1. **Arriba del formulario**, antes del `<sheet>` — el mismo lugar donde Odoo
   pone sus propios avisos (p. ej. "este pedido puede estar duplicado"). Se ve
   desde cualquier pestaña, no solo desde *Costeo*. Ahí van los cuatro estados
   accionables: *bajo el costo*, *bajo el objetivo*, *sobre el objetivo* y
   *sin precio*, con el margen real y el objetivo a la vista.
2. **Pestaña Costeo**: los números (margen real, desvío en puntos) y el badge de
   estado, incluido el verde *En el objetivo*.
3. **Lista de cotizaciones**: columna de estado y margen, para detectar un
   negocio mal precificado sin abrirlo uno por uno.

No se pone un aviso verde permanente arriba: la confirmación positiva vive en el
badge de *Costeo*. Arriba solo aparece lo que pide acción, o el aviso pierde
fuerza por costumbre.

**No corrige el precio solo.** Puede haber una razón comercial para vender fuera
del objetivo; lo que no puede pasar es que ocurra sin que se vea.

Verificado los seis estados en la base: precio bajo el costo → *Bajo el costo*
(−40 pp); margen 10% con objetivo 30 → *Bajo el objetivo* (−20 pp); tras aplicar
el sugerido → *En el objetivo* (0,0 pp); margen 60% → *Sobre el objetivo*
(+30 pp); sin costos → *Sin costear*; línea en 0 → *Sin precio*.

### Pendiente

### Calculadora de flete DHL (2026-08-10)

Botón **Calcular flete DHL** en el resumen de arriba. Abre un asistente que
estima el flete y lo escribe en la línea de costo *Envío*, con su supuesto.

**El algoritmo está validado contra la guía impresa:** reproduce **144 de 144**
filas publicadas (6 zonas × 24 pesos sobre 10 kg) con cero desvío. Si algún día
deja de cuadrar, es que cambió la guía — reparsear con
`scripts/parse_dhl_rate_guide.py`.

Entradas: país de origen (propone la zona solo, tolera acentos), zona, piezas,
peso real, dimensiones por pieza, mercancías peligrosas, área remota,
liberación formal y recargo por combustible.

Calcula, en este orden:

1. **Peso volumétrico** = largo × ancho × alto / 5000, por pieza.
2. **Peso facturable** = el mayor entre real y volumétrico, redondeado
   **hacia arriba** (0,5 kg hasta 30 kg, 1 kg después).
3. **Tarifa base** del tramo de la zona.
4. **Recargos**: peligrosas, sobrepeso (>70 kg pieza), excedida de tamaño
   (lado >100 cm o segundo lado >80 cm), área remota, liberación formal.
5. **Combustible** sobre transporte *y* recargos.

> **Desviación deliberada de la guía:** el documento dice redondear al 0,5 kg
> *más cercano*; aquí se redondea **hacia arriba**, que es lo que DHL hace en la
> práctica. Quedarse corto en el flete se come el margen en silencio, que es
> justo lo que este sistema existe para evitar.

Verificado end-to-end: una caja de 60×50×55 cm con **25 kg reales factura
33 kg** porque manda el volumen — exactamente el caso que describió el operador
(«el proveedor manda cosas extra que ocupan más espacio»). Con zona 4 y 28% de
combustible da 1.715,33 USD = 1.567.571 CLP, y queda escrito en la línea de
flete con el supuesto «DHL zona 4, 33.0 kg facturables, combustible 28,0%».
Sin dimensiones, 25 kg zona 4 da 1.043,48 USD, idéntico a la tabla impresa.

El **recargo por combustible** se guarda como parámetro del sistema
(`origenlab.dhl_fuel_surcharge_pct`) y se reutiliza en la siguiente cotización.
Mientras esté en cero, el asistente avisa que el estimado queda corto 20-30%.

### Pendiente del flete

- **El recargo por combustible sigue cargándose a mano.** DHL lo publica cada
  mes; automatizarlo requiere leerlo de su web.
- El asistente asume **una sola configuración de bulto**. Un embarque con piezas
  de distinto tamaño hay que estimarlo por partes.

### Preguntas abiertas

- **P1 — Divisas y tipo de cambio.** ¿Qué divisas activo además de USD y EUR?
  ¿El tipo de cambio se ingresa a mano por negocio, o se actualiza automático?
- **P2 — Histórico de precio del proveedor.** Al cerrar una compra a precio
  negociado, ¿ese precio debe quedar registrado automáticamente como referencia
  para el próximo negocio, o se mantiene a mano?
- **P3 — "Inventario".** ¿Catálogo de equipos que podemos vender (sin stock
  físico, importados contra pedido), o stock real en bodega? Cambia si los
  productos son *almacenables* o de tipo *servicio/consumible*.
