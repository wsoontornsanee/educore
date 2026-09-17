/**
 * VA/QRIS payment screen: fee breakdown, instructions, expiry countdown,
 * and settlement polling in place of a not-yet-built payment-settled push
 * (spec/08 PAR-006, PAR-007, PAR-008 acceptance criterion #2).
 */
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { createPaymentIntent, fetchPaymentIntent } from '../../services/payments';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import { track } from '../../services/analytics.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import type { PaymentIntentItem } from '../../types';

interface PaymentScreenProps {
  invoiceIds: number[];
  onDone: () => void;
}

const POLL_INTERVAL_MS = 3000;

// Terminal states of finance.PaymentIntentStatus. PENDING is the only non-terminal
// one, so polling must stop on any of these three.
const SUCCESS_STATUS = 'COMPLETED';

export const PaymentScreen: React.FC<PaymentScreenProps> = ({ invoiceIds, onDone }) => {
  const { t } = useLocale();
  const [method, setMethod] = useState<'VA' | 'QRIS' | null>(null);
  const [intent, setIntent] = useState<PaymentIntentItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [terminalMsg, setTerminalMsg] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const handleBack = () => {
    stopPolling();
    onDone();
  };

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  useEffect(() => {
    track('pay_start');
  }, []);

  useEffect(() => {
    if (!intent?.expires_at) return;
    const tick = () => {
      const remaining = Math.max(0, Math.floor((new Date(intent.expires_at).getTime() - Date.now()) / 1000));
      setSecondsLeft(remaining);
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [intent?.expires_at]);

  const handleChooseMethod = async (chosen: 'VA' | 'QRIS') => {
    track('pay_method_selected');
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setMethod(chosen);
    setIntent(null);
    setLoading(true);
    setErrorMsg(null);
    setTerminalMsg(null);
    try {
      const created = await createPaymentIntent(invoiceIds, chosen);
      setIntent(created);
      track('pay_intent_created');
      pollRef.current = setInterval(async () => {
        try {
          const refreshed = await fetchPaymentIntent(created.id);
          setIntent(refreshed);
          if (refreshed.status === SUCCESS_STATUS) {
            // Guard against two in-flight ticks both observing a terminal status:
            // only the first to see pollRef.current still set may clear it and
            // call onDone(); a losing tick sees it already null and bails out.
            if (!pollRef.current) return;
            clearInterval(pollRef.current);
            pollRef.current = null;
            track('pay_completed');
            onDone();
          } else if (refreshed.status === 'EXPIRED' || refreshed.status === 'CANCELLED') {
            if (!pollRef.current) return;
            clearInterval(pollRef.current);
            pollRef.current = null;
            setTerminalMsg(
              refreshed.status === 'EXPIRED'
                ? t('payment.expired_msg', 'Waktu pembayaran telah habis. Silakan ulangi dari daftar tagihan.')
                : t('payment.cancelled_msg', 'Pembayaran dibatalkan. Silakan ulangi dari daftar tagihan.')
            );
          }
        } catch {
          // Network hiccup during polling — keep trying on the next tick.
        }
      }, POLL_INTERVAL_MS);
    } catch (err: any) {
      setErrorMsg(err?.response?.data?.error || t('common.error'));
    } finally {
      setLoading(false);
    }
  };

  if (!method) {
    return (
      <View style={styles.center}>
        <Text style={styles.title}>{t('payment.choose_bank')}</Text>
        <TouchableOpacity style={styles.methodButton} onPress={() => handleChooseMethod('VA')}>
          <Text style={styles.methodButtonText}>Virtual Account (VA)</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.methodButton} onPress={() => handleChooseMethod('QRIS')}>
          <Text style={styles.methodButtonText}>QRIS</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={handleBack} style={styles.cancelLink}>
          <Text style={styles.cancelText}>{t('common.cancel')}</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (terminalMsg) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>{terminalMsg}</Text>
        <TouchableOpacity onPress={handleBack} style={styles.cancelLink}>
          <Text style={styles.cancelText}>{t('payment.back_to_invoices', 'Kembali ke daftar tagihan')}</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (loading || !intent) {
    return (
      <View style={styles.center}>
        {errorMsg ? (
          <>
            <Text style={styles.errorText}>{errorMsg}</Text>
            <TouchableOpacity onPress={() => setMethod(null)} style={styles.cancelLink}>
              <Text style={styles.cancelText}>{t('common.retry')}</Text>
            </TouchableOpacity>
          </>
        ) : (
          <ActivityIndicator size="large" color={colors.primary} />
        )}
      </View>
    );
  }

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.summaryCard}>
        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>{t('invoice.total_amount')}</Text>
          <Text style={styles.summaryValue}>{intent.currency} {intent.base_amount}</Text>
        </View>
        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>{t('payment.admin_fee')}</Text>
          <Text style={styles.summaryValue}>{intent.currency} {intent.convenience_fee_amount}</Text>
        </View>
        <View style={[styles.summaryRow, styles.summaryTotalRow]}>
          <Text style={styles.summaryTotalLabel}>{t('payment.total_pay')}</Text>
          <Text style={styles.summaryTotalValue}>{intent.currency} {intent.amount}</Text>
        </View>
      </View>

      <View style={styles.instructionCard}>
        {intent.method === 'VA' ? (
          <>
            <Text style={styles.cardTitle}>{t('wallet.va_number')} — {intent.va_bank}</Text>
            <Text style={styles.vaNumber}>{intent.va_number}</Text>
            <Text style={styles.instructionBody}>
              1. Buka aplikasi mobile banking {intent.va_bank}.{'\n'}
              2. Pilih menu Transfer ke Virtual Account.{'\n'}
              3. Masukkan nomor VA di atas, lalu konfirmasi jumlah yang tertera.
            </Text>
          </>
        ) : (
          <>
            <Text style={styles.cardTitle}>{t('payment.qris_option')}</Text>
            <Text style={styles.qrisLabel}>{t('payment.qris_code', 'Kode QRIS')}</Text>
            <Text style={styles.qrisPayload} selectable>{intent.qris_payload}</Text>
            <Text style={styles.instructionBody}>
              1. Buka aplikasi e-wallet atau mobile banking Anda.{'\n'}
              2. Pilih menu Bayar/Scan QRIS.{'\n'}
              3. Jika tidak dapat memindai langsung, salin kode di atas dan tempelkan pada kolom kode QRIS di aplikasi Anda.
            </Text>
          </>
        )}
        <Text style={styles.countdown}>
          {t('payment.expires_in', 'Kedaluwarsa dalam')} {minutes}:{String(seconds).padStart(2, '0')}
        </Text>
      </View>

      <Text style={styles.waitingNote}>{t('wallet.topup_waiting')}</Text>

      {/* Escape hatch: the poll may never settle (VA paid later, network down),
          so the guardian must always be able to leave this screen. */}
      <TouchableOpacity onPress={handleBack} style={styles.backButton} accessibilityRole="button">
        <Text style={styles.backButtonText}>{t('payment.back_to_invoices', 'Kembali ke daftar tagihan')}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.base },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.xl },
  title: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold, color: colors.heading, marginBottom: spacing.lg },
  methodButton: {
    width: '100%', backgroundColor: colors.primary, borderRadius: radius.button,
    paddingVertical: spacing.md, alignItems: 'center', marginBottom: spacing.sm,
  },
  methodButtonText: { color: colors.white, fontWeight: typography.fontWeight.bold },
  // PAR-016: >= 44dp tappable height (12 + 12 padding + ~20 line height).
  cancelLink: { marginTop: spacing.base, paddingVertical: spacing.md, paddingHorizontal: spacing.base, minHeight: 44, justifyContent: 'center' },
  cancelText: { color: colors.muted, fontSize: typography.fontSize.sm, lineHeight: typography.lineHeight.base },
  backButton: {
    marginTop: spacing.base, paddingVertical: spacing.md, minHeight: 44,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.button,
    backgroundColor: colors.white,
  },
  backButtonText: { color: colors.body, fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, lineHeight: typography.lineHeight.base },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.sm, textAlign: 'center' },
  summaryCard: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border, borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base },
  summaryRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.xs },
  summaryLabel: { fontSize: typography.fontSize.sm, color: colors.body },
  summaryValue: { fontSize: typography.fontSize.sm, color: colors.heading },
  summaryTotalRow: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.sm, marginTop: spacing.xs },
  summaryTotalLabel: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading },
  summaryTotalValue: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.primary },
  instructionCard: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border, borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base },
  cardTitle: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading, marginBottom: spacing.sm },
  vaNumber: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.primary, letterSpacing: 1, marginBottom: spacing.sm },
  qrisLabel: { fontSize: typography.fontSize.xs, color: colors.muted, marginBottom: spacing.xs },
  qrisPayload: {
    fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.primary,
    fontFamily: 'monospace', backgroundColor: colors.surfaceAlt, borderRadius: radius.button,
    padding: spacing.sm, marginBottom: spacing.sm,
  },
  instructionBody: { fontSize: typography.fontSize.sm, color: colors.body, lineHeight: 20 },
  countdown: { fontSize: typography.fontSize.sm, color: colors.offline, fontWeight: typography.fontWeight.bold, marginTop: spacing.base },
  waitingNote: { fontSize: typography.fontSize.xs, color: colors.muted, textAlign: 'center' },
});
