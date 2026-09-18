/**
 * Behaviour Recording Modal (spec/09 TCH-008/009).
 *
 * Multi-step flow (≤3 taps from teacher agenda):
 *   Step 1: Pick student from class roster
 *   Step 2: Pick reason from school-configured catalogue (TCH-009)
 *   Step 3: Optional note → confirm
 *
 * Each step is a full-screen slide inside the modal. The step counter and
 * back button persist across all steps so the user always knows where they
 * are and can correct a mistake without cancelling entirely.
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { fetchBehaviourReasons, submitBehaviourRecord } from '../services/behaviour.ts';
import { colors, radius, spacing, typography } from '../theme/tokens.ts';
import type { BehaviourReason, BehaviourStep, StudentRosterItem } from '../types/index.ts';

interface BehaviourModalProps {
  visible: boolean;
  students: StudentRosterItem[];
  onClose: () => void;
  onSaved: () => void;
}

export const BehaviourModal: React.FC<BehaviourModalProps> = ({
  visible,
  students,
  onClose,
  onSaved,
}) => {
  const [step, setStep] = useState<BehaviourStep>('SELECT_STUDENT');
  const [selectedStudent, setSelectedStudent] = useState<StudentRosterItem | null>(null);
  const [selectedReason, setSelectedReason] = useState<BehaviourReason | null>(null);
  const [note, setNote] = useState('');

  const [reasons, setReasons] = useState<BehaviourReason[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filter: positive at top, then negative
  const [filter, setFilter] = useState<'ALL' | 'POSITIVE' | 'NEGATIVE'>('ALL');

  useEffect(() => {
    if (!visible) return;
    // Reset state on open
    setStep('SELECT_STUDENT');
    setSelectedStudent(null);
    setSelectedReason(null);
    setNote('');
    setError(null);
    setFilter('ALL');

    // Fetch reasons on mount
    (async () => {
      setLoading(true);
      try {
        const result = await fetchBehaviourReasons();
        setReasons(result);
      } catch {
        setError('Tidak dapat memuat daftar alasan. Periksa koneksi Anda.');
      } finally {
        setLoading(false);
      }
    })();
  }, [visible]);

  const handleSelectStudent = (student: StudentRosterItem) => {
    setSelectedStudent(student);
    setStep('SELECT_REASON');
  };

  const handleSelectReason = (reason: BehaviourReason) => {
    setSelectedReason(reason);
    setStep('ADD_NOTE');
  };

  const handleConfirm = async () => {
    if (!selectedStudent || !selectedReason) return;
    setSaving(true);
    setError(null);
    try {
      await submitBehaviourRecord({
        student_id: selectedStudent.student_id,
        reason_id: selectedReason.id,
        note: note.trim() || undefined,
        occurred_at: new Date().toISOString(),
      });
      onSaved();
      onClose();
    } catch (err: any) {
      setError(err?.message || 'Gagal menyimpan. Coba lagi.');
    } finally {
      setSaving(false);
    }
  };

  const filteredReasons = reasons.filter((r) => {
    if (filter === 'POSITIVE') return r.is_positive;
    if (filter === 'NEGATIVE') return !r.is_positive;
    return true;
  });

  const stepLabels = ['Pilih Siswa', 'Pilih Alasan', 'Catatan (opsional)'];

  const renderStudentList = () => (
    <ScrollView style={styles.listContainer}>
      {students.map((s) => (
        <TouchableOpacity
          key={s.student_id}
          style={styles.listItem}
          onPress={() => handleSelectStudent(s)}
          accessibilityRole="button"
          accessibilityLabel={`Pilih ${s.full_name}`}
        >
          <View style={styles.studentAvatar}>
            <Text style={styles.studentAvatarText}>
              {s.full_name.charAt(0).toUpperCase()}
            </Text>
          </View>
          <View style={styles.studentInfo}>
            <Text style={styles.studentName}>{s.full_name}</Text>
            <Text style={styles.studentNis}>{s.nis || s.nisn || '-'}</Text>
          </View>
          {s.medical_flags && s.medical_flags.length > 0 && (
            <Text style={styles.medicalFlag}>⚠️</Text>
          )}
        </TouchableOpacity>
      ))}
    </ScrollView>
  );

  const renderReasonList = () => (
    <View style={{ flex: 1 }}>
      {/* Filter chips: Semua / Positif / Negatif */}
      <View style={styles.filterRow}>
        {(['ALL', 'POSITIVE', 'NEGATIVE'] as const).map((f) => (
          <TouchableOpacity
            key={f}
            style={[styles.filterChip, filter === f && styles.filterChipActive]}
            onPress={() => setFilter(f)}
            accessibilityRole="button"
            accessibilityState={{ selected: filter === f }}
          >
            <Text style={[styles.filterChipText, filter === f && styles.filterChipTextActive]}>
              {f === 'ALL' ? 'Semua' : f === 'POSITIVE' ? 'Positif' : 'Negatif'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {loading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView style={styles.listContainer}>
          {filteredReasons.map((r) => (
            <TouchableOpacity
              key={r.id}
              style={[styles.reasonItem, r.is_positive ? styles.reasonPositive : styles.reasonNegative]}
              onPress={() => handleSelectReason(r)}
              accessibilityRole="button"
              accessibilityLabel={`${r.name}, ${r.point_value} poin`}
            >
              <View style={styles.reasonInfo}>
                <Text style={styles.reasonName}>{r.name}</Text>
                {r.description ? (
                  <Text style={styles.reasonDesc}>{r.description}</Text>
                ) : null}
              </View>
              <View style={[styles.pointBadge, r.is_positive ? styles.pointBadgePositive : styles.pointBadgeNegative]}>
                <Text style={[styles.pointText, r.is_positive ? styles.pointTextPositive : styles.pointTextNegative]}>
                  {r.is_positive ? '+' : ''}{r.point_value}
                </Text>
              </View>
            </TouchableOpacity>
          ))}
          {filteredReasons.length === 0 && !loading && (
            <View style={styles.centerContainer}>
              <Text style={styles.emptyText}>Tidak ada alasan untuk filter ini.</Text>
            </View>
          )}
        </ScrollView>
      )}
    </View>
  );

  const renderNoteStep = () => (
    <View style={styles.noteContainer}>
      <View style={styles.summaryCard}>
        <Text style={styles.summaryLabel}>Siswa</Text>
        <Text style={styles.summaryValue}>{selectedStudent?.full_name}</Text>
        <View style={styles.summaryDivider} />
        <Text style={styles.summaryLabel}>Alasan</Text>
        <Text style={styles.summaryValue}>
          {selectedReason?.name} ({selectedReason!.is_positive ? '+' : ''}{selectedReason!.point_value})
        </Text>
      </View>

      <Text style={styles.noteLabel}>Catatan (opsional)</Text>
      <TextInput
        style={styles.noteInput}
        value={note}
        onChangeText={setNote}
        placeholder="Tambahkan catatan..."
        placeholderTextColor={colors.subtle}
        multiline
        numberOfLines={3}
      />

      {error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      <TouchableOpacity
        style={[styles.confirmButton, saving && styles.confirmButtonDisabled]}
        onPress={handleConfirm}
        disabled={saving}
        accessibilityRole="button"
        accessibilityLabel="Simpan catatan perilaku"
      >
        {saving ? (
          <ActivityIndicator size="small" color={colors.white} />
        ) : (
          <Text style={styles.confirmButtonText}>SIMPAN</Text>
        )}
      </TouchableOpacity>
    </View>
  );

  const stepIndex = step === 'SELECT_STUDENT' ? 0 : step === 'SELECT_REASON' ? 1 : 2;

  return (
    <Modal visible={visible} animationType="slide" transparent>
      <View style={styles.overlay}>
        <View style={styles.card}>
          {/* Header */}
          <View style={styles.header}>
            <TouchableOpacity
              onPress={step === 'SELECT_STUDENT' ? onClose : () => setStep(step === 'SELECT_REASON' ? 'SELECT_STUDENT' : 'SELECT_REASON')}
              style={styles.backButton}
              accessibilityRole="button"
              accessibilityLabel="Kembali"
            >
              <Text style={styles.backText}>←</Text>
            </TouchableOpacity>
            <View style={styles.headerCenter}>
              <Text style={styles.headerTitle}>Catat Perilaku</Text>
              <Text style={styles.headerStep}>{stepLabels[stepIndex]} ({stepIndex + 1}/3)</Text>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.closeButton} accessibilityRole="button" accessibilityLabel="Tutup">
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>

          {/* Step indicator */}
          <View style={styles.stepIndicator}>
            {[0, 1, 2].map((i) => (
              <View key={i} style={[styles.stepDot, i <= stepIndex && styles.stepDotActive]} />
            ))}
          </View>

          {/* Step content */}
          <View style={styles.content}>
            {step === 'SELECT_STUDENT' && renderStudentList()}
            {step === 'SELECT_REASON' && renderReasonList()}
            {step === 'ADD_NOTE' && renderNoteStep()}
          </View>
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  card: {
    backgroundColor: colors.white,
    borderTopLeftRadius: 0,
    borderTopRightRadius: 0,
    maxHeight: '90%',
    minHeight: '60%',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  backButton: {
    minWidth: 44,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'flex-start',
  },
  backText: {
    fontSize: typography.fontSize.xl,
    color: colors.primary,
    fontWeight: typography.fontWeight.bold,
  },
  headerCenter: {
    flex: 1,
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  headerStep: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  closeButton: {
    minWidth: 44,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'flex-end',
  },
  closeText: {
    fontSize: typography.fontSize.lg,
    color: colors.muted,
    fontWeight: typography.fontWeight.bold,
  },
  stepIndicator: {
    flexDirection: 'row',
    justifyContent: 'center',
    paddingVertical: spacing.sm,
    gap: spacing.sm,
  },
  stepDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.border,
  },
  stepDotActive: {
    backgroundColor: colors.primary,
    width: 24,
  },
  content: {
    flex: 1,
  },
  listContainer: {
    flex: 1,
    paddingHorizontal: spacing.base,
  },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.xl,
  },
  emptyText: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    textAlign: 'center',
  },
  // Student list
  listItem: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 56,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceAlt,
  },
  studentAvatar: {
    width: 40,
    height: 40,
    backgroundColor: colors.primaryLight,
    borderWidth: 1,
    borderColor: colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: spacing.md,
  },
  studentAvatarText: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  studentInfo: {
    flex: 1,
  },
  studentName: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
  },
  studentNis: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  medicalFlag: {
    fontSize: typography.fontSize.sm,
    marginLeft: spacing.sm,
  },
  // Reason list
  filterRow: {
    flexDirection: 'row',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
  },
  filterChip: {
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    minHeight: 36,
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.borderDark,
  },
  filterChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  filterChipText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.body,
  },
  filterChipTextActive: {
    color: colors.white,
  },
  reasonItem: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 56,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceAlt,
  },
  reasonPositive: {
    borderLeftWidth: 3,
    borderLeftColor: colors.hadir,
  },
  reasonNegative: {
    borderLeftWidth: 3,
    borderLeftColor: colors.alpa,
  },
  reasonInfo: {
    flex: 1,
    marginRight: spacing.sm,
  },
  reasonName: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
  },
  reasonDesc: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  pointBadge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    minWidth: 36,
    alignItems: 'center',
  },
  pointBadgePositive: {
    backgroundColor: colors.hadirLight,
    borderWidth: 1,
    borderColor: colors.hadir,
  },
  pointBadgeNegative: {
    backgroundColor: colors.alpaLight,
    borderWidth: 1,
    borderColor: colors.alpa,
  },
  pointText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
  },
  pointTextPositive: {
    color: colors.hadir,
  },
  pointTextNegative: {
    color: colors.alpa,
  },
  // Note step
  noteContainer: {
    flex: 1,
    padding: spacing.base,
  },
  summaryCard: {
    backgroundColor: colors.surfaceAlt,
    padding: spacing.base,
    marginBottom: spacing.lg,
  },
  summaryLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    fontWeight: typography.fontWeight.semibold,
    marginBottom: 2,
  },
  summaryValue: {
    fontSize: typography.fontSize.base,
    color: colors.heading,
    fontWeight: typography.fontWeight.semibold,
    marginBottom: spacing.sm,
  },
  summaryDivider: {
    height: 1,
    backgroundColor: colors.border,
    marginVertical: spacing.sm,
  },
  noteLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
    marginBottom: spacing.sm,
  },
  noteInput: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    padding: spacing.md,
    fontSize: typography.fontSize.base,
    color: colors.heading,
    minHeight: 80,
    textAlignVertical: 'top',
    marginBottom: spacing.lg,
  },
  errorBox: {
    backgroundColor: colors.alpaLight,
    borderWidth: 1,
    borderColor: colors.alpa,
    padding: spacing.sm,
    marginBottom: spacing.md,
  },
  errorText: {
    color: colors.alpa,
    fontSize: typography.fontSize.sm,
  },
  confirmButton: {
    backgroundColor: colors.primary,
    minHeight: 48,
    justifyContent: 'center',
    alignItems: 'center',
  },
  confirmButtonDisabled: {
    backgroundColor: colors.subtle,
  },
  confirmButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },
});
