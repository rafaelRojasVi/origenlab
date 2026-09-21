import { describe, expect, it, beforeEach } from 'vitest';
import { createTestDb, rows } from './helpers/d1';
import { FakeSender, NullSender, type EmailSender } from '../src/email';
import { handleConfirm, handleSubscribe, deriveUnsubscribeToken } from '../src/handlers';
import { parseSubscribe } from '../src/parse';
import { isSuppressed } from '../src/db';
import type { HandlerContext } from '../src/handlers';
import { RATE_MAX_PER_EMAIL } from '../src/config';

const PEPPER = 'pimienta-de-prueba';
const RATE = 'clave-de-caudal';

function form(overrides: Record<string, string | string[]> = {}): string {
  const base: Record<string, string | string[]> = {
    email: 'lab@ejemplo.cl',
    consentimiento: 'si',
    consent_version: '2026-09-21',
    source: 'web_newsletter_page',
    sitio_web: '',
    ...overrides,
  };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(base)) {
    if (Array.isArray(value)) for (const item of value) params.append(key, item);
    else params.set(key, value);
  }
  return params.toString();
}

function context(
  db: D1Database,
  sender: EmailSender = new FakeSender(),
  clientId = '203.0.113.7',
): HandlerContext {
  return {
    db,
    sender,
    pepper: PEPPER,
    rateSecret: RATE,
    siteOrigin: 'https://origenlab.cl',
    clientId,
    now: new Date('2026-09-21T12:00:00.000Z'),
  };
}

async function subscribe(ctx: HandlerContext, body = form()) {
  const parsed = parseSubscribe(body);
  if (!parsed.ok) throw new Error(`no debería rechazarse: ${parsed.rejection}`);
  return handleSubscribe(ctx, parsed.input, parsed.emailNorm);
}

describe('validación del cuerpo', () => {
  it('normaliza la dirección a minúsculas', () => {
    const parsed = parseSubscribe(form({ email: '  LAB@Ejemplo.CL ' }));
    expect(parsed.ok && parsed.emailNorm).toBe('lab@ejemplo.cl');
  });

  it('rechaza una dirección sin forma válida', () => {
    for (const email of ['sin-arroba', 'a@b', 'a@b.', '@ejemplo.cl', 'a b@ejemplo.cl']) {
      const parsed = parseSubscribe(form({ email }));
      expect(parsed.ok, email).toBe(false);
    }
  });

  it('rechaza el envío sin la casilla de consentimiento', () => {
    const params = new URLSearchParams(form());
    params.delete('consentimiento');
    const parsed = parseSubscribe(params.toString());
    expect(parsed.ok).toBe(false);
    expect(!parsed.ok && parsed.rejection).toBe('missing_consent');
  });

  it('no acepta un consentimiento con cualquier otro valor', () => {
    const parsed = parseSubscribe(form({ consentimiento: 'no' }));
    expect(!parsed.ok && parsed.rejection).toBe('missing_consent');
  });

  it('rechaza una versión de texto de consentimiento desconocida', () => {
    const parsed = parseSubscribe(form({ consent_version: '1999-01-01' }));
    expect(!parsed.ok && parsed.rejection).toBe('unknown_consent_version');
  });

  it('rechaza un interés que no está en la lista cerrada', () => {
    const parsed = parseSubscribe(form({ intereses: ['centrifugacion', 'criptomonedas'] }));
    expect(!parsed.ok && parsed.rejection).toBe('unknown_interest');
  });

  it('quita intereses repetidos', () => {
    const parsed = parseSubscribe(form({ intereses: ['centrifugacion', 'centrifugacion'] }));
    expect(parsed.ok && parsed.input.interests).toEqual(['centrifugacion']);
  });

  it('rechaza campos demasiado largos', () => {
    const parsed = parseSubscribe(form({ nombre: 'a'.repeat(400) }));
    expect(!parsed.ok && parsed.rejection).toBe('field_too_long');
  });

  it('trata un campo opcional en blanco como ausente', () => {
    const parsed = parseSubscribe(form({ nombre: '   ', organizacion: '' }));
    expect(parsed.ok && parsed.input.name).toBeNull();
    expect(parsed.ok && parsed.input.organization).toBeNull();
  });
});

