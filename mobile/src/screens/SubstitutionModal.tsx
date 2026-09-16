/**
 * Timetable Substitution Review Modal (spec/09 §3 TCH-015, ACD-019).
 * 
 * Displays substitution details, original teacher's notes, medical-flag summary,
 * and handles Accept / Decline with mandatory reason.
 */
import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { acceptSubstitution, declineSubstitution } from '../services/agenda';
import { colors, radius, spacing, typography } from '../theme/tokens';
import { TimetableSlotItem } from '../types';

interface SubstitutionModalProps {
  visible: boolean;
  slot: TimetableSlotItem | null;
  onClose: () => void;
  onResolved: () => void;
}

export const SubstitutionModal: React.FC<SubstitutionModalProps> = ({
  visible,
  slot,
  onClose,
  onResolved,
}) => {
  const [submitting, setSubmitting] = useState(false);
  const [showDeclineInput, setShowDeclineInput] = useState(false);
  const [declineReason, setDeclineReason] = useState('');

  if (!slot) return null;

  const handleAccept = async () => {
    if (!slot.substitution_id) return;
    setSubmitting(true);
    try {
      await acceptSubstitution(slot.substitution_id);
      Alert.alert('Tugas Diterima', 'Anda telah menerima tugas mengajar pengganti ini.', [
        {
          text: 'OK',
          onPress: () => {
            onClose();
            onResolved();
          },
        },
      ]);
    } catch (err: any) {
      Alert.alert('Gagal', err?.response?.data?.error || err.message || 'Gagal menerima tugas.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDecline = async () => {
    if (!slot.substitution_id) return;
    if (!declineReason.trim()) {
      Alert.alert('Alasan Wajib', 'Mohon isi alasan Anda tidak dapat mengajar.');
      return;
    }

    setSubmitting(true);
    try {
      await declineSubstitution(slot.substitution_id, declineReason.trim());
      Alert.alert(
        'Tugas Ditolak',
        'Penolakan dan alasan Anda telah diteruskan ke bagian kurikulum/admin.',
        [
          {
            text: 'OK',
            onPress: () => {
              setShowDeclineInput(false);
              setDeclineReason('');
              onClose();
              onResolved();
            },
          },
        ]
      );
    } catch (err: any) {
      Alert.alert('Gagal', err?.response?.data?.error || err.message || 'Gagal menolak tugas.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" transparent>
      <View style={styles.overlay}>
        <SafeAreaView style={styles.modalContent}>
          {/* Top Bar */}
          <View style={styles.header}>
            <View>
              <Text style={styles.badgeText}>PENUGASAN GURU PENGGANTI</Text>
              <Text style={styles.title}>{slot.subject_name}</Text>
            </View>
            <TouchableOpacity onPress={onClose} disabled={submitting}>
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>

          {/* Details Card */}
          <View style={styles.body}>
            <View style={styles.detailRow}>
              <Text style={styles.detailLabel}>GURU UTAMA</Text>
              <Text style={styles.detailValue}>
                {slot.original_teacher_name || 'Guru Berhalangan'}
              </Text>
            </View>

            <View style={styles.detailRow}>
              <Text style={styles.detailLabel}>KELAS & RUANG</Text>
              <Text style={styles.detailValue}>
                {slot.class_group_name} · Ruang {slot.room}
              </Text>
            </View>

            <View style={styles.detailRow}>
              <Text style={styles.detailLabel}>WAKTU MENGAJAR</Text>
              <Text style={styles.detailValue}>
                Jam ke-{slot.period_no} ({slot.start_time} - {slot.end_time})
              </Text>
            </View>

            {/* Medical / Attention Notes Flag */}
            <View style={styles.medicalNotice}>
              <Text style={styles.medicalNoticeTitle}>
                ℹ Catatan Khusus & Kesehatan Siswa (Flags-Only)
              </Text>
              <Text style={styles.medicalNoticeBody}>
                Terdapat 2 siswa di kelas ini dengan catatan kesehatan aktif (Asma, Alergi Makanan).
                Daftar lengkap bertanda khusus di lembar presensi.
              </Text>
            </View>

            {/* Lesson Plan Notes */}
            <View style={styles.lessonPlanBox}>
              <Text style={styles.lessonPlanTitle}>Modul / Materi Pengganti:</Text>
              <Text style={styles.lessonPlanBody}>
                Latihan soal Bab 3 (Halaman 45-48). Siswa diarahkan mengerjakan mandiri di buku
                latihan, dilanjutkan pembahasan kelompok.
              </Text>
            </View>

            {/* Decline Reason Input if toggled */}
            {showDeclineInput && (
              <View style={styles.declineBox}>
                <Text style={styles.declineLabel}>ALASAN TIDAK DAPAT MENGAJAR (WAJIB)</Text>
                <TextInput
                  style={styles.declineInput}
                  placeholder="Contoh: Jadwal bertabrakan / Sedang rapat dinas luar..."
                  placeholderTextColor={colors.subtle}
                  value={declineReason}
                  onChangeText={setDeclineReason}
                  multiline
                  numberOfLines={3}
                />
              </View>
            )}
          </View>

          {/* Action Buttons */}
          <View style={styles.footer}>
            {submitting ? (
              <ActivityIndicator size="small" color={colors.primary} />
            ) : showDeclineInput ? (
              <View style={styles.actionRow}>
                <TouchableOpacity
                  style={[styles.btn, styles.btnCancel]}
                  onPress={() => setShowDeclineInput(false)}
                >
                  <Text style={styles.btnCancelText}>Kembali</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.btn, styles.btnDanger]}
                  onPress={handleDecline}
                >
                  <Text style={styles.btnDangerText}>Kirim Penolakan</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <View style={styles.actionRow}>
                <TouchableOpacity
                  style={[styles.btn, styles.btnSecondary]}
                  onPress={() => setShowDeclineInput(true)}
                >
                  <Text style={styles.btnSecondaryText}>Tidak Bisa Mengajar</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.btn, styles.btnPrimary]}
                  onPress={handleAccept}
                >
                  <Text style={styles.btnPrimaryText}>Terima Tugas</Text>
                </TouchableOpacity>
              </View>
            )}
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(15, 23, 42, 0.7)',
    justifyContent: 'center',
    padding: spacing.base,
  },
  modalContent: {
    backgroundColor: colors.white,
    borderRadius: radius.modal,
    borderWidth: 1,
    borderColor: colors.borderDark,
    maxHeight: '90%',
  },
  header: {
    padding: spacing.base,
    borderBottomWidth: 1,
    borderColor: colors.border,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  badgeText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.substitute,
    letterSpacing: 1,
  },
  title: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginTop: 2,
  },
  closeText: {
    fontSize: 20,
    color: colors.muted,
    padding: spacing.xs,
  },
  body: {
    padding: spacing.base,
  },
  detailRow: {
    marginBottom: spacing.sm,
  },
  detailLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    fontWeight: typography.fontWeight.bold,
    fontFamily: typography.fontFamily.mono,
  },
  detailValue: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
    marginTop: 1,
  },
  medicalNotice: {
    backgroundColor: '#FFFBEB',
    borderWidth: 1,
    borderColor: '#FDE68A',
    borderRadius: radius.card,
    padding: spacing.sm,
    marginVertical: spacing.sm,
  },
  medicalNoticeTitle: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: '#92400E',
  },
  medicalNoticeBody: {
    fontSize: typography.fontSize.xs,
    color: '#B45309',
    marginTop: 2,
    lineHeight: 16,
  },
  lessonPlanBox: {
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.card,
    padding: spacing.sm,
    marginTop: spacing.xs,
  },
  lessonPlanTitle: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  lessonPlanBody: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    marginTop: 4,
    lineHeight: 18,
  },
  declineBox: {
    marginTop: spacing.md,
  },
  declineLabel: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.alpa,
    marginBottom: 4,
  },
  declineInput: {
    borderWidth: 1,
    borderColor: colors.alpa,
    borderRadius: radius.input,
    padding: spacing.sm,
    fontSize: typography.fontSize.sm,
    color: colors.heading,
    backgroundColor: colors.white,
    textAlignVertical: 'top',
  },
  footer: {
    padding: spacing.base,
    borderTopWidth: 1,
    borderColor: colors.border,
  },
  actionRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  btn: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: radius.button,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnPrimary: {
    backgroundColor: colors.substitute,
    marginLeft: spacing.xs,
  },
  btnPrimaryText: {
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
    fontSize: typography.fontSize.sm,
  },
  btnSecondary: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
    marginRight: spacing.xs,
  },
  btnSecondaryText: {
    color: colors.body,
    fontWeight: typography.fontWeight.semibold,
    fontSize: typography.fontSize.sm,
  },
  btnCancel: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
    marginRight: spacing.xs,
  },
  btnCancelText: {
    color: colors.body,
    fontWeight: typography.fontWeight.semibold,
  },
  btnDanger: {
    backgroundColor: colors.alpa,
    marginLeft: spacing.xs,
  },
  btnDangerText: {
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
  },
});
