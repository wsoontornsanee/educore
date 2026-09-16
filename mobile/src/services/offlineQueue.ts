/**
 * SQLite-backed Offline Attendance Queue (spec/09 §3 TCH-004, docs/frontend-plan.md).
 * 
 * Enqueues period attendance submissions when offline with UUID idempotency keys
 * and replays them in oldest-first FIFO order to /api/v1/period-attendance/sync/.
 */
import { apiClient } from './api.ts';
import type { OfflineQueueItem, PeriodAttendanceEntry, SyncBatchResult } from '../types/index.ts';

export interface EnqueueResult {
  id: string;
  idempotency_key: string;
  item: OfflineQueueItem;
}

// In-memory store fallback for node/jest and testing
const memoryQueue: Map<string, OfflineQueueItem> = new Map();

// Helper to generate simple random UUID4 string
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
  // Fallback to memoryQueue
}

export async function initQueueDb(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.execSync === 'function') {
    try {
      sqliteDb.execSync(`
        CREATE TABLE IF NOT EXISTS attendance_sync_queue (
          id TEXT PRIMARY KEY,
          idempotency_key TEXT UNIQUE,
          slot_id INTEGER,
          date TEXT,
          entries_json TEXT,
          status TEXT,
          attempts INTEGER,
          created_at TEXT,
          last_error TEXT
        );
      `);
      return;
    } catch {
      // Fallback
    }
  }
}

export async function enqueueAttendance(
  slotId: number,
  date: string,
  entries: PeriodAttendanceEntry[]
): Promise<EnqueueResult> {
  const id = generateUUID();
  const idempotency_key = generateUUID();
  const now = new Date().toISOString();

  const item: OfflineQueueItem = {
    id,
    idempotency_key,
    slot_id: slotId,
    date,
    entries,
    status: 'PENDING',
    attempts: 0,
    created_at: now,
    last_error: null,
  };

  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(
        `INSERT INTO attendance_sync_queue (id, idempotency_key, slot_id, date, entries_json, status, attempts, created_at, last_error)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          item.id,
          item.idempotency_key,
          item.slot_id,
          item.date,
          JSON.stringify(item.entries),
          item.status,
          item.attempts,
          item.created_at,
          item.last_error,
        ]
      );
      return { id, idempotency_key, item };
    } catch {
      // Fallback to memoryQueue
    }
  }

  memoryQueue.set(id, item);
  return { id, idempotency_key, item };
}

export async function getPendingQueue(): Promise<OfflineQueueItem[]> {
  if (sqliteDb && typeof sqliteDb.getAllSync === 'function') {
    try {
      const rows: any[] = sqliteDb.getAllSync(
        `SELECT * FROM attendance_sync_queue WHERE status IN ('PENDING', 'FAILED') ORDER BY created_at ASC`
      );
      return rows.map((r) => ({
        id: r.id,
        idempotency_key: r.idempotency_key,
        slot_id: r.slot_id,
        date: r.date,
        entries: JSON.parse(r.entries_json),
        status: r.status,
        attempts: r.attempts,
        created_at: r.created_at,
        last_error: r.last_error,
      }));
    } catch {
      // Fallback to memoryQueue
    }
  }

  const items = Array.from(memoryQueue.values())
    .filter((i) => i.status === 'PENDING' || i.status === 'FAILED')
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  return items;
}

export async function getPendingCount(): Promise<number> {
  const items = await getPendingQueue();
  return items.length;
}

export async function markItemStatus(
  id: string,
  status: 'PENDING' | 'SYNCING' | 'SYNCED' | 'FAILED',
  error: string | null = null
): Promise<void> {
  const isAttempt = status === 'FAILED' || status === 'SYNCED';
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(
        `UPDATE attendance_sync_queue 
         SET status = ?, attempts = attempts + ?, last_error = ? 
         WHERE id = ?`,
        [status, isAttempt ? 1 : 0, error, id]
      );
      return;
    } catch {
      // Fallback
    }
  }

  const item = memoryQueue.get(id);
  if (item) {
    item.status = status;
    if (isAttempt) {
      item.attempts += 1;
    }
    item.last_error = error;
  }
}

export async function clearSynced(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(`DELETE FROM attendance_sync_queue WHERE status = 'SYNCED'`);
      return;
    } catch {
      // Fallback
    }
  }

  for (const [key, item] of memoryQueue.entries()) {
    if (item.status === 'SYNCED') {
      memoryQueue.delete(key);
    }
  }
}

export async function clearAllForTesting(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(`DELETE FROM attendance_sync_queue`);
    } catch {
      // Fallback
    }
  }
  memoryQueue.clear();
}

/**
 * Replays all pending offline entries in FIFO order to the server.
 */
export async function syncPendingEntries(): Promise<{
  total: number;
  succeeded: number;
  failed: number;
}> {
  const pending = await getPendingQueue();
  if (pending.length === 0) {
    return { total: 0, succeeded: 0, failed: 0 };
  }

  // Mark all as syncing
  for (const item of pending) {
    await markItemStatus(item.id, 'SYNCING');
  }

  // Build batch payload: exceptions only for each slot per TCH-002
  const batchPayload = {
    entries: pending.map((item) => ({
      slot_id: item.slot_id,
      date: item.date,
      exceptions: item.entries
        .filter((e) => e.status !== 'HADIR')
        .map((e) => ({
          student_id: e.student_id,
          status: e.status,
        })),
    })),
  };

  try {
    const response = await apiClient.post<SyncBatchResult>(
      '/period-attendance/sync/',
      batchPayload,
      {
        headers: {
          'Idempotency-Key': generateUUID(),
        },
      }
    );

    // If HTTP 200, mark all synced
    for (const item of pending) {
      await markItemStatus(item.id, 'SYNCED');
    }
    await clearSynced();

    return {
      total: pending.length,
      succeeded: pending.length,
      failed: 0,
    };
  } catch (err: any) {
    const errorMessage = err?.response?.data?.error || err.message || 'Sync failed';
    for (const item of pending) {
      await markItemStatus(item.id, 'FAILED', errorMessage);
    }
    return {
      total: pending.length,
      succeeded: 0,
      failed: pending.length,
    };
  }
}
