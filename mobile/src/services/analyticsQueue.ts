/**
 * SQLite-backed Offline Analytics Event Queue (spec/08 §5, spec/15 RPT-015).
 *
 * Enqueues product analytics events and batch-syncs them FIFO to
 * /analytics/events/. Never blocks or throws on the caller — analytics must
 * never break the screen it instruments.
 */
import { apiClient } from './api.ts';

export interface AnalyticsQueueItem {
  id: string;
  event_name: string;
  school_id: number | null;
  occurred_at: string;
  status: 'PENDING' | 'SYNCING' | 'SYNCED' | 'FAILED';
  attempts: number;
  created_at: string;
  last_error: string | null;
}

const memoryQueue: Map<string, AnalyticsQueueItem> = new Map();

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Deterministic non-cryptographic hash (djb2) over a string. Used to derive a
 * stable Idempotency-Key from batch membership so retrying the SAME batch
 * (e.g. after a lost response) reuses the SAME key, while a batch with
 * different/additional rows gets a different key.
 */
function djb2Hash(input: string): string {
  let hash = 5381;
  for (let i = 0; i < input.length; i++) {
    hash = (hash * 33) ^ input.charCodeAt(i);
  }
  // Force unsigned 32-bit, hex-encode.
  return (hash >>> 0).toString(16);
}

function batchIdempotencyKey(items: AnalyticsQueueItem[]): string {
  const membership = items
    .map((item) => item.id)
    .sort()
    .join(',');
  return `analytics-batch-${djb2Hash(membership)}`;
}

// Guards against overlapping syncPendingEvents() calls (e.g. track() firing
// several events in quick succession) selecting and POSTing the same rows.
let syncInFlight = false;

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

export async function initAnalyticsQueueDb(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.execSync === 'function') {
    try {
      sqliteDb.execSync(`
        CREATE TABLE IF NOT EXISTS analytics_event_queue (
          id TEXT PRIMARY KEY,
          event_name TEXT,
          school_id INTEGER,
          occurred_at TEXT,
          status TEXT,
          attempts INTEGER,
          created_at TEXT,
          last_error TEXT
        );
      `);
      // A row stuck in SYNCING can only mean a previous run was killed
      // mid-sync (this is a single-threaded JS runtime, so nothing else
      // could legitimately still hold that state across an app restart).
      // Reset it to PENDING so it gets retried.
      sqliteDb.execSync(`UPDATE analytics_event_queue SET status = 'PENDING' WHERE status = 'SYNCING';`);
      return;
    } catch {
      // Fallback
    }
  }
}

export async function enqueueEvent(eventName: string, schoolId: number | null): Promise<void> {
  const id = generateUUID();
  const now = new Date().toISOString();
  const item: AnalyticsQueueItem = {
    id,
    event_name: eventName,
    school_id: schoolId,
    occurred_at: now,
    status: 'PENDING',
    attempts: 0,
    created_at: now,
    last_error: null,
  };

  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(
        `INSERT INTO analytics_event_queue (id, event_name, school_id, occurred_at, status, attempts, created_at, last_error)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        [item.id, item.event_name, item.school_id, item.occurred_at, item.status, item.attempts, item.created_at, item.last_error]
      );
      return;
    } catch {
      // Fallback to memoryQueue
    }
  }

  memoryQueue.set(id, item);
}

async function getPendingEvents(): Promise<AnalyticsQueueItem[]> {
  if (sqliteDb && typeof sqliteDb.getAllSync === 'function') {
    try {
      const rows: any[] = sqliteDb.getAllSync(
        `SELECT * FROM analytics_event_queue WHERE status IN ('PENDING', 'FAILED') ORDER BY created_at ASC`
      );
      return rows.map((r) => ({ ...r }));
    } catch {
      // Fallback to memoryQueue
    }
  }

  return Array.from(memoryQueue.values())
    .filter((i) => i.status === 'PENDING' || i.status === 'FAILED')
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export async function getPendingEventCount(): Promise<number> {
  const items = await getPendingEvents();
  return items.length;
}

async function markEventStatus(id: string, status: AnalyticsQueueItem['status'], error: string | null = null): Promise<void> {
  const isAttempt = status === 'FAILED' || status === 'SYNCED';
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(
        `UPDATE analytics_event_queue SET status = ?, attempts = attempts + ?, last_error = ? WHERE id = ?`,
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
    if (isAttempt) item.attempts += 1;
    item.last_error = error;
  }
}

async function clearSyncedEvents(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(`DELETE FROM analytics_event_queue WHERE status = 'SYNCED'`);
      return;
    } catch {
      // Fallback
    }
  }

  for (const [key, item] of memoryQueue.entries()) {
    if (item.status === 'SYNCED') memoryQueue.delete(key);
  }
}

export async function clearAllAnalyticsForTesting(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(`DELETE FROM analytics_event_queue`);
    } catch {
      // Fallback
    }
  }
  memoryQueue.clear();
  // Defense-in-depth for test isolation: a prior test's fire-and-forget
  // track() call could still be mid-sync when this runs.
  syncInFlight = false;
}

export async function syncPendingEvents(): Promise<{ total: number; succeeded: number; failed: number }> {
  if (syncInFlight) {
    // A sync is already running; let it own the current pending set instead
    // of racing to select and POST the same rows twice.
    return { total: 0, succeeded: 0, failed: 0 };
  }
  syncInFlight = true;

  try {
    const pending = await getPendingEvents();
    if (pending.length === 0) {
      return { total: 0, succeeded: 0, failed: 0 };
    }

    for (const item of pending) {
      await markEventStatus(item.id, 'SYNCING');
    }

    const batchPayload = {
      events: pending.map((item) => ({
        event_name: item.event_name,
        school_id: item.school_id,
        occurred_at: item.occurred_at,
      })),
    };

    try {
      await apiClient.post('/analytics/events/', batchPayload, {
        headers: { 'Idempotency-Key': batchIdempotencyKey(pending) },
      });

      for (const item of pending) {
        await markEventStatus(item.id, 'SYNCED');
      }
      await clearSyncedEvents();

      return { total: pending.length, succeeded: pending.length, failed: 0 };
    } catch (err: any) {
      const errorMessage = err?.response?.data?.error || err.message || 'Sync failed';
      for (const item of pending) {
        await markEventStatus(item.id, 'FAILED', errorMessage);
      }
      return { total: pending.length, succeeded: 0, failed: pending.length };
    }
  } finally {
    syncInFlight = false;
  }
}
