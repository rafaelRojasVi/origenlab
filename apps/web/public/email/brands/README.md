# Partner logos — fuentes oficiales

Este directorio es el almacén de originales para **dos** salidas distintas, que
desde 2026-09-06 ya no coinciden:

- **Sitio público** (`npm run build:brand-logos` → `public/brands/`): las seis
  marcas aprobadas — Hielscher, Ortoalresa, IKA, Adam Equipment, Löser y SERVA.
- **Firma de correo** (`npm run build:email-brands`): sigue con el conjunto
  anterior, que incluye Ollital y CRTOP y no incluye a Adam ni a Löser.
  **Pendiente de regenerar**; ver `docs/design/CONTENT_NEEDED.md`.

Los originales de Ollital y CRTOP se conservan por eso, aunque el sitio ya no
los publique.

Official sources (downloaded to `*-source.*` for regeneration):

| Brand | Source |
|-------|--------|
| SERVA | https://www.serva.de/lib/images/serva-logo.png |
| Ortoalresa | https://ortoalresa.com/static/images/logo-header-normal-1c27f117243d1215a0b668f0ee824e57.svg |
| IKA | https://www.ika.com/ika/images/Logo-IKA-without-Claim.png |
| CRTOP | https://www.crtopmachine.com/uploadfile/userimg/f00993a6a3fa4cec3aae84af3d87d9da.jpg |
| Ollital | https://www.ollital.com/uploadfile/userimg/9f6fd3332271a26b56dbc789cec01c68.jpeg |
| Hielscher | https://www.hielscher.com/wp-content/uploads/hielscher-logo2.svg |
| Adam Equipment | https://adamequipment.com/media/logo/default/Adam_Logo_2.png |
| Löser Messtechnik | http://www.loeser-osmometer.de/LoeLogo.jpg |

## Outputs

- `*-logo.png` — normalized grayscale (~36px tall)
- `../origenlab-brand-strip.png` — combined strip (export 820×68, display **410×34**)

## Regenerate

```bash
cd apps/web
npm run build:email-brands
npm run build:email-signature-embedded
```

Wording in signature: **Marcas con las que trabajamos** (not “distribuidor oficial”).
