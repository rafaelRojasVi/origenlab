/**
 * Tokens de confirmación y de baja.
 *
 * Reglas, y ninguna es opcional:
 *
 * - **Opacos y aleatorios.** 32 bytes de `crypto.getRandomValues`, en
 *   hexadecimal. No se derivan de la dirección, así que el enlace no filtra a
 *   quién pertenece ni permite fabricar el de otra persona.
 * - **La base guarda la huella, nunca el token.** La huella es un HMAC-SHA-256
 *   con una clave secreta del Worker: quien lea la base no puede reconstruir un
 *   enlace válido, y un volcado robado no sirve para dar de baja a nadie ni para
 *   confirmar suscripciones ajenas.
 * - **Comparación en tiempo constante.** La búsqueda se hace por huella, que ya
 *   es una igualdad exacta en un índice único, y la comparación directa de
 *   huellas usa `timingSafeEqual`.
 * - **Nunca se registran.** Ni el token, ni la huella, ni la dirección.
 */

const encoder = new TextEncoder();

export function mintToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function isTokenShape(value: string): boolean {
  return /^[0-9a-f]{64}$/.test(value);
}

async function key(pepper: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    encoder.encode(pepper),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
}

/** Huella almacenable de un token. Requiere la clave secreta del Worker. */
export async function hashToken(token: string, pepper: string): Promise<string> {
  const signature = await crypto.subtle.sign('HMAC', await key(pepper), encoder.encode(token));
  return [...new Uint8Array(signature)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Clave de caudal. Es un HMAC con clave secreta y no un hash corriente: sin la
 * clave, nadie puede comprobar si una IP concreta está en la tabla probando
 * candidatas, que es justo lo que un hash simple de un espacio pequeño permite.
 */
export async function rateKey(value: string, secret: string): Promise<string> {
  return hashToken(`rate:${value}`, secret);
}

export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
