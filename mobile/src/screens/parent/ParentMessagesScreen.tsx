/**
 * Parent Messages: unified inbox for school announcements (broadcasts) and
 * permission slips (spec/08 §2 Messages tab, PAR-012, PAR-015, PAR-016).
 *
 * Segmented control: "Pengumuman" (broadcasts) | "Izin" (permission slips).
 * Broadcasts are read-only; permission slips support digital signature.
 * Offline degradation follows PAR-015: cached data shown with staleness stamp,
 * write actions disabled.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  acknowledgePermissionSlip,
  fetchStudentPermissionSlips,
  resolveSlipStatus,
} from '../../services/permissionSlips.ts';
import { fetchStudentBroadcasts } from '../../services/broadcasts.ts';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type { BroadcastItem, ChildSummary, PermissionSlipItem, PermissionSlipResponse } from '../../types/index.ts';

interface ParentMessagesScreenProps {
  child: ChildSummary;
}

type ScreenState = 'LOADING' | 'EMPTY' | 'READY' | 'ERROR';
type MessagesTab = 'ANNOUNCEMENTS' | 'PERMISSION_SLIPS';

const RESPONSE_CONFIG: Record<PermissionSlipResponse, { label: string; bg: string; text: string }> = {
  APPROVED: { label: 'Disetujui', bg: colors.hadirLight, text: colors.hadir },
  DECLINED: { label: 'Ditolak', bg: colors.alpaLight, text: colors.alpa },
  PENDING: { label: 'Menunggu Tanda Tangan', bg: colors.izinLight, text: colors.izin },
};

const TAB_OPTIONS: { key: MessagesTab; label: string }[] = [
  { key: 'ANNOUNCEMENTS', label: 'Pengumuman' },
  { key: 'PERMISSION_SLIPS', label: 'Izin' },
];

export const ParentMessagesScreen: React.FC<ParentMessagesScreenProps> = ({ child }) => {
  const [activeTab, setActiveTab] = useState<MessagesTab>('ANNOUNCEMENTS');
  const [bcState, setBcState] = useState<ScreenState>('LOADING');
  const [slipState, setSlipState] = useState<ScreenState>('LOADING');
  const [broadcasts, setBroadcasts] = useState<BroadcastItem[]>([]);
  const [slips, setSlips] = useState<PermissionSlipItem[]>([]);
  const [offline, setOffline] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  // Sign modal state
  const [signSlip, setSignSlip] = useState<PermissionSlipItem | null>(null);
  const [signature, setSignature] = useState('');
  const [signError, setSignError] = useState<string | null>(null);
  const [signing, setSigning] = useState(false);

  const loadBroadcasts = useCallback(async () => {
    setBcState('LOADING');
    try {
      const result = await fetchStudentBroadcasts(child.student_id);
      setBroadcasts(result.broadcasts);
      setOffline(result.isOfflineCached);
      setLastUpdated(result.lastUpdated);
      setBcState(result.broadcasts.length === 0 ? 'EMPTY' : 'READY');
    } catch {
      setBroadcasts([]);
      setOffline(true);
      setBcState('ERROR');
    }
  }, [child.student_id]);

  const loadSlips = useCallback(async () => {
    setSlipState('LOADING');
    try {
      const result = await fetchStudentPermissionSlips(child.student_id);
      setSlips(result.slips);
      setOffline(result.isOfflineCached);
      setLastUpdated(result.lastUpdated);
      setSlipState(result.slips.length === 0 ? 'EMPTY' : 'READY');
    } catch {
      setSlips([]);
      setOffline(true);
      setSlipState('ERROR');
    }
  }, [child.student_id]);

  useEffect(() => {
    loadBroadcasts();
    loadSlips();
  }, [loadBroadcasts, loadSlips]);

  const openSignModal = (slip: PermissionSlipItem) => {
    setSignSlip(slip);
    setSignature('');
    setSignError(null);
  };

  const submitAcknowledgement = async (response: 'APPROVED' | 'DECLINED') => {
    if (!signSlip) return;
    if (!signature.trim()) {
      setSignError('Tanda tangan wajib diisi: ketik nama lengkap Anda.');
      return;
    }
    setSigning(true);
    setSignError(null);
    try {
      await acknowledgePermissionSlip(signSlip.id, {
        student_id: child.student_id,
        response,
        signature: signature.trim(),
      });
      setSignSlip(null);
      await loadSlips();
    } catch (err: any) {
      const message =
        err?.data?.error ||
        err?.message ||
        'Gagal mengirim persetujuan. Coba lagi.';
      setSignError(String(message));
    } finally {
      setSigning(false);
    }
  };

  const renderBroadcast = ({ item }: { item: BroadcastItem }) => (
    <View style={styles.card}>
      <Text style={styles.cardTitle} accessibilityLabel={`Pengumuman: ${item.title}`}>
        {item.title}
      </Text>
      <Text style={styles.cardBody}>{item.body}</Text>
      <View style={styles.metaRow}>
        <Text style={styles.cardMeta}>
          {item.sender_name} &middot; {item.class_group_name}
        </Text>
        <Text style={styles.cardMeta}>{item.sent_at}</Text>
      </View>
    </View>
  );

  const renderSlip = ({ item }: { item: PermissionSlipItem }) => {
    const status = resolveSlipStatus(item);
    const config = RESPONSE_CONFIG[status];
    const eventLine = item.event_date
      ? `Tanggal acara: ${item.event_date}${item.location ? ` • ${item.location}` : ''}`
      : item.location
        ? `Lokasi: ${item.location}`
        : null;

    return (
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitle} accessibilityLabel={`Izin: ${item.title}`}>
            {item.title}
          </Text>
          <View style={[styles.badge, { backgroundColor: config.bg }]}>
            <Text style={[styles.badgeText, { color: config.text }]}>{config.label}</Text>
          </View>
        </View>

        {!!item.description && <Text style={styles.cardBody}>{item.description}</Text>}
        {!!eventLine && <Text style={styles.cardMeta}>{eventLine}</Text>}
        {!!item.due_at && !item.is_closed && (
          <Text style={styles.cardMeta}>Batas respon: {item.due_at}</Text>
        )}
        {item.is_closed && <Text style={styles.cardClosed}>Jendela persetujuan telah ditutup.</Text>}
        {!!item.my_responded_at && status !== 'PENDING' && (
          <Text style={styles.cardMeta}>Ditandatangani: {item.my_responded_at}</Text>
        )}

        {!item.is_closed && item.my_pending && (
          <View style={styles.actionRow}>
            <TouchableOpacity
              style={[styles.actionButton, styles.actionApprove]}
              onPress={() => openSignModal(item)}
              disabled={offline}
              accessibilityLabel={`Setujui ${item.title}`}
              accessibilityRole="button">
              <Text style={styles.actionButtonText}>Setujui</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.actionButton, styles.actionDecline]}
              onPress={() => openSignModal(item)}
              disabled={offline}
              accessibilityLabel={`Tolak ${item.title}`}
              accessibilityRole="button">
              <Text style={styles.actionButtonText}>Tolak</Text>
            </TouchableOpacity>
          </View>
        )}
        {offline && item.my_pending && !item.is_closed && (
          <Text style={styles.offlineNote}>
            Tanda tangan tidak tersedia saat offline. Sambungkan internet lalu coba lagi.
          </Text>
        )}
      </View>
    );
  };

  const renderTabContent = () => {
    if (activeTab === 'ANNOUNCEMENTS') {
      if (bcState === 'LOADING') {
        return (
          <View style={styles.centered} accessibilityLabel="Memuat">
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.centeredText}>Memuat pengumuman...</Text>
          </View>
        );
      }
      if (bcState === 'EMPTY') {
        return (
          <View style={styles.centered}>
            <Text style={styles.emptyTitle}>Belum Ada Pengumuman</Text>
            <Text style={styles.centeredText}>
              Belum ada pengumuman dari sekolah untuk {child.full_name}.
            </Text>
          </View>
        );
      }
      if (bcState === 'ERROR') {
        return (
          <View style={styles.centered}>
            <Text style={styles.emptyTitle}>Gagal Memuat</Text>
            <Text style={styles.centeredText}>
              Tidak dapat memuat pengumuman. Periksa koneksi Anda lalu coba lagi.
            </Text>
            <TouchableOpacity style={styles.retryButton} onPress={loadBroadcasts} accessibilityRole="button">
              <Text style={styles.actionButtonText}>Coba Lagi</Text>
            </TouchableOpacity>
          </View>
        );
      }
      return (
        <FlatList
          data={broadcasts}
          keyExtractor={(item) => `bc-${item.id}`}
          renderItem={renderBroadcast}
          contentContainerStyle={styles.list}
        />
      );
    }

    // PERMISSION_SLIPS tab
    if (slipState === 'LOADING') {
      return (
        <View style={styles.centered} accessibilityLabel="Memuat">
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.centeredText}>Memuat izin...</Text>
        </View>
      );
    }
    if (slipState === 'EMPTY') {
      return (
        <View style={styles.centered}>
          <Text style={styles.emptyTitle}>Belum Ada Izin</Text>
          <Text style={styles.centeredText}>
            Belum ada permintaan izin dari sekolah untuk {child.full_name}.
          </Text>
        </View>
      );
    }
    if (slipState === 'ERROR') {
      return (
        <View style={styles.centered}>
          <Text style={styles.emptyTitle}>Gagal Memuat</Text>
          <Text style={styles.centeredText}>
            Tidak dapat memuat daftar izin. Periksa koneksi Anda lalu coba lagi.
          </Text>
          <TouchableOpacity style={styles.retryButton} onPress={loadSlips} accessibilityRole="button">
            <Text style={styles.actionButtonText}>Coba Lagi</Text>
          </TouchableOpacity>
        </View>
      );
    }
    return (
      <FlatList
        data={slips}
        keyExtractor={(item) => `slip-${item.id}`}
        renderItem={renderSlip}
        contentContainerStyle={styles.list}
      />
    );
  };

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Pesan &amp; Izin</Text>
        <Text style={styles.headerSubtitle}>{child.full_name}</Text>
      </View>

      {/* Segmented control */}
      <View style={styles.segmentRow}>
        {TAB_OPTIONS.map((tab) => {
          const isActive = activeTab === tab.key;
          return (
            <TouchableOpacity
              key={tab.key}
              style={[styles.segmentTab, isActive && styles.segmentTabActive]}
              onPress={() => setActiveTab(tab.key)}
              accessibilityRole="tab"
              accessibilityState={{ selected: isActive }}
              accessibilityLabel={tab.label}>
              <Text style={[styles.segmentText, isActive && styles.segmentTextActive]}>
                {tab.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <StaleOfflineBanner isOffline={offline} lastSyncedAt={lastUpdated} />

      {renderTabContent()}

      <Modal visible={signSlip !== null} transparent animationType="slide" onRequestClose={() => setSignSlip(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Tanda Tangan Digital</Text>
            {!!signSlip && <Text style={styles.modalSubtitle}>{signSlip.title}</Text>}
            <Text style={styles.modalNote}>
              Ketik nama lengkap Anda sebagai tanda tangan digital. Waktu persetujuan dicatat otomatis
              oleh sistem.
            </Text>
            <TextInput
              style={styles.signatureInput}
              placeholder="Nama lengkap Anda"
              placeholderTextColor={colors.subtle}
              value={signature}
              onChangeText={setSignature}
              editable={!signing}
              accessibilityLabel="Kolom tanda tangan digital"
            />
            {!!signError && <Text style={styles.signError}>{signError}</Text>}
            <View style={styles.modalActions}>
              <TouchableOpacity
                style={[styles.actionButton, styles.actionDecline]}
                onPress={() => submitAcknowledgement('DECLINED')}
                disabled={signing}
                accessibilityRole="button">
                <Text style={styles.actionButtonText}>Tolak</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.actionButton, styles.actionApprove]}
                onPress={() => submitAcknowledgement('APPROVED')}
                disabled={signing}
                accessibilityRole="button">
                {signing ? (
                  <ActivityIndicator size="small" color={colors.white} />
                ) : (
                  <Text style={styles.actionButtonText}>Setujui</Text>
                )}
              </TouchableOpacity>
            </View>
            <TouchableOpacity
              style={styles.modalCancel}
              onPress={() => setSignSlip(null)}
              disabled={signing}
              accessibilityRole="button">
              <Text style={styles.modalCancelText}>Batal</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    backgroundColor: colors.white,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  headerTitle: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  headerSubtitle: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: 2 },
  segmentRow: {
    flexDirection: 'row',
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  segmentTab: {
    flex: 1,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  segmentTabActive: { borderBottomColor: colors.primary },
  segmentText: { fontSize: typography.fontSize.sm, color: colors.muted, fontWeight: typography.fontWeight.medium },
  segmentTextActive: { color: colors.primary, fontWeight: typography.fontWeight.bold },
  list: { padding: spacing.md, gap: spacing.md },
  card: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: spacing.sm },
  cardTitle: { flex: 1, fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 4, borderRadius: radius.none },
  badgeText: { fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold },
  cardBody: { fontSize: typography.fontSize.sm, color: colors.body, marginTop: spacing.sm, lineHeight: 20 },
  cardMeta: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: spacing.xs },
  metaRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: spacing.xs },
  cardClosed: { fontSize: typography.fontSize.xs, color: colors.alpa, marginTop: spacing.xs, fontWeight: typography.fontWeight.medium },
  actionRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.md },
  actionButton: {
    flex: 1,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  actionApprove: { backgroundColor: colors.hadir },
  actionDecline: { backgroundColor: colors.alpa },
  actionButtonText: { color: colors.white, fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold },
  offlineNote: { fontSize: typography.fontSize.xs, color: colors.offline, marginTop: spacing.sm },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.xl },
  centeredText: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: spacing.sm, textAlign: 'center' },
  emptyTitle: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold, color: colors.heading },
  retryButton: {
    backgroundColor: colors.primary,
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
    marginTop: spacing.md,
  },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(15,23,42,0.5)', justifyContent: 'flex-end' },
  modalCard: {
    backgroundColor: colors.white,
    padding: spacing.lg,
    borderTopWidth: 3,
    borderTopColor: colors.primary,
  },
  modalTitle: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold, color: colors.heading },
  modalSubtitle: { fontSize: typography.fontSize.base, color: colors.body, marginTop: spacing.xs },
  modalNote: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: spacing.sm, lineHeight: 18 },
  signatureInput: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.md,
    minHeight: 44,
    marginTop: spacing.md,
    color: colors.heading,
    fontSize: typography.fontSize.base,
  },
  signError: { color: colors.alpa, fontSize: typography.fontSize.xs, marginTop: spacing.sm },
  modalActions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.md },
  modalCancel: { alignItems: 'center', paddingVertical: spacing.md, minHeight: 44, justifyContent: 'center' },
  modalCancelText: { color: colors.muted, fontSize: typography.fontSize.sm },
});
