#!/usr/bin/env node
/**
 * Prueba de humo de los modelos 3D de la sala (`src/lib/showroom/models/<id>.ts`).
 *
 * Para cada id (todos los de `catalog.ts` si no se pasa ninguno) importa el
 * modelo, llama a `build()` y exige que `root` tenga hijos, que existan
 * las vistas y anclas que nombra `content/<id>.ts` (`views.general` la calcula
 * la escena, no el modelo) y que `dispose()` no lance. Imprime una línea por modelo y
 * sale con código distinto de cero si alguno falla. Sin WebGL: sólo geometría;
 * el `canvas` de `canvasTexture` se sustituye por un contexto 2D falso.
 *
 * Ejecutar: npm run test:models            (todos)
 *           node scripts/test-models.mjs hielscher-up200st   (uno)
 */
import { existsSync, readFileSync } from 'node:fs';
import { register } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const modelsDir = join(root, 'src/lib/showroom/models');
const contentDir = join(root, 'src/lib/showroom/content');

// Los modelos importan `./kit` sin extensión (lo resuelve Vite, no Node): este
// gancho local reintenta con `.ts` cuando falta el archivo.
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

// `document` mínimo: lo único que toca la geometría es `createElement('canvas')`.
// El contexto 2D acepta cualquier llamada sin hacer nada; los degradados y
// `measureText` devuelven objetos con la misma regla.
const noop = () => {};
const fakeValue = () =>
  new Proxy(noop, {
    get: (target, key) => (key === 'width' ? 0 : key === Symbol.toPrimitive ? () => 0 : fakeValue()),
    apply: () => fakeValue(),
  });
const context2d = new Proxy(
  {},
  {
    get: (target, key) => (key in target ? target[key] : fakeValue()),
    set: (target, key, value) => ((target[key] = value), true),
  },
);
globalThis.Path2D = class Path2D {
  constructor() {
    return new Proxy(this, { get: (target, key) => (key in target ? target[key] : noop) });
  }
};
globalThis.document = {
  createElement(tag) {
    if (tag !== 'canvas') throw new Error(`test-models: document.createElement('${tag}') no está simulado`);
    return { width: 0, height: 0, style: {}, getContext: () => context2d };
  },
};

/** Ids de `catalog.ts`: cada `import { entry as eN } from './content/<id>'`. */
function catalogIds() {
  const source = readFileSync(join(root, 'src/lib/showroom/catalog.ts'), 'utf8');
  return [...source.matchAll(/from '\.\/content\/([a-z0-9-]+)'/g)].map((m) => m[1]).filter((id) => id !== 'types');
}

