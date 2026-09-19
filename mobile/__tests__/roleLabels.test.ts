import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ROLE_DISPLAY_ORDER, roleLabel, rolesLabel } from '../src/services/roleLabels.ts';
import { STAFF_ROLES } from '../src/services/roleRouting.ts';

const roles = (...codes: string[]) => codes.map((role) => ({ role }));

/** The `RoleAssignment.ROLE_*` values in apps/identity/models.py, in declaration order. */
function backendRoles(): string[] {
  const src = readFileSync(new URL('../../apps/identity/models.py', import.meta.url), 'utf8');
  const start = src.indexOf('class RoleAssignment(');
  const end = src.indexOf('user = models.ForeignKey(User, on_delete=models.CASCADE, related_name=\'role_assignments\')', start);
  assert.ok(start > -1 && end > start, 'failed to locate RoleAssignment in the backend model');
  const choices = /ROLE_CHOICES\s*=\s*\[([^\]]*)\]/.exec(src.slice(start, end))?.[1] ?? '';
  return [...choices.matchAll(/\(ROLE_([A-Z_]+),/g)].map((m) => m[1].toLowerCase());
}

describe('Role labels', () => {
  it('has a label in both languages for every role the backend can assign', () => {
    const backend = backendRoles();
    assert.ok(backend.length >= 8, 'failed to parse the backend role list');
    for (const role of backend) {
      for (const locale of ['id-ID', 'en-US'] as const) {
        const label = roleLabel(role, locale);
        assert.notStrictEqual(label, roleLabel('not_a_role', locale), `${role} (${locale}) falls back to the generic label`);
        assert.ok(label.length > 0 && label !== `role.${role}`, `${role} (${locale}) has no label`);
      }
    }
  });

  it('ROLE_DISPLAY_ORDER covers exactly the backend roles', () => {
    assert.deepStrictEqual([...ROLE_DISPLAY_ORDER].sort(), backendRoles().sort());
  });

  it('every staff-routing role is a known role', () => {
    for (const role of STAFF_ROLES) assert.ok((ROLE_DISPLAY_ORDER as readonly string[]).includes(role), role);
  });

  it('uses the Indonesian persona terms (spec/00-overview section 2)', () => {
    assert.strictEqual(roleLabel('finance_officer'), 'Bendahara');
    assert.strictEqual(roleLabel('counsellor'), 'Guru BK');
    assert.strictEqual(roleLabel('clinic_officer'), 'Petugas UKS');
    assert.strictEqual(roleLabel('parent'), 'Wali Murid');
    assert.strictEqual(roleLabel('canteen_operator'), 'Operator Kantin');
  });

  it('follows the language switch', () => {
    assert.strictEqual(roleLabel('clinic_officer', 'en-US'), 'Clinic Officer');
    assert.strictEqual(rolesLabel(roles('teacher', 'parent'), 'en-US'), 'Teacher, Parent / Guardian');
  });

  it('no longer reads clinic officer, canteen operator or parent as generic staff', () => {
    for (const role of ['clinic_officer', 'canteen_operator', 'parent']) {
      assert.notStrictEqual(rolesLabel(roles(role)), 'Staf', role);
    }
  });
});

describe('rolesLabel', () => {
  it('shows every held role, senior first, regardless of the order the API listed them', () => {
    assert.strictEqual(rolesLabel(roles('parent', 'teacher')), 'Guru, Wali Murid');
    assert.strictEqual(rolesLabel(roles('teacher', 'school_admin', 'counsellor')), 'Admin Sekolah, Guru, Guru BK');
  });

  it('collapses the same role held in several schools', () => {
    assert.strictEqual(rolesLabel(roles('school_admin', 'school_admin', 'school_admin')), 'Admin Sekolah');
  });

  it('sorts a role this build does not know last and labels it generically', () => {
    assert.strictEqual(rolesLabel(roles('future_role', 'teacher')), 'Guru, Staf');
  });

  it('reads no roles as the generic staff label, not as a teacher', () => {
    assert.strictEqual(rolesLabel([]), 'Staf');
    assert.strictEqual(rolesLabel(undefined), 'Staf');
    assert.strictEqual(rolesLabel(null), 'Staf');
  });
});
