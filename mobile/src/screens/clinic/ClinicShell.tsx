/**
 * Clinic officer app shell (Petugas UKS): three read-only tabs, Kunjungan, Stok obat, Profil.
 * The spec gives this role the web console as its primary surface; this is the small mobile companion.
 */
import React, { useState } from 'react';
import { SafeAreaView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { ClinicVisitsScreen } from './ClinicVisitsScreen';
import { MedicationStockScreen } from './MedicationStockScreen';
import { TeacherProfileScreen } from '../teacher/TeacherProfileScreen';
import { colors, spacing, typography } from '../../theme/tokens';
import { useLocale } from '../../i18n/LocaleContext';
import type { UserProfile } from '../../types';

type ClinicTab = 'VISITS' | 'STOCK' | 'PROFILE';

interface ClinicShellProps {
  user: UserProfile;
  onLogout: () => void;
}

export const ClinicShell: React.FC<ClinicShellProps> = ({ user, onLogout }) => {
  const { t } = useLocale();
  const [tab, setTab] = useState<ClinicTab>('VISITS');

  const tabs: Array<{ key: ClinicTab; label: string }> = [
    { key: 'VISITS', label: t('tab.visits') },
    { key: 'STOCK', label: t('tab.stock') },
    { key: 'PROFILE', label: t('tab.profile') },
  ];

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.content}>
        {tab === 'VISITS' && <ClinicVisitsScreen />}
        {tab === 'STOCK' && <MedicationStockScreen />}
        {tab === 'PROFILE' && <TeacherProfileScreen user={user} onLogout={onLogout} />}
      </View>
      <View style={styles.tabBar} accessibilityRole="tabbar">
        {tabs.map((item) => (
          <TouchableOpacity
            key={item.key}
            style={styles.tabItem}
            onPress={() => setTab(item.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === item.key }}
            accessibilityLabel={item.label}
          >
            <Text style={[styles.tabLabel, tab === item.key && styles.tabLabelActive]}>{item.label}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.surface },
  content: { flex: 1 },
  tabBar: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.white },
  tabItem: { flex: 1, paddingVertical: spacing.md, minHeight: 44, justifyContent: 'center', alignItems: 'center' },
  tabLabel: { fontSize: typography.fontSize.sm, color: colors.muted, fontWeight: typography.fontWeight.medium },
  tabLabelActive: { color: colors.primary, fontWeight: typography.fontWeight.bold },
});
