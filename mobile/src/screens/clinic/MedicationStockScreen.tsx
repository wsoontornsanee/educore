/**
 * Clinic officer: medication stock for their school(s) (spec/10 LIF-004/005). Read-only; items that are low or
 * expired come first so the officer sees what needs action.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, SafeAreaView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { fetchMedicationStock, isExpired, isLowStock, sortStockForAttention } from '../../services/clinicStaff.ts';
import { todayWib } from '../../services/localDate.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type { MedicationStockItem } from '../../types/index.ts';

export const MedicationStockScreen: React.FC = () => {
  const { t } = useLocale();
  const [stock, setStock] = useState<MedicationStockItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const today = todayWib();

  const load = useCallback(async (isRefresh: boolean) => {
    if (isRefresh) setRefreshing(true); else setLoading(true);
    setError(false);
    try {
      setStock(await fetchMedicationStock());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load]);

  if (loading) {
    return <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>;
  }

  return (
    <SafeAreaView style={styles.root}>
      <FlatList
        data={sortStockForAttention(stock, today)}
        keyExtractor={(item) => String(item.id)}
        refreshing={refreshing}
        onRefresh={() => load(true)}
        contentContainerStyle={styles.list}
        ListHeaderComponent={
          <View>
            <Text style={styles.title}>{t('clinicstaff.stock.title')}</Text>
            {error && (
              <View style={styles.errorCard} accessibilityRole="alert">
                <Text style={styles.errorText}>{t('clinic.load_error')}</Text>
                <TouchableOpacity onPress={() => load(false)} accessibilityRole="button" style={styles.retry}>
                  <Text style={styles.retryText}>{t('common.retry')}</Text>
                </TouchableOpacity>
              </View>
            )}
          </View>
        }
        ListEmptyComponent={error ? null : <Text style={styles.empty}>{t('clinicstaff.stock.empty')}</Text>}
        renderItem={({ item }) => {
          const low = isLowStock(item);
          const expired = isExpired(item, today);
          return (
            <View style={styles.card}>
              <View style={styles.row}>
                <Text style={styles.name}>{item.name}</Text>
                <Text style={styles.qty}>{item.quantity} {item.unit}</Text>
              </View>
              {(low || expired) && (
                <View style={styles.badges}>
                  {low && <Text style={styles.badge}>{t('clinicstaff.stock.low')}</Text>}
                  {expired && <Text style={styles.badge}>{t('clinicstaff.stock.expired')}</Text>}
                </View>
              )}
              <Text style={styles.meta}>
                {t('clinicstaff.stock.reorder')}: {item.reorder_level}
                {item.expiry_date ? `  ·  ${t('clinicstaff.stock.expiry')}: ${item.expiry_date}` : ''}
              </Text>
            </View>
          );
        }}
      />
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surface },
  list: { padding: spacing.base },
  title: {
    fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading,
    marginBottom: spacing.md,
  },
  empty: { textAlign: 'center', color: colors.muted, marginTop: spacing.xl, fontSize: typography.fontSize.base },
  errorCard: {
    backgroundColor: colors.alpaLight, borderRadius: radius.card, padding: spacing.md, marginBottom: spacing.md,
  },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.sm },
  retry: { marginTop: spacing.sm, minHeight: 44, justifyContent: 'center' },
  retryText: { color: colors.primary, fontWeight: typography.fontWeight.bold },
  card: {
    backgroundColor: colors.white, borderRadius: radius.card, borderWidth: 1, borderColor: colors.border,
    padding: spacing.md, marginBottom: spacing.sm,
  },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  name: { flex: 1, fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading },
  qty: { fontSize: typography.fontSize.base, color: colors.body },
  badges: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs },
  badge: {
    fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold, color: colors.alpa,
    backgroundColor: colors.alpaLight, paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.card,
  },
  meta: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: spacing.xs },
});
