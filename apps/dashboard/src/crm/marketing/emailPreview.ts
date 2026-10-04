/**
 * A campaign's HTML, made safe to render inside the dashboard.
 *
 * The preview must never *do* anything: no script runs, no form submits, and — because a
 * tracking pixel is just an image — nothing is fetched from a host other than the OrigenLab
 * website, whose verified product images the templates use. Three layers, each sufficient for
 * scripts on its own:
 *
 * 1. **Parsing, not regex.** `DOMParser` builds an inert document (it runs no script and loads
 *    no image), and the cleanup walks real elements and attributes.
 * 2. **A Content-Security-Policy** as the first element of `<head>`: `default-src 'none'`,
 *    images only from the two OrigenLab website origins (`https://origenlab.cl` and
 *    `https://www.origenlab.cl` — the historical Hielscher mailings reference the www host)
 *    and `data:`, inline styles only.
 * 3. **`<iframe sandbox="">`** in `EmailFrame`: unique origin, no scripts, no forms, no popups,
 *    no top-level navigation; `referrerpolicy="no-referrer"`.
 *
 * Links are kept visible but inert (`href="#"`, the target in `title`; `target`, `ping` and
 * `download` removed), so a click in the preview neither loads a third-party page into the frame
 * nor navigates the dashboard.
 */

/**
 * Exactly the origins a preview may load an image from. An origin is scheme + host (+ port),
 * so `http://origenlab.cl`, `https://origenlab.cl.attacker.test`, `https://cdn.origenlab.cl`
 * and `https://origenlab.cl:8443` are all different strings and all refused.
 */
export const PREVIEW_IMAGE_ORIGINS: readonly string[] = ["https://origenlab.cl", "https://www.origenlab.cl"];

export const PREVIEW_CSP =
  `default-src 'none'; img-src ${PREVIEW_IMAGE_ORIGINS.join(" ")} data:; style-src 'unsafe-inline'; font-src data:; ` +
  "form-action 'none'; base-uri 'none'; frame-src 'none'; media-src 'none'; connect-src 'none'";

const REMOVED_ELEMENTS = [
  "script", "noscript", "iframe", "frame", "frameset", "object", "embed", "applet", "form", "input",
  "button", "textarea", "select", "base", "link", "meta", "video", "audio", "source", "track", "portal",
  "svg image", "svg use", "svg feImage",
];

const URL_ATTRIBUTES = ["src", "srcset", "background", "poster", "lowsrc", "dynsrc", "xlink:href", "data"];

export interface PreviewResult {
  /** The full document to put in `srcdoc`. */
  html: string;
  /** Remote images (possible tracking pixels) not loaded, by URL. */
  blockedImages: string[];
  /** Kinds of element or attribute removed, for the notice under the preview. */
  removed: string[];
}

/**
 * True only for a raster `data:` image or an absolute `https:` URL whose origin is one of
 * `PREVIEW_IMAGE_ORIGINS`, carrying no credentials and no explicit port. `URL.origin` drops
 * `user:pw@`, so the credential check is explicit: `https://user:pw@origenlab.cl/x` would
 * otherwise pass on origin alone. Relative and protocol-relative URLs fail to parse and are
 * refused; SVG `data:` URLs are refused because they can carry script.
 */
export function isAllowedImageUrl(raw: string): boolean {
  const value = raw.trim();
  if (/^data:image\/(png|jpe?g|gif|webp);/i.test(value)) return true;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.protocol !== "https:") return false;
  if (url.username !== "" || url.password !== "") return false;
  if (url.port !== "") return false;
  return PREVIEW_IMAGE_ORIGINS.includes(url.origin);
}

/**
 * Image resizers our own emails use to scale website photos (`wsrv.nl/?url=origenlab.cl/…&w=420`).
 * Allowing the resizer itself would let any image on the internet through it, so it stays out of
 * the CSP; instead the preview shows the original image the resizer points at, when that original
 * is itself allowed. Exact origins, as for `PREVIEW_IMAGE_ORIGINS`.
 */
const IMAGE_RESIZER_ORIGINS: readonly string[] = ["https://wsrv.nl", "https://images.weserv.nl"];

/**
 * The allowed original behind a resizer URL, or null. The source must resolve to an
 * `isAllowedImageUrl` image: a scheme-less source is read as https; any other scheme, a
 * protocol-relative source, credentials or a port are refused like everywhere else.
 */
