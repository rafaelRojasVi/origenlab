/**
 * Signed URLs for product images, asked for in batches (Catálogo v2).
 *
 * Every id requested within the same short window goes into one `GET /v2/catalog/images/urls`
 * (sixty per request at most). A URL is reused until shortly before it expires; an `<img>` that
 * fails anyway calls `invalidateImageUrl` and asks once more. When Storage answers 503 (not
 * configured), every image resolves to `null` — the placeholder — and nothing more is fetched
 * for the rest of the session, so a page of thumbnails costs one failed request, not one per row.
 */
import { OperatorApiError } from "../../api/operatorClient";
import { fetchImageUrls } from "./catalogApi";

const WINDOW_MS = 30;
const MAX_IDS = 60;
const EARLY_S = 30;

type Resolver = (url: string | null) => void;
let pending = new Map<string, Resolver[]>();
let timer: ReturnType<typeof setTimeout> | null = null;
let cache = new Map<string, { url: string; until: number }>();
let storageDown = false;

export function resetImageUrls(): void {
  pending = new Map();
  if (timer) clearTimeout(timer);
  timer = null;
  cache = new Map();
  storageDown = false;
}

export function invalidateImageUrl(id: string): void {
  cache.delete(id);
}

async function flush(): Promise<void> {
  timer = null;
  const batch = pending;
  pending = new Map();
  const ids = [...batch.keys()];
  for (let i = 0; i < ids.length; i += MAX_IDS) {
    const chunk = ids.slice(i, i + MAX_IDS);
    let urls: Record<string, string> = {};
    if (!storageDown) {
      try {
        const answer = await fetchImageUrls(chunk);
        const until = Date.now() + Math.max(0, answer.expires_in - EARLY_S) * 1000;
        urls = answer.urls;
        for (const [id, url] of Object.entries(urls)) cache.set(id, { url, until });
      } catch (error) {
        if (error instanceof OperatorApiError && error.status === 503) storageDown = true;
      }
    }
    for (const id of chunk) for (const resolve of batch.get(id) ?? []) resolve(urls[id] ?? null);
  }
}

export function requestImageUrl(id: string): Promise<string | null> {
  if (storageDown) return Promise.resolve(null);
  const hit = cache.get(id);
  if (hit && hit.until > Date.now()) return Promise.resolve(hit.url);
  return new Promise((resolve) => {
    const waiting = pending.get(id);
    if (waiting) waiting.push(resolve);
    else pending.set(id, [resolve]);
    if (!timer) timer = setTimeout(() => void flush(), WINDOW_MS);
  });
}
