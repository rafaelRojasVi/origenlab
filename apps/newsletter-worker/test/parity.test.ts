import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CONSENT_TEXT_VERSIONS, FIELD_LIMITS, INTERESTS, PAGES } from '../src/config';

/**
 * El sitio y el Worker son dos despliegues independientes que tienen que decir
 * lo mismo. Nada los obliga a coincidir en tiempo de compilación, así que la
 * comprobación vive aquí: si alguien añade una familia de equipo, cambia el
 * texto de consentimiento o renombra una página de resultado en un lado y no en
 * el otro, esta prueba falla antes de que lo haga una persona rellenando el
 * formulario.
 */
const web = join(dirname(fileURLToPath(import.meta.url)), '../../web');
const read = (path: string) => readFileSync(join(web, path), 'utf8');

describe('el Worker y el sitio dicen lo mismo', () => {
  it('la lista de intereses es la de equipmentScope.ts', () => {
    const source = read('src/data/equipmentScope.ts');
    const entries = source.slice(source.indexOf('export const equipmentScope'));
    const ids = [...entries.matchAll(/^\s{4}id: '([^']+)'/gm)].map((match) => match[1]);
    expect(ids.length).toBe(6);
    expect([...INTERESTS].sort()).toEqual([...ids].sort());
  });

  it('la versión del texto de consentimiento es la del sitio', () => {
    const source = read('src/data/newsletter.ts');
    const version = source.match(/CONSENT_TEXT_VERSION = '([^']+)'/)?.[1];
    expect(version).toBeDefined();
    expect(CONSENT_TEXT_VERSIONS).toContain(version!);
  });

  it('los límites de campo son los que valida el navegador', () => {
    const source = read('src/data/newsletter.ts');
    const block = source.slice(source.indexOf('export const FIELD_LIMITS'));
    for (const [field, limit] of Object.entries({
      email: FIELD_LIMITS.email,
      name: FIELD_LIMITS.name,
      organization: FIELD_LIMITS.organization,
    })) {
      const value = block.match(new RegExp(`${field}: (\\d+)`))?.[1];
      expect(Number(value), field).toBe(limit);
    }
  });

  /**
   * Las seis rutas del boletín las emite un solo archivo,
   * `src/pages/newsletter/[...slug].astro`, cuyo `getStaticPaths` devuelve la
   * lista vacía mientras el boletín siga desactivado: así la compilación
   * pública no contiene ninguna. La paridad que importa sigue siendo la misma,
   * y es que cada destino al que el Worker redirige esté declarado ahí.
   */
  it('cada página de resultado la emite la ruta del boletín', () => {
    const route = read('src/pages/newsletter/[...slug].astro');
    for (const target of Object.values(PAGES)) {
      const slug = target.replace(/^\/newsletter\/|\/$/g, '');
      if (slug === 'newsletter' || slug === '') {
        expect(route, target).toContain('slug: undefined');
        continue;
      }
      expect(route, target).toContain(`slug: '${slug}'`);
      expect(route, target).toContain(`slug === '${slug}'`);
    }
  });

  it('el endpoint que el formulario usa es el que el Worker sirve', () => {
    const source = read('src/data/newsletter.ts');
    const endpoint = source.match(/NEWSLETTER_ENDPOINT = '([^']+)'/)?.[1];
    expect(endpoint).toBe('/api/newsletter/subscribe');
    const routes = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../src/index.ts'), 'utf8');
    expect(routes).toContain("path === '/api/newsletter/subscribe'");
  });
});
