/**
 * Indonesian local-date helpers (id-ID first).
 *
 * `new Date().toISOString().split('T')[0]` yields the UTC calendar date, which is
 * still YESTERDAY for every moment between 00:00 and 07:00 WIB — exactly the hours
 * in which a parent checks whether their child has arrived at school.
 *
 * Intl time-zone data is not guaranteed in the Expo/Hermes runtime this app targets,
 * so the offset is applied arithmetically instead.
 */

/** WIB (Asia/Jakarta) is a fixed UTC+7 offset with no daylight saving. */
export const WIB_OFFSET_MINUTES = 7 * 60;

/** Today's calendar date in WIB, as YYYY-MM-DD. */
export function todayWib(now: Date = new Date()): string {
  const shifted = new Date(now.getTime() + WIB_OFFSET_MINUTES * 60 * 1000);
  return shifted.toISOString().split('T')[0];
}
