/**
 * Geometría de las tres exploraciones de marca OrigenLab.
 *
 * Fuente única: de aquí salen tanto los SVG de `public/logo/` y de
 * `design/logo-explorations/` como las constantes de
 * `src/lib/logo/generatedMark.ts` que consumen los componentes Astro. Nada se
 * dibuja dos veces, de modo que la marca del encabezado y el archivo que se
 * entrega a un proveedor no pueden divergir.
 *
 * Todo se resuelve en un lienzo de 48x48 con el centro en (24, 24). Esa
 * elección no es estética: 48 es divisible por 16, 24 y 48, así que los tres
 * tamaños críticos caen sobre la rejilla de píxeles sin medio punto.
 *
 * Reglas que la geometría tiene que cumplir por sí sola, sin ayuda del CSS:
 *
 * - Ningún trazo por debajo de 3 unidades (6,25% del lienzo). A 16 px eso es
 *   un píxel entero; por debajo, el trazo desaparece en pantallas no HiDPI.
 * - Ningún nodo con radio menor que 4 unidades. A 16 px da 2,7 px de diámetro,
 *   que es el mínimo con el que un punto sigue leyéndose como punto.
 * - Ninguna opacidad menor que 1 en los elementos que definen la identidad.
 *   La marca anterior dibujaba las órbitas al 24% y los nodos con radio 0,1 en
 *   un viewBox de 8,5 unidades: 0,38 px de radio a 32 px, es decir, invisibles.
 */

/* -------------------------------------------------------------------------- */
/* Utilidades                                                                 */
/* -------------------------------------------------------------------------- */

const TAU = Math.PI * 2;

function rotate([x, y], deg) {
  const r = (deg * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [x * c - y * s, x * s + y * c];
}

function bounds(points) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const [x, y] of points) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return { minX, minY, maxX, maxY, width: maxX - minX, height: maxY - minY };
}

/**
 * Encaja una polilínea dentro de una caja centrada en (24, 24), conservando la
 * proporción. `pad` es el margen libre en unidades a cada lado: es el espacio
 * que necesita el propio grosor del trazo para no salirse del viewBox.
 */
function fitToCanvas(points, pad) {
  const b = bounds(points);
  const available = 48 - pad * 2;
  const scale = Math.min(available / b.width, available / b.height);
  const cx = (b.minX + b.maxX) / 2;
  const cy = (b.minY + b.maxY) / 2;
  return points.map(([x, y]) => [24 + (x - cx) * scale, 24 + (y - cy) * scale]);
}

function round(n) {
  return Number(n.toFixed(2));
}

/** Longitud acumulada de una polilínea, para repartir nodos por arco. */
function arcLengths(points) {
  const acc = [0];
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i][0] - points[i - 1][0];
    const dy = points[i][1] - points[i - 1][1];
    acc.push(acc[i - 1] + Math.hypot(dx, dy));
  }
  return acc;
}

/** Punto sobre la polilínea a una fracción dada de su longitud total. */
function pointAtFraction(points, fraction) {
  const acc = arcLengths(points);
  const total = acc[acc.length - 1];
  const target = ((fraction % 1) + 1) % 1 * total;
  for (let i = 1; i < acc.length; i += 1) {
    if (acc[i] >= target) {
      const span = acc[i] - acc[i - 1] || 1;
      const t = (target - acc[i - 1]) / span;
      return [
        points[i - 1][0] + (points[i][0] - points[i - 1][0]) * t,
        points[i - 1][1] + (points[i][1] - points[i - 1][1]) * t,
      ];
    }
  }
  return points[points.length - 1];
}

/**
 * Convierte una polilínea cerrada en una curva suave (Catmull-Rom a Bezier).
 *
 * Se emite Bezier y no la polilínea cruda porque la trayectoria tiene que
 * seguir siendo suave cuando el mismo archivo se escala a un pliego impreso:
 * una polilínea de 60 segmentos se ve facetada en cuanto pasa de 200 px.
 */
function closedSmoothPath(points, tension = 1) {
  const n = points.length;
  const at = (i) => points[((i % n) + n) % n];
  let d = `M ${round(points[0][0])} ${round(points[0][1])}`;
  for (let i = 0; i < n; i += 1) {
    const p0 = at(i - 1);
    const p1 = at(i);
    const p2 = at(i + 1);
    const p3 = at(i + 2);
    const c1 = [p1[0] + ((p2[0] - p0[0]) / 6) * tension, p1[1] + ((p2[1] - p0[1]) / 6) * tension];
    const c2 = [p2[0] - ((p3[0] - p1[0]) / 6) * tension, p2[1] - ((p3[1] - p1[1]) / 6) * tension];
    d += ` C ${round(c1[0])} ${round(c1[1])}, ${round(c2[0])} ${round(c2[1])}, ${round(p2[0])} ${round(p2[1])}`;
  }
  return `${d} Z`;
}

function openSmoothPath(points, tension = 1) {
  const n = points.length;
  const at = (i) => points[Math.max(0, Math.min(n - 1, i))];
  let d = `M ${round(points[0][0])} ${round(points[0][1])}`;
  for (let i = 0; i < n - 1; i += 1) {
    const p0 = at(i - 1);
    const p1 = at(i);
    const p2 = at(i + 1);
    const p3 = at(i + 2);
    const c1 = [p1[0] + ((p2[0] - p0[0]) / 6) * tension, p1[1] + ((p2[1] - p0[1]) / 6) * tension];
    const c2 = [p2[0] - ((p3[0] - p1[0]) / 6) * tension, p2[1] - ((p3[1] - p1[1]) / 6) * tension];
    d += ` C ${round(c1[0])} ${round(c1[1])}, ${round(c2[0])} ${round(c2[1])}, ${round(p2[0])} ${round(p2[1])}`;
  }
  return d;
}

