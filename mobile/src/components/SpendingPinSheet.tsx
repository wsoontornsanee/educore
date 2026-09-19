/**
 * Guardian spending PIN — change and forgotten-PIN reset (spec 18 QRS-029).
 *
 * One sheet, two modes:
 *  - CHANGE: current PIN + new PIN. The current PIN counts against the attempt limit like any entry.
 *  - RESET:  a fresh OTP to the account's own phone proves control, then a new PIN. This is the only
 *            way out of PIN_RESET_REQUIRED (10 failures in 24 h), so the QR confirm step links here.
 *
 * Rules and API calls live in services/qrCharge.ts.
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { useLocale } from '../i18n/LocaleContext.tsx';
import {
  PIN_LENGTH,
  QrFailure,
  changePin,
  checkNewPinEntry,
  describeQrError,
  requestPinResetOtp,
  resetPinWithOtp,
} from '../services/qrCharge';
import { colors, spacing, typography } from '../theme/tokens';

export type SpendingPinSheetMode = 'CHANGE' | 'RESET';

interface SpendingPinSheetProps {
  visible: boolean;
  mode: SpendingPinSheetMode;
  /** The guardian's own phone (E.164): the OTP goes here, never to a number typed in the sheet. */
  phoneE164: string;
  /** Already-masked phone for display, e.g. "+62 812 ****7890". */
  maskedPhone: string;
  onClose: () => void;
  /** Called after the PIN was changed or reset so callers can refresh the PIN status. */
  onDone: (mode: SpendingPinSheetMode) => void;
}

const digitsOnly = (v: string) => v.replace(/\D/g, '').slice(0, PIN_LENGTH);

export const SpendingPinSheet: React.FC<SpendingPinSheetProps> = ({
  visible,
  mode,
  phoneE164,
  maskedPhone,
  onClose,
  onDone,
}) => {
  const { t } = useLocale();
  const [challengeId, setChallengeId] = useState<number | null>(null);
  const [current, setCurrent] = useState('');
  const [code, setCode] = useState('');
  const [newPin, setNewPin] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<QrFailure | null>(null);

  useEffect(() => {
    if (!visible) return;
    setChallengeId(null);
    setCurrent('');
    setCode('');
    setNewPin('');
    setRepeat('');
    setFailure(null);
  }, [visible, mode]);

  const failureText = (f: QrFailure): string => {
    if (f.code === 'PIN_MISMATCH') return t('qr.pin_mismatch');
    if (f.code === 'PIN_INVALID_FORMAT') return t('qr.enter_pin');
    if (f.code === 'PIN_LOCKED') return t('qr.pin_locked');
    if (f.code === 'PIN_RESET_REQUIRED') return t('qr.pin_reset_required');
    if (f.code === 'PIN_INVALID' && f.attemptsLeft !== undefined) {
      return `${f.message} ${t('qr.pin_attempts_left')} ${f.attemptsLeft}`;
    }
    return f.message;
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setFailure(null);
    try {
      await action();
    } catch (err) {
      setFailure(describeQrError(err));
    } finally {
      setBusy(false);
    }
  };

  const handleSendOtp = () =>
    run(async () => {
      setChallengeId(await requestPinResetOtp(phoneE164));
    });

  const handleSubmit = () => {
    const problem = checkNewPinEntry(newPin, repeat);
    if (problem) {
      setFailure({ code: problem, message: problem });
      return;
    }
    return run(async () => {
      if (mode === 'CHANGE') {
        await changePin(current, newPin);
      } else if (challengeId !== null) {
        await resetPinWithOtp(challengeId, code, newPin);
      }
      onDone(mode);
    });
  };

  const field = (label: string, value: string, onChange: (v: string) => void, testID: string, secure = true) => (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={(v) => onChange(digitsOnly(v))}
        keyboardType="number-pad"
        secureTextEntry={secure}
        maxLength={PIN_LENGTH}
        accessibilityLabel={label}
        testID={testID}
      />
    </View>
  );

  const otpStage = mode === 'RESET' && challengeId === null;
  const canSubmit =
    !busy &&
    newPin.length === PIN_LENGTH &&
    repeat.length === PIN_LENGTH &&
    (mode === 'CHANGE' ? current.length === PIN_LENGTH : code.length === PIN_LENGTH);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={styles.overlay}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>{mode === 'CHANGE' ? t('pin.change') : t('pin.reset_title')}</Text>
            <TouchableOpacity
              style={styles.closeBtn}
              onPress={onClose}
              accessibilityRole="button"
              accessibilityLabel={t('pin.sheet_close')}
            >
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>

          <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
            {otpStage ? (
              <>
                <Text style={styles.desc}>{t('pin.reset_desc')}</Text>
                <Text style={styles.phone}>{maskedPhone}</Text>
              </>
            ) : (
              <>
                {mode === 'RESET' && (
                  <Text style={styles.desc}>
                    {t('pin.otp_sent')} {maskedPhone}
                  </Text>
                )}
                {mode === 'CHANGE'
                  ? field(t('pin.current'), current, setCurrent, 'pin-current')
                  : field(t('pin.otp_code'), code, setCode, 'pin-otp', false)}
                {field(t('qr.pin_new'), newPin, setNewPin, 'pin-new')}
                {field(t('qr.pin_repeat'), repeat, setRepeat, 'pin-repeat')}
              </>
            )}

            {failure && (
              <Text style={styles.error} accessibilityLiveRegion="polite">
                {failureText(failure)}
              </Text>
            )}

            <TouchableOpacity
              style={[styles.primaryBtn, (otpStage ? busy : !canSubmit) && styles.disabled]}
              onPress={otpStage ? handleSendOtp : handleSubmit}
              disabled={otpStage ? busy : !canSubmit}
              accessibilityRole="button"
            >
              {busy ? (
                <ActivityIndicator color={colors.white} />
              ) : (
                <Text style={styles.primaryText}>
                  {otpStage ? t('pin.send_otp') : mode === 'CHANGE' ? t('pin.change_submit') : t('pin.reset_submit')}
                </Text>
              )}
            </TouchableOpacity>
          </ScrollView>
        </View>
      </SafeAreaView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', padding: spacing.base },
  sheet: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.borderDark, maxHeight: '90%' },
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
  body: { padding: spacing.base, gap: spacing.sm },
  desc: { fontSize: typography.fontSize.base, lineHeight: typography.lineHeight.base, color: colors.body },
  phone: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.semibold, color: colors.heading },
  field: { gap: spacing.xs, marginVertical: spacing.xs },
  label: { fontSize: typography.fontSize.sm, color: colors.muted, fontWeight: typography.fontWeight.medium },
  input: {
    height: 56,
    borderWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.base,
    fontSize: typography.fontSize.xl,
    letterSpacing: 8,
    color: colors.heading,
  },
  error: { fontSize: typography.fontSize.base, color: colors.alpa, fontWeight: typography.fontWeight.medium },
  primaryBtn: {
    minHeight: 56,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
    marginTop: spacing.sm,
  },
  primaryText: { color: colors.white, fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold },
  disabled: { opacity: 0.4 },
});
