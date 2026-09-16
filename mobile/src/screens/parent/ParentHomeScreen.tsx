/**
 * Parent Home: per-child status card + outstanding balance summary (spec/08 §2, §4, PAR-002).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { fetchAttendanceForChild } from '../../services/parentAttendance';
import { fetchInvoicesForChild } from '../../services/invoices';
import { cacheGet, cacheSet } from '../../services/storage';
import { todayWib } from '../../services/localDate';
import { attendanceStatusLabel } from '../../constants/attendance';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { AttendanceDayItem, ChildSummary, InvoiceItem } from '../../types';

interface ParentHomeScreenProps {
  child: ChildSummary;
}

export const ParentHomeScreen: React.FC<ParentHomeScreenProps> = ({ child }) => {
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [today, setToday] = useState<AttendanceDayItem | null>(null);
  const [outstandingInvoices, setOutstandingInvoices] = useState<InvoiceItem[]>([]);

  const cacheKey = `educore_parent_home_${child.student_id}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const [attendance, invoices] = await Promise.all([
          fetchAttendanceForChild(child.student_id),
          child.financial_responsible ? fetchInvoicesForChild(child.student_id) : Promise.resolve([]),
        ]);
        if (cancelled) return;
        // Must be the WIB calendar date, not the UTC one (see services/localDate).
        const todayStr = todayWib();
        const todayRow = attendance.find((a) => a.date === todayStr) ?? null;
        // Nearest due date first — the API orders by -created_at, which is not
        // the same thing (matches ParentInvoicesScreen's ordering).
        const outstanding = invoices
          .filter((inv) => Number(inv.balance_due) > 0)
          .sort((a, b) => a.due_date.localeCompare(b.due_date));
        setToday(todayRow);
        setOutstandingInvoices(outstanding);
        setOffline(false);
        setCachedAt(null);
        await cacheSet(cacheKey, { today: todayRow, outstanding });
      } catch {
        if (cancelled) return;
        const cached = await cacheGet<{ today: AttendanceDayItem | null; outstanding: InvoiceItem[] }>(cacheKey);
        if (cached) {
          setToday(cached.value.today);
          setOutstandingInvoices(cached.value.outstanding);
          setCachedAt(cached.cachedAt);
        }
        setOffline(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id, child.financial_responsible]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  const statusLabel = attendanceStatusLabel(today?.status);

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

      <View style={styles.statusCard}>
        <Text style={styles.statusLabel}>{statusLabel}</Text>
        {today?.first_in_at && <Text style={styles.statusTime}>Tiba: {today.first_in_at}</Text>}
      </View>

      {child.financial_responsible && (
        <View style={styles.invoiceCard}>
          <Text style={styles.cardTitle}>Tagihan</Text>
          {outstandingInvoices.length === 0 ? (
            <Text style={styles.cardBody}>Tidak ada tagihan tertunggak.</Text>
          ) : (
            <Text style={styles.cardBody}>
              {outstandingInvoices.length} tagihan belum lunas — jatuh tempo terdekat {outstandingInvoices[0].due_date}.
            </Text>
          )}
        </View>
      )}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.base },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  statusCard: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base,
  },
  statusLabel: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  statusTime: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: spacing.xs },
  invoiceCard: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.card, padding: spacing.lg,
  },
  cardTitle: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading, marginBottom: spacing.xs },
  cardBody: { fontSize: typography.fontSize.sm, color: colors.body },
});
