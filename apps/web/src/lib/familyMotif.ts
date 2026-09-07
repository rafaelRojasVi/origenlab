/**
 * Motivo de muestra por familia de equipo.
 *
 * El repositorio sólo tiene fotografía aprobada de una familia, la de
 * centrifugación. Rellenar las otras cinco con fotografía de archivo, con
 * imágenes generadas o con el equipo de otro fabricante sería mentir sobre lo
 * que OrigenLab tiene; dejarlas como tarjetas de texto igualadas convertiría la
 * portada en la rejilla de seis cajas que el rediseño quiere evitar.
 *
 * La salida es un diagrama, y se lee como diagrama: trazo, nodos y nada más.
 * Cada uno dibuja lo que el equipo le hace a la muestra, de modo que la
 * ilustración dice algo cierto en vez de decorar. Los tres nodos repiten el
 * lenguaje de la marca: la muestra es siempre un punto que se mueve.
 *
 * Todo es determinista. No hay azar, ni en construcción ni en ejecución: el
 * mismo dato tiene que dar siempre el mismo dibujo, o dejaría de ser un
 * diagrama para ser ruido.
 *
 * Lienzo común de 160 x 120 para que las seis compartan escala óptica aunque se
 * presenten a tamaños muy distintos.
 */

export const MOTIF_WIDTH = 160;
export const MOTIF_HEIGHT = 120;

/** Trazo de estructura y trazo de muestra. Sin opacidades intermedias. */
const LINE = 'var(--motif-line, var(--color-ink-800))';
const HAIR = 'var(--motif-hair, var(--color-hairline))';
const NODE = 'var(--motif-node, var(--color-teal-700))';

function dot(x: number, y: number, r = 4.5): string {
  return `<circle cx="${x}" cy="${y}" r="${r}" fill="${NODE}"/>`;
}

function line(x1: number, y1: number, x2: number, y2: number, w = 2, color = LINE): string {
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${w}" stroke-linecap="round"/>`;
}

function path(d: string, w = 2, color = LINE): string {
  return `<path d="${d}" fill="none" stroke="${color}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"/>`;
}

/* -------------------------------------------------------------------------- */

/** Pesaje: un plato, una escala fina y la muestra asentándose sobre él. */
function pesaje(): string {
  const ticks = Array.from({ length: 13 }, (_, i) => {
    const x = 26 + i * 9;
    const tall = i % 4 === 0;
    return line(x, 100, x, tall ? 86 : 93, tall ? 2 : 1.5, tall ? LINE : HAIR);
  }).join('');
  return [
    ticks,
    line(20, 100, 140, 100, 2),
    // Plato
    line(52, 64, 108, 64, 3),
    line(80, 64, 80, 100, 2),
    // La muestra cae y se asienta: tres posiciones del mismo punto
    dot(80, 22, 3.4),
    dot(80, 40, 4),
    dot(80, 56, 5),
  ].join('');
}

/** Dispersión: un centro que reparte la muestra hacia fuera. */
function dispersion(): string {
  const arms = [0, 120, 240].map((deg) => {
    const r = (deg * Math.PI) / 180;
    // Espiral corta: sale del centro y se abre
    const x1 = 80 + Math.cos(r) * 14;
    const y1 = 60 + Math.sin(r) * 14;
    const cx = 80 + Math.cos(r + 0.9) * 44;
    const cy = 60 + Math.sin(r + 0.9) * 44;
    const x2 = 80 + Math.cos(r + 1.5) * 54;
    const y2 = 60 + Math.sin(r + 1.5) * 54;
    return path(`M ${x1.toFixed(1)} ${y1.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`, 2) +
      dot(x2, y2, 4.5);
  });
  return [
    `<circle cx="80" cy="60" r="10" fill="none" stroke="${LINE}" stroke-width="2.5"/>`,
    `<circle cx="80" cy="60" r="3.2" fill="${LINE}"/>`,
    ...arms,
  ].join('');
}

