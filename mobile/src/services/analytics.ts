/**
 * Product analytics tracking (spec/08 §5). Fire-and-forget: never throws,
 * never awaited by callers, never blocks the screen it instruments.
 */
import { enqueueEvent, syncPendingEvents } from './analyticsQueue.ts';

export type AnalyticsEventName =
  | 'app_open'
  | 'child_switch'
  | 'invoice_view'
  | 'pay_start'
  | 'pay_method_selected'
  | 'pay_intent_created'
  | 'pay_completed'
  | 'topup_completed'
  | 'notification_opened';

export function track(eventName: AnalyticsEventName, schoolId: number | null = null): void {
  enqueueEvent(eventName, schoolId)
    .then(() => syncPendingEvents())
    .catch(() => {
      // Analytics must never surface an error to the UI.
    });
}
