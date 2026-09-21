import { describe, expect, it, beforeEach } from 'vitest';
import { createTestDb, rows } from './helpers/d1';
import worker from '../src/index';
import { deriveUnsubscribeToken, handleConfirm, handleSubscribe } from '../src/handlers';
import { parseSubscribe } from '../src/parse';
import { FakeSender } from '../src/email';
import type { Env } from '../src/types';
import { MAX_BODY_BYTES } from '../src/config';

const ORIGIN = 'https://origenlab.cl';
const PEPPER = 'pimienta-de-prueba';

function env(db: D1Database, sender = 'null'): Env {
  return {
    DB: db,
    ORIGENLAB_SITE_ORIGIN: ORIGIN,
    ORIGENLAB_EMAIL_SENDER: sender,
    ORIGENLAB_TOKEN_PEPPER: PEPPER,
    ORIGENLAB_RATE_LIMIT_KEY: 'clave-de-caudal',
  };
}

function post(path: string, body: string, headers: Record<string, string> = {}): Request {
  return new Request(`${ORIGIN}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', ...headers },
    body,
  });
}

describe('método y origen', () => {
  let db: D1Database;
  beforeEach(() => {
    db = createTestDb();
  });

  it('un GET a /subscribe no hace nada', async () => {
    const response = await worker.fetch(new Request(`${ORIGIN}/api/newsletter/subscribe`), env(db));
    expect(response.status).toBe(405);
  });

  it('rechaza un envío desde otro origen', async () => {
    const response = await worker.fetch(
      post('/api/newsletter/subscribe', 'email=lab%40ejemplo.cl', { origin: 'https://otro.example' }),
      env(db),
    );
    expect(response.status).toBe(403);
  });

  it('no devuelve ninguna cabecera CORS', async () => {
    const response = await worker.fetch(
      post('/api/newsletter/subscribe', 'email=lab%40ejemplo.cl', { origin: ORIGIN }),
      env(db),
    );
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
  });

  it('rechaza un cuerpo desmesurado', async () => {
    const body = `email=lab%40ejemplo.cl&nombre=${'a'.repeat(MAX_BODY_BYTES + 100)}`;
    const response = await worker.fetch(
      post('/api/newsletter/subscribe', body, { accept: 'application/json' }),
      env(db),
    );
    expect(response.status).toBe(413);
  });

  it('sin secretos configurados no opera', async () => {
    const incomplete = { ...env(db), ORIGENLAB_TOKEN_PEPPER: undefined };
    const response = await worker.fetch(
      post('/api/newsletter/subscribe', 'email=lab%40ejemplo.cl'),
      incomplete,
    );
    expect(response.status).toBe(503);
  });

  it('las respuestas no se guardan en caché ni se indexan', async () => {
    const response = await worker.fetch(
      post('/api/newsletter/subscribe', 'email=mal'),
      env(db),
    );
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('x-robots-tag')).toBe('noindex, nofollow');
    expect(response.headers.get('referrer-policy')).toBe('no-referrer');
  });
});

describe('remitente nulo en producción', () => {
  it('responde 503 y no finge haber registrado nada', async () => {
    const db = createTestDb();
    const response = await worker.fetch(
      post(
        '/api/newsletter/subscribe',
        new URLSearchParams({
          email: 'lab@ejemplo.cl',
          consentimiento: 'si',
          consent_version: '2026-09-21',
          source: 'web_home',
        }).toString(),
        { accept: 'application/json' },
      ),
      env(db, 'null'),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ status: 'rejected', reason: 'sender_unavailable' });
    expect(await rows(db, 'select * from subscription_request')).toHaveLength(0);
  });

  it('un remitente desconocido no se interpreta con optimismo', async () => {
    const db = createTestDb();
    const response = await worker.fetch(
      post(
        '/api/newsletter/subscribe',
        new URLSearchParams({
          email: 'lab@ejemplo.cl',
          consentimiento: 'si',
          consent_version: '2026-09-21',
        }).toString(),
        { accept: 'application/json' },
      ),
      env(db, 'proveedor-que-no-existe'),
    );
    expect(response.status).toBe(503);
  });
});

describe('camino sin JavaScript', () => {
  it('un envío inválido redirige a la página de fallo, no a la de éxito', async () => {
    const db = createTestDb();
    const response = await worker.fetch(post('/api/newsletter/subscribe', 'email=mal'), env(db));
    expect(response.status).toBe(303);
    expect(response.headers.get('location')).toBe(`${ORIGIN}/newsletter/no-enviado/`);
  });

  it('un remitente caído redirige a la página de fallo', async () => {
    const db = createTestDb();
    const response = await worker.fetch(
      post(
        '/api/newsletter/subscribe',
        new URLSearchParams({
          email: 'lab@ejemplo.cl',
          consentimiento: 'si',
          consent_version: '2026-09-21',
        }).toString(),
      ),
      env(db),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get('location')).toBe(`${ORIGIN}/newsletter/no-enviado/`);
  });
});

describe('confirmación por HTTP', () => {
  let db: D1Database;
  let sender: FakeSender;
  let token: string;

  beforeEach(async () => {
    db = createTestDb();
    sender = new FakeSender();
    const parsed = parseSubscribe(
      new URLSearchParams({
        email: 'lab@ejemplo.cl',
        consentimiento: 'si',
        consent_version: '2026-09-21',
        source: 'web_home',
      }).toString(),
    );
    if (!parsed.ok) throw new Error('cuerpo inválido');
    await handleSubscribe(
      {
        db,
        sender,
        pepper: PEPPER,
        rateSecret: 'clave-de-caudal',
        siteOrigin: ORIGIN,
        clientId: '203.0.113.7',
        now: new Date(),
      },
      parsed.input,
      parsed.emailNorm,
    );
    token = new URL(sender.sent[0]!.confirmUrl).searchParams.get('t')!;
  });

  it('el GET dibuja un botón y no confirma nada', async () => {
    const response = await worker.fetch(
      new Request(`${ORIGIN}/api/newsletter/confirm?t=${token}`),
      env(db),
    );
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain('method="post"');
    expect(html).toContain('Confirmar mi suscripción');
    expect(await rows(db, 'select * from subscription')).toHaveLength(0);
  });

  it('el GET responde igual con un token inventado', async () => {
    const real = await worker.fetch(
      new Request(`${ORIGIN}/api/newsletter/confirm?t=${token}`),
      env(db),
    );
    const fake = await worker.fetch(
      new Request(`${ORIGIN}/api/newsletter/confirm?t=${'0'.repeat(64)}`),
      env(db),
    );
    expect(real.status).toBe(fake.status);
    const a = (await real.text()).replace(token, 'X');
    const b = (await fake.text()).replace('0'.repeat(64), 'X');
    expect(a).toBe(b);
  });

  it('el POST confirma y redirige a la página de confirmada', async () => {
    const response = await worker.fetch(
      post('/api/newsletter/confirm', `token=${token}`),
      env(db),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get('location')).toBe(`${ORIGIN}/newsletter/confirmada/`);
    expect(await rows(db, 'select * from subscription')).toHaveLength(1);
  });

  it('un token inválido lleva a la página de enlace no válido', async () => {
    const response = await worker.fetch(
      post('/api/newsletter/confirm', 'token=no-vale'),
      env(db),
    );
    expect(response.headers.get('location')).toBe(`${ORIGIN}/newsletter/enlace-no-valido/`);
  });
});

describe('baja por HTTP', () => {
  let db: D1Database;
  let unsubToken: string;

  beforeEach(async () => {
    db = createTestDb();
    const sender = new FakeSender();
    const parsed = parseSubscribe(
      new URLSearchParams({
        email: 'lab@ejemplo.cl',
        consentimiento: 'si',
        consent_version: '2026-09-21',
      }).toString(),
    );
    if (!parsed.ok) throw new Error('cuerpo inválido');
    const context = {
      db,
      sender,
      pepper: PEPPER,
      rateSecret: 'clave-de-caudal',
      siteOrigin: ORIGIN,
      clientId: '203.0.113.7',
      now: new Date(),
    };
    await handleSubscribe(context, parsed.input, parsed.emailNorm);
    const confirmToken = new URL(sender.sent[0]!.confirmUrl).searchParams.get('t')!;
    await handleConfirm(context, confirmToken);
    const subs = await rows<{ id: string }>(db, 'select id from subscription');
    unsubToken = await deriveUnsubscribeToken(subs[0]!.id, PEPPER);
  });

  it('el GET no da de baja a nadie', async () => {
    const response = await worker.fetch(
      new Request(`${ORIGIN}/api/newsletter/unsubscribe?t=${unsubToken}`),
      env(db),
    );
    expect(response.status).toBe(200);
    expect(await response.text()).toContain('Darme de baja');

    const subs = await rows<{ revoked_at: string | null }>(db, 'select revoked_at from subscription');
    expect(subs[0]!.revoked_at).toBeNull();
    expect(await rows(db, 'select * from suppression')).toHaveLength(0);
  });

  it('el POST del navegador da de baja y redirige', async () => {
    const response = await worker.fetch(
      post('/api/newsletter/unsubscribe', `token=${unsubToken}`),
      env(db),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get('location')).toBe(`${ORIGIN}/newsletter/baja-confirmada/`);
    expect(await rows(db, 'select * from suppression')).toHaveLength(1);
  });

  it('el POST de un clic de RFC 8058 da de baja y no redirige', async () => {
    const response = await worker.fetch(
      new Request(`${ORIGIN}/api/newsletter/unsubscribe?t=${unsubToken}`, {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: 'List-Unsubscribe=One-Click',
      }),
      env(db),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get('location')).toBeNull();
    expect(await response.text()).toBe('unsubscribed');
    expect(await rows(db, 'select * from suppression')).toHaveLength(1);
  });

  it('los dos caminos dejan exactamente el mismo rastro', async () => {
    const dbOneClick = db;
    await worker.fetch(
      new Request(`${ORIGIN}/api/newsletter/unsubscribe?t=${unsubToken}`, {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: 'List-Unsubscribe=One-Click',
      }),
      env(dbOneClick),
    );
    const suppression = await rows<{ reason: string }>(db, 'select reason from suppression');
    const events = await rows<{ kind: string }>(
      db,
      "select kind from subscription_event where kind = 'unsubscribed'",
    );
    expect(suppression[0]!.reason).toBe('unsubscribe_request');
    expect(events).toHaveLength(1);
  });
});

describe('ruta desconocida', () => {
  it('no existe nada más bajo /api/newsletter', async () => {
    const db = createTestDb();
    const response = await worker.fetch(
      new Request(`${ORIGIN}/api/newsletter/lista`),
      env(db),
    );
    expect(response.status).toBe(404);
  });
});
