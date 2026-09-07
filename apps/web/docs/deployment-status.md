# Deployment status — OrigenLab

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-03-24

**External / operational snapshot:** Live hosting, DNS, and cPanel state are **not provable from git**. Treat this file as a **human-maintained** record. Re-check HostGator/cPanel/DNS before acting on “current state” claims.

**Last verified externally:** 2026-03-24 *(update when you have confirmed hosting/DNS in the real control panels).*

**Current state (as documented, not guaranteed by repo):** The site is described as live at origenlab.cl on HostGator shared hosting.

**Cloudflare (registrado 2026-09-05):** la respuesta en producción incluye
`server: cloudflare` y `cf-ray`, es decir hay un proxy de Cloudflare delante del
origen HostGator. Esto no estaba documentado. Implica que Cloudflare procesa las
IP de los visitantes como proveedor de infraestructura y que las cabeceras del
origen pueden verse alteradas o complementadas en el borde. Verificar en el panel
de Cloudflare antes de dar por hecho el comportamiento de caché o de cabeceras.

---

## What is deployed

- **Site:** Static Astro + Tailwind site.
- **Hosting:** HostGator shared hosting.
- **Domain:** origenlab.cl (primary domain in cPanel).
- **Deployment method:** Manual upload of Astro build output. No CI/CD; no automatic deploy.

---

## How deployment works

1. **Local (WSL/Ubuntu):**
   - `npm run check`
   - `npm run build`
   - Contents of the `dist/` folder are zipped or uploaded.

2. **HostGator:**
   - Upload (or extract) into **`public_html`** so that the **contents** of `dist/` are at the root of `public_html`, not inside a subfolder.

**Critical:** `public_html` must contain the site files directly (e.g. `index.html`, `favicon.svg`, `_astro/`, `.htaccess`, `productos/`, etc.). It must **not** contain a nested `dist` folder (e.g. `public_html/dist/index.html`). A nested structure caused 403/default-page issues before; the fix was uploading the contents of `dist/` into `public_html`.

3. **Include `.htaccess`:**
   - The project has `public/.htaccess`, which is copied into `dist/` on build. Ensure `.htaccess` is uploaded (some FTP clients hide dotfiles).

---

## Hosting and DNS summary

| Item | Value |
|------|--------|
| Hosting | HostGator shared hosting |
| Primary domain | origenlab.cl |
| Document root | public_html |
| Nameservers | ns00010.hostgator.cl, ns00011.hostgator.cl |
| DNS management | HostGator / cPanel Zone Editor (domain uses HostGator nameservers) |

Do not change DNS casually; the site and email are working with the current setup.

---

## Repo and branches

- **Git:** GitHub repo exists.
- **Branches:** `main` and `dev`.
- **Build output:** Deploy the contents of `dist/` after a local build; the repo does not store built files (dist is gitignored).

---

## Security (static site)

- No backend; no public admin or dashboard.
- No secrets in the repo.
- `public/.htaccess` provides HTTPS redirect and basic security headers.
- Canonical URLs use baseUrl `https://origenlab.cl`.

---

## CDN

CDN was explored but not enabled. Leave as-is unless there is a clear requirement to enable it.
