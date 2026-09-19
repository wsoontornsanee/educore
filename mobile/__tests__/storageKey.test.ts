import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { secureStoreKey } from '../src/services/storage.ts';

// expo-secure-store: "keys must not be empty and contain only alphanumeric characters, '.', '-', and '_'".
const VALID = /^[A-Za-z0-9._-]+$/;

describe('secureStoreKey', () => {
  it('makes the keys the app really uses acceptable to the keychain', () => {
    for (const key of [
      'educore_pos_terminal_qr_key:1',
      'educore_parent_wallet:tx:42',
      'educore_nutrition:7:2026-09-01:2026-09-19',
      'educore_access_token',
    ]) {
      assert.match(secureStoreKey(key), VALID, key);
    }
  });

  it('leaves an already valid key unchanged', () => {
    assert.strictEqual(secureStoreKey('educore_access_token'), 'educore_access_token');
  });

  it('keeps the keys the app uses distinct', () => {
    assert.notStrictEqual(secureStoreKey('a:b'), secureStoreKey('a_b'));
    assert.notStrictEqual(secureStoreKey('a:b'), secureStoreKey('a.b'));
  });
});
