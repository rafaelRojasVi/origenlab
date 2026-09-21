/**
 * Páginas intermedias de confirmación y de baja.
 *
 * Existen por una razón concreta: **un GET no puede cambiar nada**. Los
 * sistemas de seguridad de correo abren los enlaces de un mensaje para
 * analizarlos, y un enlace de baja que actuara al abrirse daría de baja a gente
 * que nunca lo pidió. Un enlace de confirmación que actuara al abrirse sería
 * peor todavía: un analizador automático estaría dando un consentimiento en
 * nombre de una persona, que es exactamente lo que el doble opt-in existe para
 * impedir.
 *
 * Por eso el GET sólo dibuja un botón y el cambio lo hace el POST. La página no
 * consulta la base: se dibuja igual para un token válido que para uno
 * inventado, de modo que ni el contenido ni el tiempo de respuesta dicen si esa
 * dirección existe. El token se devuelve en un campo oculto, así que el camino
 * funciona sin JavaScript.
 *
 * El estilo va incrustado y es mínimo a propósito: esta página la sirve el
 * Worker y no el sitio, y no debe depender de ningún recurso externo.
 */

const STYLE = `
  :root { color-scheme: light }
  body {
    margin: 0; padding: 3rem 1.25rem; background: #fafaf7; color: #2b2e30;
    font: 16px/1.6 system-ui, -apple-system, 'Segoe UI', sans-serif;
  }
  main { max-width: 34rem; margin: 0 auto }
  h1 { font-size: 1.5rem; line-height: 1.2; color: #141617; margin: 0 0 1rem }
  p { margin: 0 0 1rem; max-width: 60ch }
  button {
    min-height: 2.875rem; padding: .75rem 1.375rem; border: 0; border-radius: 2px;
    background: #0f766e; color: #fff; font: inherit; font-weight: 600; cursor: pointer;
  }
  button:hover { background: #115e59 }
  button:focus-visible { outline: 2px solid #115e59; outline-offset: 2px }
  .foot { margin-top: 2rem; font-size: .875rem; color: #676d71 }
  a { color: #115e59 }
`;

function page(options: {
  title: string;
  heading: string;
  body: string;
  action: string;
  token: string;
  cta: string;
}): Response {
  const html = `<!doctype html>
<html lang="es-CL">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>${options.title} | OrigenLab</title>
<style>${STYLE}</style>
</head>
<body>
<main>
<h1>${options.heading}</h1>
${options.body}
<form method="post" action="${options.action}">
<input type="hidden" name="token" value="${escapeAttribute(options.token)}">
<button type="submit">${options.cta}</button>
</form>
<p class="foot">Si usted no pidió esto, cierre esta página. No ocurre nada si no pulsa el botón.</p>
</main>
</body>
</html>`;
  return new Response(html, {
    status: 200,
    headers: {
      'content-type': 'text/html; charset=utf-8',
      'x-robots-tag': 'noindex, nofollow',
      'referrer-policy': 'no-referrer',
      'cache-control': 'no-store',
      'content-security-policy':
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    },
  });
}

function escapeAttribute(value: string): string {
  return value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

export function confirmPage(token: string, action: string): Response {
  return page({
    title: 'Confirmar suscripción',
    heading: 'Confirme su suscripción',
    body:
      '<p>Para completar la suscripción al boletín de OrigenLab, pulse el botón. Hasta entonces no está suscrito y no recibirá ninguna comunicación comercial.</p>',
    action,
    token,
    cta: 'Confirmar mi suscripción',
  });
}

export function unsubscribePage(token: string, action: string): Response {
  return page({
    title: 'Darse de baja',
    heading: 'Darse de baja del boletín',
    body:
      '<p>Pulse el botón para dejar de recibir el boletín de OrigenLab. No hace falta contraseña ni motivo, y la baja es inmediata.</p><p>Esto no afecta a la respuesta de una cotización que usted haya pedido.</p>',
    action,
    token,
    cta: 'Darme de baja',
  });
}
