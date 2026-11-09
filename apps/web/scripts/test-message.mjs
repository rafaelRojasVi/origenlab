#!/usr/bin/env node
/**
 * Pruebas de la capa de mensaje: etiquetas CTA, texto de WhatsApp, pasos de
 * compra y preguntas de compra (`ctaLabels.ts`, `whatsapp.ts`,
 * `consultation.ts`, `faq.ts`, `company.ts`).
 *
 * La capa nunca escribe una afirmación que el registro no apruebe: cada
 * función recibe el resolvedor de afirmaciones (`publicClaim` por defecto) y la
 * prueba lo sustituye para comprobar las dos ramas, aprobada y sin aprobar.
 *
 * Ejecutar: npm run test:message
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';

// Los módulos importan `./contact` sin extensión (lo resuelve Vite, no Node).
register(
  'data:text/javascript,' +
    encodeURIComponent(`
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context);
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND' && /^\\.\\.?\\//.test(specifier) && !/\\.[cm]?[jt]s$/.test(specifier)) {
      return next(specifier + '.ts', context);
    }
    throw error;
  }
}`),
);

const { ctaLabels } = await import('../src/lib/ctaLabels.ts');
const { buildWhatsAppQuoteMessage, buildWhatsAppQuoteUrl } = await import('../src/lib/whatsapp.ts');
const { buyingSteps } = await import('../src/data/consultation.ts');
const { buyingFaqs } = await import('../src/data/faq.ts');
const { company } = await import('../src/data/company.ts');
const { claims, publicClaim, publicClaimNames } = await import('../src/data/claims.ts');
const { registrationLogos } = await import('../src/data/registrations.ts');
const { withheldClaimTexts, publishesClaimText, hiddenCopySurfaces } = await import('./lib/claim-variants.mjs');

let failed = 0;
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** Resolvedor que sólo aprueba los ids indicados, con la redacción real del registro. */
const only = (...ids) => (id) => (ids.includes(id) ? claims.find((claim) => claim.id === id)?.text : undefined);
const none = () => undefined;
const all = (id) => claims.find((claim) => claim.id === id)?.text;

/* -- Etiquetas ----------------------------------------------------------- */
test('ctaLabels: vocabulario del tablero Mensaje', () => {
  assert.equal(ctaLabels.solicitarCotizacion, 'Solicitar cotización');
  assert.equal(ctaLabels.pedirCotizacion, 'Pedir cotización');
  assert.equal(ctaLabels.escribirWhatsapp, 'Escribir por WhatsApp');
  assert.equal(ctaLabels.porWhatsapp, 'Por WhatsApp');
  assert.equal(ctaLabels.whatsappCorto, 'WhatsApp');
  assert.equal(ctaLabels.verModelo, 'Ver modelo');
  assert.equal(ctaLabels.verEquipos, 'Ver los equipos');
  assert.equal(ctaLabels.fichaPdf, 'Ficha técnica del fabricante (PDF)');
  assert.equal(ctaLabels.cuentenosAplicacion, 'Cuéntenos su aplicación');
});
test('ctaLabels: «Cotizar por WhatsApp» ya no existe', () => {
  assert.equal('whatsapp' in ctaLabels, false);
});

/* -- WhatsApp ------------------------------------------------------------ */
test('whatsapp: con modelo nombra el modelo, con o sin marca', () => {
  assert.equal(buildWhatsAppQuoteMessage({ productName: 'UP400St' }), 'Hola OrigenLab, quiero cotizar el UP400St.');
  assert.equal(
    buildWhatsAppQuoteMessage({ productName: 'UP400St', brandName: 'Hielscher' }),
    'Hola OrigenLab, quiero cotizar el UP400St.',
  );
});
test('whatsapp: sólo marca', () => {
  assert.equal(buildWhatsAppQuoteMessage({ brandName: 'OHAUS' }), 'Hola OrigenLab, quiero cotizar un equipo OHAUS.');
});
test('whatsapp: genérico', () => {
  assert.equal(buildWhatsAppQuoteMessage(), 'Hola OrigenLab, quiero cotizar un equipo.');
  assert.equal(buildWhatsAppQuoteMessage({}), 'Hola OrigenLab, quiero cotizar un equipo.');
});
test('whatsapp: la URL codifica el mensaje y apunta al número corporativo', () => {
  const url = new URL(buildWhatsAppQuoteUrl({ productName: 'UP400St' }));
  assert.equal(url.origin + url.pathname, 'https://wa.me/56962567816');
  assert.equal(url.searchParams.get('text'), 'Hola OrigenLab, quiero cotizar el UP400St.');
});

