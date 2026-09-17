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
}

export async function syncPendingEvents(): Promise<{ total: number; succeeded: number; failed: number }> {
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
      headers: { 'Idempotency-Key': generateUUID() },
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
}
