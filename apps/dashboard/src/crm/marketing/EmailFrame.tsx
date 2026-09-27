import { useEffect, useMemo } from "react";
import { buildPreviewDocument } from "./emailPreview";

/**
 * A campaign's HTML rendered in isolation: sanitized, under a CSP that allows images only from
 * origenlab.cl, in an iframe with an empty `sandbox` (no scripts, forms, popups or navigation
 * out of the frame) and no referrer.
 *
 * `scale` shrinks a 600 px render into a thumbnail; the thumbnail is decorative (the card
 * carries the campaign's name and subject as text) and cannot be focused or clicked into.
 */
export function EmailFrame({
  html,
  width,
  height,
  scale,
  title,
  onBlocked,
}: {
  html: string;
  width: number;
  height: number;
  scale?: number;
  title: string;
  onBlocked?: (blocked: string[], removed: string[]) => void;
}) {
  const preview = useMemo(() => buildPreviewDocument(html), [html]);
  useEffect(() => onBlocked?.(preview.blockedImages, preview.removed), [preview, onBlocked]);

  const frame = (
    <iframe
      title={title}
      sandbox=""
      referrerPolicy="no-referrer"
      srcDoc={preview.html}
      loading="lazy"
      data-testid="email-frame"
      tabIndex={scale ? -1 : undefined}
      aria-hidden={scale ? true : undefined}
      style={{
        width,
        height,
        border: 0,
        background: "#ffffff",
        ...(scale ? { transform: `scale(${scale})`, transformOrigin: "0 0", pointerEvents: "none" } : {}),
      }}
    />
  );
  if (!scale) return frame;
  return (
    <div style={{ width: width * scale, height: height * scale, overflow: "hidden" }} className="relative">
      {frame}
    </div>
  );
}
