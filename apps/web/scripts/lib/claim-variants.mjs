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

/** ¿El texto visible publica esa redacción, sin distinguir mayúsculas ni el espacio ante «%»? */
export function publishesClaimText(visibleText, claimText) {
  return normaliseClaimText(visibleText).includes(normaliseClaimText(claimText));
}