/* -- Portada -------------------------------------------------------------- */
test('company.homeHeroLine: Mensaje B, textual', () => {
  assert.equal(
    company.homeHeroLine,
    'Trabajamos directo con Hielscher, OHAUS, Ortoalresa y más. Despacho a todo Chile, puesta en marcha y capacitación con fábrica.',
  );
});

/* -- Pasos de compra ------------------------------------------------------ */
test('buyingSteps: cuatro pasos, con las afirmaciones aprobadas', () => {
  const steps = buyingSteps(all);
  assert.equal(steps.length, 4);
  assert.equal(steps[0].title, 'Cuéntenos la aplicación');
  assert.equal(steps[1].title, 'Cotización el mismo día hábil');
  assert.match(steps[1].body, /ficha técnica del fabricante/);
  assert.match(steps[1].body, /Mercado Público/);
  assert.equal(steps[3].title, 'Entrega y puesta en marcha');
  assert.equal(steps[3].body, 'Despacho a todo Chile, con puesta en marcha y capacitación con fábrica.');
});
test('buyingSteps: el paso de pago sólo habla de pago cuando el registro lo aprueba', () => {
  const approved = buyingSteps(only('pago-50-50'))[2];
  assert.equal(approved.title, 'Abono al pedido');
  assert.equal(buyingSteps(none)[2].title, 'Pago y confirmación del pedido');
  assert.equal(approved.body, 'Pago 50 % al pedido, 50 % contra entrega.');
  const withheld = buyingSteps(none)[2];
  assert.equal(withheld.body, 'Condiciones de pago en la cotización.');
  assert.doesNotMatch(JSON.stringify(withheld), /50/);
});
test('buyingSteps: sin afirmaciones aprobadas no hay promesa de plazo ni de cobertura', () => {
  const text = JSON.stringify(buyingSteps(none));
  assert.doesNotMatch(text, /mismo día/i);
  assert.doesNotMatch(text, /todo Chile/);
  assert.doesNotMatch(text, /Mercado Público/);
  assert.equal(buyingSteps(none)[1].title, 'Cotización con asesoría técnica');
  assert.equal(buyingSteps(none).length, 4);
});
test('buyingSteps: con el registro real, nunca el 50 % ni la garantía', () => {
  const text = JSON.stringify(buyingSteps());
  assert.doesNotMatch(text, /50 ?%/);
  assert.doesNotMatch(text, /garant/i);
  assert.match(text, /mismo día hábil/);
});

/* -- Preguntas de compra ---------------------------------------------------- */
test('buyingFaqs: con todo aprobado, cinco preguntas y ninguna de garantía', () => {
  const faqs = buyingFaqs(all);
  assert.deepEqual(
    faqs.map((faq) => faq.q),
    [
      '¿Cuánto demora la cotización?',
      '¿Despachan fuera de Santiago?',
      '¿Incluye puesta en marcha y capacitación?',
      '¿Cómo se paga?',
      '¿Pueden vender por Mercado Público?',
    ],
  );
  assert.ok(faqs.every((faq) => faq.q && faq.a));
  assert.doesNotMatch(JSON.stringify(faqs), /garant/i);
});
test('buyingFaqs: una pregunta se descarta si su afirmación no está aprobada', () => {
  assert.deepEqual(
    buyingFaqs(only('cotizacion-mismo-dia-habil')).map((faq) => faq.q),
    ['¿Cuánto demora la cotización?', '¿Cómo se paga?'],
  );
  assert.deepEqual(
    buyingFaqs(only('despacho-todo-chile')).map((faq) => faq.q),
    ['¿Despachan fuera de Santiago?', '¿Cómo se paga?'],
  );
  assert.deepEqual(buyingFaqs(none).map((faq) => faq.q), ['¿Cómo se paga?']);
});
test('buyingFaqs: «¿Cómo se paga?» nunca descarta la pregunta: sin 50/50 remite a la cotización', () => {
  const real = buyingFaqs().find((faq) => faq.q === '¿Cómo se paga?');
  assert.equal(real?.a, 'Condiciones de pago en la cotización.');
  assert.equal(buyingFaqs(none).find((faq) => faq.q === '¿Cómo se paga?')?.a, real.a);
  assert.equal(
    buyingFaqs(only('pago-50-50')).find((faq) => faq.q === '¿Cómo se paga?')?.a,
    'Pago 50 % al pedido, 50 % contra entrega.',
  );
});
test('buyingFaqs: respuestas del tablero Modelo', () => {
  const byQ = Object.fromEntries(buyingFaqs(all).map((faq) => [faq.q, faq.a]));
  assert.equal(byQ['¿Despachan fuera de Santiago?'], 'Despachamos a todo Chile.');
  assert.equal(
    byQ['¿Incluye puesta en marcha y capacitación?'],
    'Ofrecemos puesta en marcha y capacitación con fábrica. Las condiciones quedan indicadas en su cotización.',
  );
  assert.equal(
    byQ['¿Pueden vender por Mercado Público?'],
    'Sí, vendemos por Mercado Público y participamos en licitaciones. Indíquenos el número de la licitación al cotizar.',
  );
});

