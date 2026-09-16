/**
 * Teacher Mobile Agenda Surface (spec/09 §3 TCH-001, ACD-019).
 * 
 * Lists today's timetable periods, highlights current ongoing period,
 * displays substitution tasks, offline banner, and provides 1-tap roll call entry.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { fetchTeacherAgenda, findCurrentSlot } from '../services/agenda';
import { getPendingCount, syncPendingEntries } from '../services/offlineQueue';
import { StaleOfflineBanner } from '../components/StaleOfflineBanner';
import { StatusBadge } from '../components/StatusBadge';
import { SyncStatusPill } from '../components/SyncStatusPill';
import { colors, radius, spacing, typography } from '../theme/tokens';
import { TimetableSlotItem, UserProfile } from '../types';

interface AgendaScreenProps {
  user: UserProfile;
  onSelectSlot: (slot: TimetableSlotItem) => void;
  onOpenSubstitution: (slot: TimetableSlotItem) => void;
  onLogout: () => void;
}

export const AgendaScreen: React.FC<AgendaScreenProps> = ({
  user,
  onSelectSlot,
  onOpenSubstitution,
  onLogout,
}) => {
  const [slots, setSlots] = useState<TimetableSlotItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [isOffline, setIsOffline] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [isSyncing, setIsSyncing] = useState(false);
  const [currentSlotId, setCurrentSlotId] = useState<number | null>(null);

  const flatListRef = useRef<FlatList>(null);

  const todayStr = new Date().toISOString().split('T')[0];

  const loadAgenda = async (isPullToRefresh = false) => {
    if (!isPullToRefresh) setLoading(true);
    try {
      const data = await fetchTeacherAgenda(todayStr);
      setSlots(data);
      setIsOffline(false);

      // Find current period
      const ongoing = findCurrentSlot(data);
      if (ongoing) {
        setCurrentSlotId(ongoing.id);
      }
    } catch {
      setIsOffline(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }

    // Refresh pending count
    const count = await getPendingCount();
    setPendingCount(count);
  };

  useEffect(() => {
    loadAgenda();
  }, []);

  const handleSyncPress = async () => {
    setIsSyncing(true);
    try {
      await syncPendingEntries();
      await loadAgenda(true);
    } finally {
      setIsSyncing(false);
    }
  };

  const renderSlotCard = ({ item }: { item: TimetableSlotItem }) => {
    const isCurrent = item.id === currentSlotId;

    return (
      <View
        style={[
          styles.card,
          isCurrent && styles.currentCard,
          item.is_substitution && styles.substituteCard,
        ]}
      >
        {/* Left Color Indicator */}
        <View
          style={[
            styles.indicatorStrip,
            isCurrent
              ? { backgroundColor: colors.primary }
              : item.is_substitution
              ? { backgroundColor: colors.substitute }
              : { backgroundColor: colors.borderDark },
          ]}
        />

        <View style={styles.cardContent}>
          {/* Top Row: Period No & Time */}
          <View style={styles.topRow}>
            <View style={styles.periodBadge}>
              <Text style={styles.periodText}>JAM KE-{item.period_no}</Text>
            </View>
            <Text style={styles.timeText}>
              {item.start_time} - {item.end_time}
            </Text>
          </View>

          {/* Subject & Class Group */}
          <View style={styles.midRow}>
            <Text style={styles.subjectText}>{item.subject_name}</Text>
            <Text style={styles.classText}>
              {item.class_group_name} · Ruang {item.room}
            </Text>
          </View>

          {/* Substitution notice if applicable */}
          {item.is_substitution && (
            <View style={styles.substitutionBox}>
              <Text style={styles.substitutionText}>
                Menggantikan: {item.original_teacher_name || 'Guru Berhalangan'}
              </Text>
              {item.substitution_status === 'PENDING' && (
                <TouchableOpacity
                  style={styles.reviewSubButton}
                  onPress={() => onOpenSubstitution(item)}
                >
                  <Text style={styles.reviewSubText}>Tinjau Tugas</Text>
                </TouchableOpacity>
              )}
            </View>
          )}

          {/* Bottom Row: Status Badge & Roll Call CTA */}
          <View style={styles.bottomRow}>
            <View style={styles.badgeWrapper}>
              {item.attendance_submitted ? (
                <StatusBadge type="HADIR" label="Presensi Selesai" size="sm" />
              ) : (
                <StatusBadge type="PENDING" label="Belum Presensi" size="sm" />
              )}
              {item.is_substitution && (
                <View style={{ marginLeft: spacing.xs }}>
                  <StatusBadge type="SUBSTITUTE" size="sm" />
                </View>
              )}
            </View>

            <TouchableOpacity
              style={[
                styles.rollCallButton,
                item.attendance_submitted && styles.rollCallButtonSecondary,
              ]}
              onPress={() => onSelectSlot(item)}
              activeOpacity={0.85}
            >
              <Text
                style={[
                  styles.rollCallButtonText,
                  item.attendance_submitted && styles.rollCallButtonTextSecondary,
                ]}
              >
                {item.attendance_submitted ? 'Edit Presensi' : 'Isi Presensi'}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      {/* App Header */}
      <View style={styles.header}>
        <View>
          <Text style={styles.headerDate}>{todayStr}</Text>
          <Text style={styles.headerTeacher}>{user.full_name}</Text>
        </View>
        <TouchableOpacity style={styles.logoutButton} onPress={onLogout}>
          <Text style={styles.logoutText}>Keluar</Text>
        </TouchableOpacity>
      </View>

      {/* Stale / Offline Banner */}
      <StaleOfflineBanner
        isOffline={isOffline}
        pendingCount={pendingCount}
        onSyncPress={handleSyncPress}
        isSyncing={isSyncing}
      />

      {/* Main Agenda List */}
      {loading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>Memuat jadwal hari ini...</Text>
        </View>
      ) : slots.length === 0 ? (
        <View style={styles.centerContainer}>
          <Text style={styles.emptyTitle}>Tidak Ada Jam Mengajar</Text>
          <Text style={styles.emptySubtitle}>
            Anda tidak memiliki jadwal mengajar terdaftar untuk hari ini.
          </Text>
        </View>
      ) : (
        <FlatList
          ref={flatListRef}
          data={slots}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderSlotCard}
          contentContainerStyle={styles.listContent}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                loadAgenda(true);
              }}
              colors={[colors.primary]}
            />
          }
        />
      )}

      {/* Floating Sync Pill */}
      {pendingCount > 0 && (
        <View style={styles.floatingPillContainer}>
          <SyncStatusPill
            pendingCount={pendingCount}
            onPress={handleSyncPress}
          />
        </View>
      )}
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  header: {
    backgroundColor: colors.white,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderColor: colors.border,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerDate: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 1,
    fontFamily: typography.fontFamily.mono,
  },
  headerTeacher: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginTop: 2,
  },
  logoutButton: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  logoutText: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    fontWeight: typography.fontWeight.semibold,
  },
  listContent: {
    padding: spacing.base,
  },
  card: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.card,
    marginBottom: spacing.base,
    flexDirection: 'row',
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 0,
  },
  currentCard: {
    borderColor: colors.primary,
    borderWidth: 2,
  },
  substituteCard: {
    backgroundColor: '#FAF5FF',
  },
  indicatorStrip: {
    width: 6,
  },
  cardContent: {
    flex: 1,
    padding: spacing.md,
  },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
  },
  periodBadge: {
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
    borderRadius: radius.badge,
  },
  periodText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.body,
    fontFamily: typography.fontFamily.mono,
  },
  timeText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
    color: colors.muted,
    fontFamily: typography.fontFamily.mono,
  },
  midRow: {
    marginBottom: spacing.md,
  },
  subjectText: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  classText: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginTop: 2,
  },
  substitutionBox: {
    backgroundColor: colors.substituteLight,
    padding: spacing.sm,
    borderRadius: radius.card,
    marginBottom: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  substitutionText: {
    fontSize: typography.fontSize.xs,
    color: colors.substitute,
    fontWeight: typography.fontWeight.semibold,
    flex: 1,
  },
  reviewSubButton: {
    backgroundColor: colors.substitute,
    borderRadius: radius.button,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  reviewSubText: {
    fontSize: typography.fontSize.xs,
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
  },
  bottomRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: spacing.xs,
    borderTopWidth: 1,
    borderColor: colors.border,
  },
  badgeWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  rollCallButton: {
    backgroundColor: colors.primary,
    borderRadius: radius.button,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs + 2,
  },
  rollCallButtonSecondary: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
  },
  rollCallButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    textTransform: 'uppercase',
  },
  rollCallButtonTextSecondary: {
    color: colors.body,
  },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.xl,
  },
  loadingText: {
    marginTop: spacing.sm,
    color: colors.muted,
    fontSize: typography.fontSize.sm,
  },
  emptyTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  emptySubtitle: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    textAlign: 'center',
    marginTop: spacing.xs,
  },
  floatingPillContainer: {
    position: 'absolute',
    bottom: spacing.lg,
    right: spacing.base,
  },
});
