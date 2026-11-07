#!/usr/bin/env node
/**
 * Pruebas del mapa de scroll de las páginas de producto (`scrollStory.ts`).
 *
 * Sin dependencias ni DOM real: las funciones puras se prueban directo y
 * `createScrollStory` con un `window` y un resorte falsos. `productStory.ts`
 * (`createStoryScene`) importa three y necesita WebGL; no se prueba en node y
 * lo cubre el QA de interacción en el navegador.
 *
 * Ejecutar: npm run test:scroll-story
 */
import assert from 'node:assert/strict';

const { mapProgress, smoothstep, activeBeat, createScrollStory, beatsFromSpecs } = await import('../src/lib/showroom/scrollStory.ts');

let failed = 0;
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/* ── funciones puras ── */
test('mapProgress: 0 al inicio, 1 al final del recorrido', () => {
  assert.equal(mapProgress(0, 6000, 1000), 0);
  assert.equal(mapProgress(5000, 6000, 1000), 1);
  assert.equal(mapProgress(2500, 6000, 1000), 0.5);
});
test('mapProgress: se acota fuera del recorrido y no divide por cero', () => {
  assert.equal(mapProgress(-300, 6000, 1000), 0);
  assert.equal(mapProgress(9000, 6000, 1000), 1);
  assert.equal(mapProgress(100, 1000, 1000), 0);
  assert.equal(mapProgress(100, 800, 1000), 0);
  assert.equal(mapProgress(Number.NaN, 6000, 1000), 0);
});
test('smoothstep: extremos, punto medio y acotado', () => {
  assert.equal(smoothstep(0), 0);
  assert.equal(smoothstep(1), 1);
  assert.equal(smoothstep(0.5), 0.5);
  assert.equal(smoothstep(-2), 0);
  assert.equal(smoothstep(3), 1);
  assert.ok(smoothstep(0.25) < 0.25 && smoothstep(0.75) > 0.75);
});

const beats = [
  { id: 'a', start: 0, end: 0.3 },
  { id: 'b', start: 0.3, end: 0.4 },
  { id: 'c', start: 0.4, end: 1 },
];
test('activeBeat: el beat cuyo [inicio, fin) contiene el progreso', () => {
  const hit = activeBeat(0.35, beats);
  assert.equal(hit.beat.id, 'b');
  assert.ok(Math.abs(hit.localT - 0.5) < 1e-9);
  assert.equal(activeBeat(0, beats).beat.id, 'a');
  assert.equal(activeBeat(0.3, beats).beat.id, 'b'); // el fin es exclusivo
  assert.equal(activeBeat(0.3, beats).localT, 0);
});
test('activeBeat: progreso 1 pertenece al último beat con localT 1; huecos dan null', () => {
  const last = activeBeat(1, beats);
  assert.equal(last.beat.id, 'c');
  assert.equal(last.localT, 1);
  assert.equal(activeBeat(0.5, [{ start: 0, end: 0.2 }, { start: 0.6, end: 1 }]), null);
  assert.equal(activeBeat(0.5, []), null);
});

test('beatsFromSpecs: convierte rangos, mueve la cámara al entrar y corre el hook', () => {
  const log = [];
  const scene = { tweenTo: (v) => log.push(`tween:${v}`) };
  const built = beatsFromSpecs([
    { id: 'x', progressStart: 0, progressEnd: 0.5, cameraView: 'general', animationHook: (_s, t) => log.push(`hook:${t}`) },
    { id: 'y', progressStart: 0.5, progressEnd: 1 },
  ]);
  assert.deepEqual(built.map((b) => [b.id, b.start, b.end]), [['x', 0, 0.5], ['y', 0.5, 1]]);
  built[0].enter(scene, 0);
  built[0].tick(scene, 0.25);
  built[1].enter(scene, 0); // sin cameraView no mueve la cámara
  built[1].tick(scene, 0.25);
  assert.deepEqual(log, ['tween:general', 'hook:0.25']);
});
test('beatsFromSpecs: un rango fuera de orden o de 0–1 falla al montar', () => {
  const bad = (a, b, c, d) => () => beatsFromSpecs([{ id: 'a', progressStart: a, progressEnd: b }, { id: 'b', progressStart: c, progressEnd: d }]);
  assert.throws(bad(0, 0.5, 0.4, 1), /fuera de orden/);
  assert.throws(bad(0, 0.5, 0.5, 1.2), /fuera de orden/);
  assert.throws(bad(0, 0, 0, 1), /fuera de orden/);
});

