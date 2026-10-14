/**
 * A product photo by a short-lived signed URL (Catálogo v2). Thumbnails ask for their URL only
 * once they scroll into view, batched with their neighbours (`imageUrls.ts`). A URL that fails to
 * load is re-signed once — it may simply have expired while the page stayed open — and after a
 * second failure, or when there is no image or no Storage, the placeholder shows instead of a
 * broken box.
 */
import { useEffect, useRef, useState } from "react";
import { KIND_LABEL } from "./catalogApi";
import { invalidateImageUrl, requestImageUrl } from "./imageUrls";

type Size = "thumb" | "large";

const BOX: Record<Size, string> = {
  thumb: "h-10 w-10 rounded",
  large: "h-48 w-full rounded-md",
};

export function ProductImage({ imageId, kind, alt, size }: {
  imageId: string | null;
  kind: string | null;
  alt: string;
  size: Size;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(size === "large" || typeof IntersectionObserver === "undefined");
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const retried = useRef(false);

  useEffect(() => {
    setUrl(null);
    setFailed(false);
    retried.current = false;
  }, [imageId]);

  useEffect(() => {
    if (visible || !box.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "200px" });
    observer.observe(box.current);
    return () => observer.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!imageId || !visible || url || failed) return;
    let live = true;
    void requestImageUrl(imageId).then((signed) => {
      if (!live) return;
      if (signed) setUrl(signed);
      else setFailed(true);
    });
    return () => {
      live = false;
    };
  }, [imageId, visible, url, failed]);

  function onError() {
    if (!imageId) return;
    invalidateImageUrl(imageId);
    if (retried.current) {
      setFailed(true);
      return;
    }
    retried.current = true;
    setUrl(null);
  }

  if (!imageId || failed) return <Placeholder kind={kind} size={size} />;
  return (
    <div ref={box} className={`${BOX[size]} shrink-0 overflow-hidden bg-canvas-sunken/60`}>
      {url ? (
        <img
          src={url}
          alt={alt}
          width={size === "thumb" ? 40 : undefined}
          height={size === "thumb" ? 40 : 192}
          loading={size === "thumb" ? "lazy" : "eager"}
          decoding="async"
          onError={onError}
          className="h-full w-full object-contain"
        />
      ) : (
        <div className="crm-skeleton h-full w-full" aria-hidden="true" />
      )}
    </div>
  );
}

function Placeholder({ kind, size }: { kind: string | null; size: Size }) {
  const label = (kind && KIND_LABEL[kind]) || "Producto";
  return (
    <div
      data-testid="image-placeholder"
      aria-hidden="true"
      className={`${BOX[size]} flex shrink-0 items-center justify-center border border-dashed border-line-strong bg-canvas-sunken/60 font-mono text-ink-faint ${
        size === "thumb" ? "text-[10px]" : "text-xs"
      }`}
    >
      {size === "thumb" ? label.slice(0, 3).toUpperCase() : `Sin imagen · ${label}`}
    </div>
  );
}
