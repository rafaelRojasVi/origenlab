/**
 * Render de las marcas a SVG. Sólo composición: la geometría vive en
 * `mark-geometry.mjs` y la paleta repite los tokens de `src/styles/global.css`.
 *
 * Tres esquemas y ni uno más, porque son los tres fondos que el sitio usa de
 * verdad: papel, tinta y una sola tinta para sellos, fax y grabado.
 */
import { CANVAS, CONCEPTS } from './mark-geometry.mjs';

export const MARK_PALETTE = {
  paper: '#fafaf7',
  ink: '#141617',
  teal700: '#0f766e',
  teal500: '#14b8a6',
  teal100: '#ccfbf1',
};

/** Esquemas de color. `mono` hereda del contexto (`currentColor`). */
export const SCHEMES = {
  light: { trace: MARK_PALETTE.ink, node: MARK_PALETTE.teal700, nucleus: MARK_PALETTE.ink },
  dark: { trace: MARK_PALETTE.paper, node: MARK_PALETTE.teal500, nucleus: MARK_PALETTE.paper },
  mono: { trace: 'currentColor', node: 'currentColor', nucleus: 'currentColor' },
};

/* --- Contraste (WCAG 1.4.11, gráficos no textuales: 3:1) ----------------- */

function channel(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance(hex) {
  const h = hex.replace('#', '');
  const r = channel(parseInt(h.slice(0, 2), 16));
  const g = channel(parseInt(h.slice(2, 4), 16));
  const b = channel(parseInt(h.slice(4, 6), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a, b) {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const light = Math.max(la, lb);
  const dark = Math.min(la, lb);
  return (light + 0.05) / (dark + 0.05);
}

/* --- Cuerpo de la marca --------------------------------------------------- */

/**
 * Devuelve sólo el interior del `<svg>`, para poder incrustarlo tanto en un
 * archivo suelto como dentro de un lockup sin volver a dibujar nada.
 *
 * `scale` reduce la marca dentro del lienzo sin tocar el viewBox, que es como
 * se construye la versión de favicon: mismo dibujo, menos detalle alrededor.
 */
export function markBody(conceptId, scheme = 'light', { simplified = false } = {}) {
  const c = CONCEPTS[conceptId];
  const colors = SCHEMES[scheme] ?? SCHEMES.light;
  const parts = [];

  // A 16 px la trayectoria se engrosa: un trazo que a 48 px es elegante, a
  // 16 px cae por debajo del píxel y deja tres puntos flotando sin relato.
  const stroke = simplified ? c.strokeWidth * 1.25 : c.strokeWidth;
  const nodeR = simplified ? c.nodeRadius * 1.12 : c.nodeRadius;

  const paths = c.paths ?? [c.path];
  for (const d of paths) {
    parts.push(
      `<path d="${d}" fill="none" stroke="${colors.trace}" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round"/>`,
    );
  }

  if (c.nucleusRadius) {
    parts.push(
      `<circle cx="24" cy="24" r="${c.nucleusRadius}" fill="${colors.nucleus}"/>`,
    );
  }

  c.nodes.forEach(([x, y], i) => {
    const r = (nodeR * (c.nodeScale?.[i] ?? 1)).toFixed(2);
    // El halo sólo existe en la versión a una tinta. Ahí nodo y trayectoria
    // comparten color y sin un recorte se funden en una mancha. En papel y en
    // tinta el color ya los separa (teal-700 sobre tinta da 3,3:1) y el halo
    // sobraba: partía la trayectoria en trozos sueltos y la lemniscata dejaba
    // de leerse como un recorrido continuo. Ver design/logo-explorations.
    if (scheme === 'mono') {
      parts.push(
        `<circle cx="${x}" cy="${y}" r="${(Number(r) + stroke * 0.42).toFixed(
          2,
        )}" fill="var(--mark-void, #fafaf7)"/>`,
      );
    }
    parts.push(`<circle cx="${x}" cy="${y}" r="${r}" fill="${colors.node}"/>`);
  });

  return parts.join('');
}

/** Archivo SVG completo, con título accesible cuando la marca identifica. */
export function markSvg(
  conceptId,
  scheme = 'light',
  { size, title = 'OrigenLab', simplified = false, background } = {},
) {
  const dim = size ? ` width="${size}" height="${size}"` : '';
  const bg = background
    ? `<rect width="${CANVAS}" height="${CANVAS}" fill="${background}"/>`
    : '';
  const label = title
    ? ` role="img" aria-label="${title}"`
    : ' aria-hidden="true" focusable="false"';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CANVAS} ${CANVAS}"${dim}${label}>${
    title ? `<title>${title}</title>` : ''
  }${bg}${markBody(conceptId, scheme, { simplified })}</svg>`;
}

/* --- Lockups -------------------------------------------------------------- */

/**
 * Lockup horizontal y apilado.
 *
 * El texto va como trazado (`<path>` no: aquí se usa `<text>` con la familia
 * del sistema) sólo en los archivos de entrega; dentro del sitio el lockup se
 * compone con HTML real, que es seleccionable, traducible y no arrastra la
 * tipografía dentro del SVG. Ver `docs/logo-system.md`.
 */
export function lockupSvg(conceptId, scheme = 'light', { stacked = false } = {}) {
  const colors = SCHEMES[scheme] ?? SCHEMES.light;
  const word = scheme === 'dark' ? MARK_PALETTE.paper : MARK_PALETTE.ink;
  const font =
    "font-family=\"'Plus Jakarta Sans', ui-sans-serif, system-ui, sans-serif\" font-weight=\"700\"";

  if (stacked) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 96" role="img" aria-label="OrigenLab"><title>OrigenLab</title><g transform="translate(56 4)">${markBody(
      conceptId,
      scheme,
    )}</g><text x="80" y="80" text-anchor="middle" ${font} font-size="22" letter-spacing="-0.5" fill="${word}">OrigenLab</text></svg>`;
  }

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 208 48" role="img" aria-label="OrigenLab"><title>OrigenLab</title>${markBody(
    conceptId,
    scheme,
  )}<text x="58" y="32" ${font} font-size="24" letter-spacing="-0.6" fill="${word}">OrigenLab</text></svg>`;
}

export { CONCEPTS, CANVAS };
