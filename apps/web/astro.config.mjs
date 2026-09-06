// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  site: 'https://origenlab.cl',
  /** La canónica del sitio siempre lleva barra final; el build debe coincidir. */
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  integrations: [
    sitemap({
      // Superficies internas: no se indexan y no entran al sitemap.
      filter: (page) => !page.includes('/logo-lab/'),
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
  },
});