describe('alta', () => {
  let db: D1Database;
  beforeEach(() => {
    db = createTestDb();
  });

  it('guarda una solicitud pendiente y envía la confirmación', async () => {
    const sender = new FakeSender();
    const result = await subscribe(context(db, sender));
    expect(result).toEqual({ ok: true, status: 'pending' });

    const requests = await rows<{ email_norm: string; consent_text_version: string }>(
      db,
      'select * from subscription_request',
    );
    expect(requests).toHaveLength(1);
    expect(requests[0]!.email_norm).toBe('lab@ejemplo.cl');
    expect(requests[0]!.consent_text_version).toBe('2026-09-21');

    /* Una solicitud pendiente NO es una suscripción. */
    const subscriptions = await rows(db, 'select * from subscription');
    expect(subscriptions).toHaveLength(0);

    expect(sender.sent).toHaveLength(1);
    expect(sender.sent[0]!.to).toBe('lab@ejemplo.cl');
  });

  it('nunca guarda el token, sólo su huella', async () => {
    const sender = new FakeSender();
    await subscribe(context(db, sender));
    const token = new URL(sender.sent[0]!.confirmUrl).searchParams.get('t')!;
    const stored = await rows<{ confirm_token_hash: string }>(
      db,
      'select confirm_token_hash from subscription_request',
    );
    expect(token).toMatch(/^[0-9a-f]{64}$/);
    expect(stored[0]!.confirm_token_hash).not.toBe(token);
  });

  it('el enlace de confirmación no contiene la dirección', async () => {
    const sender = new FakeSender();
    await subscribe(context(db, sender), form({ email: 'persona.unica@destino-unico.cl' }));
    const url = sender.sent[0]!.confirmUrl;
    /* Ni la parte local, ni el dominio, ni una forma codificada de la arroba. */
    expect(url).not.toContain('persona.unica');
    expect(url).not.toContain('destino-unico');
    expect(url).not.toContain('%40');
    expect(url).not.toContain('@');
    /* Lo único variable del enlace es el token opaco. */
    expect(url).toMatch(
      /^https:\/\/origenlab\.cl\/api\/newsletter\/confirm\?t=[0-9a-f]{64}$/,
    );
  });

  it('responde igual para una dirección ya suscrita, sin decirlo', async () => {
    const sender = new FakeSender();
    const first = await subscribe(context(db, sender));
    const token = new URL(sender.sent[0]!.confirmUrl).searchParams.get('t')!;
    await handleConfirm(context(db, sender), token);

    const second = await subscribe(context(db, sender, '198.51.100.9'));
    expect(second).toEqual(first);
  });

  it('sustituye la solicitud pendiente anterior de la misma dirección', async () => {
    const sender = new FakeSender();
    await subscribe(context(db, sender));
    await subscribe(context(db, sender));
    const requests = await rows(db, 'select * from subscription_request where consumed_at is null');
    expect(requests).toHaveLength(1);

    const events = await rows<{ kind: string }>(db, 'select kind from subscription_event');
    expect(events.map((e) => e.kind)).toContain('request_replaced');
  });
});

describe('remitente no disponible', () => {
  it('falla de forma cerrada y no deja rastro de la solicitud', async () => {
    const db = createTestDb();
    const result = await subscribe(context(db, new NullSender()));
    expect(result).toEqual({ ok: false, rejection: 'sender_unavailable' });

    const requests = await rows(db, 'select * from subscription_request');
    expect(requests).toHaveLength(0);
    const subscriptions = await rows(db, 'select * from subscription');
    expect(subscriptions).toHaveLength(0);
  });

  it('un fallo del remitente real tampoco deja la solicitud a medias', async () => {
    const db = createTestDb();
    const sender = new FakeSender();
    sender.shouldFail = true;
    const result = await subscribe(context(db, sender));
    expect(result.ok).toBe(false);
    expect(await rows(db, 'select * from subscription_request')).toHaveLength(0);
  });
});

describe('trampas contra envíos automáticos', () => {
  it('el campo señuelo relleno no guarda nada y no lo dice', async () => {
    const db = createTestDb();
    const sender = new FakeSender();
    const result = await subscribe(context(db, sender), form({ sitio_web: 'https://spam.example' }));
    expect(result).toEqual({ ok: true, status: 'pending' });
    expect(await rows(db, 'select * from subscription_request')).toHaveLength(0);
    expect(sender.sent).toHaveLength(0);
  });

  it('un envío inmediato no guarda nada', async () => {
    const db = createTestDb();
    const sender = new FakeSender();
    const ctx = context(db, sender);
    const body = form({ rendered_at: String(ctx.now.getTime() - 200) });
    const result = await subscribe(ctx, body);
    expect(result).toEqual({ ok: true, status: 'pending' });
    expect(await rows(db, 'select * from subscription_request')).toHaveLength(0);
  });

  it('un envío con tiempo razonable sí se guarda', async () => {
    const db = createTestDb();
    const ctx = context(db);
    const body = form({ rendered_at: String(ctx.now.getTime() - 30_000) });
    await subscribe(ctx, body);
    expect(await rows(db, 'select * from subscription_request')).toHaveLength(1);
  });
});

describe('caudal', () => {
  it('corta tras el máximo por dirección y no guarda la IP', async () => {
    const db = createTestDb();
    let last = await subscribe(context(db));
    for (let i = 0; i < RATE_MAX_PER_EMAIL + 2; i += 1) {
      last = await subscribe(context(db, new FakeSender(), `203.0.113.${i}`));
    }
    expect(last).toEqual({ ok: false, rejection: 'rate_limited' });

    const buckets = await rows<{ key_hmac: string }>(db, 'select key_hmac from rate_bucket');
    expect(buckets.length).toBeGreaterThan(0);
    for (const bucket of buckets) {
      expect(bucket.key_hmac).toMatch(/^[0-9a-f]{64}$/);
      expect(bucket.key_hmac).not.toContain('203.0.113');
      expect(bucket.key_hmac).not.toContain('lab@');
    }
  });
});

describe('supresión', () => {
  it('una dirección sin historia no está suprimida', async () => {
    const db = createTestDb();
    expect(await isSuppressed(db, 'lab@ejemplo.cl')).toBe(false);
  });
});

describe('token de baja', () => {
  it('se deriva del identificador y no de la dirección', async () => {
    const a = await deriveUnsubscribeToken('11111111-1111-4111-8111-111111111111', PEPPER);
    const b = await deriveUnsubscribeToken('22222222-2222-4222-8222-222222222222', PEPPER);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    expect(a).not.toBe(b);
    /* Sin la clave secreta no se puede reproducir. */
    const otra = await deriveUnsubscribeToken('11111111-1111-4111-8111-111111111111', 'otra');
    expect(otra).not.toBe(a);
  });
});