/* -- Registro --------------------------------------------------------------- */
test('registro: estado de las afirmaciones nuevas', () => {
  const status = (id) => claims.find((claim) => claim.id === id)?.status;
  assert.equal(status('cotizacion-mismo-dia-habil'), 'approved');
  assert.equal(status('directo-con-el-fabricante'), 'approved');
  assert.equal(status('despacho-todo-chile'), 'approved');
  assert.equal(status('puesta-en-marcha-con-fabrica'), 'approved');
  assert.equal(status('vende-mercado-publico'), 'approved');
  assert.equal(status('pago-50-50'), 'proposed');
  assert.equal(publicClaim('pago-50-50'), undefined);
  assert.equal(publicClaim('vende-mercado-publico'), 'Vendemos por Mercado Público y participamos en licitaciones');
  assert.equal(status('proveedor-inscrito'), 'approved');
  assert.equal(
    publicClaim('proveedor-inscrito'),
    'Proveedor inscrito en universidades, laboratorios farmacéuticos y empresas agroindustriales · Acreditado en ChileProveedores',
  );
  assert.doesNotMatch(publicClaim('proveedor-inscrito'), /\d/);
});
// Nombres y logos aprobados por la dueña el 2026-11-08: exactamente esta lista,
// en el registro y en la franja. Cualquier otro nombre necesita su permiso escrito.
const APPROVED_REGISTRATION_NAMES = [
  'Laboratorios Saval',
  'Universidad Santo Tomás',
  'ChileProveedores (Mercado Público)',
  'Corteva Agriscience',
  'Golden Omega',
  'Universidad de Concepción',
];
test('registro: «proveedor inscrito» nombra sólo la lista aprobada', () => {
  assert.deepEqual([...publicClaimNames('proveedor-inscrito')], APPROVED_REGISTRATION_NAMES);
  assert.deepEqual(registrationLogos.map((logo) => logo.name), APPROVED_REGISTRATION_NAMES);
  assert.ok(claims.filter((claim) => claim.names?.length).every((claim) => claim.id === 'proveedor-inscrito'));
  for (const logo of registrationLogos) assert.match(logo.src, /^\/clientes\/[a-z-]+\.(svg|png)$/);
});
test('registro: «garantía del fabricante» no es una afirmación', () => {
  assert.equal(claims.some((claim) => /garant/i.test(claim.text) || /garant/i.test(claim.id)), false);
});

