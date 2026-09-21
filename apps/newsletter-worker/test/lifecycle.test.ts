import { describe, expect, it, beforeEach } from 'vitest';
import { createTestDb, rows } from './helpers/d1';
import { FakeSender } from '../src/email';
import {
  deriveUnsubscribeToken,
  handleConfirm,
  handleSubscribe,
  handleUnsubscribe,
  type HandlerContext,
} from '../src/handlers';
import { parseSubscribe } from '../src/parse';
import { isSuppressed } from '../src/db';
import { CONFIRM_TTL_HOURS } from '../src/config';

const PEPPER = 'pimienta-de-prueba';

function context(db: D1Database, sender: FakeSender, now: Date): HandlerContext {
  return {
    db,
    sender,
    pepper: PEPPER,
    rateSecret: 'clave-de-caudal',
    siteOrigin: 'https://origenlab.cl',
    clientId: '203.0.113.7',
    now,
  };
}

function body(email = 'lab@ejemplo.cl'): string {
  return new URLSearchParams({
    email,
    consentimiento: 'si',
    consent_version: '2026-09-21',
    source: 'web_newsletter_page',
  }).toString();
}

async function requestSubscription(db: D1Database, sender: FakeSender, now: Date, email?: string) {
  const parsed = parseSubscribe(body(email));
  if (!parsed.ok) throw new Error('cuerpo de prueba inválido');
  await handleSubscribe(context(db, sender, now), parsed.input, parsed.emailNorm);
  const last = sender.sent.at(-1)!;
  return new URL(last.confirmUrl).searchParams.get('t')!;
}

const T0 = new Date('2026-09-21T12:00:00.000Z');
const later = (hours: number) => new Date(T0.getTime() + hours * 3600_000);

describe('confirmación', () => {
  let db: D1Database;
  let sender: FakeSender;
  beforeEach(() => {
    db = createTestDb();
    sender = new FakeSender();
  });

  it('crea la suscripción y deja constancia de lo aceptado', async () => {
    const token = await requestSubscription(db, sender, T0);
    const result = await handleConfirm(context(db, sender, later(1)), token);
    expect(result.status).toBe('confirmed');

    const subs = await rows<{
      email_norm: string;
      consent_text_version: string;
      consent_at: string;
      confirmed_at: string;
      source: string;
    }>(db, 'select * from subscription');
    expect(subs).toHaveLength(1);
    expect(subs[0]!.email_norm).toBe('lab@ejemplo.cl');
    expect(subs[0]!.consent_text_version).toBe('2026-09-21');
    expect(subs[0]!.consent_at).toBe(T0.toISOString());
    expect(subs[0]!.confirmed_at).toBe(later(1).toISOString());
    expect(subs[0]!.source).toBe('web_newsletter_page');
  });

  it('el enlace sirve una sola vez', async () => {
    const token = await requestSubscription(db, sender, T0);
    expect((await handleConfirm(context(db, sender, later(1)), token)).status).toBe('confirmed');
    expect((await handleConfirm(context(db, sender, later(2)), token)).status).toBe('invalid');
    expect(await rows(db, 'select * from subscription')).toHaveLength(1);
  });

  it('el enlace caduca', async () => {
    const token = await requestSubscription(db, sender, T0);
    const result = await handleConfirm(context(db, sender, later(CONFIRM_TTL_HOURS + 1)), token);
    expect(result.status).toBe('invalid');
    expect(await rows(db, 'select * from subscription')).toHaveLength(0);
  });

  it('un token inventado no confirma nada', async () => {
    await requestSubscription(db, sender, T0);
    for (const token of ['', 'no-es-un-token', 'f'.repeat(64), 'F'.repeat(64)]) {
      expect((await handleConfirm(context(db, sender, later(1)), token)).status).toBe('invalid');
    }
    expect(await rows(db, 'select * from subscription')).toHaveLength(0);
  });

  it('confirmar dos veces con enlaces distintos no duplica la suscripción', async () => {
    const first = await requestSubscription(db, sender, T0);
    await handleConfirm(context(db, sender, later(1)), first);
    const second = await requestSubscription(db, sender, later(2));
    const result = await handleConfirm(context(db, sender, later(3)), second);
    expect(result.status).toBe('already_confirmed');
    expect(await rows(db, 'select * from subscription')).toHaveLength(1);
  });
});

