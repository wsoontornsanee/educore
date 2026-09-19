import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { isClinicOfficerOnly, isParent, isStaff, STAFF_ROLES } from '../src/services/roleRouting.ts';
import type { RoleAssignment, UserProfile } from '../src/types/index.ts';

const user = (roles: string[]): UserProfile => ({
  id: 1,
  full_name: 'Uji Coba',
  phone_e164: '+6281200000000',
  email: null,
  foundation_id: 1,
  roles: roles.map((role, idx): RoleAssignment => ({
    id: idx + 1,
    role,
    scope_type: 'FOUNDATION',
    scope_id: 1,
  })),
});

describe('App tree routing precedence (IAM-009)', () => {
  it('routes a guardian-only user to the parent tree', () => {
    assert.strictEqual(isParent(user(['parent'])), true);
    assert.strictEqual(isStaff(user(['parent'])), false);
  });

  it('keeps a teacher who is also a guardian in the teacher tree', () => {
    assert.strictEqual(isParent(user(['teacher', 'parent'])), false);
    assert.strictEqual(isStaff(user(['teacher', 'parent'])), true);
  });

  it('keeps every other staff role in the teacher tree', () => {
    for (const staffRole of ['school_admin', 'finance_officer', 'counsellor', 'foundation_admin']) {
      assert.strictEqual(isParent(user([staffRole, 'parent'])), false, staffRole);
    }
  });

  it('treats a clinic officer as staff, not parent', () => {
    assert.strictEqual(isStaff(user(['clinic_officer'])), true);
    assert.strictEqual(isParent(user(['clinic_officer', 'parent'])), false);
  });

  it('STAFF_ROLES matches the backend guardian_access.STAFF_ROLES set', () => {
    const src = readFileSync(
      new URL('../../apps/identity/guardian_access.py', import.meta.url),
      'utf8',
    );
    const block = /STAFF_ROLES\s*=\s*\{([^}]*)\}/.exec(src)?.[1] ?? '';
    // RoleAssignment.ROLE_FOO = 'foo' — the constant name lower-cased is the value.
    const backend = [...block.matchAll(/RoleAssignment\.ROLE_([A-Z_]+)/g)]
      .map((m) => m[1].toLowerCase())
      .sort();
    assert.ok(backend.length > 0, 'failed to parse backend STAFF_ROLES');
    assert.deepStrictEqual([...STAFF_ROLES].sort(), backend);
  });

  it('sends a clinic officer with no other staff role to the clinic app', () => {
    assert.strictEqual(isClinicOfficerOnly(user(['clinic_officer'])), true);
    // A guardian role does not change it: staff wins, and the only staff role is clinic_officer.
    assert.strictEqual(isClinicOfficerOnly(user(['clinic_officer', 'parent'])), true);
  });

  it('keeps anyone with another staff role in the teacher tree', () => {
    for (const other of ['teacher', 'counsellor', 'school_admin', 'foundation_admin', 'finance_officer', 'canteen_operator']) {
      assert.strictEqual(isClinicOfficerOnly(user(['clinic_officer', other])), false, other);
    }
  });

  it('is not a clinic officer without the role, without roles, or when signed out', () => {
    assert.strictEqual(isClinicOfficerOnly(user(['teacher'])), false);
    assert.strictEqual(isClinicOfficerOnly(user(['parent'])), false);
    assert.strictEqual(isClinicOfficerOnly(user([])), false);
    assert.strictEqual(isClinicOfficerOnly(null), false);
  });

  it('routes nobody to the parent tree without a parent role', () => {
    assert.strictEqual(isParent(user([])), false);
    assert.strictEqual(isParent(null), false);
  });
});
