/** Client-generated Idempotency-Key for a durable write command. */
export function newIdempotencyKey(kind: string): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi) {
    throw new Error("No se pudo generar una clave segura para la operación.");
  }
  if (typeof cryptoApi.randomUUID === "function") {
    return `${kind}:${cryptoApi.randomUUID()}`;
  }
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  return `${kind}:${Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("")}`;
}
