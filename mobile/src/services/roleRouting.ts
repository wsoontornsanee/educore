/**
 * Which app tree a signed-in user belongs in (spec/08 §2, spec/02 IAM-009).
 *
 * Mirrors the backend's precedence in apps/identity/guardian_access.py: staff wins.
 * A teacher who is also the guardian of their own child must keep the teacher app,
 * otherwise the guardian link would silently lock them out of their own workday.
 */
import type { UserProfile } from '../types/index.ts';

/** Mirrors apps.identity.guardian_access.STAFF_ROLES. */
export const STAFF_ROLES = [
  'foundation_admin',
  'school_admin',
  'finance_officer',
  'teacher',
  'counsellor',
];

export function isStaff(user: UserProfile | null): boolean {
  return !!user?.roles?.some((r) => STAFF_ROLES.includes(r.role));
}

export function isParent(user: UserProfile | null): boolean {
  if (isStaff(user)) return false;
  return !!user?.roles?.some((r) => r.role === 'parent');
}