/* ── createScrollStory con window falso ── */
function fakeWindow({ reduced = false } = {}) {
  const listeners = new Map(); // tipo → [{fn, options}]
  const frames = [];
  const win = {
    innerHeight: 1000,
    addEventListener: (type, fn, options) => listeners.set(type, [...(listeners.get(type) ?? []), { fn, options }]),
    removeEventListener: (type, fn) => listeners.set(type, (listeners.get(type) ?? []).filter((l) => l.fn !== fn)),
    requestAnimationFrame: (fn) => frames.push(fn),
    cancelAnimationFrame: () => frames.splice(0),
    matchMedia: () => ({
      matches: reduced,
      addEventListener: (type, fn) => listeners.set(`mq:${type}`, [{ fn }]),
      removeEventListener: (type, fn) => listeners.set(`mq:${type}`, (listeners.get(`mq:${type}`) ?? []).filter((l) => l.fn !== fn)),
    }),
  };
  return {
    win,
    listeners,
    pending: () => frames.length,
    flush: () => frames.splice(0).forEach((fn) => fn(0)),
    emit: (type) => (listeners.get(type) ?? []).forEach((l) => l.fn()),
  };
}
const withWindow = (env, fn) => {
  const had = Object.getOwnPropertyDescriptor(globalThis, 'window');
  globalThis.window = env.win;
  try {
    return fn();
  } finally {
    if (had) Object.defineProperty(globalThis, 'window', had);
    else delete globalThis.window;
  }
};
const spacerAt = (state) => ({ getBoundingClientRect: () => ({ top: -state.scrolled, height: 6000 }) });
const fakeScene = () => {
  const calls = [];
  return { calls, renderOverlays: (p) => calls.push(['overlays', p]) };
};
const recorder = (log) =>
  ['a', 'b', 'c'].map((id, i) => ({
    id,
    start: [0, 0.3, 0.4][i],
    end: [0.3, 0.4, 1][i],
    enter: () => log.push(`enter:${id}`),
    tick: (_s, t) => log.push(`tick:${id}:${t.toFixed(2)}`),
    leave: () => log.push(`leave:${id}`),
  }));

test('createScrollStory: escuchas pasivas, un solo rAF por ráfaga y dispose las quita', () => {
  const env = fakeWindow();
  const state = { scrolled: 0 };
  const log = [];
  withWindow(env, () => {
    const story = createScrollStory(spacerAt(state), fakeScene(), recorder(log));
    const scroll = env.listeners.get('scroll');
    assert.equal(scroll.length, 1);
    assert.equal(scroll[0].options.passive, true);
    assert.equal(env.listeners.get('resize')[0].options.passive, true);
    assert.equal(env.pending(), 0);
    env.emit('scroll');
    env.emit('scroll');
    env.emit('scroll');
    assert.equal(env.pending(), 1, 'tres eventos de scroll, un solo cuadro pedido');
    story.dispose();
    assert.equal(env.pending(), 0, 'dispose cancela el cuadro pendiente');
    assert.equal(env.listeners.get('scroll').length, 0);
    assert.equal(env.listeners.get('resize').length, 0);
    assert.equal(env.listeners.get('mq:change').length, 0);
    env.emit('scroll');
    assert.equal(env.pending(), 0, 'tras dispose el scroll ya no hace nada');
  });
});
test('createScrollStory: enter/tick/leave y progreso; renderOverlays en cada cuadro', () => {
  const env = fakeWindow();
  const state = { scrolled: 0 };
  const log = [];
  const scene = fakeScene();
  withWindow(env, () => {
    const story = createScrollStory(spacerAt(state), scene, recorder(log));
    assert.deepEqual(log, ['enter:a', 'tick:a:0.00'], 'el primer cuadro se calcula al crear');
    state.scrolled = 5000 * 0.35;
    env.emit('scroll');
    env.flush();
    assert.deepEqual(log.slice(2), ['leave:a', 'enter:b', 'tick:b:0.50']);
    assert.ok(Math.abs(story.progress() - 0.35) < 1e-9);
    assert.deepEqual(scene.calls.at(-1), ['overlays', story.progress()]);
    // Salto de a hasta c: pasa por b para que el estado acumulado quede bien.
    log.length = 0;
    state.scrolled = 5000;
    env.emit('scroll');
    env.flush();
    assert.deepEqual(log, ['leave:b', 'enter:c', 'tick:c:1.00']);
    // Vuelta atrás hasta a: recorre c, b, a.
    log.length = 0;
    state.scrolled = 0;
    env.emit('scroll');
    env.flush();
    assert.deepEqual(log, ['leave:c', 'enter:b', 'leave:b', 'enter:a', 'tick:a:0.00']);
    story.dispose();
  });
});
test('createScrollStory: con movimiento reducido informa el progreso pero no anima', () => {
  const env = fakeWindow({ reduced: true });
  const state = { scrolled: 0 };
  const log = [];
  const scene = fakeScene();
  const seen = [];
  withWindow(env, () => {
    const story = createScrollStory(spacerAt(state), scene, recorder(log), { onProgress: (p) => seen.push(p) });
    state.scrolled = 2500;
    env.emit('scroll');
    env.flush();
    assert.deepEqual(log, [], 'ningún beat se ejecuta');
    assert.equal(story.progress(), 0.5);
    assert.equal(seen.at(-1), 0.5);
    story.dispose();
  });
});

for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`  ok   ${name}`);
  } catch (error) {
    failed++;
    console.error(`  FAIL ${name}\n${error.stack}`);
  }
}
if (failed) {
  console.error(`\n${failed} de ${cases.length} pruebas fallaron`);
  process.exit(1);
}
console.log(`\n${cases.length} pruebas de scroll-story ok`);
