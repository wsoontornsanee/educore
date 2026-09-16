/**
 * Parent app shell: persistent child switcher header + bottom tab bar (spec/08 §2, PAR-003).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, SafeAreaView, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { fetchChildren } from '../../services/children';
import { getLastChildId, saveLastChildId } from '../../services/storage';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { ChildSummary } from '../../types';

export type ParentTab = 'HOME' | 'ATTENDANCE' | 'INVOICES';

interface ParentShellProps {
  activeTab: ParentTab;
  onTabChange: (tab: ParentTab) => void;
  onLogout: () => void;
  children: (ctx: { selectedChild: ChildSummary; allChildren: ChildSummary[] }) => React.ReactNode;
}

export const ParentShell: React.FC<ParentShellProps> = ({ activeTab, onTabChange, onLogout, children }) => {
  const [loading, setLoading] = useState(true);
  const [allChildren, setAllChildren] = useState<ChildSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    (async () => {
      const kids = await fetchChildren();
      setAllChildren(kids);
      const lastId = await getLastChildId();
      const initial = kids.find((k) => k.student_id === lastId) ?? kids[0] ?? null;
      setSelectedId(initial ? initial.student_id : null);
      setLoading(false);
    })();
  }, []);

  const handleSelectChild = async (id: number) => {
    setSelectedId(id);
    await saveLastChildId(id);
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  const selectedChild = allChildren.find((k) => k.student_id === selectedId);
  if (!selectedChild) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={styles.emptyText}>Tidak ada data anak terhubung.</Text>
        <TouchableOpacity onPress={onLogout} style={styles.logoutLink}>
          <Text style={styles.logoutText}>Keluar</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  const showInvoicesTab = selectedChild.financial_responsible;

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.childSwitcher} contentContainerStyle={styles.childSwitcherContent}>
        {allChildren.map((child) => (
          <TouchableOpacity
            key={child.student_id}
            style={[styles.childChip, child.student_id === selectedId && styles.childChipActive]}
            onPress={() => handleSelectChild(child.student_id)}
          >
            <Text style={[styles.childChipText, child.student_id === selectedId && styles.childChipTextActive]}>
              {child.full_name}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <View style={styles.content}>{children({ selectedChild, allChildren })}</View>

      <View style={styles.tabBar}>
        <TouchableOpacity style={styles.tabItem} onPress={() => onTabChange('HOME')}>
          <Text style={[styles.tabLabel, activeTab === 'HOME' && styles.tabLabelActive]}>Home</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.tabItem} onPress={() => onTabChange('ATTENDANCE')}>
          <Text style={[styles.tabLabel, activeTab === 'ATTENDANCE' && styles.tabLabelActive]}>Absensi</Text>
        </TouchableOpacity>
        {showInvoicesTab && (
          <TouchableOpacity style={styles.tabItem} onPress={() => onTabChange('INVOICES')}>
            <Text style={[styles.tabLabel, activeTab === 'INVOICES' && styles.tabLabelActive]}>Tagihan</Text>
          </TouchableOpacity>
        )}
      </View>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surface, padding: spacing.xl },
  emptyText: { fontSize: typography.fontSize.base, color: colors.muted, textAlign: 'center', marginBottom: spacing.lg },
  logoutLink: { padding: spacing.sm },
  logoutText: { color: colors.primary, fontWeight: typography.fontWeight.bold },
  childSwitcher: { backgroundColor: colors.white, borderBottomWidth: 1, borderBottomColor: colors.border },
  childSwitcherContent: { paddingHorizontal: spacing.base, paddingVertical: spacing.sm },
  childChip: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.badge, marginRight: spacing.sm },
  childChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  childChipText: { fontSize: typography.fontSize.sm, color: colors.body, fontWeight: typography.fontWeight.medium },
  childChipTextActive: { color: colors.white },
  content: { flex: 1 },
  tabBar: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.white },
  tabItem: { flex: 1, paddingVertical: spacing.md, alignItems: 'center' },
  tabLabel: { fontSize: typography.fontSize.sm, color: colors.muted, fontWeight: typography.fontWeight.medium },
  tabLabelActive: { color: colors.primary, fontWeight: typography.fontWeight.bold },
});
