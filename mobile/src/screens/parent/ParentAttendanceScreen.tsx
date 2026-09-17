/**
 * Parent Attendance: per-child attendance timeline and absence requests (spec/08 §2, PAR-011, ATT-002, PAR-015).
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { fetchAttendanceForChild } from '../../services/parentAttendance.ts';
import {
  MAX_ABSENCE_ATTACHMENT_BYTES,
  fetchAbsenceRequests,
  submitAbsenceRequest,
  validateAbsenceAttachment,
} from '../../services/absence.ts';
import { cacheGet, cacheSet } from '../../services/storage.ts';
import { todayWib } from '../../services/localDate.ts';
import { attendanceStatusLabel } from '../../constants/attendance.ts';
import { findAttendanceRowIndex } from '../../services/deepLink.ts';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type {
  AbsenceRequestItem,
  AbsenceRequestStatus,
  AbsenceType,
  AttendanceDayItem,
  ChildSummary,
} from '../../types/index.ts';

interface ParentAttendanceScreenProps {
  child: ChildSummary;
  highlightDate?: string | null;
}

type AttendanceSubTab = 'TIMELINE' | 'ABSENCE';

const STATUS_COLOR: Record<string, string> = {
  HADIR: colors.hadir,
  TERLAMBAT: colors.izin,
  SAKIT: colors.sakit,
  IZIN: colors.izin,
  ALPA: colors.alpa,
  DISPEN: colors.izin,
};

const ABSENCE_STATUS_CONFIG: Record<
  AbsenceRequestStatus,
  { label: string; bg: string; text: string }
> = {
  PENDING: { label: 'Menunggu Persetujuan', bg: colors.izinLight, text: colors.izin },
  APPROVED: { label: 'Disetujui', bg: colors.hadirLight, text: colors.hadir },
  REJECTED: { label: 'Ditolak', bg: colors.alpaLight, text: colors.alpa },
};

export const ParentAttendanceScreen: React.FC<ParentAttendanceScreenProps> = ({ child, highlightDate }) => {
  const [activeTab, setActiveTab] = useState<AttendanceSubTab>('TIMELINE');
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Timeline data
  const [days, setDays] = useState<AttendanceDayItem[]>([]);

  // Absence Requests data
  const [absenceList, setAbsenceList] = useState<AbsenceRequestItem[]>([]);

  // Submission Modal state
  const [modalVisible, setModalVisible] = useState(false);
  const [formType, setFormType] = useState<AbsenceType>('SAKIT');
  const [formDateFrom, setFormDateFrom] = useState(todayWib());
  const [formDateTo, setFormDateTo] = useState(todayWib());
  const [formReason, setFormReason] = useState('');
  const [formAttachmentName, setFormAttachmentName] = useState<string | null>(null);
  const [formAttachmentUri, setFormAttachmentUri] = useState<string | null>(null);
  const [formAttachmentSize, setFormAttachmentSize] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const flatListRef = useRef<FlatList<AttendanceDayItem>>(null);

  const timelineCacheKey = `educore_parent_attendance_${child.student_id}`;

  const loadTimelineData = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAttendanceForChild(child.student_id);
      const sorted = [...result].sort((a, b) => b.date.localeCompare(a.date));
      setDays(sorted);
      setOffline(false);
      setCachedAt(null);
      await cacheSet(timelineCacheKey, sorted);
    } catch {
      const cached = await cacheGet<AttendanceDayItem[]>(timelineCacheKey);
      if (cached) {
        setDays(cached.value);
        setCachedAt(cached.cachedAt);
      }
      setOffline(true);
    } finally {
      setLoading(false);
    }
  };

  const loadAbsenceData = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAbsenceRequests(child.student_id);
      setAbsenceList(result);
      setOffline(false);
      setCachedAt(null);
    } catch {
      setOffline(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'TIMELINE') {
      loadTimelineData();
    } else {
      loadAbsenceData();
    }
  }, [child.student_id, activeTab]);

  useEffect(() => {
    if (activeTab !== 'TIMELINE' || !highlightDate || days.length === 0) return;
    const index = findAttendanceRowIndex(days, highlightDate);
    if (index === -1) return;
    // scrollToIndex can throw if the target row hasn't been measured/laid
    // out yet on a long list — harmless to skip in that case, the row is
    // still visually highlighted below even if not auto-scrolled to.
    try {
      flatListRef.current?.scrollToIndex({ index, animated: true, viewPosition: 0.3 });
    } catch {
      // ignore
    }
  }, [days, highlightDate, activeTab]);

  const handleSelectSampleAttachment = (isOverLimit: boolean = false) => {
    if (isOverLimit) {
      // 1.5MB to trigger PAR-011 validation error
      setFormAttachmentName('surat_keterangan_besar.jpg');
      setFormAttachmentUri('file:///cache/surat_keterangan_besar.jpg');
      setFormAttachmentSize(1.5 * 1024 * 1024);
      setFormError('Ukuran lampiran maksimal 1MB (PAR-011). Silakan pilih foto dengan ukuran lebih kecil.');
    } else {
      // 350KB valid attachment
      setFormAttachmentName('surat_dokter.jpg');
      setFormAttachmentUri('file:///cache/surat_dokter.jpg');
      setFormAttachmentSize(350 * 1024);
      setFormError(null);
    }
  };

  const handleClearAttachment = () => {
    setFormAttachmentName(null);
    setFormAttachmentUri(null);
    setFormAttachmentSize(null);
    setFormError(null);
  };

  const handleSubmit = async () => {
    setFormError(null);
    if (!formReason.trim()) {
      setFormError('Alasan izin / sakit wajib diisi.');
      return;
    }
    if (formDateFrom > formDateTo) {
      setFormError('Tanggal selesai tidak boleh sebelum tanggal mulai.');
      return;
    }
    const validation = validateAbsenceAttachment(formAttachmentSize);
    if (!validation.valid) {
      setFormError(validation.error || 'Ukuran file melebihi batas 1MB.');
      return;
    }

    setSubmitting(true);
    try {
      await submitAbsenceRequest({
        student_id: child.student_id,
        type: formType,
        date_from: formDateFrom,
        date_to: formDateTo,
        reason: formReason.trim(),
        attachmentUri: formAttachmentUri,
        attachmentName: formAttachmentName,
        attachmentType: 'image/jpeg',
        attachmentSize: formAttachmentSize,
      });
      setModalVisible(false);
      setFormReason('');
      handleClearAttachment();
      await loadAbsenceData();
    } catch (err: any) {
      setFormError(err?.message || 'Gagal mengirim pengajuan. Silakan periksa koneksi internet Anda.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={styles.root}>
      {/* Sub-tab navigation */}
      <View style={styles.tabBar}>
        <TouchableOpacity
          style={[styles.tabButton, activeTab === 'TIMELINE' && styles.tabButtonActive]}
          onPress={() => setActiveTab('TIMELINE')}
          accessibilityRole="tab"
          accessibilityState={{ selected: activeTab === 'TIMELINE' }}
        >
          <Text style={[styles.tabText, activeTab === 'TIMELINE' && styles.tabTextActive]}>
            Riwayat Presensi
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tabButton, activeTab === 'ABSENCE' && styles.tabButtonActive]}
          onPress={() => setActiveTab('ABSENCE')}
          accessibilityRole="tab"
          accessibilityState={{ selected: activeTab === 'ABSENCE' }}
        >
          <Text style={[styles.tabText, activeTab === 'ABSENCE' && styles.tabTextActive]}>
            Pengajuan Izin / Sakit
          </Text>
        </TouchableOpacity>
      </View>

      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>Memuat data presensi...</Text>
        </View>
      ) : activeTab === 'TIMELINE' ? (
        <FlatList
          ref={flatListRef}
          data={days}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          onScrollToIndexFailed={() => {}}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>Belum ada data riwayat presensi.</Text>
            </View>
          }
          renderItem={({ item }) => (
            <View style={[styles.row, item.date === highlightDate && styles.rowHighlighted]}>
              <View style={[styles.dot, { backgroundColor: STATUS_COLOR[item.status] ?? colors.muted }]} />
              <View style={styles.rowText}>
                <Text style={styles.rowDate}>{item.date}</Text>
                <Text style={styles.rowStatus}>
                  {attendanceStatusLabel(item.status)}
                  {item.first_in_at ? ` — Tiba ${item.first_in_at}` : ''}
                </Text>
              </View>
            </View>
          )}
        />
      ) : (
        <View style={styles.tabContent}>
          {/* Header Action CTA */}
          <View style={styles.absenceHeader}>
            <TouchableOpacity
              style={[styles.ctaButton, offline && styles.ctaButtonDisabled]}
              disabled={offline}
              onPress={() => {
                setFormDateFrom(todayWib());
                setFormDateTo(todayWib());
                setFormReason('');
                handleClearAttachment();
                setModalVisible(true);
              }}
              accessibilityRole="button"
            >
              <Text style={styles.ctaButtonText}>+ Ajukan Izin / Sakit</Text>
            </TouchableOpacity>
            {offline && (
              <Text style={styles.offlineWarningText}>
                Pengajuan baru dinonaktifkan saat mode offline (PAR-015).
              </Text>
            )}
          </View>

          <FlatList
            data={absenceList}
            keyExtractor={(item) => String(item.id)}
            contentContainerStyle={styles.list}
            ListEmptyComponent={
              <View style={styles.emptyContainer}>
                <Text style={styles.emptyTitle}>Belum Ada Pengajuan</Text>
                <Text style={styles.emptyText}>
                  Pengajuan izin atau sakit untuk {child.full_name} akan tercantum di sini.
                </Text>
              </View>
            }
            renderItem={({ item }) => {
              const statusCfg = ABSENCE_STATUS_CONFIG[item.status] || ABSENCE_STATUS_CONFIG.PENDING;
              return (
                <View style={styles.absenceCard}>
                  <View style={styles.cardHeader}>
                    <View
                      style={[
                        styles.typeBadge,
                        item.type === 'SAKIT' ? styles.typeBadgeSakit : styles.typeBadgeIzin,
                      ]}
                    >
                      <Text
                        style={[
                          styles.typeBadgeText,
                          item.type === 'SAKIT' ? styles.typeTextSakit : styles.typeTextIzin,
                        ]}
                      >
                        {item.type === 'SAKIT' ? 'SAKIT' : 'IZIN'}
                      </Text>
                    </View>
                    <View style={[styles.statusBadge, { backgroundColor: statusCfg.bg }]}>
                      <Text style={[styles.statusBadgeText, { color: statusCfg.text }]}>
                        {statusCfg.label}
                      </Text>
                    </View>
                  </View>

                  <Text style={styles.dateRangeText}>
                    {item.date_from === item.date_to
                      ? item.date_from
                      : `${item.date_from} s/d ${item.date_to}`}
                  </Text>

                  <Text style={styles.reasonText}>
                    <Text style={styles.reasonLabel}>Alasan: </Text>
                    {item.reason}
                  </Text>

                  {item.attachment_url && (
                    <View style={styles.attachmentIndicator}>
                      <Text style={styles.attachmentIndicatorText}>📎 Foto / Surat Terlampir</Text>
                    </View>
                  )}

                  {item.decision_note ? (
                    <View style={styles.decisionBox}>
                      <Text style={styles.decisionNoteTitle}>Catatan Pihak Sekolah:</Text>
                      <Text style={styles.decisionNoteText}>{item.decision_note}</Text>
                      {item.decided_by_name && (
                        <Text style={styles.decidedByText}>Oleh: {item.decided_by_name}</Text>
                      )}
                    </View>
                  ) : null}
                </View>
              );
            }}
          />
        </View>
      )}

      {/* Submission Modal */}
      <Modal visible={modalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <ScrollView contentContainerStyle={styles.modalScroll}>
              <Text style={styles.modalTitle}>Ajukan Izin / Sakit</Text>
              <Text style={styles.modalSubtitle}>Siswa: {child.full_name}</Text>

              {formError && (
                <View style={styles.errorBanner}>
                  <Text style={styles.errorBannerText}>{formError}</Text>
                </View>
              )}

              {/* Type Selection */}
              <Text style={styles.inputLabel}>Jenis Pengajuan</Text>
              <View style={styles.typeSelector}>
                <TouchableOpacity
                  style={[styles.typeOption, formType === 'SAKIT' && styles.typeOptionActive]}
                  onPress={() => setFormType('SAKIT')}
                >
                  <Text style={[styles.typeOptionText, formType === 'SAKIT' && styles.typeOptionTextActive]}>
                    Sakit
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.typeOption, formType === 'IZIN' && styles.typeOptionActive]}
                  onPress={() => setFormType('IZIN')}
                >
                  <Text style={[styles.typeOptionText, formType === 'IZIN' && styles.typeOptionTextActive]}>
                    Izin
                  </Text>
                </TouchableOpacity>
              </View>

              {/* Dates */}
              <View style={styles.dateRow}>
                <View style={styles.dateCol}>
                  <Text style={styles.inputLabel}>Dari Tanggal</Text>
                  <TextInput
                    style={styles.input}
                    value={formDateFrom}
                    onChangeText={setFormDateFrom}
                    placeholder="YYYY-MM-DD"
                  />
                </View>
                <View style={styles.dateCol}>
                  <Text style={styles.inputLabel}>Sampai Tanggal</Text>
                  <TextInput
                    style={styles.input}
                    value={formDateTo}
                    onChangeText={setFormDateTo}
                    placeholder="YYYY-MM-DD"
                  />
                </View>
              </View>

              {/* Reason */}
              <Text style={styles.inputLabel}>Alasan / Keterangan</Text>
              <TextInput
                style={[styles.input, styles.textArea]}
                value={formReason}
                onChangeText={setFormReason}
                placeholder="Jelaskan alasan izin atau kondisi sakit..."
                multiline
                numberOfLines={3}
              />

              {/* Photo Attachment (PAR-011 limit 1MB) */}
              <Text style={styles.inputLabel}>Lampiran Foto / Surat Dokter (Maks. 1MB)</Text>
              {formAttachmentName ? (
                <View style={styles.attachmentPreview}>
                  <View style={styles.attachmentMeta}>
                    <Text style={styles.attachmentName} numberOfLines={1}>
                      {formAttachmentName}
                    </Text>
                    <Text style={styles.attachmentSize}>
                      {formAttachmentSize
                        ? `${(formAttachmentSize / 1024).toFixed(1)} KB`
                        : ''}
                    </Text>
                  </View>
                  <TouchableOpacity
                    style={styles.removeAttachmentButton}
                    onPress={handleClearAttachment}
                  >
                    <Text style={styles.removeAttachmentText}>Hapus</Text>
                  </TouchableOpacity>
                </View>
              ) : (
                <View style={styles.attachmentPickerRow}>
                  <TouchableOpacity
                    style={styles.pickFileButton}
                    onPress={() => handleSelectSampleAttachment(false)}
                  >
                    <Text style={styles.pickFileButtonText}>📷 Pilih Foto Surat</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={styles.pickFileButtonSecondary}
                    onPress={() => handleSelectSampleAttachment(true)}
                  >
                    <Text style={styles.pickFileButtonSecondaryText}>⚠️ Uji File &gt; 1MB</Text>
                  </TouchableOpacity>
                </View>
              )}

              {/* Actions */}
              <View style={styles.modalActions}>
                <TouchableOpacity
                  style={styles.cancelButton}
                  onPress={() => setModalVisible(false)}
                  disabled={submitting}
                >
                  <Text style={styles.cancelButtonText}>Batal</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.submitButton, submitting && styles.submitButtonDisabled]}
                  onPress={handleSubmit}
                  disabled={submitting}
                >
                  {submitting ? (
                    <ActivityIndicator size="small" color={colors.white} />
                  ) : (
                    <Text style={styles.submitButtonText}>Kirim Pengajuan</Text>
                  )}
                </TouchableOpacity>
              </View>
            </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.xl },
  loadingText: { marginTop: spacing.md, fontSize: typography.fontSize.sm, color: colors.muted },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  tabButton: {
    flex: 1,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    borderBottomWidth: 3,
    borderBottomColor: 'transparent',
  },
  tabButtonActive: {
    borderBottomColor: colors.primary,
  },
  tabText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.muted,
  },
  tabTextActive: {
    color: colors.primary,
  },
  tabContent: { flex: 1 },
  absenceHeader: {
    padding: spacing.base,
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  ctaButton: {
    backgroundColor: colors.primary,
    minHeight: 44,
    paddingHorizontal: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radius.button,
  },
  ctaButtonDisabled: {
    backgroundColor: colors.subtle,
  },
  ctaButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },
  offlineWarningText: {
    color: colors.offline,
    fontSize: typography.fontSize.xs,
    marginTop: spacing.xs,
    textAlign: 'center',
  },
  list: { padding: spacing.base },
  emptyContainer: {
    padding: spacing.xl,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.xl,
  },
  emptyTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  emptyText: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    textAlign: 'center',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.card,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  rowHighlighted: {
    borderColor: colors.primary,
    borderWidth: 2,
    backgroundColor: colors.surfaceAlt,
  },
  dot: { width: 10, height: 10, marginRight: spacing.md },
  rowText: { flex: 1 },
  rowDate: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  rowStatus: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: 2 },
  absenceCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.card,
    padding: spacing.base,
    marginBottom: spacing.md,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.sm,
  },
  typeBadge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.badge,
  },
  typeBadgeSakit: { backgroundColor: colors.sakitLight },
  typeBadgeIzin: { backgroundColor: colors.izinLight },
  typeBadgeText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
  },
  typeTextSakit: { color: colors.sakit },
  typeTextIzin: { color: colors.izin },
  statusBadge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.badge,
  },
  statusBadgeText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
  },
  dateRangeText: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  reasonText: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginBottom: spacing.sm,
  },
  reasonLabel: {
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
  },
  attachmentIndicator: {
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radius.badge,
    alignSelf: 'flex-start',
    marginBottom: spacing.sm,
  },
  attachmentIndicatorText: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
  },
  decisionBox: {
    marginTop: spacing.sm,
    padding: spacing.sm,
    backgroundColor: colors.surface,
    borderLeftWidth: 3,
    borderLeftColor: colors.primary,
  },
  decisionNoteTitle: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  decisionNoteText: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    marginTop: 2,
  },
  decidedByText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
    fontStyle: 'italic',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    padding: spacing.base,
  },
  modalCard: {
    backgroundColor: colors.white,
    borderRadius: radius.modal,
    maxHeight: '90%',
  },
  modalScroll: {
    padding: spacing.lg,
  },
  modalTitle: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  modalSubtitle: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    marginBottom: spacing.md,
  },
  errorBanner: {
    backgroundColor: colors.alpaLight,
    borderWidth: 1,
    borderColor: colors.alpa,
    padding: spacing.sm,
    borderRadius: radius.card,
    marginBottom: spacing.md,
  },
  errorBannerText: {
    color: colors.alpa,
    fontSize: typography.fontSize.xs,
  },
  inputLabel: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
    marginBottom: spacing.xs,
    marginTop: spacing.sm,
  },
  typeSelector: {
    flexDirection: 'row',
    marginBottom: spacing.sm,
  },
  typeOption: {
    flex: 1,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    marginRight: spacing.sm,
    minHeight: 44,
  },
  typeOptionActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  typeOptionText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.body,
  },
  typeOptionTextActive: {
    color: colors.white,
  },
  dateRow: {
    flexDirection: 'row',
    marginBottom: spacing.sm,
  },
  dateCol: {
    flex: 1,
    marginRight: spacing.sm,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.input,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: typography.fontSize.sm,
    color: colors.heading,
    minHeight: 44,
  },
  textArea: {
    minHeight: 80,
    textAlignVertical: 'top',
  },
  attachmentPickerRow: {
    flexDirection: 'row',
    marginTop: spacing.xs,
  },
  pickFileButton: {
    flex: 1,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    marginRight: spacing.sm,
  },
  pickFileButtonText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.body,
  },
  pickFileButtonSecondary: {
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
  },
  pickFileButtonSecondaryText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  attachmentPreview: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.card,
    padding: spacing.sm,
    marginTop: spacing.xs,
    minHeight: 44,
  },
  attachmentMeta: {
    flex: 1,
    marginRight: spacing.sm,
  },
  attachmentName: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  attachmentSize: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  removeAttachmentButton: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  removeAttachmentText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.alpa,
  },
  modalActions: {
    flexDirection: 'row',
    marginTop: spacing.lg,
    justifyContent: 'flex-end',
  },
  cancelButton: {
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.button,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.sm,
  },
  cancelButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.muted,
  },
  submitButton: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.xl,
    borderRadius: radius.button,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  submitButtonDisabled: {
    backgroundColor: colors.subtle,
  },
  submitButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.white,
  },
});
