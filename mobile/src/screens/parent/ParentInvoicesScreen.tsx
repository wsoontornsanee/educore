/**
 * Parent Invoices: outstanding + paid invoice list, quick-pay CTA (spec/08 PAR-005, PAR-006).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { fetchInvoicesForChild } from '../../services/invoices';
import { cacheGet, cacheSet } from '../../services/storage';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { PaymentScreen } from './PaymentScreen'; // added in Task 12
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { ChildSummary, InvoiceItem } from '../../types';

interface ParentInvoicesScreenProps {
  child: ChildSummary;
}

export const ParentInvoicesScreen: React.FC<ParentInvoicesScreenProps> = ({ child }) => {
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [invoices, setInvoices] = useState<InvoiceItem[]>([]);
  const [payingInvoiceIds, setPayingInvoiceIds] = useState<number[] | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const cacheKey = `educore_parent_invoices_${child.student_id}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await fetchInvoicesForChild(child.student_id);
        if (cancelled) return;
        setInvoices(result);
        setOffline(false);
        setCachedAt(null);
        await cacheSet(cacheKey, result);
      } catch {
        if (cancelled) return;
        const cached = await cacheGet<InvoiceItem[]>(cacheKey);
        if (cached) {
          setInvoices(cached.value);
          setCachedAt(cached.cachedAt);
        }
        setOffline(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id, reloadToken]);

  if (payingInvoiceIds) {
    return (
      <PaymentScreen
        invoiceIds={payingInvoiceIds}
        onDone={() => { setPayingInvoiceIds(null); setReloadToken((t) => t + 1); }}
      />
    );
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  const outstanding = invoices
    .filter((inv) => Number(inv.balance_due) > 0)
    .sort((a, b) => a.due_date.localeCompare(b.due_date));

  const handleQuickPay = () => {
    if (outstanding.length === 1) {
      setPayingInvoiceIds([outstanding[0].id]);
    } else if (outstanding.length > 1) {
      setPayingInvoiceIds([outstanding[0].id]); // oldest pre-selected, PAR-005
    }
  };

  return (
    <View style={styles.root}>
      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

      {outstanding.length > 0 && !offline && (
        <TouchableOpacity style={styles.payButton} onPress={handleQuickPay} activeOpacity={0.85}>
          <Text style={styles.payButtonText}>
            BAYAR {outstanding.length === 1 ? 'TAGIHAN INI' : `TAGIHAN TERLAMA (${outstanding[0].period})`}
          </Text>
        </TouchableOpacity>
      )}

      <FlatList
        data={invoices}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.emptyText}>Belum ada tagihan.</Text>}
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={styles.rowText}>
              <Text style={styles.rowNumber}>{item.number} — {item.period}</Text>
              <Text style={styles.rowDue}>Jatuh tempo: {item.due_date}</Text>
            </View>
            <View style={styles.rowAmounts}>
              <Text style={styles.rowTotal}>{item.currency} {item.total}</Text>
              <Text style={[styles.rowStatus, item.status === 'PAID' && styles.rowStatusPaid]}>{item.status}</Text>
            </View>
          </View>
        )}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  payButton: { backgroundColor: colors.primary, margin: spacing.base, paddingVertical: spacing.md, alignItems: 'center', borderRadius: radius.button },
  payButtonText: { color: colors.white, fontWeight: typography.fontWeight.bold, letterSpacing: 1 },
  list: { paddingHorizontal: spacing.base, paddingBottom: spacing.base },
  emptyText: { fontSize: typography.fontSize.sm, color: colors.muted, textAlign: 'center', marginTop: spacing.xl },
  row: {
    flexDirection: 'row', justifyContent: 'space-between', backgroundColor: colors.white,
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.card,
    padding: spacing.md, marginBottom: spacing.sm,
  },
  rowText: { flex: 1 },
  rowNumber: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowDue: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: 2 },
  rowAmounts: { alignItems: 'flex-end' },
  rowTotal: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowStatus: { fontSize: typography.fontSize.xs, color: colors.alpa, marginTop: 2 },
  rowStatusPaid: { color: colors.hadir },
});
