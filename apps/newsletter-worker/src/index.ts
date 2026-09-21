/**
 * OrigenLab, endpoint del boletín.
 *
 * Tres rutas, y la forma de cada respuesta depende de quién pregunta:
 *
 *   POST /api/newsletter/subscribe
 *       fetch    -> JSON {"status":"pending"}
 *       sin JS   -> 303 a una página estática del sitio
 *
 *   GET  /api/newsletter/confirm?t=<token>     dibuja un botón, no cambia nada
 *   POST /api/newsletter/confirm               confirma
 *
 *   GET  /api/newsletter/unsubscribe?t=<token> dibuja un botón, no cambia nada
 *   POST /api/newsletter/unsubscribe           da de baja
 *       un clic (RFC 8058) -> 200 text/plain, **sin redirección**
 *       navegador          -> 303 a una página estática del sitio
 *
 * Por qué los GET no cambian nada: los sistemas de seguridad de correo abren
 * los enlaces de un mensaje para analizarlos. Un enlace de baja que actuara al
 * abrirse daría de baja a personas que no lo pidieron, y un enlace de
 * confirmación que actuara al abrirse dejaría que un analizador automático
 * consintiera en nombre de alguien. RFC 8058 existe justamente para eso, y por
 * eso su POST se responde con 200 y no con una redirección: quien lo envía es
 * un cliente de correo, no un navegador que deba navegar a ningún sitio.
 *
 * Lo que este Worker no hace nunca: registrar una dirección, un token o un
 * cuerpo de petición. No hay pregunta operativa que lo necesite y sí hay una
 * forma evidente de filtrar una lista de correos.
 */
import { PAGES } from './config';
import { handleConfirm, handleSubscribe, handleUnsubscribe, type HandlerContext } from './handlers';
import { senderFor } from './email';
import { confirmPage, unsubscribePage } from './pages';
import { parseSubscribe, readBody } from './parse';
import type { Env } from './types';

const SECURITY_HEADERS = {
  'cache-control': 'no-store',
  'referrer-policy': 'no-referrer',
  'x-content-type-options': 'nosniff',
  'x-robots-tag': 'noindex, nofollow',
};

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...SECURITY_HEADERS },
  });
}

function seeOther(location: string, origin: string): Response {
  return new Response(null, {
    status: 303,
    headers: { location: `${origin}${location}`, ...SECURITY_HEADERS },
  });
}

function text(body: string, status: number): Response {
  return new Response(body, {
    status,
    headers: { 'content-type': 'text/plain; charset=utf-8', ...SECURITY_HEADERS },
  });
}

/** El navegador pidió JSON: vino por fetch y no por envío nativo del formulario. */
function wantsJson(request: Request): boolean {
  return (request.headers.get('accept') ?? '').includes('application/json');
}

/**
 * Sólo se acepta un envío del propio sitio.
 *
 * Un formulario nativo puede no mandar `Origin` en algunos navegadores, así que
 * su ausencia no se castiga; lo que se rechaza es un `Origin` que exista y no
 * sea el del sitio. No se devuelve ninguna cabecera CORS: no hay ningún origen
 * al que se quiera dar permiso para llamar a este endpoint.
 */
function originAllowed(request: Request, siteOrigin: string): boolean {
  const origin = request.headers.get('origin');
  if (origin === null) return true;
  return origin === siteOrigin;
}

/** RFC 8058: el cliente de correo manda exactamente este par en el cuerpo. */
function isOneClick(body: string): boolean {
  return new URLSearchParams(body).get('List-Unsubscribe') === 'One-Click';
}

