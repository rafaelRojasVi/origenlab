# Campaña OHAUS (octubre 2026) — imágenes de correo

Imágenes del correo `public/email/campaigns/ohaus-balanzas-2026-10.html`. Se
publican con el próximo despliegue del sitio en
`https://origenlab.cl/email-assets/ohaus-2026-10/…` y el correo las referencia
sólo por esa URL absoluta.

Sólo para el correo: OHAUS **no** es una de las seis marcas del sitio público
(`APPROVED_BRAND_IDS`), y ninguna página del sitio usa estas imágenes.

## Fuentes oficiales

Fotografías del fabricante, descargadas el 2026-10-19 de la ficha oficial de
cada modelo en us.ohaus.com. 600×600 px es el tamaño mayor que publica OHAUS
(el servidor ignora `?width=`); ninguna imagen se amplió por encima del original.

| Archivo | Modelo | Ficha oficial | Imagen original | sha256 del original |
|---|---|---|---|---|
| `ohaus-pr224.jpg` | PR224, balanza analítica | https://us.ohaus.com/en-us/products/balances-scales/analytical-balances/pr-series-analytical/electronic-balance-pr224 | https://us.ohaus.com/getmedia/684b169b-d376-4d71-8f4d-eca4246a8ee0/PR-Analytical_Draftshield-Closed_Right_DMX-ID_4294988919_WebShop | `0188a7c6a4190b6c0e7cb990c415d9aecb995781cf11950586d788dff75433a2` |
| `ohaus-nv2201.jpg` | Navigator NV2201, balanza portátil | https://us.ohaus.com/en-ap/products/balances-scales/portable-balances/navigator/electronic-balance-nv2201 | https://us.ohaus.com/getmedia/dcb9d39f-e2c5-411b-8488-76eea5a17a13/NV_Square-Pan_Sensor_0_0g_Right_DMX-ID_4294992093_WebShop | `4b69885e23841e461300c7d4a12bcf2c9e17dac788b8bedd39ae85b81e7c833a` |
| `ohaus-mb32.jpg` | MB32, analizador de humedad | https://us.ohaus.com/en-ap/products/balances-scales/moisture-analyzers/mb32/moisture-analyzer-mb32 | https://us.ohaus.com/getmedia/32409205-52fb-443a-93e9-a1ced6bc0cfa/MB32_Left_DMX_ID_4295015507_WebShop | `424e05bf5f20f18ea6acfb3dad7385b3af9249b4d9025b477ab4b95b67c7f208` |
| `ohaus-hero.jpg` | los tres anteriores, compuestos | — | las tres imágenes de arriba | — |
| `ohaus-logo.png` | logotipo OHAUS | — | copia de `public/email/brands/ohaus-logo.png` (ver su README) | — |

Las cifras del correo (capacidad y resolución) se comprobaron contra las mismas
fichas el 2026-10-19: PR224 220 g / 0,1 mg; NV2201 2.200 g / 0,1 g; MB32 90 g /
0,01 % de humedad.

## Tratamiento

Pillow, sin máscaras ni recorte a mano:

1. recorte al contorno del producto (todo lo que no es blanco puro);
2. escala a una caja común, nunca por encima del 100 %;
3. el producto se *multiplica* sobre un degradado radial de estudio (centro
   `#fdfdfb`, borde `#e2eae7`): el blanco del original toma el tono del fondo y
   el producto se funde sin halo ni borde;
4. JPEG progresivo, calidad 84, 4:4:4.

| Archivo | Píxeles | Se muestra a |
|---|---|---|
| `ohaus-hero.jpg` | 1200×480 | 600×240 |
| `ohaus-pr224.jpg`, `ohaus-nv2201.jpg`, `ohaus-mb32.jpg` | 520×400 | 260×200 |
| `ohaus-logo.png` | 152×144 | 34×32 |

Todas pesan menos de 150 KB.
