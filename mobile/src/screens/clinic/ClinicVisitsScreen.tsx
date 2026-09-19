/**
 * Clinic officer: recent clinic visits (spec/10 LIF-001). Read-only. Health notes are shown only in the detail
 * sheet and are never persisted on the device (see services/clinicStaff.ts).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { fetchRecentClinicVisits } from '../../services/clinicStaff.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type { ClinicVisitItem } from '../../types/index.ts';

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('id-ID', {
      day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

export const ClinicVisitsScreen: React.FC = () => {
  const { t } = useLocale();
  const [visits, setVisits] = useState<ClinicVisitItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<ClinicVisitItem | null>(null);

  const load = useCallback(async (isRefresh: boolean) => {
    if (isRefresh) setRefreshing(true); else setLoading(true);
    setError(false);
    try {
      setVisits(await fetchRecentClinicVisits());
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
        data={visits}
        keyExtractor={(item) => String(item.id)}
        refreshing={refreshing}
        onRefresh={() => load(true)}
        contentContainerStyle={styles.list}
        ListHeaderComponent={
          <View>
            <Text style={styles.title}>{t('clinicstaff.visits.title')}</Text>
            <Text style={styles.privacy}>{t('clinicstaff.privacy')}</Text>
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
        ListEmptyComponent={error ? null : <Text style={styles.empty}>{t('clinicstaff.visits.empty')}</Text>}
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.card} onPress={() => setSelected(item)} accessibilityRole="button">
            <Text style={styles.student}>{item.student_name}</Text>
            <Text style={styles.meta}>{formatDateTime(item.occurred_at)}</Text>
            <Text style={styles.meta}>
              {t('clinic.outcome')}: {t(`clinic.outcome.${item.outcome}`, item.outcome)}
            </Text>
          </TouchableOpacity>
        )}
      />

      <Modal visible={!!selected} animationType="slide" onRequestClose={() => setSelected(null)}>
        <SafeAreaView style={styles.root}>
          {selected && (
            <ScrollView contentContainerStyle={styles.detail}>
              <Text style={styles.title}>{selected.student_name}</Text>
              <Text style={styles.meta}>{formatDateTime(selected.occurred_at)}</Text>
              <Field label={t('clinicstaff.complaint')} value={selected.complaint} />
              <Field label={t('clinicstaff.treatment')} value={selected.treatment} />
              <Field
                label={t('clinicstaff.medication')}
                value={selected.medication_name
                  ? `${selected.medication_name}${selected.medication_quantity_used ? ` × ${selected.medication_quantity_used}` : ''}`
                  : ''}
              />
              <Field label={t('clinic.outcome')} value={t(`clinic.outcome.${selected.outcome}`, selected.outcome)} />
              <Field label={t('clinic.handled_by')} value={selected.handled_by_name} />
              {selected.guardian_consent_confirmed && <Text style={styles.meta}>{t('clinicstaff.consent')}</Text>}
              <TouchableOpacity style={styles.close} onPress={() => setSelected(null)} accessibilityRole="button">
                <Text style={styles.closeText}>{t('common.close')}</Text>
              </TouchableOpacity>
            </ScrollView>
          )}
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
};

const Field: React.FC<{ label: string; value: string }> = ({ label, value }) =>
  value ? (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.fieldValue}>{value}</Text>
    </View>
  ) : null;

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surface },
  list: { padding: spacing.base },
  title: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  privacy: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: spacing.xs, marginBottom: spacing.md },
  empty: { textAlign: 'center', color: colors.muted, marginTop: spacing.xl, fontSize: typography.fontSize.base },
  errorCard: {
    backgroundColor: colors.alpaLight, borderRadius: radius.card, padding: spacing.md, marginBottom: spacing.md,
  },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.sm },
  retry: { marginTop: spacing.sm, minHeight: 44, justifyContent: 'center' },
  retryText: { color: colors.primary, fontWeight: typography.fontWeight.bold },
  card: {
    backgroundColor: colors.white, borderRadius: radius.card, borderWidth: 1, borderColor: colors.border,
    padding: spacing.md, marginBottom: spacing.sm, minHeight: 44,
  },
  student: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading },
  meta: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: 2 },
  detail: { padding: spacing.base },
  field: { marginTop: spacing.md },
  fieldLabel: { fontSize: typography.fontSize.xs, color: colors.muted, fontWeight: typography.fontWeight.bold },
  fieldValue: { fontSize: typography.fontSize.base, color: colors.body, marginTop: 2 },
  close: {
    marginTop: spacing.xl, minHeight: 44, justifyContent: 'center', alignItems: 'center',
    backgroundColor: colors.primary, borderRadius: radius.card,
  },
  closeText: { color: colors.white, fontWeight: typography.fontWeight.bold },
});
