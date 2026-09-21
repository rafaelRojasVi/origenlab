# apps/newsletter-worker

Endpoint de suscripción al boletín de OrigenLab. **No está desplegado.**

Cloudflare Worker con una base D1, en la ruta `origenlab.cl/api/newsletter/*`
de la zona que ya sirve el sitio. Es el ingreso de una suscripción desde el
sitio público y el registro que la prueba.

---

## Qué es y qué no es

**Es** el punto de entrada y el depósito de evidencia de una suscripción.

**No es** la autoridad de campañas. La verdad comercial duradera vive en el CRM
(`outbound.contact_permission` y `outbound.contact_control`), y esta base no la
sustituye ni la anticipa. Mientras el puente al CRM no exista, ningún envío de
campaña puede apoyarse en esta base por copia manual: una copia se queda quieta
mientras el estado de baja cambia.

## Por qué en la misma zona

El sitio ya se sirve detrás de Cloudflare. Una ruta en `origenlab.cl/api/*` la
atiende el Worker sin que el navegador haga ninguna petición a otro origen:

- la CSP del sitio sigue con `connect-src 'self'` y `form-action 'self'`;
- el visitante no ve ningún tercero en la pestaña de red;
- no hay preflight CORS ni cookie de terceros.

**Esto no lo hace gratuito en privacidad.** Que Cloudflare ya sea proxy del
dominio no convierte en inocuo guardar suscriptores en D1: es una **finalidad
nueva** y un **sistema de almacenamiento nuevo**, con su propio análisis de
ubicación y de transferencia internacional. Las dos filas están declaradas por
separado en `apps/web/src/data/legal.ts` (`cloudflare-worker` y `cloudflare-d1`)
y el análisis está pendiente (`transferencia-cloudflare-d1`).

## Rutas

| Método y ruta | Efecto | Respuesta |
|---|---|---|
| `POST /api/newsletter/subscribe` | Crea una solicitud pendiente y envía la confirmación | `202 {"status":"pending"}` con `Accept: application/json`; `303` a `/newsletter/solicitud-recibida/` sin JavaScript |
| `GET /api/newsletter/confirm?t=` | **Ninguno.** Dibuja un botón | `200` HTML |
| `POST /api/newsletter/confirm` | Confirma la suscripción | `303` a `/newsletter/confirmada/` o a `/newsletter/enlace-no-valido/` |
| `GET /api/newsletter/unsubscribe?t=` | **Ninguno.** Dibuja un botón | `200` HTML |
| `POST /api/newsletter/unsubscribe` | Da de baja | `303` a `/newsletter/baja-confirmada/` |
| `POST /api/newsletter/unsubscribe?t=` con cuerpo `List-Unsubscribe=One-Click` | Da de baja | `200 text/plain`, **sin redirección** (RFC 8058) |

### Por qué ningún GET cambia nada

Los sistemas de seguridad de correo abren los enlaces de un mensaje para
analizarlos. Un enlace de baja que actuara al abrirse daría de baja a gente que
no lo pidió. Un enlace de confirmación que actuara al abrirse sería peor: un
analizador automático estaría consintiendo en nombre de una persona, que es
justo lo que el doble opt-in existe para impedir. Por eso el GET sólo dibuja un
botón, el cambio lo hace el POST, y el POST de un clic de RFC 8058 se responde
con `200` y no con una redirección, porque quien lo envía es un cliente de
correo y no un navegador que deba ir a ninguna parte.

Los dos caminos de baja, el botón y el clic de RFC 8058, dejan **el mismo
registro de supresión**.

## Seguridad

- **Tokens opacos.** 32 bytes aleatorios en hexadecimal. Nunca se derivan de la
  dirección, así que un enlace no dice de quién es.
- **La base guarda huellas, nunca tokens.** La huella es un HMAC-SHA-256 con un
  secreto del Worker. Un volcado robado no da de baja a nadie.
- **El token de baja se deriva**, no se guarda: `HMAC(pepper, "unsubscribe:"+id)`
  a partir del UUID aleatorio de la fila. Se puede reconstruir en cada envío, no
  caduca, y sin el secreto no se puede fabricar.