describe('baja', () => {
  let db: D1Database;
  let sender: FakeSender;

  async function confirmed(): Promise<string> {
    const token = await requestSubscription(db, sender, T0);
    await handleConfirm(context(db, sender, later(1)), token);
    const subs = await rows<{ id: string }>(db, 'select id from subscription');
    return deriveUnsubscribeToken(subs[0]!.id, PEPPER);
  }

  beforeEach(() => {
    db = createTestDb();
    sender = new FakeSender();
  });

  it('da de baja en un paso y deja la supresión', async () => {
    const token = await confirmed();
    const result = await handleUnsubscribe(context(db, sender, later(5)), token);
    expect(result.status).toBe('unsubscribed');

    const subs = await rows<{ revoked_at: string | null; revoked_reason: string | null }>(
      db,
      'select revoked_at, revoked_reason from subscription',
    );
    expect(subs[0]!.revoked_at).toBe(later(5).toISOString());
    expect(subs[0]!.revoked_reason).toBe('unsubscribe_request');

    const suppressions = await rows(db, 'select * from suppression');
    expect(suppressions).toHaveLength(1);
    expect(await isSuppressed(db, 'lab@ejemplo.cl')).toBe(true);
  });

  it('es idempotente y no acumula supresiones', async () => {
    const token = await confirmed();
    await handleUnsubscribe(context(db, sender, later(5)), token);
    const again = await handleUnsubscribe(context(db, sender, later(6)), token);
    expect(again.status).toBe('already_unsubscribed');
    expect(await rows(db, 'select * from suppression')).toHaveLength(1);
  });

  it('un token de baja ajeno o inventado no da de baja a nadie', async () => {
    await confirmed();
    const ajeno = await deriveUnsubscribeToken('00000000-0000-4000-8000-000000000000', PEPPER);
    expect((await handleUnsubscribe(context(db, sender, later(5)), ajeno)).status).toBe('invalid');
    expect(await isSuppressed(db, 'lab@ejemplo.cl')).toBe(false);
  });

  it('sin la clave secreta el token no se puede fabricar', async () => {
    await confirmed();
    const subs = await rows<{ id: string }>(db, 'select id from subscription');
    const falsificado = await deriveUnsubscribeToken(subs[0]!.id, 'otra-clave');
    expect((await handleUnsubscribe(context(db, sender, later(5)), falsificado)).status).toBe(
      'invalid',
    );
    expect(await isSuppressed(db, 'lab@ejemplo.cl')).toBe(false);
  });

  it('la base no guarda ningún token de baja en claro', async () => {
    const token = await confirmed();
    const stored = await rows<{ unsubscribe_token_hash: string }>(
      db,
      'select unsubscribe_token_hash from subscription',
    );
    expect(stored[0]!.unsubscribe_token_hash).not.toBe(token);
    expect(stored[0]!.unsubscribe_token_hash).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe('volver a suscribirse', () => {
  it('no borra la supresión ni la historia anterior', async () => {
    const db = createTestDb();
    const sender = new FakeSender();

    const first = await requestSubscription(db, sender, T0);
    await handleConfirm(context(db, sender, later(1)), first);
    const subs = await rows<{ id: string }>(db, 'select id from subscription');
    const unsubToken = await deriveUnsubscribeToken(subs[0]!.id, PEPPER);
    await handleUnsubscribe(context(db, sender, later(5)), unsubToken);
    expect(await isSuppressed(db, 'lab@ejemplo.cl')).toBe(true);

    /* Alta nueva, explícita, mucho después. */
    const second = await requestSubscription(db, sender, later(100));
    await handleConfirm(context(db, sender, later(101)), second);

    /* La supresión sigue ahí como evidencia. */
    expect(await rows(db, 'select * from suppression')).toHaveLength(1);
    /* Y las dos suscripciones también: la revocada y la nueva. */
    const all = await rows<{ revoked_at: string | null }>(db, 'select revoked_at from subscription');
    expect(all).toHaveLength(2);
    expect(all.filter((row) => row.revoked_at === null)).toHaveLength(1);
    /* La confirmación posterior a la supresión vuelve a hacerla elegible. */
    expect(await isSuppressed(db, 'lab@ejemplo.cl')).toBe(false);
  });

  it('una suscripción viva no puede duplicarse en la base', async () => {
    const db = createTestDb();
    await db
      .prepare(
        `insert into subscription (id, email_norm, interests, consent_text_version, consent_at,
           confirmed_at, source, request_id, unsubscribe_token_hash, created_at)
         values (?, ?, '[]', 'v', 't', 't', 's', 'r', ?, 't')`,
      )
      .bind('a', 'lab@ejemplo.cl', 'hash-a')
      .run();

    await expect(
      db
        .prepare(
          `insert into subscription (id, email_norm, interests, consent_text_version, consent_at,
             confirmed_at, source, request_id, unsubscribe_token_hash, created_at)
           values (?, ?, '[]', 'v', 't', 't', 's', 'r', ?, 't')`,
        )
        .bind('b', 'lab@ejemplo.cl', 'hash-b')
        .run(),
    ).rejects.toThrow();
  });
});

describe('registro de sucesos', () => {
  it('deja una traza legible del ciclo completo', async () => {
    const db = createTestDb();
    const sender = new FakeSender();
    const token = await requestSubscription(db, sender, T0);
    await handleConfirm(context(db, sender, later(1)), token);
    const subs = await rows<{ id: string }>(db, 'select id from subscription');
    await handleUnsubscribe(
      context(db, sender, later(5)),
      await deriveUnsubscribeToken(subs[0]!.id, PEPPER),
    );

    const events = await rows<{ kind: string }>(db, 'select kind from subscription_event order by id');
    expect(events.map((e) => e.kind)).toEqual(['requested', 'confirmed', 'unsubscribed']);
  });
});
