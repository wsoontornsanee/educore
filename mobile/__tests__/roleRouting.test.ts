import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { isParent, isStaff } from '../src/services/roleRouting.ts';
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

  it('routes nobody to the parent tree without a parent role', () => {
    assert.strictEqual(isParent(user([])), false);
    assert.strictEqual(isParent(null), false);
  });
});
