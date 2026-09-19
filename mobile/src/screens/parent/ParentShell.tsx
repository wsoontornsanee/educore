/**
 * Parent app shell: persistent child switcher header + bottom tab bar (spec/08 §2, PAR-003).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, SafeAreaView, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { fetchChildren } from '../../services/children';
import { getLastChildId, saveLastChildId } from '../../services/storage';
import { useLocale } from '../../i18n/LocaleContext';
import { t } from '../../i18n/strings';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import { track } from '../../services/analytics.ts';
import { resolveDeepLinkChild } from '../../services/deepLink.ts';
import type { ChildSummary } from '../../types';

export type ParentTab = 'HOME' | 'ATTENDANCE' | 'ACADEMIC' | 'MESSAGES' | 'WALLET' | 'NUTRITION' | 'INVOICES' | 'PROFILE';


interface ParentShellProps {
  activeTab: ParentTab;
  onTabChange: (tab: ParentTab) => void;
  onLogout: () => void;
  deepLinkChildId?: number | null;
  children: (ctx: { selectedChild: ChildSummary; allChildren: ChildSummary[] }) => React.ReactNode;
}

export const ParentShell: React.FC<ParentShellProps> = ({ activeTab, onTabChange, onLogout, deepLinkChildId, children }) => {
  const { locale, t } = useLocale();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [allChildren, setAllChildren] = useState<ChildSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError(false);
      try {
        const kids = await fetchChildren();
        const lastId = await getLastChildId();
        if (cancelled) return;
        setAllChildren(kids);
        const initial = kids.find((k) => k.student_id === lastId) ?? kids[0] ?? null;
        setSelectedId(initial ? initial.student_id : null);
      } catch {
        // Network error, 403, expired session… anything here previously left the
        // guardian staring at a spinner forever with no way out.
        if (cancelled) return;
        setLoadError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [reloadToken]);

  const handleSelectChild = async (id: number) => {
    setSelectedId(id);
    await saveLastChildId(id);
    track('child_switch');
  };

  const selectedChild = allChildren.find((k) => k.student_id === selectedId);

  // PAR-017: never leave the Tagihan tab active for a child the guardian is not
  // financially responsible for. This covers both the initial-load resolution
  // and every subsequent child switch, since selectedChild is recomputed above
  // on every render and this effect re-checks it whenever selectedChild or
  // activeTab changes.
  useEffect(() => {
    if (selectedChild && activeTab === 'INVOICES' && !selectedChild.financial_responsible) {
      onTabChange('HOME');
    }
  }, [selectedChild, activeTab, onTabChange]);

  // PAR-004: a push-driven deep link names a specific child; switch to it
  // once the child list has loaded, covering both "already viewing this
  // child" (no-op) and "switch to a different child" cases.
  useEffect(() => {
    if (allChildren.length === 0 || deepLinkChildId === undefined || deepLinkChildId === null) return;
    const target = resolveDeepLinkChild(allChildren, deepLinkChildId);
    if (target && target.student_id !== selectedId) {
      handleSelectChild(target.student_id);
    }
  }, [allChildren, deepLinkChildId]);

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  if (loadError) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={styles.emptyText}>
          {t('common.error')}
        </Text>
        <TouchableOpacity
          onPress={() => setReloadToken((t) => t + 1)}
          style={styles.retryButton}
          accessibilityRole="button"
        >
          <Text style={styles.retryText}>{t('common.retry')}</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onLogout} style={styles.logoutLink} accessibilityRole="button">
          <Text style={styles.logoutText}>{t('profile.logout')}</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  if (!selectedChild) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={styles.emptyText}>{t('profile.section.children')}</Text>
        <TouchableOpacity onPress={onLogout} style={styles.logoutLink} accessibilityRole="button">
          <Text style={styles.logoutText}>{t('profile.logout')}</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  const showInvoicesTab = selectedChild.financial_responsible;
  // PAR-017: also block the render synchronously (not just via the effect above)
  // so a non-financial-responsible child never has invoice content painted even
  // for a single frame while onTabChange('HOME') propagates back up to App.tsx.
  const invoicesBlocked = activeTab === 'INVOICES' && !showInvoicesTab;

  const tabs: Array<{ key: ParentTab; label: string }> = [
    { key: 'HOME', label: t('tab.home', locale) },
    { key: 'ATTENDANCE', label: t('tab.attendance', locale) },
    { key: 'ACADEMIC', label: t('tab.academic', locale) },
    { key: 'MESSAGES', label: t('tab.messages', locale) },
    { key: 'WALLET', label: t('tab.wallet', locale) },
    { key: 'NUTRITION', label: t('tab.nutrition', locale) },
    ...(showInvoicesTab ? [{ key: 'INVOICES' as ParentTab, label: t('tab.invoices', locale) }] : []),
    { key: 'PROFILE', label: t('tab.profile', locale) },
  ];

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

      <View style={styles.content}>{invoicesBlocked ? null : children({ selectedChild, allChildren })}</View>

      <View style={styles.tabBar} accessibilityRole="tabbar">
        {tabs.map(({ key, label }) => (
          <TouchableOpacity
            key={key}
            style={styles.tabItem}
            onPress={() => onTabChange(key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: activeTab === key }}
            accessibilityLabel={label}
          >
            {/* Up to eight tabs share the width: a long word shrinks to fit instead of wrapping mid-word. */}
            <Text
              style={[styles.tabLabel, activeTab === key && styles.tabLabelActive]}
              numberOfLines={1}
              adjustsFontSizeToFit
              minimumFontScale={0.7}
            >
              {label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surface, padding: spacing.xl },
  emptyText: { fontSize: typography.fontSize.base, color: colors.muted, textAlign: 'center', marginBottom: spacing.lg },
  // PAR-016: every interactive element is at least 44dp tall.
  logoutLink: { paddingVertical: spacing.md, paddingHorizontal: spacing.base, minHeight: 44, justifyContent: 'center' },
  logoutText: { color: colors.primary, fontWeight: typography.fontWeight.bold, lineHeight: typography.lineHeight.base },
  retryButton: {
    minHeight: 44, justifyContent: 'center', alignItems: 'center',
    paddingVertical: spacing.md, paddingHorizontal: spacing.xl,
    backgroundColor: colors.primary, borderRadius: radius.button, marginBottom: spacing.sm,
  },
  retryText: { color: colors.white, fontWeight: typography.fontWeight.bold, lineHeight: typography.lineHeight.base },
  // A horizontal ScrollView defaults to flexGrow: 1, so in this column it shares the free height with `content`
  // and the chip row grew to about half the screen. It must size to its chips.
  childSwitcher: { flexGrow: 0, backgroundColor: colors.white, borderBottomWidth: 1, borderBottomColor: colors.border },
  childSwitcherContent: { paddingHorizontal: spacing.base, paddingVertical: spacing.sm },
  // PAR-016: chip is a primary control (child switcher) — keep it >= 44dp tall.
  childChip: {
    paddingHorizontal: spacing.base, paddingVertical: spacing.md, minHeight: 44, justifyContent: 'center',
    borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.badge, marginRight: spacing.sm,
  },
  childChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  childChipText: { fontSize: typography.fontSize.sm, color: colors.body, fontWeight: typography.fontWeight.medium },
  childChipTextActive: { color: colors.white },
  content: { flex: 1 },
  tabBar: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.white },
  tabItem: { flex: 1, paddingVertical: spacing.md, minHeight: 44, justifyContent: 'center', alignItems: 'center' },
  tabLabel: { fontSize: typography.fontSize.sm, color: colors.muted, fontWeight: typography.fontWeight.medium },
  tabLabelActive: { color: colors.primary, fontWeight: typography.fontWeight.bold },
});
