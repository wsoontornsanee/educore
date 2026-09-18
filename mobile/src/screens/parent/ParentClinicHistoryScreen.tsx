/**
 * Parent Clinic Visit History: read-only view of a child's UKS/clinic visits
 * and health profile (spec/10 §3 LIF-006; Notion "Parent App: Guardian Read
 * Scoping for Clinic Visit History"). Guardian scoping is enforced server-side
 * (get_guardian_student_ids/can_guardian_access_student) — this screen simply
 * calls the same clinic.read-gated endpoints any other caller would.
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { fetchClinicVisits, fetchHealthProfile } from '../../services/clinic.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type { ChildSummary, ClinicVisitItem, HealthProfileItem } from '../../types/index.ts';

interface ParentClinicHistoryScreenProps {
  child: ChildSummary;
  onClose: () => void;
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('id-ID', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

export const ParentClinicHistoryScreen: React.FC<ParentClinicHistoryScreenProps> = ({ child, onClose }) => {
  const { t } = useLocale();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [visits, setVisits] = useState<ClinicVisitItem[]>([]);
  const [profile, setProfile] = useState<HealthProfileItem | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(false);
      try {
        const [visitList, healthProfile] = await Promise.all([
          fetchClinicVisits(child.student_id),
          fetchHealthProfile(child.student_id),
        ]);
        if (cancelled) return;
        setVisits(visitList);
        setProfile(healthProfile);
      } catch {
        if (!cancelled) setError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id]);

  const hasHealthNotes = !!profile && (
    profile.has_medical_alert || profile.allergies.length > 0 || profile.chronic_conditions.length > 0
  );

  return (
    <SafeAreaView style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>{t('clinic.title')}</Text>
        <TouchableOpacity
          onPress={onClose}
          accessibilityRole="button"
          accessibilityLabel={t('clinic.close')}
          style={styles.closeButton}
        >
          <Text style={styles.closeButtonText}>{t('clinic.close')}</Text>
        </TouchableOpacity>
      </View>
      <Text style={styles.childName}>{child.full_name}</Text>

      {loading ? (
        <ActivityIndicator size="large" color={colors.primary} style={styles.loader} />
      ) : error ? (
        <View style={styles.centerBox}>
          <Text style={styles.errorText}>{t('clinic.load_error')}</Text>
        </View>
      ) : (
        <FlatList
          data={visits}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.listContent}
          ListHeaderComponent={
            hasHealthNotes ? (
              <View style={styles.alertCard}>
                <Text style={styles.alertTitle}>{t('clinic.medical_alert')}</Text>
                {profile!.allergies.length > 0 && (
                  <Text style={styles.alertLine}>
                    {t('clinic.allergies')}: {profile!.allergies.join(', ')}
                  </Text>
                )}
                {profile!.chronic_conditions.length > 0 && (
                  <Text style={styles.alertLine}>
                    {t('clinic.chronic_conditions')}: {profile!.chronic_conditions.join(', ')}
                  </Text>
                )}
              </View>
            ) : null
          }
          ListEmptyComponent={
            <View style={styles.centerBox}>
              <Text style={styles.emptyText}>{t('clinic.no_visits')}</Text>
            </View>
          }
          renderItem={({ item }) => (
            <View style={styles.card}>
              <Text style={styles.visitDate}>{formatDateTime(item.occurred_at)}</Text>
              <Text style={styles.visitComplaint}>{item.complaint}</Text>
              {!!item.treatment && <Text style={styles.visitTreatment}>{item.treatment}</Text>}
              <View style={styles.visitMetaRow}>
                <Text style={styles.visitMeta}>
                  {t('clinic.outcome')}: {t(`clinic.outcome.${item.outcome}`, item.outcome)}
                </Text>
                <Text style={styles.visitMeta}>
                  {t('clinic.handled_by')}: {item.handled_by_name}
                </Text>
              </View>
            </View>
          )}
        />
      )}
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.base,
    paddingTop: spacing.base,
  },
  headerTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  closeButton: { minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.sm },
  closeButtonText: { color: colors.primary, fontWeight: typography.fontWeight.medium },
  childName: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    paddingHorizontal: spacing.base,
    marginTop: spacing.xs,
    marginBottom: spacing.sm,
  },
  loader: { marginTop: spacing.xl },
  centerBox: { alignItems: 'center', justifyContent: 'center', paddingVertical: spacing.xl },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.base },
  emptyText: { color: colors.muted, fontSize: typography.fontSize.base },
  listContent: { paddingHorizontal: spacing.base, paddingBottom: spacing.xl },
  alertCard: {
    backgroundColor: colors.alpaLight,
    borderRadius: radius.card,
    borderWidth: 1,
    borderColor: colors.alpa,
    padding: spacing.base,
    marginBottom: spacing.base,
  },
  alertTitle: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.alpa,
    marginBottom: spacing.xs,
  },
  alertLine: { fontSize: typography.fontSize.sm, color: colors.body },
  card: {
    backgroundColor: colors.white,
    borderRadius: radius.card,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.base,
    marginBottom: spacing.sm,
  },
  visitDate: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginBottom: spacing.xs,
  },
  visitComplaint: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.medium,
    color: colors.heading,
  },
  visitTreatment: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginTop: spacing.xs,
  },
  visitMetaRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.sm,
  },
  visitMeta: { fontSize: typography.fontSize.xs, color: colors.muted },
});
