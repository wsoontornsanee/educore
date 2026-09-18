/**
 * Period Roll Call Screen (spec/09 §3 TCH-002, TCH-003, TCH-004).
 * 
 * Defaults all students to HADIR, pre-fills gate exceptions (ALPA with Gate badge),
 * high tap targets for ≤15s 32-student roll call, offline SQLite queuing.
 */
import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { submitPeriodAttendance } from '../services/agenda';
import { StatusBadge } from '../components/StatusBadge';
import { colors, radius, spacing, typography } from '../theme/tokens';
import { AttendanceStatus, PeriodAttendanceEntry, StudentRosterItem, TimetableSlotItem } from '../types';

interface RollCallScreenProps {
  slot: TimetableSlotItem;
  dateStr: string;
  initialRoster?: StudentRosterItem[];
  onBack: () => void;
  onSaved: (wasOffline: boolean) => void;
}

export const RollCallScreen: React.FC<RollCallScreenProps> = ({
  slot,
  dateStr,
  initialRoster = [],
  onBack,
  onSaved,
}) => {
  // Initialize state map: student_id -> status
  const [attendanceMap, setAttendanceMap] = useState<Record<number, AttendanceStatus>>(() => {
    const initial: Record<number, AttendanceStatus> = {};
    for (const student of initialRoster) {
      // Pre-fill from gate if no IN scan (TCH-003), else default HADIR (TCH-002)
      if (student.gate_status === 'NO_SCAN' || student.gate_status === 'OUT') {
        initial[student.student_id] = 'ALPA';
      } else {
        initial[student.student_id] = student.prefill_status || 'HADIR';
      }
    }
    return initial;
  });

  const [saving, setSaving] = useState(false);

  // Status counts for header KPI
  const counts = Object.values(attendanceMap).reduce(
    (acc, status) => {
      acc[status] = (acc[status] || 0) + 1;
      return acc;
    },
    { HADIR: 0, SAKIT: 0, IZIN: 0, ALPA: 0 } as Record<AttendanceStatus, number>
  );

  const handleSetStatus = (studentId: number, status: AttendanceStatus) => {
    setAttendanceMap((prev) => ({
      ...prev,
      [studentId]: status,
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    const entries: PeriodAttendanceEntry[] = initialRoster.map((s) => ({
      student_id: s.student_id,
      status: attendanceMap[s.student_id] || 'HADIR',
    }));

    try {
      const result = await submitPeriodAttendance(slot.id, dateStr, entries);
      if (result.success) {
        if (result.queuedOffline) {
          Alert.alert(
            'Tersimpan Offline',
            'Koneksi internet tidak tersedia. Presensi berhasil disimpan di perangkat dan akan disinkronkan saat sinyal pulih.',
            [{ text: 'OK', onPress: () => onSaved(true) }]
          );
        } else {
          Alert.alert('Presensi Berhasil Disimpan', 'Data presensi kelas berhasil diunggah ke server.', [
            { text: 'OK', onPress: () => onSaved(false) },
          ]);
        }
      } else {
        Alert.alert('Gagal Menyimpan', result.error || 'Terjadi kesalahan sistem.');
      }
    } catch (err: any) {
      Alert.alert('Gagal Menyimpan', err.message || 'Terjadi kesalahan.');
    } finally {
      setSaving(false);
    }
  };

  const renderStudentRow = ({ item }: { item: StudentRosterItem }) => {
    const currentStatus = attendanceMap[item.student_id] || 'HADIR';
    const isGatePrefill =
      (item.gate_status === 'NO_SCAN' || item.gate_status === 'OUT') && currentStatus === 'ALPA';

    return (
      <View style={styles.studentCard}>
        {/* Left Info */}
        <View style={styles.studentInfo}>
          <Text style={styles.studentName} numberOfLines={1}>
            {item.full_name}
          </Text>
          <View style={styles.metaRow}>
            <Text style={styles.studentIdText}>NIS: {item.nis || item.nisn || '-'}</Text>
            {isGatePrefill && (
              <View style={{ marginLeft: spacing.xs }}>
                <StatusBadge type="GATE" label="Tidak Scan Masuk" size="sm" />
              </View>
            )}
            {item.medical_flags && item.medical_flags.length > 0 && (
              <View style={styles.medicalPill}>
                <Text style={styles.medicalText}>Perlu Perhatian Khusus</Text>
              </View>
            )}
          </View>
        </View>

        {/* 1-Tap Quick Exception Toggles (H, S, I, A) */}
        <View style={styles.toggleGroup}>
          {(['HADIR', 'SAKIT', 'IZIN', 'ALPA'] as AttendanceStatus[]).map((statusKey) => {
            const isSelected = currentStatus === statusKey;
            let activeColor = colors.hadir;
            let activeBg = colors.hadirLight;
            let label = 'H';

            if (statusKey === 'SAKIT') {
              activeColor = colors.sakit;
              activeBg = colors.sakitLight;
              label = 'S';
            } else if (statusKey === 'IZIN') {
              activeColor = colors.izin;
              activeBg = colors.izinLight;
              label = 'I';
            } else if (statusKey === 'ALPA') {
              activeColor = colors.alpa;
              activeBg = colors.alpaLight;
              label = 'A';
            }

            return (
              <TouchableOpacity
                key={statusKey}
                style={[
                  styles.toggleButton,
                  isSelected && { backgroundColor: activeBg, borderColor: activeColor },
                ]}
                onPress={() => handleSetStatus(item.student_id, statusKey)}
                activeOpacity={0.7}
              >
                <Text
                  style={[
                    styles.toggleLabel,
                    isSelected ? { color: activeColor, fontWeight: '700' } : { color: colors.muted },
                  ]}
                >
                  {label}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={onBack}>
          <Text style={styles.backText}>← Batal</Text>
        </TouchableOpacity>
        <View style={styles.headerTitleBox}>
          <Text style={styles.headerTitle}>{slot.subject_name}</Text>
          <Text style={styles.headerSubtitle}>
            {slot.class_group_name} · Jam ke-{slot.period_no} ({slot.start_time} - {slot.end_time})
          </Text>
        </View>
      </View>

      {/* KPI Stats Bar */}
      <View style={styles.kpiBar}>
        <View style={styles.kpiItem}>
          <Text style={[styles.kpiValue, { color: colors.hadir }]}>{counts.HADIR}</Text>
          <Text style={styles.kpiLabel}>Hadir</Text>
        </View>
        <View style={styles.kpiItem}>
          <Text style={[styles.kpiValue, { color: colors.sakit }]}>{counts.SAKIT}</Text>
          <Text style={styles.kpiLabel}>Sakit</Text>
        </View>
        <View style={styles.kpiItem}>
          <Text style={[styles.kpiValue, { color: colors.izin }]}>{counts.IZIN}</Text>
          <Text style={styles.kpiLabel}>Izin</Text>
        </View>
        <View style={styles.kpiItem}>
          <Text style={[styles.kpiValue, { color: colors.alpa }]}>{counts.ALPA}</Text>
          <Text style={styles.kpiLabel}>Alpa</Text>
        </View>
      </View>

      {/* Student List */}
      <FlatList
        data={initialRoster}
        keyExtractor={(item) => String(item.student_id)}
        renderItem={renderStudentRow}
        contentContainerStyle={styles.listContent}
      />

      {/* Bottom Floating Save Button */}
      <View style={styles.bottomBar}>
        <TouchableOpacity
          style={styles.saveButton}
          onPress={handleSave}
          disabled={saving}
          activeOpacity={0.85}
        >
          {saving ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Text style={styles.saveButtonText}>
              SIMPAN PRESENSI ({initialRoster.length} SISWA)
            </Text>
          )}
        </TouchableOpacity>
      </View>
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
  },
  backButton: {
    paddingRight: spacing.base,
    paddingVertical: spacing.xs,
  },
  backText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  headerTitleBox: {
    flex: 1,
  },
  headerTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  headerSubtitle: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  kpiBar: {
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderColor: colors.border,
    flexDirection: 'row',
    justifyContent: 'space-around',
    paddingVertical: spacing.sm,
  },
  kpiItem: {
    alignItems: 'center',
  },
  kpiValue: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    fontFamily: typography.fontFamily.mono,
  },
  kpiLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    textTransform: 'uppercase',
  },
  listContent: {
    padding: spacing.base,
    paddingBottom: 80,
  },
  studentCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.card,
    padding: spacing.sm + 2,
    marginBottom: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  studentInfo: {
    flex: 1,
    marginRight: spacing.sm,
  },
  studentName: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  metaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    marginTop: 2,
  },
  studentIdText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    fontFamily: typography.fontFamily.mono,
  },
  medicalPill: {
    backgroundColor: '#FEF2F2',
    borderWidth: 1,
    borderColor: '#FECACA',
    borderRadius: radius.badge,
    paddingHorizontal: 4,
    marginLeft: spacing.xs,
  },
  medicalText: {
    fontSize: 9,
    color: colors.alpa,
    fontWeight: '600',
  },
  toggleGroup: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  toggleButton: {
    width: 44,
    minHeight: 44,    // PAR-016: was 38px — below 44dp minimum touch target, also clips text at 200% font scale
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 4,
    backgroundColor: colors.surfaceAlt,
  },
  toggleLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
  },
  bottomBar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: colors.white,
    borderTopWidth: 1,
    borderColor: colors.border,
    padding: spacing.base,
  },
  saveButton: {
    backgroundColor: colors.primary,
    borderRadius: radius.button,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    letterSpacing: 0.5,
  },
});
