/** Catalog category codes as the server sends them (Product.category), with their id-ID labels for the kiosk. */
const CATEGORY_LABELS: Record<string, string> = {
  ALL: 'Semua',
  FOOD: 'Makanan',
  DRINK: 'Minuman',
  SNACK: 'Camilan',
};

/** Indonesian label for a category code (id-ID first); an unknown category shows as the server sent it. */
export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category.toUpperCase()] ?? category;
}