/* -- Detección de paráfrasis (validate:dist) --------------------------------- */
const fakeClaims = `header
  {
    id: 'aprobada',
    text: 'Despacho a todo Chile',
    value: null,
    status: 'approved',
    variants: ['Envío a todo Chile'],
  },
  {
    id: 'pago',
    text: 'Pago 50 % al pedido, 50 % contra entrega',
    value: 50,
    status: 'proposed',
    variants: [
      '50 % al pedido',
      '50/50',
      '50%',
      '50 por ciento',
      'mitad al pedido',
      'la mitad al confirmar',
      'anticipo del pedido',
      '50 % de anticipo',
      'abono al pedido',
      'saldo contra entrega',
    ],
    note: 'x',
  },
`;
test('variantes: sólo las afirmaciones sin aprobar se vigilan, con su text y sus variants', () => {
  const watched = withheldClaimTexts(fakeClaims);
  assert.ok(watched.every((claim) => claim.id === 'pago'));
  assert.equal(watched.length, 11);
  assert.ok(watched.some((claim) => claim.text === 'Pago 50 % al pedido, 50 % contra entrega'));
});
test('variantes: cada paráfrasis se detecta, sin importar mayúsculas ni el espacio ante «%»', () => {
  const watched = withheldClaimTexts(fakeClaims);
  const caught = (page) => watched.some((claim) => publishesClaimText(page, claim.text));
  for (const page of [
    'Pago 50 % al pedido, 50 % contra entrega',
    'pago 50% al pedido, 50% contra entrega',
    'Se paga 50/50',
    'Aceptamos 50%',
    'La MITAD AL PEDIDO y el resto a la entrega',
    'Con un 50  % de anticipo',
    'Pedimos el anticipo del pedido',
    'Se paga el 50 por ciento al inicio',
    'La mitad al confirmar y el resto después',
    'Abono al pedido y saldo contra entrega',
    'Pagamos 50 % contra entrega',
  ]) {
    assert.ok(caught(page), `no detectó: ${page}`);
  }
});
test('variantes: el texto aprobado y la redacción neutra no se marcan', () => {
  const watched = withheldClaimTexts(fakeClaims);
  const caught = (page) => watched.some((claim) => publishesClaimText(page, claim.text));
  for (const page of [
    'Despacho a todo Chile',
    'Envío a todo Chile',
    'Condiciones de pago en la cotización.',
    'Cotización el mismo día hábil',
    'Pago y confirmación del pedido',
    'Humedad relativa de 0–50 % en la cámara',
    'Rango 0-50 % HR',
    'Hasta 150 % de la carga nominal',
    'Tolerancia de 1,50 % del valor',
    'Se entrega de anticipo una muestra',
  ]) {
    assert.ok(!caught(page), `marcó por error: ${page}`);
  }
});
test('variantes: con el registro real, ninguna página de pasos ni preguntas publica el pago 50/50', () => {
  const watched = withheldClaimTexts(readFileSync(new URL('../src/data/claims.ts', import.meta.url), 'utf8'));
  const page = JSON.stringify([buyingSteps(), buyingFaqs(), company]);
  for (const claim of watched) assert.ok(!publishesClaimText(page, claim.text), `publica ${claim.id}: ${claim.text}`);
});

const pageWith = (head, body = '<p>Texto neutro.</p>') =>
  `<!doctype html><html><head><title>t</title>${head}</head><body>${body}</body></html>`;
const surfaceHit = (html) => {
  const watched = withheldClaimTexts(fakeClaims);
  return hiddenCopySurfaces(html).some((surface) => watched.some((claim) => publishesClaimText(surface.text, claim.text)));
};
test('superficies: un «50 % al pedido» sólo en la meta description se detecta', () => {
  assert.ok(surfaceHit(pageWith('<meta name="description" content="Compre con 50 % al pedido y el resto después">')));
  assert.ok(surfaceHit(pageWith('<meta property="og:description" content="Pago 50&#37; al pedido">')));
  assert.ok(surfaceHit(pageWith('<meta name="twitter:description" content="Pagamos &quot;50%&quot; al pedido">')));
});
test('superficies: alt, aria-label y JSON-LD también se escanean', () => {
  assert.ok(surfaceHit(pageWith('', '<img src="x.jpg" alt="Abono al pedido y saldo contra entrega">')));
  assert.ok(surfaceHit(pageWith('', '<a href="/" aria-label="Pago 50/50">Ir</a>')));
  assert.ok(
    surfaceHit(
      pageWith('<script type="application/ld+json">{"@type":"Offer","description":"Mitad al pedido, mitad contra entrega"}</script>'),
    ),
  );
});
test('superficies: la copia neutra y los rangos de humedad no se marcan', () => {
  assert.ok(!surfaceHit(pageWith('<meta name="description" content="Cómo comprar: cuéntenos su aplicación y reciba la cotización el mismo día hábil.">')));
  assert.ok(!surfaceHit(pageWith('<meta name="description" content="Humedad 0–50 % HR">', '<img alt="Termobalanza" src="x.jpg">')));
  assert.ok(!surfaceHit(pageWith('<script type="application/ld+json">{"name":"Condiciones de pago en la cotización"}</script>')));
});
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`  ok  ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  FAIL ${name}\n       ${String(error?.message ?? error).split('\n').slice(0, 4).join('\n       ')}`);
  }
}
console.log(`\n${cases.length - failed}/${cases.length} pruebas pasan`);
process.exit(failed ? 1 : 0);
