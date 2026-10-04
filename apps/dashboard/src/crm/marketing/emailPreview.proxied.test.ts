import { describe, expect, it } from "vitest";
import { buildPreviewDocument, unproxyImageUrl } from "./emailPreview";

// Every URL below is invented except the OrigenLab website origin the preview already allows.
const parse = (html: string) => new DOMParser().parseFromString(buildPreviewDocument(html).html, "text/html");

describe("images resized through an image proxy", () => {
  it("previews the original OrigenLab image behind a wsrv.nl resize", () => {
    const src = "https://wsrv.nl/?url=origenlab.cl/products/ika/t-25-digital.png&w=420&h=420&fit=contain";
    const out = buildPreviewDocument(`<img src="${src}">`);
    expect(parse(`<img src="${src}">`).querySelector("img")?.getAttribute("src")).toBe(
      "https://origenlab.cl/products/ika/t-25-digital.png",
    );
    expect(out.blockedImages).toEqual([]);
  });

  it.each([
    ["https://wsrv.nl/?url=origenlab.cl/a.png&w=1", "https://origenlab.cl/a.png"],
    ["https://wsrv.nl/?url=https://origenlab.cl/a.png", "https://origenlab.cl/a.png"],
    ["https://wsrv.nl/?url=www.origenlab.cl/a.png", "https://www.origenlab.cl/a.png"],
    ["https://images.weserv.nl/?url=origenlab.cl/a.png", "https://origenlab.cl/a.png"],
    ["https://wsrv.nl/?w=1&url=origenlab.cl%2Fa.png", "https://origenlab.cl/a.png"],
  ])("reads %s as %s", (proxied, original) => {
    expect(unproxyImageUrl(proxied)).toBe(original);
  });

  it.each([
    "https://wsrv.nl/?url=tracker.test/open.gif", // a tracker behind the proxy
    "https://wsrv.nl/?url=origenlab.cl.attacker.test/a.png", // look-alike host
    "https://wsrv.nl/?url=http://origenlab.cl/a.png", // not https
    "https://wsrv.nl/?url=user:pw@origenlab.cl/a.png", // credentials
    "https://wsrv.nl/?url=origenlab.cl:8443/a.png", // explicit port
    "https://wsrv.nl/?url=//origenlab.cl/a.png", // protocol-relative
    "https://wsrv.nl/", // no source
    "https://evil.test/?url=origenlab.cl/a.png", // not a known resizer
    "http://wsrv.nl/?url=origenlab.cl/a.png", // the proxy itself over http
  ])("keeps blocking %s", (proxied) => {
    expect(unproxyImageUrl(proxied)).toBeNull();
    const out = buildPreviewDocument(`<img src="${proxied}">`);
    expect(out.blockedImages).toEqual([proxied]);
  });

  it("applies the same rule in srcset and CSS backgrounds", () => {
    const ok = "https://wsrv.nl/?url=origenlab.cl/b.png&w=2";
    const bad = "https://wsrv.nl/?url=tracker.test/c.png";
    const doc = parse(
      `<img srcset="${ok} 2x, ${bad} 1x"><div style="background:url('${ok}')">x</div><td style="background:url(${bad})"></td>`,
    );
    expect(doc.querySelector("img")?.getAttribute("srcset")).toBe("https://origenlab.cl/b.png 2x");
    expect(doc.querySelector("div")?.getAttribute("style")).toContain("https://origenlab.cl/b.png");
    expect(doc.body.innerHTML).not.toContain("tracker.test");
  });
});
