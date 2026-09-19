/**
 * SQLite-backed Offline POS Queue (spec/07 §5 WAL-015, WAL-016, spec/12).
 *
 * Enqueues canteen POS purchases when offline with client UUID transaction IDs,
 * stores local transaction records, and replays them in FIFO order to
 * POST /pos/transactions/batch/.
 */
import { apiClient } from './api.ts';
import { classifyBatchResults, type ServerBatchResult } from './posAdapter.ts';
import type { POSOfflineTransaction } from '../types/index.ts';

// In-memory store fallback for node/jest environment
const memoryPosQueue: Map<string, POSOfflineTransaction> = new Map();

export function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

let sqliteDb: any = null;
try {
  const SQLite = require('expo-sqlite');
  if (SQLite && typeof SQLite.openDatabaseSync === 'function') {
    sqliteDb = SQLite.openDatabaseSync('educore_offline.db');
  } else if (SQLite && typeof SQLite.openDatabase === 'function') {
    sqliteDb = SQLite.openDatabase('educore_offline.db');
  }
} catch {
  // Fallback to memoryPosQueue
}

export async function initPosQueueDb(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.execSync === 'function') {
    try {
      sqliteDb.execSync(`
        CREATE TABLE IF NOT EXISTS pos_sync_queue (
          id TEXT PRIMARY KEY,
          client_transaction_id TEXT UNIQUE,
          terminal_id INTEGER,
          student_id INTEGER,
          student_name TEXT,
          items_json TEXT,
          subtotal REAL,
          total REAL,
          occurred_at TEXT,
          status TEXT,
          attempts INTEGER,
          created_at TEXT,
          last_error TEXT,
          qr_token TEXT
        );
      `);
      // Installs created before QR tokens existed have the table without the column.
      const columns = (sqliteDb.getAllSync?.(`PRAGMA table_info(pos_sync_queue)`) ?? []) as Array<{ name: string }>;
      if (columns.length > 0 && !columns.some((c) => c.name === 'qr_token')) {
        sqliteDb.execSync(`ALTER TABLE pos_sync_queue ADD COLUMN qr_token TEXT;`);
      }
      return;
    } catch {
      // Fallback to memory
    }
  }
}