function contextFor(request: Request, env: Env): HandlerContext | null {
  const pepper = env.ORIGENLAB_TOKEN_PEPPER;
  const rateSecret = env.ORIGENLAB_RATE_LIMIT_KEY;
  /* Sin secretos no se opera. Un valor por defecto aquí sería un token falsificable. */
  if (!pepper || !rateSecret) return null;

  return {
    db: env.DB,
    sender: senderFor(env.ORIGENLAB_EMAIL_SENDER),
    pepper,
    rateSecret,
    siteOrigin: env.ORIGENLAB_SITE_ORIGIN,
    clientId: request.headers.get('cf-connecting-ip') ?? 'desconocido',
    now: new Date(),
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, '');
    const siteOrigin = env.ORIGENLAB_SITE_ORIGIN;

    if (!originAllowed(request, siteOrigin)) {
      return json({ status: 'rejected', reason: 'bad_origin' }, 403);
    }

    const context = contextFor(request, env);
    if (!context) return json({ status: 'error', reason: 'not_configured' }, 503);

    /* -- Alta ------------------------------------------------------------- */
    if (path === '/api/newsletter/subscribe') {
      if (request.method !== 'POST') return text('method not allowed', 405);

      const body = await readBody(request);
      if (body === null) {
        return wantsJson(request)
          ? json({ status: 'rejected', reason: 'body_too_large' }, 413)
          : seeOther(PAGES.notSent, siteOrigin);
      }

      const parsed = parseSubscribe(body);
      if (!parsed.ok) {
        return wantsJson(request)
          ? json({ status: 'rejected', reason: parsed.rejection }, 400)
          : seeOther(PAGES.notSent, siteOrigin);
      }

      const result = await handleSubscribe(context, parsed.input, parsed.emailNorm);
      if (!result.ok) {
        const status = result.rejection === 'rate_limited' ? 429 : 503;
        return wantsJson(request)
          ? json({ status: 'rejected', reason: result.rejection }, status)
          : seeOther(PAGES.notSent, siteOrigin);
      }

      return wantsJson(request)
        ? json({ status: 'pending' }, 202)
        : seeOther(PAGES.requestReceived, siteOrigin);
    }

    /* -- Confirmación ----------------------------------------------------- */
    if (path === '/api/newsletter/confirm') {
      if (request.method === 'GET') {
        return confirmPage(url.searchParams.get('t') ?? '', `${siteOrigin}/api/newsletter/confirm`);
      }
      if (request.method !== 'POST') return text('method not allowed', 405);

      const body = await readBody(request);
      if (body === null) return seeOther(PAGES.invalidLink, siteOrigin);
      const token = new URLSearchParams(body).get('token') ?? '';
      const result = await handleConfirm(context, token);

      if (wantsJson(request)) return json(result, result.status === 'invalid' ? 400 : 200);
      return seeOther(
        result.status === 'invalid' ? PAGES.invalidLink : PAGES.confirmed,
        siteOrigin,
      );
    }

    /* -- Baja -------------------------------------------------------------- */
    if (path === '/api/newsletter/unsubscribe') {
      if (request.method === 'GET') {
        return unsubscribePage(
          url.searchParams.get('t') ?? '',
          `${siteOrigin}/api/newsletter/unsubscribe`,
        );
      }
      if (request.method !== 'POST') return text('method not allowed', 405);

      const body = await readBody(request);
      if (body === null) return seeOther(PAGES.invalidLink, siteOrigin);
      const oneClick = isOneClick(body);
      /*
       * En un clic de RFC 8058 el token no viaja en el cuerpo: el cuerpo es el
       * par fijo que define la norma, y el enlace lo lleva en su propia URL.
       */
      const token = oneClick
        ? (url.searchParams.get('t') ?? '')
        : (new URLSearchParams(body).get('token') ?? '');

      const result = await handleUnsubscribe(context, token);

      if (oneClick) {
        /* Sin redirección: el cliente de correo no navega a ninguna parte. */
        return result.status === 'invalid'
          ? text('invalid', 400)
          : text('unsubscribed', 200);
      }
      if (wantsJson(request)) return json(result, result.status === 'invalid' ? 400 : 200);
      return seeOther(
        result.status === 'invalid' ? PAGES.invalidLink : PAGES.unsubscribed,
        siteOrigin,
      );
    }

    return text('not found', 404);
  },
};
