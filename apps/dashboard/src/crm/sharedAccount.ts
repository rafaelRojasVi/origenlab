/** The shared account the team works in; Drive and Gmail open as it, not as the browser's first account. */
export const SHARED_ACCOUNT = "contacto@origenlab.cl";

/** `url` opened as the shared account (Google's `authuser`). */
export const asShared = (url: string): string => `${url}?authuser=${encodeURIComponent(SHARED_ACCOUNT)}`;