/* -------------------------------------------------------------------------- */
/* Concepto A — Órbita                                                        */
/* -------------------------------------------------------------------------- */

/**
 * Lemniscata de Bernoulli, que es la forma de la coreografía en ocho de los
 * tres cuerpos iguales (Moore / Chenciner). Se gira -30 grados por dos razones
 * a la vez: la caja de una lemniscata horizontal es de 2,83:1 y no llena un
 * icono cuadrado, y el giro convierte el eje en una diagonal ascendente, que es
 * la que lee como recorrido de la muestra al resultado.
 *
 * Tres nodos macizos repartidos por longitud de arco, no por parámetro: por
 * parámetro se agrupan dos junto al cruce y la marca queda coja.
 */
function conceptOrbita() {
  const raw = [];
  const steps = 480;
  // La lemniscata pura tiene una caja de 2,83:1 y deja el icono medio vacío.
  // Estirarla en Y antes de girarla la lleva a ~1,5:1, que ya llena el cuadrado
  // sin dejar de ser la misma curva: es un cambio de proporción, no de forma.
  const stretchY = 1.95;
  for (let i = 0; i < steps; i += 1) {
    const t = (i / steps) * TAU;
    const denom = 1 + Math.sin(t) ** 2;
    raw.push(
      rotate([Math.cos(t) / denom, ((Math.sin(t) * Math.cos(t)) / denom) * stretchY], -30),
    );
  }
  const points = fitToCanvas(raw, 6);

  // Fase elegida sobre un barrido real (design/logo-explorations): con 0,17 los
  // tres nodos forman un triángulo estable en vez de alinearse en fila, que es
  // lo que hacía que la marca se leyera como una oruga y no como tres cuerpos.
  // Ninguno cae sobre el cruce central, donde un nodo se convierte en borrón.
  const phase = 0.17;
  const nodes = [0, 1, 2].map((k) => pointAtFraction(points, phase + k / 3));

  // El peso crece de izquierda a derecha: la muestra entra por la izquierda y
  // el resultado es el nodo mayor. Se ordena por posición y no por índice de
  // recorrido para que el gradiente sobreviva a cualquier ajuste de fase.
  const byX = nodes.map((p, i) => ({ p, i })).sort((a, b) => a.p[0] - b.p[0]);
  const weights = [0.86, 1, 1.16];
  const nodeScale = new Array(3);
  byX.forEach((entry, rank) => {
    nodeScale[entry.i] = weights[rank];
  });

  return {
    id: 'orbita',
    name: 'Órbita',
    path: closedSmoothPath(points.filter((_, i) => i % 8 === 0)),
    nodes: nodes.map(([x, y]) => [round(x), round(y)]),
    strokeWidth: 3.6,
    nodeRadius: 4.8,
    nodeScale,
  };
}

/* -------------------------------------------------------------------------- */
/* Concepto B — Núcleo                                                        */
/* -------------------------------------------------------------------------- */

/**
 * El átomo declarado: núcleo macizo, una sola órbita elíptica y tres nodos.
 * Una órbita y no tres porque tres elipses cruzadas a 16 px se funden en una
 * mancha; el precio es que se parece más a un icono de stock.
 */
function conceptNucleo() {
  const rx = 20;
  const ry = 9.5;
  const tilt = -24;
  const ellipse = [];
  for (let i = 0; i < 160; i += 1) {
    const t = (i / 160) * TAU;
    ellipse.push(rotate([rx * Math.cos(t), ry * Math.sin(t)], tilt));
  }
  const points = ellipse.map(([x, y]) => [24 + x, 24 + y]);
  const nodes = [0, 1, 2].map((k) => {
    const t = (k / 3) * TAU + 0.35;
    const [x, y] = rotate([rx * Math.cos(t), ry * Math.sin(t)], tilt);
    return [round(24 + x), round(24 + y)];
  });

  return {
    id: 'nucleo',
    name: 'Núcleo',
    path: closedSmoothPath(points.filter((_, i) => i % 8 === 0)),
    nodes,
    strokeWidth: 3.2,
    nodeRadius: 4.6,
    nucleusRadius: 6.4,
    nodeScale: [1, 1, 1],
  };
}

/* -------------------------------------------------------------------------- */
/* Concepto C — Muestra                                                       */
/* -------------------------------------------------------------------------- */

/**
 * Dos arcos que se cruzan y tres nodos que crecen en diagonal ascendente: la
 * muestra entra, se procesa y sale como resultado. Es el concepto más narrativo
 * y el menos parecido a un átomo, con el riesgo de leerse como un gráfico.
 */
function conceptMuestra() {
  const a = [];
  const b = [];
  for (let i = 0; i <= 60; i += 1) {
    const t = i / 60;
    const x = 6 + t * 36;
    a.push([x, 34 - t * 20 + Math.sin(t * Math.PI) * 7]);
    b.push([x, 34 - t * 20 - Math.sin(t * Math.PI) * 7]);
  }
  const nodes = [
    [round(a[0][0]), round(24 + 10)],
    [24, 24],
    [round(a[a.length - 1][0]), round(24 - 10)],
  ];

  return {
    id: 'muestra',
    name: 'Muestra',
    paths: [openSmoothPath(a), openSmoothPath(b)],
    nodes,
    strokeWidth: 3.2,
    nodeRadius: 4.4,
    nodeScale: [0.82, 1, 1.2],
  };
}

export const CONCEPTS = {
  orbita: conceptOrbita(),
  nucleo: conceptNucleo(),
  muestra: conceptMuestra(),
};

export const CANVAS = 48;
