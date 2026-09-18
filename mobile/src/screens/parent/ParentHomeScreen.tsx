/**
 * Parent Home: per-child status card + outstanding balance summary (spec/08 §2, §4, PAR-002).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { fetchAttendanceForChild } from '../../services/parentAttendance';
import { fetchInvoicesForChild } from '../../services/invoices';
import { fetchWallet, formatRupiah } from '../../services/wallet';
import { fetchStudentBroadcasts } from '../../services/broadcasts';
import { cacheGet, cacheSet, getItem, setItem } from '../../services/storage';
import { todayWib } from '../../services/localDate';
import { attendanceStatusLabel } from '../../constants/attendance';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { useLocale } from '../../i18n/LocaleContext';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { AttendanceDayItem, BroadcastItem, ChildSummary, InvoiceItem, WalletData } from '../../types';
import type { ParentTab } from './ParentShell';

const LAST_VIEWED_BROADCAST_KEY = 'educore_last_viewed_broadcast';

interface ParentHomeScreenProps {
  child: ChildSummary;
  onNavigateTab?: (tab: ParentTab) => void;
}

export const ParentHomeScreen: React.FC<ParentHomeScreenProps> = ({ child, onNavigateTab }) => {
  const { t, locale } = useLocale();
  const [loading, setLoading] = useState(true);

  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [today, setToday] = useState<AttendanceDayItem | null>(null);
  const [outstandingInvoices, setOutstandingInvoices] = useState<InvoiceItem[]>([]);
  const [wallet, setWallet] = useState<WalletData | null>(null);
  const [unreadAnnouncements, setUnreadAnnouncements] = useState<BroadcastItem[]>([]);
  const [announcementsOffline, setAnnouncementsOffline] = useState(false);

  const cacheKey = `educore_parent_home_${child.student_id}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const [attendance, invoices, walletRes, broadcastsRes] = await Promise.all([
          fetchAttendanceForChild(child.student_id),
          child.financial_responsible ? fetchInvoicesForChild(child.student_id) : Promise.resolve([]),
          fetchWallet(child.student_id).catch(() => null),
          fetchStudentBroadcasts(child.student_id).catch(() => ({ broadcasts: [] as BroadcastItem[], isOfflineCached: false, lastUpdated: '' })),
        ]);
        if (cancelled) return;
        // Must be the WIB calendar date, not the UTC one (see services/localDate).
        const todayStr = todayWib();
        const todayRow = attendance.find((a) => a.date === todayStr) ?? null;
        const walletData = walletRes ? walletRes.wallet : null;
        // Nearest due date first — the API orders by -created_at, which is not
        // the same thing (matches ParentInvoicesScreen's ordering).
        const outstanding = invoices
          .filter((inv) => Number(inv.balance_due) > 0)
          .sort((a, b) => a.due_date.localeCompare(b.due_date));
        setToday(todayRow);
        setOutstandingInvoices(outstanding);
        setWallet(walletData);
        setOffline(false);
        setCachedAt(null);

        // PAR-002: filter for unread announcements (sent_at > lastViewedAt)
        const lastViewedRaw = await getItem(LAST_VIEWED_BROADCAST_KEY);
        const lastViewed = lastViewedRaw ? new Date(lastViewedRaw).getTime() : 0;
        const unread = (broadcastsRes.broadcasts || []).filter((b) => {
          if (!b.sent_at) return false;
          return new Date(b.sent_at).getTime() > lastViewed;
        });
        setUnreadAnnouncements(unread);
        setAnnouncementsOffline(broadcastsRes.isOfflineCached);

        await cacheSet(cacheKey, { today: todayRow, outstanding, wallet: walletData, unread });
      } catch {
        if (cancelled) return;
        const cached = await cacheGet<{ today: AttendanceDayItem | null; outstanding: InvoiceItem[]; wallet?: WalletData | null; unread?: BroadcastItem[] }>(cacheKey);
        if (cached) {
          setToday(cached.value.today);
          setOutstandingInvoices(cached.value.outstanding);
          setWallet(cached.value.wallet ?? null);
          setUnreadAnnouncements(cached.value.unread ?? []);
          setCachedAt(cached.cachedAt);
        }
        setOffline(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id, child.financial_responsible]);

  const handleOpenAnnouncements = async () => {
    // Mark all as read by recording current time
    await setItem(LAST_VIEWED_BROADCAST_KEY, new Date().toISOString());
    setUnreadAnnouncements([]);
    onNavigateTab?.('MESSAGES');
  };

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  const statusLabel = attendanceStatusLabel(today?.status, locale);

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

      <View style={styles.statusCard}>
        <Text style={styles.statusLabel}>{statusLabel}</Text>
        {today?.first_in_at && (
          <Text style={styles.statusTime}>
            {t('home.arrived')} {today.first_in_at}
          </Text>
        )}
      </View>

      {/* Wallet Balance Card (PAR-002, WAL-008) */}
      <View style={styles.walletCard}>
        <View style={styles.walletCardTop}>
          <View>
            <Text style={styles.cardTitle}>{t('home.wallet_title')}</Text>
            <Text style={styles.walletBalanceText}>{formatRupiah(wallet?.balance)}</Text>
          </View>
          {wallet && (
            <View
              style={[
                styles.walletBadge,
                wallet.status === 'ACTIVE' ? styles.walletBadgeActive : styles.walletBadgeFrozen,
              ]}
            >
              <Text
                style={[
                  styles.walletBadgeText,
                  wallet.status === 'ACTIVE' ? styles.walletBadgeTextActive : styles.walletBadgeTextFrozen,
                ]}
              >
                {wallet.status === 'ACTIVE' ? t('wallet.badge_active') : t('wallet.badge_frozen')}
              </Text>
            </View>
          )}
        </View>

        {onNavigateTab && (
          <TouchableOpacity
            style={styles.walletActionBtn}
            onPress={() => onNavigateTab('WALLET')}
            accessibilityRole="button"
          >
            <Text style={styles.walletActionBtnText}>{t('home.open_wallet')}</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Announcements Card (PAR-002: unread announcements) */}
      {unreadAnnouncements.length > 0 && (
        <TouchableOpacity
          style={styles.announcementCard}
          onPress={handleOpenAnnouncements}
          accessibilityRole="button"
          accessibilityLabel={`${unreadAnnouncements.length} pengumuman baru. Ketuk untuk membuka.`}
        >
          <View style={styles.announcementCardTop}>
            <Text style={styles.announcementBadge}>{unreadAnnouncements.length}</Text>
            <Text style={styles.announcementTitle}>
              {unreadAnnouncements.length} {t('home.announcements_title')}
            </Text>
          </View>
          <Text style={styles.announcementLatest} numberOfLines={1}>
            {unreadAnnouncements[0].title}
          </Text>
          {announcementsOffline && (
            <Text style={styles.announcementOfflineNote}>{t('home.announcements_offline')}</Text>
          )}
        </TouchableOpacity>
      )}

      {child.financial_responsible && (
        <View style={styles.invoiceCard}>
          <Text style={styles.cardTitle}>{t('home.invoices_title')}</Text>
          {outstandingInvoices.length === 0 ? (
            <Text style={styles.cardBody}>{t('home.no_invoices')}</Text>
          ) : (
            <Text style={styles.cardBody}>
              {outstandingInvoices.length} {t('home.invoices_due')} {outstandingInvoices[0].due_date}.
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
  walletCard: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.borderDark,
    borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base,
  },
  walletCardTop: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start',
  },
  walletBalanceText: {
    fontSize: typography.fontSize.xxl, fontWeight: typography.fontWeight.bold,
    color: colors.heading, marginTop: spacing.xs,
  },
  walletBadge: {
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
    borderRadius: radius.badge, borderWidth: 1,
  },
  walletBadgeActive: {
    backgroundColor: colors.hadirLight, borderColor: colors.hadir,
  },
  walletBadgeFrozen: {
    backgroundColor: colors.alpaLight, borderColor: colors.alpa,
  },
  walletBadgeText: {
    fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold,
  },
  walletBadgeTextActive: { color: colors.hadir },
  walletBadgeTextFrozen: { color: colors.alpa },
  walletActionBtn: {
    marginTop: spacing.md, paddingTop: spacing.md,
    borderTopWidth: 1, borderTopColor: colors.border,
    minHeight: 44, justifyContent: 'center',
  },
  walletActionBtnText: {
    fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  invoiceCard: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.card, padding: spacing.lg,
  },
  announcementCard: {
    backgroundColor: colors.primaryLight,
    borderWidth: 1, borderColor: colors.primary,
    borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base,
  },
  announcementCardTop: {
    flexDirection: 'row', alignItems: 'center', marginBottom: spacing.xs,
  },
  announcementBadge: {
    backgroundColor: colors.primary,
    color: colors.white,
    fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold,
    paddingHorizontal: 6, paddingVertical: 2,
    borderRadius: radius.badge,
    marginRight: spacing.sm, overflow: 'hidden',
    minWidth: 22, textAlign: 'center',
  },
  announcementTitle: {
    fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold,
    color: colors.primaryDark,
  },
  announcementLatest: {
    fontSize: typography.fontSize.sm, color: colors.body, marginTop: spacing.xs,
  },
  announcementOfflineNote: {
    fontSize: typography.fontSize.xs, color: colors.muted, fontStyle: 'italic', marginTop: spacing.xs,
  },
  cardTitle: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading, marginBottom: spacing.xs },
  cardBody: { fontSize: typography.fontSize.sm, color: colors.body },
});
