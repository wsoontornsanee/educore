/**
 * Canteen terminal — pay with a self-signed offline QR (spec 18 §6, QRS-022/023).
 *
 * The cashier has built the cart and picked the student. This modal shows a single-use QR that the
 * terminal signed itself (no server round-trip), the student scans it with the guardian app and
 * confirms with the spending PIN, and the cashier then confirms here that the student paid — which
 * queues the sale with the token so the server can tie the two together at sync.
 *
 * A terminal must be paired once, online, by someone allowed to manage hardware. Everything else is
 * local. Signing and pairing rules live in services/terminalQr.ts.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Modal, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';
import { colors, spacing, typography } from '../theme/tokens.ts';
import {
  PairingFailure,
  describePairingFailure,
  getTerminalPairing,
  mintOfflineQrToken,
  pairTerminal,
  type OfflineQrToken,
  type TerminalQrPairing,
} from '../services/terminalQr.ts';

interface OfflineQrModalProps {
  visible: boolean;
  terminalId: number;
  /** Shown so the cashier confirms the amount the student is about to type in their app. */
  totalLabel: string;
  onClose: () => void;
  /** The student has paid: record the sale against this token. */
  onPaid: (qrToken: string) => void;
}

const PAIRING_ERRORS: Record<PairingFailure, string> = {
  FORBIDDEN: 'Akun ini tidak boleh memasangkan terminal. Minta admin sekolah untuk memasangkannya.',
  NOT_FOUND: 'Terminal tidak ditemukan di server.',
  NETWORK: 'Tidak ada koneksi. Pemasangan terminal perlu internet sekali saja.',
  UNKNOWN: 'Gagal memasangkan terminal. Coba lagi.',
};

function secondsLeft(token: OfflineQrToken | null, now: number): number {
  return token ? Math.max(0, Math.ceil((new Date(token.expiresAt).getTime() - now) / 1000)) : 0;
}

export const OfflineQrModal: React.FC<OfflineQrModalProps> = ({
  visible,
  terminalId,
  totalLabel,
  onClose,
  onPaid,
}) => {
  const [pairing, setPairing] = useState<TerminalQrPairing | null>(null);
  const [checking, setChecking] = useState(true);
  const [pairingBusy, setPairingBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState<OfflineQrToken | null>(null);
  const [now, setNow] = useState(Date.now());

  const mint = useCallback((p: TerminalQrPairing) => {
    setToken(mintOfflineQrToken(p));
    setNow(Date.now());
  }, []);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    setError(null);
    setToken(null);
    setChecking(true);
    getTerminalPairing(terminalId).then((p) => {
      if (cancelled) return;
      setPairing(p);
      if (p) mint(p);
      setChecking(false);
    });
    return () => {
      cancelled = true;
    };
  }, [visible, terminalId, mint]);

  useEffect(() => {
    if (!visible || !token) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [visible, token]);

  const handlePair = async () => {
    setPairingBusy(true);
    setError(null);
    try {
      const p = await pairTerminal(terminalId);
      setPairing(p);
      mint(p);
    } catch (err) {
      setError(PAIRING_ERRORS[describePairingFailure(err)]);
    } finally {
      setPairingBusy(false);
    }
  };

  const remaining = secondsLeft(token, now);
  const expired = !!token && remaining === 0;

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>Bayar dengan QR (Offline)</Text>
            <TouchableOpacity
              onPress={onClose}
              style={styles.closeBtn}
              accessibilityRole="button"
              accessibilityLabel="Tutup"
            >
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.body}>
            {checking ? (
              <ActivityIndicator color={colors.primary} />
            ) : !pairing ? (
              <>
                <Text style={styles.desc}>
                  Terminal ini belum dipasangkan untuk QR offline. Pasangkan sekali saat terhubung ke
                  internet; setelah itu QR dibuat langsung di perangkat tanpa jaringan.
                </Text>
                {error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
                <TouchableOpacity
                  style={[styles.primaryBtn, pairingBusy && styles.disabled]}
                  onPress={handlePair}
                  disabled={pairingBusy}
                  accessibilityRole="button"
                >
                  {pairingBusy ? (
                    <ActivityIndicator color={colors.white} />
                  ) : (
                    <Text style={styles.primaryText}>Pasangkan Terminal</Text>
                  )}
                </TouchableOpacity>
              </>
            ) : (
              <>
                <Text style={styles.total}>{totalLabel}</Text>
                <View style={styles.qrFrame} accessible accessibilityLabel="Kode QR pembayaran">
                  {token && !expired ? (
                    <QRCode value={token.token} size={260} ecl="L" />
                  ) : (
                    <Text style={styles.expiredText}>QR kedaluwarsa</Text>
                  )}
                </View>
                <Text style={styles.desc}>
                  {expired
                    ? 'Buat QR baru, lalu minta siswa memindainya.'
                    : `Siswa memindai QR ini di aplikasi, memasukkan jumlah, dan konfirmasi PIN. Berlaku ${remaining} detik.`}
                </Text>
                {error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
                <TouchableOpacity
                  style={[styles.primaryBtn, expired && styles.disabled]}
                  onPress={() => token && onPaid(token.token)}
                  disabled={expired}
                  accessibilityRole="button"
                >
                  <Text style={styles.primaryText}>Siswa Sudah Membayar</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.secondaryBtn}
                  onPress={() => mint(pairing)}
                  accessibilityRole="button"
                >
                  <Text style={styles.secondaryText}>Buat QR Baru</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={handlePair} disabled={pairingBusy} accessibilityRole="button">
                  <Text style={styles.linkText}>
                    {pairingBusy ? 'Memasangkan…' : 'Pasangkan ulang terminal (perlu internet)'}
                  </Text>
                </TouchableOpacity>
              </>
            )}
          </View>
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', padding: spacing.base },
  sheet: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.borderDark, maxWidth: 480, width: '100%', alignSelf: 'center' },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.base,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  title: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold, color: colors.heading },
  closeBtn: { minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  closeText: { fontSize: 20, color: colors.muted, fontWeight: typography.fontWeight.bold },
  body: { padding: spacing.base, gap: spacing.md, alignItems: 'center' },
  total: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  qrFrame: {
    width: 292,
    height: 292,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
  },
  expiredText: { fontSize: typography.fontSize.lg, color: colors.muted, fontWeight: typography.fontWeight.semibold },
  desc: { fontSize: typography.fontSize.base, lineHeight: typography.lineHeight.base, color: colors.body, textAlign: 'center' },
  error: { fontSize: typography.fontSize.base, color: colors.alpa, fontWeight: typography.fontWeight.medium, textAlign: 'center' },
  primaryBtn: { minHeight: 56, alignSelf: 'stretch', alignItems: 'center', justifyContent: 'center', backgroundColor: colors.primary },
  primaryText: { color: colors.white, fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold },
  secondaryBtn: { minHeight: 48, alignSelf: 'stretch', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.borderDark },
  secondaryText: { color: colors.heading, fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.semibold },
  linkText: { color: colors.muted, fontSize: typography.fontSize.sm, textDecorationLine: 'underline', paddingVertical: spacing.xs },
  disabled: { opacity: 0.4 },
});
