/**
 * Parent Attendance: per-child attendance timeline (spec/08 §2 "Attendance").
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, StyleSheet, Text, View } from 'react-native';
import { fetchAttendanceForChild } from '../../services/parentAttendance';
import { cacheGet, cacheSet } from '../../services/storage';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { AttendanceDayItem, ChildSummary } from '../../types';

interface ParentAttendanceScreenProps {
  child: ChildSummary;
}

const STATUS_COLOR: Record<string, string> = {
  HADIR: colors.hadir,
  TERLAMBAT: colors.izin,
  SAKIT: colors.sakit,
  IZIN: colors.izin,
  ALPA: colors.alpa,
  DISPEN: colors.izin,
};

export const ParentAttendanceScreen: React.FC<ParentAttendanceScreenProps> = ({ child }) => {
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [days, setDays] = useState<AttendanceDayItem[]>([]);

  const cacheKey = `educore_parent_attendance_${child.student_id}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await fetchAttendanceForChild(child.student_id);
        if (cancelled) return;
        const sorted = [...result].sort((a, b) => b.date.localeCompare(a.date));
        setDays(sorted);
        setOffline(false);
        setCachedAt(null);
        await cacheSet(cacheKey, sorted);
      } catch {
        if (cancelled) return;
        const cached = await cacheGet<AttendanceDayItem[]>(cacheKey);
        if (cached) {
          setDays(cached.value);
          setCachedAt(cached.cachedAt);
        }
        setOffline(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />
      <FlatList
        data={days}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.emptyText}>Belum ada data presensi.</Text>}
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={[styles.dot, { backgroundColor: STATUS_COLOR[item.status] ?? colors.muted }]} />
            <View style={styles.rowText}>
              <Text style={styles.rowDate}>{item.date}</Text>
              <Text style={styles.rowStatus}>
                {item.status}{item.first_in_at ? ` — Tiba ${item.first_in_at}` : ''}
              </Text>
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
  list: { padding: spacing.base },
  emptyText: { fontSize: typography.fontSize.sm, color: colors.muted, textAlign: 'center', marginTop: spacing.xl },
  row: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.white,
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.card,
    padding: spacing.md, marginBottom: spacing.sm,
  },
  dot: { width: 10, height: 10, marginRight: spacing.md },
  rowText: { flex: 1 },
  rowDate: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowStatus: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: 2 },
});
