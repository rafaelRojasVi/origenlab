/**
 * Detección de afirmaciones sin aprobar en el texto visible de una página.
 *
 * Vive aparte de `validate-dist.mjs` para poder probarla (`npm run test:message`).
 * Lee el código fuente de `src/data/claims.ts` como texto: los bloques que no
 * están `approved` aportan su `text` y cada entrada de `variants`.
 */

/** Minúsculas, un solo espacio y «N %» con espacio: «50%», «50 %» y «50  %» son lo mismo. */
export function normaliseClaimText(text) {
  return text
    .toLowerCase()
    .replace(/\s*%/g, ' %')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Redacciones vigiladas: `{ id, text }` por cada `text` y `variants` de una afirmación no aprobada. */
export function withheldClaimTexts(claimsSrc) {
  return claimsSrc
    .split(/\n  \{\n/)
    .slice(1)
    .filter((block) => !/status: 'approved'/.test(block))
    .flatMap((block) => {
      const id = block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)';
      const variantList = block.match(/variants: \[([^\]]*)\]/)?.[1] ?? '';
      const variants = [...variantList.matchAll(/'([^']*)'/g)].map((match) => match[1]);
      return [block.match(/text: '([^']*)'/)?.[1] ?? '', ...variants].map((text) => ({ id, text }));
    })
    .filter((claim) => claim.text.length > 0);
}

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * ¿El texto publica esa redacción, sin distinguir mayúsculas ni el espacio ante «%»?
 * Una frase que empieza por cifra no cuenta si la cifra es el final de otra
 * («150 %», «1,50 %») ni el extremo de un rango («0–50 %»): ahí no es la proporción
 * de pago. Una que termina en cifra tampoco si sigue otra cifra.
 */
export function publishesClaimText(visibleText, claimText) {
  const phrase = normaliseClaimText(claimText);
  if (!phrase) return false;
  const before = /^\d/.test(phrase) ? '(?<![\\d.,–—-])' : '';
  const after = /\d$/.test(phrase) ? '(?![\\d])' : '';
  return new RegExp(before + escapeRegExp(phrase) + after).test(normaliseClaimText(visibleText));
}

const decodeEntities = (text) =>
  text
    .replace(/&#x([0-9a-f]+);/gi, (_m, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_m, num) => String.fromCodePoint(Number(num)))
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');

const jsonStrings = (value, out = []) => {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((item) => jsonStrings(item, out));
  else if (value && typeof value === 'object') Object.values(value).forEach((item) => jsonStrings(item, out));
  return out;
};

/**
 * Textos de una página que `validate:dist` no ve al quitar las etiquetas: los
 * atributos `content` (meta description, og, twitter), `alt`, `aria-label` y
 * `title`, y las cadenas de los datos estructurados (`application/ld+json`).
 * Son copia pública igual que el cuerpo: un buscador o un lector de pantalla la
 * lee aunque nadie la vea.
 */
export function hiddenCopySurfaces(html) {
  const surfaces = [];
  for (const match of html.matchAll(/\s(?:content|alt|aria-label|title)=(?:"([^"]*)"|'([^']*)')/gi)) {
    surfaces.push({ where: 'atributo', text: decodeEntities(match[1] ?? match[2] ?? '') });
  }
  for (const match of html.matchAll(/<script\b[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script\s*>/gi)) {
    let strings;
    try {
      strings = jsonStrings(JSON.parse(match[1]));
    } catch {
      strings = [match[1]];
    }
    for (const text of strings) surfaces.push({ where: 'JSON-LD', text });
  }
  return surfaces.filter((surface) => surface.text.trim().length > 0);
}
