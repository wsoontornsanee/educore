/**
 * Human-readable role names for the signed-in user (spec/00-overview §2 persona terms, id-ID first).
 *
 * The role codes are the backend's `RoleAssignment.ROLE_CHOICES` (`apps/identity/models.py`); a parity test
 * fails when the backend gains a role that has no label here. The names themselves live in the string map
 * (`role.<code>`), so the language switch applies to them like everything else.
 */
import { t, type Locale } from '../i18n/strings.ts';

/** Every `RoleAssignment.role`, most senior first: the order a user's roles are listed in. */
export const ROLE_DISPLAY_ORDER = [
  'foundation_admin',
  'school_admin',
  'finance_officer',
  'teacher',
  'counsellor',
  'canteen_operator',
  'clinic_officer',
  'parent',
] as const;

/** The label for one role code; a code this build does not know reads as the generic staff label. */
export function roleLabel(role: string, locale: Locale = 'id-ID'): string {
  return t(`role.${role}`, locale, t('role.unknown', locale));
}

/**
 * Every role the user holds, senior first, each once, joined with ", " (e.g. "Guru, Wali Murid"). A user
 * holds the same role in several schools as separate assignments, so duplicates are collapsed. Roles this
 * build does not know sort last; a user with no roles reads as the generic staff label.
 */
export function rolesLabel(roles: ReadonlyArray<{ role: string }> | null | undefined, locale: Locale = 'id-ID'): string {
  const held = [...new Set((roles ?? []).map((r) => r.role))];
  if (held.length === 0) return t('role.unknown', locale);
  const rank = (role: string) => {
    const index = (ROLE_DISPLAY_ORDER as readonly string[]).indexOf(role);
    return index === -1 ? ROLE_DISPLAY_ORDER.length : index;
  };
  return held.sort((a, b) => rank(a) - rank(b)).map((role) => roleLabel(role, locale)).join(', ');
}