- **Confirmación de un solo uso y con caducidad** (`CONFIRM_TTL_HOURS`).
- **Caudal con clave secreta.** La clave de la tabla es un HMAC, no una IP ni su
  hash: sin el secreto nadie puede comprobar si una IP concreta está ahí
  probando candidatas. **Ninguna tabla guarda una dirección IP.**
- **Sin registros.** El Worker no escribe la dirección, el token ni el cuerpo de
  la petición en ningún log.
- **Tope de cuerpo** de 4 KB, contado mientras llega y no sólo declarado.
- **Origen único.** Un `Origin` distinto del sitio se rechaza, y no se devuelve
  ninguna cabecera CORS.
- **Sin secretos, no opera.** Sin `ORIGENLAB_TOKEN_PEPPER` y
  `ORIGENLAB_RATE_LIMIT_KEY` responde `503`. Un valor por defecto sería un token
  falsificable.
- **Ninguna credencial en el navegador.** No hay clave de Supabase, ni de D1, ni
  de servicio en el código del sitio. Los secretos viven en `wrangler secret`.

## Respuesta neutra

`POST /subscribe` responde lo mismo para una dirección nueva, una ya suscrita y
una dada de baja. Distinguirlas convertiría el endpoint en un comprobador de
direcciones ajenas. Los estados distintos (`confirmed`, `already_confirmed`)
sólo aparecen en la confirmación, donde el token ya prueba de quién se trata.

Una trampa (campo señuelo relleno, envío instantáneo) devuelve **también** esa
misma respuesta y no guarda nada. Decirle a un envío automático que se le ha
detectado sólo ayuda a la siguiente versión.

## Remitente

`EmailSender` es una interfaz. El proveedor **no está decidido** y es una de las
ocho puertas de activación.

`NullSender` es el remitente de producción mientras no haya otro y **falla de
forma cerrada**: el alta responde `503`, la solicitud se descarta y no queda
ningún dato guardado. No encola, no reintenta y no finge. Mientras esté
configurado, el formulario del sitio permanece apagado.

## Esquema

`migrations/0001_init.sql`. Seis tablas:

| Tabla | Para qué |
|---|---|
| `subscription_request` | Solicitud pendiente. **No es un permiso** y caduca |
| `subscription` | Suscripción confirmada. Una viva por dirección |
| `suppression` | Bajas. Se añade, nunca se borra |
| `subscription_event` | Registro de sucesos, sólo se añade |
| `rate_bucket` | Caudal por ventana, con clave HMAC |

Una dirección está **suprimida** salvo que exista una suscripción viva
confirmada después de la última supresión (`isSuppressed`). Así volver a
suscribirse no borra la prueba de que un día alguien pidió no recibir nada.

## Correspondencia con el CRM

Diseñado para que la promoción futura sea una transcripción y no un rediseño:

| Aquí | CRM (`docs/origenlab-refoundation-v2`) |
|---|---|
| `subscription.email_norm` | `outbound.contact_permission.value_norm` |
| `subscription.consent_at` | `granted_at` |
| `'web_form'` | `granted_via` (ya está en el vocabulario cerrado) |
| La solicitud y su cuerpo | `granted_evidence_source_record_id` |
| `basis` implícito | `basis = 'explicit_opt_in'` (vocabulario cerrado de uno) |
| `suppression` | `outbound.contact_control` con `kind='block'`, `source='unsubscribe_handler'` |
| `subscription.revoked_at` | `contact_permission.revoked_at`, revocación de un solo sentido |

**Una solicitud pendiente nunca se corresponde con un permiso.** En el esquema
del CRM la ausencia de permiso es negativa, y una solicitud sin confirmar es
exactamente ausencia de permiso.

El camino previsto es que una confirmación válida cree el permiso en el CRM a
través del límite de comandos auditado, con la evidencia completa. Hasta que ese
puente exista, este Worker no escribe en el CRM y nada de aquí autoriza un
envío: `outbound.send_control` sigue con las dos banderas en `false`.

## Desarrollo

```bash
npm install
npm run validate     # typecheck + pruebas
```

Las pruebas corren contra SQLite de verdad (`node:sqlite`) y contra la migración
de verdad, no contra un doble que finge saber SQL.

**No ejecute `npm run deploy`.** Desplegar es una de las ocho puertas de
`apps/web/src/data/newsletter.ts` y exige antes la decisión legal, el análisis de
transferencia, el plazo de conservación y el remitente.
