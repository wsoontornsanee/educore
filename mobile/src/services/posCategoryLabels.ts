/**
 * Indonesian labels for the canteen catalogue's category codes (the kiosk is id-ID only). The server sends a
 * free-text category; the ones it ships with are mapped, anything else is shown as sent.
 */
const LABELS: Record<string, string> = {
  ALL: 'Semua',
  DRINK: 'Minuman',
  FOOD: 'Makanan',
  SNACK: 'Camilan',
};

export function posCategoryLabel(category: string): string {
  return LABELS[category.trim().toUpperCase()] ?? category;
}