export async function enqueuePosTransaction(data: {
  terminal_id: number;
  student_id: number;
  student_name?: string;
  items: any[];
  subtotal: number;
  total: number;
  occurred_at?: string;
  client_transaction_id?: string;
  /** Terminal-minted offline QR token (QRS-022) the sale was paid with; the server claims its nonce at sync. */
  qr_token?: string;
}): Promise<POSOfflineTransaction> {
  const id = generateUUID();
  const client_transaction_id = data.client_transaction_id || `pos-offline-${generateUUID()}`;
  const occurred_at = data.occurred_at || new Date().toISOString();
  const created_at = new Date().toISOString();

  const item: POSOfflineTransaction = {
    id,
    client_transaction_id,
    terminal_id: data.terminal_id,
    student_id: data.student_id,
    student_name: data.student_name,
    items: data.items,
    subtotal: data.subtotal,
    total: data.total,
    occurred_at,
    status: 'PENDING',
    attempts: 0,
    created_at,
    last_error: null,
    qr_token: data.qr_token || null,
  };

  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      await initPosQueueDb();
      sqliteDb.runSync(
        `INSERT INTO pos_sync_queue (
          id, client_transaction_id, terminal_id, student_id, student_name,
          items_json, subtotal, total, occurred_at, status, attempts, created_at, last_error, qr_token
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          item.id,
          item.client_transaction_id,
          item.terminal_id,
          item.student_id,
          item.student_name || '',
          JSON.stringify(item.items),
          item.subtotal,
          item.total,
          item.occurred_at,
          item.status,
          item.attempts,
          item.created_at,
          item.last_error || '',
          item.qr_token || '',
        ]
      );
      return item;
    } catch {
      // Fall through to memory
    }
  }

  memoryPosQueue.set(item.id, item);
  return item;
}

export async function getPendingPosTransactions(): Promise<POSOfflineTransaction[]> {
  if (sqliteDb && typeof sqliteDb.getAllSync === 'function') {
    try {
      await initPosQueueDb();
      const rows = sqliteDb.getAllSync(
        `SELECT * FROM pos_sync_queue WHERE status IN ('PENDING', 'FAILED') ORDER BY created_at ASC`
      ) as any[];

      return rows.map((r) => ({
        id: r.id,
        client_transaction_id: r.client_transaction_id,
        terminal_id: r.terminal_id,
        student_id: r.student_id,
        student_name: r.student_name,
        items: JSON.parse(r.items_json || '[]'),
        subtotal: Number(r.subtotal),
        total: Number(r.total),
        occurred_at: r.occurred_at,
        status: r.status,
        attempts: Number(r.attempts),
        created_at: r.created_at,
        last_error: r.last_error || null,
        qr_token: r.qr_token || null,
      }));
    } catch {
      // Fallback to memory
    }
  }

  return Array.from(memoryPosQueue.values())
    .filter((i) => i.status === 'PENDING' || i.status === 'FAILED')
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export async function getPendingPosCount(): Promise<number> {
  const list = await getPendingPosTransactions();
  return list.length;
}

export async function updatePosItemStatus(
  id: string,
  status: 'PENDING' | 'SYNCING' | 'SYNCED' | 'FAILED',
  lastError: string | null = null
): Promise<void> {
  const shouldIncrement = status === 'FAILED';
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      const sql = shouldIncrement
        ? `UPDATE pos_sync_queue SET status = ?, last_error = ?, attempts = attempts + 1 WHERE id = ?`
        : `UPDATE pos_sync_queue SET status = ?, last_error = ? WHERE id = ?`;
      sqliteDb.runSync(
        sql,
        shouldIncrement ? [status, lastError || '', id] : [status, lastError || '', id]
      );
      return;
    } catch {
      // Fallback
    }
  }

  const existing = memoryPosQueue.get(id);
  if (existing) {
    existing.status = status;
    existing.last_error = lastError;
    if (shouldIncrement) {
      existing.attempts += 1;
    }
    memoryPosQueue.set(id, existing);
  }
}

/** The body of POST /pos/transactions/batch/. `qr_token` is sent only for sales paid with an offline QR. */
export function buildPosBatchPayload(terminalId: number, items: POSOfflineTransaction[]) {
  return {
    terminal_id: terminalId,
    transactions: items.map((t) => ({
      client_transaction_id: t.client_transaction_id,
      student_id: t.student_id,
      items: t.items.map((i) => ({
        sku: i.sku,
        name: i.name,
        qty: i.qty,
        unit_price: String(i.unit_price),
      })),
      occurred_at: t.occurred_at,
      ...(t.qr_token ? { qr_token: t.qr_token } : {}),
    })),
  };
}

export async function syncPendingPosTransactions(
  terminalId: number
): Promise<{ succeeded: number; reconciled: number; failed: number; errors: string[] }> {
  const pending = await getPendingPosTransactions();
  const itemsToSync = pending.filter((t) => t.terminal_id === terminalId);
  if (itemsToSync.length === 0) {
    return { succeeded: 0, reconciled: 0, failed: 0, errors: [] };
  }

  for (const item of itemsToSync) {
    await updatePosItemStatus(item.id, 'SYNCING');
  }

  const batchPayload = buildPosBatchPayload(terminalId, itemsToSync);

  try {
    const response = await apiClient.post<{ results: ServerBatchResult[] }>(
      '/pos/transactions/batch/',
      batchPayload
    );
    const outcome = classifyBatchResults(
      itemsToSync.map((t) => t.client_transaction_id),
      response.data.results ?? [],
    );

    // A sale the server refused stays in the queue as FAILED with the reason; it is never marked synced.
    const reasons = new Map(outcome.rejected.map((r) => [r.id, r.reason]));
    for (const item of itemsToSync) {
      const reason = reasons.get(item.client_transaction_id);
      await updatePosItemStatus(item.id, reason ? 'FAILED' : 'SYNCED', reason);
    }

    return {
      succeeded: outcome.accepted.length,
      reconciled: outcome.reconciled,
      failed: outcome.rejected.length,
      errors: outcome.rejected.map((r) => r.reason),
    };
  } catch (error: any) {
    const errorMsg = error?.response?.data?.error || error?.message || 'Network sync error';
    for (const item of itemsToSync) {
      await updatePosItemStatus(item.id, 'FAILED', errorMsg);
    }
    return {
      succeeded: 0,
      reconciled: 0,
      failed: itemsToSync.length,
      errors: [errorMsg],
    };
  }
}

export async function clearAllPosForTesting(): Promise<void> {
  memoryPosQueue.clear();
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(`DELETE FROM pos_sync_queue;`);
    } catch {
      // Ignore in tests
    }
  }
}
