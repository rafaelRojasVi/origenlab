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
const { claims, publicClaim } = await import('../src/data/claims.ts');

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
});
test('registro: «garantía del fabricante» no es una afirmación', () => {
  assert.equal(claims.some((claim) => /garant/i.test(claim.text) || /garant/i.test(claim.id)), false);
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