export function unproxyImageUrl(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (!IMAGE_RESIZER_ORIGINS.includes(url.origin) || url.username || url.password || url.port) return null;
  const source = url.searchParams.get("url");
  if (!source || source.startsWith("//")) return null;
  const candidate = /^[a-z][a-z0-9+.-]*:/i.test(source) ? source : `https://${source}`;
  return isAllowedImageUrl(candidate) ? new URL(candidate).href : null;
}

/** A link target that could run code: `javascript:`, `vbscript:`, or any `data:` URL (an SVG or HTML payload). */
function isScriptUrl(raw: string): boolean {
  const cleaned = raw.replace(/[\u0000- ]/g, "").toLowerCase();
  return cleaned.startsWith("javascript:") || cleaned.startsWith("vbscript:") || cleaned.startsWith("data:");
}

/** Replace every `url(...)` in CSS that is not an allowed image with `none`. */
function neutralizeCss(css: string, blocked: string[]): string {
  return css
    .replace(/@import[^;]*;?/gi, () => {
      blocked.push("@import");
      return "";
    })
    .replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi, (whole, _q: string, url: string) => {
      if (isAllowedImageUrl(url)) return whole;
      const original = unproxyImageUrl(url);
      if (original) return `url('${original}')`;
      blocked.push(url);
      return "none";
    });
}

const PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1" fill="#e3e3de"/></svg>',
  );

export function buildPreviewDocument(source: string | null | undefined): PreviewResult {
  const doc = new DOMParser().parseFromString(source ?? "", "text/html");
  const removed = new Set<string>();
  const blockedImages: string[] = [];

  for (const selector of REMOVED_ELEMENTS) {
    doc.querySelectorAll(selector).forEach((el) => {
      removed.add(`<${el.tagName.toLowerCase()}>`);
      el.remove();
    });
  }

  doc.querySelectorAll("*").forEach((el) => {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) {
        el.removeAttribute(attr.name);
        removed.add("on…=");
        continue;
      }
      // A link may not aim at another browsing context or ping a tracker on click.
      if (name === "target" || name === "formtarget" || name === "ping" || name === "download") {
        el.removeAttribute(attr.name);
        removed.add(`${name}=`);
        continue;
      }
      if (name === "style") {
        el.setAttribute("style", neutralizeCss(attr.value, blockedImages));
        continue;
      }
      if (name === "href" || name === "action" || name === "formaction") {
        if (isScriptUrl(attr.value)) removed.add("javascript:");
        if (el.tagName === "A" || el.tagName === "AREA") {
          if (!isScriptUrl(attr.value)) el.setAttribute("title", attr.value);
          el.setAttribute("href", "#");
        } else {
          el.removeAttribute(attr.name);
        }
        continue;
      }
      if (URL_ATTRIBUTES.includes(name)) {
        if (name === "srcset") {
          const kept = attr.value
            .split(",")
            .map((c) => c.trim())
            .map((c) => {
              const [first = "", ...rest] = c.split(/\s+/);
              if (isAllowedImageUrl(first)) return c;
              const original = unproxyImageUrl(first);
              if (original) return [original, ...rest].join(" ");
              if (c) blockedImages.push(first);
              return "";
            })
            .filter(Boolean);
          if (kept.length) el.setAttribute("srcset", kept.join(", "));
          else el.removeAttribute("srcset");
          continue;
        }
        if (!isAllowedImageUrl(attr.value)) {
          const original = unproxyImageUrl(attr.value);
          if (original) {
            el.setAttribute(attr.name, original);
            continue;
          }
          if (attr.value.trim()) blockedImages.push(attr.value.trim());
          if (el.tagName === "IMG" && name === "src") el.setAttribute("src", PLACEHOLDER);
          else el.removeAttribute(attr.name);
        }
      }
    }
  });

  doc.querySelectorAll("style").forEach((style) => {
    style.textContent = neutralizeCss(style.textContent ?? "", blockedImages);
  });

  const head = doc.head;
  const csp = doc.createElement("meta");
  csp.setAttribute("http-equiv", "Content-Security-Policy");
  csp.setAttribute("content", PREVIEW_CSP);
  const charset = doc.createElement("meta");
  charset.setAttribute("charset", "utf-8");
  const viewport = doc.createElement("meta");
  viewport.setAttribute("name", "viewport");
  viewport.setAttribute("content", "width=device-width, initial-scale=1");
  head.prepend(charset, csp, viewport);

  return {
    html: `<!doctype html>${doc.documentElement.outerHTML}`,
    blockedImages: [...new Set(blockedImages)],
    removed: [...removed],
  };
}
