/**
 * Teacher Mobile Shell (spec/09, teacher mobile shell).
 *
 * 4-tab navigation: Agenda, Presensi, Broadcast, Profil.
 * Manages roll-call, substitution, and behaviour modals internally.
 */
import React, { useEffect, useState } from 'react';
import { SafeAreaView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { AgendaScreen } from '../AgendaScreen';
import { RollCallScreen } from '../RollCallScreen';
import { SubstitutionModal } from '../SubstitutionModal';
import { BehaviourModal } from '../BehaviourModal';
import { TeacherBroadcastScreen } from './TeacherBroadcastScreen';
import { TeacherProfileScreen } from './TeacherProfileScreen';
import { colors, spacing, typography } from '../../theme/tokens';
import { useLocale } from '../../i18n/LocaleContext';
import { todayWib } from '../../services/localDate';
import type { StudentRosterItem, TimetableSlotItem, UserProfile } from '../../types';

type TeacherTab = 'AGENDA' | 'ATTENDANCE' | 'BROADCAST' | 'PROFILE';

interface TeacherShellProps {
  user: UserProfile;
  onLogout: () => void;
  /** Slot from a SUBSTITUTE_ASSIGNED notification tap; opens the substitution modal once. */
  substitutionDeepLinkSlot?: TimetableSlotItem | null;
  onSubstitutionDeepLinkHandled?: () => void;
}

export const TeacherShell: React.FC<TeacherShellProps> = ({
  user,
  onLogout,
  substitutionDeepLinkSlot = null,
  onSubstitutionDeepLinkHandled,
}) => {
  const { t, locale } = useLocale();
  const [activeTab, setActiveTab] = useState<TeacherTab>('AGENDA');

  // Roll call state
  const [activeSlot, setActiveSlot] = useState<TimetableSlotItem | null>(null);

  // Substitution modal state
  const [subModalSlot, setSubModalSlot] = useState<TimetableSlotItem | null>(null);

  useEffect(() => {
    if (!substitutionDeepLinkSlot) return;
    setActiveSlot(null);
    setSubModalSlot(substitutionDeepLinkSlot);
    onSubstitutionDeepLinkHandled?.();
  }, [substitutionDeepLinkSlot, onSubstitutionDeepLinkHandled]);

  // Behaviour modal state
  const [behaviourSlot, setBehaviourSlot] = useState<TimetableSlotItem | null>(null);

  const todayStr = todayWib();

  const getMockRosterForSlot = (slot: TimetableSlotItem): StudentRosterItem[] => {
    if (slot.roster && slot.roster.length > 0) return slot.roster;
    return Array.from({ length: 32 }, (_, idx) => {
      const id = idx + 1;
      const isAbsentAtGate = id === 5 || id === 18;
      return {
        student_id: id,
        full_name: `Siswa Contoh ${id}`,
        nis: `2026${String(id).padStart(3, '0')}`,
        nisn: `00${String(id).padStart(8, '0')}`,
        gate_status: isAbsentAtGate ? 'NO_SCAN' : 'IN',
        prefill_status: isAbsentAtGate ? 'ALPA' : 'HADIR',
        is_gate_prefill: isAbsentAtGate,
        medical_flags: id === 12 ? ['ASMA'] : [],
      };
    });
  };

  // When in roll-call view, show the RollCallScreen fullscreen (no tab bar)
  if (activeSlot) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <RollCallScreen
          slot={activeSlot}
          dateStr={todayStr}
          initialRoster={getMockRosterForSlot(activeSlot)}
          onBack={() => setActiveSlot(null)}
          onSaved={() => setActiveSlot(null)}
        />
      </SafeAreaView>
    );
  }

  const renderTabContent = () => {
    switch (activeTab) {
      case 'AGENDA':
        return (
          <AgendaScreen
            user={user}
            onSelectSlot={(slot) => setActiveSlot(slot)}
            onOpenSubstitution={(slot) => setSubModalSlot(slot)}
            onOpenBehaviour={(slot) => setBehaviourSlot(slot)}
            onLogout={onLogout}
          />
        );
      case 'ATTENDANCE':
        return (
          <AgendaScreen
            user={user}
            onSelectSlot={(slot) => setActiveSlot(slot)}
            onOpenSubstitution={(slot) => setSubModalSlot(slot)}
            onOpenBehaviour={(slot) => setBehaviourSlot(slot)}
            onLogout={onLogout}
          />
        );
      case 'BROADCAST':
        return (
          <TeacherBroadcastScreen onBack={() => setActiveTab('AGENDA')} />
        );
      case 'PROFILE':
        return (
          <TeacherProfileScreen user={user} onLogout={onLogout} />
        );
    }
  };

  const tabConfig: Array<{ key: TeacherTab; label: string }> = [
    { key: 'AGENDA', label: 'Agenda' },
    { key: 'ATTENDANCE', label: 'Presensi' },
    { key: 'BROADCAST', label: 'Broadcast' },
    { key: 'PROFILE', label: 'Profil' },
  ];

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.content}>
        {renderTabContent()}
      </View>

      {/* Bottom Tab Bar */}
      <View style={styles.tabBar} accessibilityRole="tabbar">
        {tabConfig.map((tab) => (
          <TouchableOpacity
            key={tab.key}
            style={styles.tabItem}
            onPress={() => setActiveTab(tab.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: activeTab === tab.key }}
            accessibilityLabel={tab.label}
          >
            <Text style={[styles.tabLabel, activeTab === tab.key && styles.tabLabelActive]}>
              {tab.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Substitution Modal */}
      <SubstitutionModal
        visible={!!subModalSlot}
        slot={subModalSlot}
        onClose={() => setSubModalSlot(null)}
        onResolved={() => setSubModalSlot(null)}
      />

      {/* Behaviour Modal (TCH-008) */}
      <BehaviourModal
        visible={!!behaviourSlot}
        students={behaviourSlot ? getMockRosterForSlot(behaviourSlot) : []}
        onClose={() => setBehaviourSlot(null)}
        onSaved={() => {}}
      />
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  content: {
    flex: 1,
  },
  tabBar: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.white,
  },
  tabItem: {
    flex: 1,
    paddingVertical: spacing.md,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  tabLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    fontWeight: typography.fontWeight.medium,
  },
  tabLabelActive: {
    color: colors.primary,
    fontWeight: typography.fontWeight.bold,
  },
});