/** Sonicación: una sonda y el frente de onda que rompe la muestra. */
function sonicacion(): string {
  // Tres frentes que pierden cuerpo al alejarse. Se adelgaza el trazo, no se
  // aclara el color: en gris de filete el frente exterior desaparecía y la
  // sonicación se quedaba sin lo único que la distingue.
  const waves = [20, 32, 44].map((r, i) =>
    path(`M ${80 - r} 66 A ${r} ${r} 0 0 0 ${80 + r} 66`, 2.5 - i * 0.5),
  );
  return [
    // Sonda
    `<rect x="75" y="14" width="10" height="34" rx="2" fill="none" stroke="${LINE}" stroke-width="2.5"/>`,
    line(80, 48, 80, 60, 3),
    ...waves,
    line(20, 66, 140, 66, 2),
    dot(46, 84, 4),
    dot(80, 92, 5),
    dot(114, 82, 4.2),
  ].join('');
}

/** Centrifugación: el campo que empuja la muestra al exterior y la separa. */
function centrifugacion(): string {
  return [
    `<ellipse cx="80" cy="60" rx="54" ry="34" fill="none" stroke="${HAIR}" stroke-width="2"/>`,
    `<circle cx="80" cy="60" r="7" fill="none" stroke="${LINE}" stroke-width="2.5"/>`,
    // Radio de trabajo
    line(87, 60, 128, 60, 2),
    // La muestra se estratifica hacia el exterior
    dot(104, 60, 3.2),
    dot(115, 60, 4.2),
    dot(128, 60, 5.4),
    path('M 80 26 A 54 34 0 0 1 128 55', 2),
    path('M 80 94 A 54 34 0 0 0 128 65', 2),
  ].join('');
}

/** Osmometría: la curva de enfriamiento y el punto de congelación marcado. */
function osmometria(): string {
  return [
    line(24, 100, 140, 100, 2, HAIR),
    line(24, 20, 24, 100, 2, HAIR),
    // Enfriamiento, sobreenfriamiento y meseta
    path('M 30 30 C 52 34, 60 74, 74 84 L 82 62 L 136 62', 2.5),
    // El punto que se mide
    dot(82, 62, 5.2),
    line(24, 62, 74, 62, 1.5, HAIR),
    dot(30, 30, 3.4),
    dot(136, 62, 4),
  ].join('');
}

/** Electroforesis: carriles y bandas separadas por migración. */
function electroforesis(): string {
  const lanes = [46, 80, 114].map((x) => line(x, 22, x, 104, 1.5, HAIR));
  const bands = [
    [46, 44],
    [46, 72],
    [80, 38],
    [80, 66],
    [80, 90],
    [114, 52],
  ].map(([x, y]) => line(x - 13, y, x + 13, y, 3.5));
  return [
    line(20, 18, 140, 18, 2),
    ...lanes,
    ...bands,
    dot(46, 92, 4.2),
    dot(80, 100, 4.6),
    dot(114, 82, 4),
  ].join('');
}

const MOTIFS: Record<string, () => string> = {
  'pesaje-humedad': pesaje,
  'dispersion-homogeneizacion': dispersion,
  sonicacion: sonicacion,
  centrifugacion: centrifugacion,
  osmometria: osmometria,
  electroforesis: electroforesis,
};

/**
 * Contenido del `<svg>` para una familia, o `undefined` si no hay motivo.
 * Devolver `undefined` y no un dibujo genérico es deliberado: una familia sin
 * motivo propio se compone sin ilustración, no con una de relleno.
 */
export function familyMotif(familyId: string): string | undefined {
  return MOTIFS[familyId]?.();
}

/**
 * Descripción para lectores de pantalla. El diagrama aporta significado, así
 * que no puede ser puramente decorativo, pero tampoco debe repetir el texto que
 * ya está al lado: describe el dibujo, no la familia.
 */
export const MOTIF_ALT: Record<string, string> = {
  'pesaje-humedad': 'Diagrama: una muestra se asienta sobre el plato de una balanza graduada.',
  'dispersion-homogeneizacion':
    'Diagrama: desde un rotor central, la muestra se reparte hacia fuera en tres trayectorias.',
  sonicacion: 'Diagrama: una sonda emite frentes de onda que dispersan la muestra.',
  centrifugacion:
    'Diagrama: en un rotor, la muestra se estratifica hacia el exterior por tamaño.',
  osmometria:
    'Diagrama: curva de enfriamiento de una muestra con el punto de congelación marcado.',
  electroforesis: 'Diagrama: tres carriles con bandas separadas a distintas alturas.',
};
