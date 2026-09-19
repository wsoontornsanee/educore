import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { posCategoryLabel } from '../src/services/posCategoryLabels.ts';

describe('posCategoryLabel', () => {
  it('shows the shipped category codes in Indonesian', () => {
    assert.strictEqual(posCategoryLabel('DRINK'), 'Minuman');
    assert.strictEqual(posCategoryLabel('FOOD'), 'Makanan');
    assert.strictEqual(posCategoryLabel('SNACK'), 'Camilan');
    assert.strictEqual(posCategoryLabel('ALL'), 'Semua');
  });

  it('matches regardless of case and stray spaces', () => {
    assert.strictEqual(posCategoryLabel(' drink '), 'Minuman');
  });

  it('shows a category it does not know exactly as the server sent it', () => {
    assert.strictEqual(posCategoryLabel('Frozen Yogurt'), 'Frozen Yogurt');
  });
});