/* Lectura de cifras de la ficha para la escala de la página de familia. */
{
  const { parseSpecNumber, familyScale } = await import(pathToFileURL(join(root, 'src/lib/modelos.ts')).href);
  const cases = [
    ['2.000 W', 2000, '2.000 W'],
    ['0,1 a 500 ml', 0.1, '0,1 a 500 ml'],
    ['0,1–2 l', 0.1, '0,1–2 l'],
    ['1.5 kW', 1.5, '1.5 kW'],
    ['125 W de entrada, 75 W de salida', 125, '125 W'],
    ['18.100 RPM / 31.865 ×g', 18100, '18.100 RPM'],
    ['Formato de gel', null, null],
    // Número chileno: `1.500` es 1500 y `1,5` es 1.5.
    ['1.500 W', 1500, '1.500 W'],
    ['1,5 kW', 1.5, '1,5 kW'],
  ];
  for (const [input, n, display] of cases) {
    const got = typeof parseSpecNumber === 'function' ? parseSpecNumber(input) : undefined;
    const ok = n === null ? got === null : got && got.n === n && got.display === display;
    if (!ok) {
      console.error(`  x   parseSpecNumber(${JSON.stringify(input)}) = ${JSON.stringify(got)}, se esperaba ${n}`);
      process.exit(1);
    }
  }
  const spec = (value) => [{ label: 'Potencia', value, source: 's' }];
  const model = (slug, value) => ({ slug, name: slug, family: 'sonicacion', specs: spec(value) });
  const scale = familyScale([model('a', '100 W'), model('b', '200 W'), model('c', '2.000 W')], 'sonicacion');
  const xs = scale?.points.map((point) => point.x.toFixed(3)).join(',');
  if (xs !== '0.000,0.231,1.000') {
    console.error(`  x   familyScale: x = ${xs}, se esperaba 0.000,0.231,1.000`);
    process.exit(1);
  }
  if (familyScale([model('a', '100 W'), model('b', '200 W')], 'sonicacion') !== null || familyScale([model('a', 'x')], 'osmometria') !== null) {
    console.error('  x   familyScale: debe ser null con menos de 3 cifras o sin escala');
    process.exit(1);
  }
  // Un máximo se ordena por el extremo alto del rango: «0,1 a 500 ml» (máx. 500) va después de «300 ml».
  const maxed = (slug, value) => ({ slug, name: slug, family: 'centrifugacion', brand: 'x', specs: [{ label: 'Velocidad máxima', value, source: 's' }] });
  const order = (await import(pathToFileURL(join(root, 'src/lib/modelos.ts')).href)).familyOrder(
    [maxed('rango', '1.000 a 5.000 RPM'), maxed('medio', '4.000 RPM'), maxed('bajo', '2.000 RPM'), maxed('alto', '6.000 RPM')],
    'centrifugacion',
  ).map((m) => m.slug).join(',');
  if (order !== 'bajo,medio,rango,alto') {
    console.error(`  x   familyOrder (máximo): ${order}, se esperaba bajo,medio,rango,alto`);
    process.exit(1);
  }
  console.log('  ok  parseSpecNumber y familyScale');
}

const ids = process.argv.slice(2).length ? process.argv.slice(2) : catalogIds();
let failed = 0;

if (ids.length === 0) {
  console.error('test:models: no hay ids que probar (¿catalog.ts cambió de forma y ya no se reconocen sus imports?)');
  process.exit(1);
}

for (const id of ids) {
  const file = join(modelsDir, `${id}.ts`);
  const started = performance.now();
  try {
    if (!existsSync(file)) throw new Error(`falta models/${id}.ts`);
    const { build } = await import(pathToFileURL(file).href);
    if (typeof build !== 'function') throw new Error('no exporta build()');
    const model = build();
    if (!(model.root?.children?.length > 0)) throw new Error('root no tiene hijos');
    // `general` no vive en `model.views`: la escena la calcula sola (types.ts).
    // Lo que sí se exige es que cada vista y cada ancla que nombra la página
    // (`content/<id>.ts`) exista en el modelo, y que cada vista esté completa.
    const { entry } = await import(pathToFileURL(join(contentDir, `${id}.ts`)).href);
    for (const view of entry.views) {
      if (view.id === 'general') continue;
      const built = model.views[view.id];
      if (!built) throw new Error(`falta views.${view.id} (la nombra content/${id}.ts)`);
      if (!built.target || !built.direction || !(built.distance > 0)) throw new Error(`views.${view.id} está incompleta`);
    }
    for (const spot of entry.hotspots) {
      if (!model.anchors[spot.anchor]) throw new Error(`falta anchors.${spot.anchor} (la nombra content/${id}.ts)`);
      if (spot.view !== 'general' && !entry.views.some((v) => v.id === spot.view))
        throw new Error(`el hotspot «${spot.anchor}» apunta a la vista «${spot.view}», que no existe`);
    }
    model.dispose();
    const ms = Math.round(performance.now() - started);
    console.log(`  ok  ${id} (${model.root.children.length} hijos, ${Object.keys(model.views).length + 1} vistas, ${ms} ms)`);
  } catch (error) {
    failed += 1;
    console.error(`  x   ${id}: ${error.message}`);
  }
}

if (failed) {
  console.error(`\ntest:models: FALLA (${failed} de ${ids.length})`);
  process.exit(1);
}
console.log(`test:models: OK (${ids.length} modelos)`);
