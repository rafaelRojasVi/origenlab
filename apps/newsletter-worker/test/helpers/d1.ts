/**
 * Adaptador D1 sobre `node:sqlite`.
 *
 * Las pruebas corren contra SQLite de verdad y contra la migración de verdad,
 * no contra un doble que finge saber SQL. Así una restricción del esquema, como
 * el índice único de una suscripción viva por dirección, se comprueba donde
 * está escrita en vez de repetirse en una aserción.
 */
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const migrations = join(dirname(fileURLToPath(import.meta.url)), '../../migrations/0001_init.sql');

class Statement {
  constructor(
    private readonly db: DatabaseSync,
    private readonly sql: string,
    private readonly args: unknown[] = [],
  ) {}

  bind(...args: unknown[]): Statement {
    return new Statement(this.db, this.sql, args);
  }

  async first<T>(): Promise<T | null> {
    const row = this.db.prepare(this.sql).get(...(this.args as never[]));
    return (row as T | undefined) ?? null;
  }

  async all<T>(): Promise<{ results: T[] }> {
    return { results: this.db.prepare(this.sql).all(...(this.args as never[])) as T[] };
  }

  async run(): Promise<{ success: true }> {
    this.db.prepare(this.sql).run(...(this.args as never[]));
    return { success: true };
  }
}

export function createTestDb(): D1Database {
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(migrations, 'utf8'));
  return {
    prepare: (sql: string) => new Statement(db, sql),
    async batch(statements: Statement[]) {
      const out = [];
      for (const statement of statements) out.push(await statement.run());
      return out;
    },
  } as unknown as D1Database;
}

/** Lectura directa, sólo para comprobar en las pruebas lo que quedó guardado. */
export async function rows<T>(db: D1Database, sql: string, ...args: unknown[]): Promise<T[]> {
  const statement = args.length > 0 ? db.prepare(sql).bind(...args) : db.prepare(sql);
  const result = await statement.all<T>();
  return result.results;
}
