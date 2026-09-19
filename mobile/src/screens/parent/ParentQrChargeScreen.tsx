/**
 * Parent App: Canteen QR Charge (spec 18 QRS-010..017, QRS-029).
 *
 * Scan → amount keypad → confirm with the guardian's spending PIN → PAID with the 4-char code.
 * All rules (token shape, cap/balance, refusal classes, idempotency) live in services/qrCharge.ts;
 * this component only sequences the steps and renders them.
 *
 * "Use my card instead" is always one tap away: QR is the fallback path, never the only one.
 */
import React, { useEffect, useRef, useState } from 'react';
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
import { CameraView, useCameraPermissions } from 'expo-camera';
import { SpendingPinSheet } from '../../components/SpendingPinSheet';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import { getUserProfile } from '../../services/storage';
import { formatRupiah } from '../../services/wallet';
import {
  PIN_LENGTH,
  PinStatus,
  QrChargeResult,
  QrFailure,
  QrResolveResult,
  applyKeypadKey,
  chargeQr,
  checkNewPinEntry,
  createPin,
  describeQrError,
  evaluateAmount,
  fetchPinStatus,
  isDeadQrFailure,
  isPinBlocking,
  isPinFailure,
  isValidPinFormat,
  newIdempotencyKey,
  normalizeScannedToken,
  resolveQr,
} from '../../services/qrCharge';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { ChildSummary } from '../../types';

type Step = 'SCAN' | 'RESOLVING' | 'AMOUNT' | 'PIN_SETUP' | 'CONFIRM' | 'PAYING' | 'PAID' | 'REFUSED';

interface ParentQrChargeScreenProps {
  visible: boolean;
  child: ChildSummary;
  onClose: () => void;
  /** Called once a payment succeeds so the wallet can refresh its balance and history. */
  onPaid: (result: QrChargeResult) => void;
}

const KEYPAD: string[][] = [
  ['1', '2', '3'],
  ['4', '5', '6'],
  ['7', '8', '9'],
  ['00', '0', 'DEL'],
];

export const ParentQrChargeScreen: React.FC<ParentQrChargeScreenProps> = ({
  visible,
  child,
  onClose,
  onPaid,
}) => {
  const { t } = useLocale();
  const [permission, requestPermission] = useCameraPermissions();

  const [step, setStep] = useState<Step>('SCAN');
  const [scanError, setScanError] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [target, setTarget] = useState<QrResolveResult | null>(null);
  const [digits, setDigits] = useState('');
  const [pinStatus, setPinStatus] = useState<PinStatus | null>(null);
  const [pin, setPin] = useState('');
  const [newPin, setNewPin] = useState('');
  const [repeatPin, setRepeatPin] = useState('');
  const [failure, setFailure] = useState<QrFailure | null>(null);
  const [savingPin, setSavingPin] = useState(false);
  const [paid, setPaid] = useState<QrChargeResult | null>(null);
  // Own phone for the OTP that resets a locked / forgotten PIN.
  const [phone, setPhone] = useState('');
  const [resetSheetOpen, setResetSheetOpen] = useState(false);

  // One key per confirmation, reused on retry after a dropped connection so a lost response
  // can never debit twice. Re-minted whenever the amount or QR changes.
  const idempotencyKey = useRef<string>(newIdempotencyKey());
  // The camera fires onBarcodeScanned many times a second; only the first hit may resolve.
  const scanLock = useRef(false);

  const reset = () => {
    scanLock.current = false;
    setStep('SCAN');
    setScanError(null);
    setToken(null);
    setTarget(null);
    setDigits('');
    setPin('');
    setNewPin('');
    setRepeatPin('');
    setFailure(null);
    setPaid(null);
    idempotencyKey.current = newIdempotencyKey();
  };

  useEffect(() => {
    if (!visible) return;
    reset();
    fetchPinStatus().then(setPinStatus).catch(() => setPinStatus(null));
    getUserProfile().then((u) => setPhone(u?.phone_e164 ?? ''));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, child.student_id]);

  // Ask once per open; a denial with canAskAgain=false leaves the fallback message + "use card".
  const askedForCamera = useRef(false);
  useEffect(() => {
    if (!visible) {
      askedForCamera.current = false;
      return;
    }
    if (permission && !permission.granted && permission.canAskAgain && !askedForCamera.current) {
      askedForCamera.current = true;
      requestPermission();
    }
  }, [visible, permission, requestPermission]);

  const handleScanned = async (raw: string) => {
    if (scanLock.current) return;
    const scanned = normalizeScannedToken(raw);
    if (!scanned) {
      setScanError(t('qr.scan_not_ours'));
      return;
    }
    scanLock.current = true;
    setScanError(null);
    setStep('RESOLVING');
    try {
      const resolved = await resolveQr(child.student_id, scanned);
      setToken(scanned);
      setTarget(resolved);
      setDigits('');
      idempotencyKey.current = newIdempotencyKey();
      setStep('AMOUNT');
    } catch (err) {
      setFailure(describeQrError(err));
      setStep('REFUSED');
    }
  };

  const amount = target ? evaluateAmount(digits, target.balance, target.max_amount) : null;

  const handleContinueToConfirm = () => {
    if (!amount || amount.state !== 'OK') return;
    setFailure(null);
    setPin('');
    setStep(pinStatus && !pinStatus.is_set ? 'PIN_SETUP' : 'CONFIRM');
  };

  const handleSavePin = async () => {
    const problem = checkNewPinEntry(newPin, repeatPin);
    if (problem) {
      setFailure({ code: problem, message: problem });
      return;
    }
    setSavingPin(true);
    setFailure(null);
    try {
      setPinStatus(await createPin(newPin));
      setNewPin('');
      setRepeatPin('');
      setStep('CONFIRM');
    } catch (err) {
      // Weak or malformed PIN: the server's message says what to change.
      setFailure(describeQrError(err));
    } finally {
      setSavingPin(false);
    }
  };

  const handlePay = async () => {
    if (!token || !amount || amount.state !== 'OK' || !isValidPinFormat(pin)) return;
    setFailure(null);
    setStep('PAYING');
    try {
      const result = await chargeQr({
        studentId: child.student_id,
        token,
        amount: amount.amount,
        pin,
        idempotencyKey: idempotencyKey.current,
      });
      setPaid(result);
      setPin('');
      setStep('PAID');
      onPaid(result);
    } catch (err) {
      const f = describeQrError(err);
      setPin('');
      if (f.code === 'PIN_NOT_SET') {
        // Status fetch failed earlier (or the PIN was removed): fall into setup instead of a dead end.
        setPinStatus({ is_set: false, locked_until: null, requires_otp_reset: false });
        setStep('PIN_SETUP');
      } else if (isDeadQrFailure(f.code)) {
        setFailure(f);
        setStep('REFUSED');
      } else if (isPinFailure(f.code) || f.code === 'NETWORK' || f.code === 'UNKNOWN') {
        // Stay on confirm: wrong PIN, lock, or a dropped connection (same key is safe to retry).
        setFailure(f);
        setStep('CONFIRM');
      } else {
        // Cap / balance / daily limit / window: the amount must change, so back to the keypad.
        setFailure(f);
        setStep('AMOUNT');
      }
    }
  };

  const pinBlocked =
    (!!failure && isPinBlocking(failure.code)) ||
    !!pinStatus?.requires_otp_reset ||
    (!!pinStatus?.locked_until && new Date(pinStatus.locked_until).getTime() > Date.now());

  const failureText = (f: QrFailure): string => {
    if (f.code === 'PIN_MISMATCH') return t('qr.pin_mismatch');
    if (f.code === 'PIN_INVALID_FORMAT') return t('qr.enter_pin');
    if (f.code === 'PIN_LOCKED') return t('qr.pin_locked');
    if (f.code === 'PIN_RESET_REQUIRED') return t('qr.pin_reset_required');
    if (f.code === 'NETWORK') return t('qr.network_retry');
    return f.message;
  };

  // ─── Renderers ──────────────────────────────────────────────────────────────

  const renderCamera = () => {
    if (!permission) {
      return <ActivityIndicator size="large" color={colors.primary} style={styles.centerFill} />;
    }
    if (!permission.granted) {
      return (
        <View style={styles.centerFill}>
          <Text style={styles.bodyText}>{t('qr.camera_denied')}</Text>
          {permission.canAskAgain && (
            <TouchableOpacity style={styles.primaryBtn} onPress={requestPermission} accessibilityRole="button">
              <Text style={styles.primaryBtnText}>{t('qr.camera_allow')}</Text>
            </TouchableOpacity>
          )}
        </View>
      );
    }
    return (
      <View style={styles.cameraFrame}>
        <CameraView
          style={styles.camera}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
          onBarcodeScanned={step === 'SCAN' ? ({ data }) => handleScanned(data) : undefined}
        />
      </View>
    );
  };

  const renderKeypad = (onKey: (k: string) => void) => (
    <View style={styles.keypad}>
      {KEYPAD.map((row) => (
        <View key={row.join('')} style={styles.keypadRow}>
          {row.map((k) => (
            <TouchableOpacity
              key={k}
              style={styles.keyBtn}
              onPress={() => onKey(k)}
              accessibilityRole="button"
              accessibilityLabel={k === 'DEL' ? 'Hapus' : k}
            >
              <Text style={styles.keyText}>{k === 'DEL' ? '⌫' : k}</Text>
            </TouchableOpacity>
          ))}
        </View>
      ))}
    </View>
  );

  const renderPinInput = (
    value: string,
    onChange: (v: string) => void,
    label: string,
    testID: string,
  ) => (
    <View style={styles.pinField}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        style={styles.pinInput}
        value={value}
        onChangeText={(v) => onChange(v.replace(/\D/g, '').slice(0, PIN_LENGTH))}
        keyboardType="number-pad"
        secureTextEntry
        maxLength={PIN_LENGTH}
        accessibilityLabel={label}
        testID={testID}
      />
    </View>
  );

  const renderBody = () => {
    switch (step) {
      case 'SCAN':
        return (
          <View style={styles.flex}>
            {renderCamera()}
            <Text style={styles.hintText}>{t('qr.scan_hint')}</Text>
            {scanError && <Text style={styles.errorText}>{scanError}</Text>}
            <TouchableOpacity style={styles.secondaryBtn} onPress={onClose} accessibilityRole="button">
              <Text style={styles.secondaryBtnText}>{t('qr.use_card')}</Text>
            </TouchableOpacity>
          </View>
        );

      case 'RESOLVING':
        return (
          <View style={styles.centerFill}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.bodyText}>{t('qr.resolving')}</Text>
          </View>
        );

      case 'AMOUNT':
        if (!target || !amount) return null;
        return (
          <ScrollView contentContainerStyle={styles.scrollBody}>
            <Text style={styles.merchant}>{target.merchant_name}</Text>
            {(target.payment_point_name || target.terminal_name) && (
              <Text style={styles.subtle}>
                {[target.payment_point_name ?? target.terminal_name, target.payment_point_location]
                  .filter(Boolean)
                  .join(' · ')}
              </Text>
            )}

            <View style={styles.balanceCard}>
              <Text style={styles.subtle}>{t('qr.balance')}</Text>
              <Text style={styles.balanceValue}>{formatRupiah(target.balance)}</Text>
              <Text style={styles.subtle}>
                {t('qr.cap_note')}: {formatRupiah(target.max_amount)}
              </Text>
            </View>

            <Text style={styles.fieldLabel}>{t('qr.amount_label')}</Text>
            <Text
              style={[styles.amountValue, amount.state === 'ABOVE_CAP' || amount.state === 'INSUFFICIENT' ? styles.amountBad : null]}
              accessibilityLiveRegion="polite"
            >
              {formatRupiah(amount.amount)}
            </Text>
            {amount.state === 'ABOVE_CAP' && <Text style={styles.errorText}>{t('qr.above_cap')}</Text>}
            {amount.state === 'INSUFFICIENT' && <Text style={styles.errorText}>{t('qr.insufficient')}</Text>}
            {failure && <Text style={styles.errorText}>{failureText(failure)}</Text>}

            {renderKeypad((k) => {
              setFailure(null);
              setDigits((d) => applyKeypadKey(d, k));
              idempotencyKey.current = newIdempotencyKey();
            })}

            <TouchableOpacity
              style={[styles.primaryBtn, amount.state !== 'OK' && styles.btnDisabled]}
              onPress={handleContinueToConfirm}
              disabled={amount.state !== 'OK'}
              accessibilityRole="button"
            >
              <Text style={styles.primaryBtnText}>{t('qr.continue')}</Text>
            </TouchableOpacity>
          </ScrollView>
        );

      case 'PIN_SETUP':
        return (
          <ScrollView contentContainerStyle={styles.scrollBody} keyboardShouldPersistTaps="handled">
            <Text style={styles.heading}>{t('qr.pin_setup_title')}</Text>
            <Text style={styles.bodyText}>{t('qr.pin_setup_desc')}</Text>
            {renderPinInput(newPin, setNewPin, t('qr.pin_new'), 'qr-pin-new')}
            {renderPinInput(repeatPin, setRepeatPin, t('qr.pin_repeat'), 'qr-pin-repeat')}
            {failure && <Text style={styles.errorText}>{failureText(failure)}</Text>}
            <TouchableOpacity
              style={[styles.primaryBtn, (savingPin || newPin.length !== PIN_LENGTH || repeatPin.length !== PIN_LENGTH) && styles.btnDisabled]}
              onPress={handleSavePin}
              disabled={savingPin || newPin.length !== PIN_LENGTH || repeatPin.length !== PIN_LENGTH}
              accessibilityRole="button"
            >
              <Text style={styles.primaryBtnText}>{t('qr.pin_save')}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.secondaryBtn} onPress={() => setStep('AMOUNT')} accessibilityRole="button">
              <Text style={styles.secondaryBtnText}>{t('qr.back')}</Text>
            </TouchableOpacity>
          </ScrollView>
        );

      case 'CONFIRM':
      case 'PAYING':
        if (!target || !amount) return null;
        return (
          <ScrollView contentContainerStyle={styles.scrollBody} keyboardShouldPersistTaps="handled">
            <Text style={styles.heading}>{t('qr.confirm_title')}</Text>
            <View style={styles.balanceCard}>
              <Text style={styles.subtle}>{t('qr.pay_to')}</Text>
              <Text style={styles.merchant}>{target.merchant_name}</Text>
              <Text style={styles.amountValue}>{formatRupiah(amount.amount)}</Text>
              <Text style={styles.subtle}>
                {t('qr.balance_after')}: {formatRupiah(Number(target.balance) - amount.amount)}
              </Text>
            </View>

            {renderPinInput(pin, setPin, t('qr.enter_pin'), 'qr-pin')}
            {failure && (
              <Text style={styles.errorText} accessibilityLiveRegion="polite">
                {failureText(failure)}
                {failure.code === 'PIN_INVALID' && failure.attemptsLeft !== undefined
                  ? ` ${t('qr.pin_attempts_left')} ${failure.attemptsLeft}`
                  : ''}
              </Text>
            )}
            {!failure && pinBlocked && (
              <Text style={styles.errorText}>
                {pinStatus?.requires_otp_reset ? t('qr.pin_reset_required') : t('qr.pin_locked')}
              </Text>
            )}

            {pinBlocked && !!phone && (
              <TouchableOpacity
                style={styles.secondaryBtn}
                onPress={() => setResetSheetOpen(true)}
                accessibilityRole="button"
              >
                <Text style={styles.secondaryBtnText}>{t('pin.forgot_short')}</Text>
              </TouchableOpacity>
            )}

            <TouchableOpacity
              style={[styles.primaryBtn, (step === 'PAYING' || pinBlocked || !isValidPinFormat(pin)) && styles.btnDisabled]}
              onPress={handlePay}
              disabled={step === 'PAYING' || pinBlocked || !isValidPinFormat(pin)}
              accessibilityRole="button"
            >
              {step === 'PAYING' ? (
                <ActivityIndicator color={colors.white} />
              ) : (
                <Text style={styles.primaryBtnText}>{t('qr.pay_now')}</Text>
              )}
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.secondaryBtn}
              onPress={() => {
                setFailure(null);
                setPin('');
                setStep('AMOUNT');
              }}
              disabled={step === 'PAYING'}
              accessibilityRole="button"
            >
              <Text style={styles.secondaryBtnText}>{t('qr.back')}</Text>
            </TouchableOpacity>
          </ScrollView>
        );

      case 'PAID':
        if (!paid) return null;
        return (
          <View style={styles.centerFill}>
            <Text style={styles.paidTitle}>{t('qr.paid_title')}</Text>
            <Text style={styles.amountValue}>{formatRupiah(paid.amount)}</Text>
            <Text style={styles.subtle}>
              {t('qr.balance_after')}: {formatRupiah(paid.balance_after)}
            </Text>
            <Text style={styles.subtle}>{t('qr.paid_code_hint')}</Text>
            <Text style={styles.codeValue} accessibilityLabel={paid.code.split('').join(' ')}>
              {paid.code}
            </Text>
            <TouchableOpacity style={styles.primaryBtn} onPress={onClose} accessibilityRole="button">
              <Text style={styles.primaryBtnText}>{t('qr.done')}</Text>
            </TouchableOpacity>
          </View>
        );

      case 'REFUSED':
        return (
          <View style={styles.centerFill}>
            <Text style={styles.heading}>{t('qr.refused_title')}</Text>
            <Text style={styles.bodyText}>{failure ? failureText(failure) : ''}</Text>
            <TouchableOpacity style={styles.primaryBtn} onPress={reset} accessibilityRole="button">
              <Text style={styles.primaryBtnText}>{t('qr.scan_again')}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.secondaryBtn} onPress={onClose} accessibilityRole="button">
              <Text style={styles.secondaryBtnText}>{t('qr.use_card')}</Text>
            </TouchableOpacity>
          </View>
        );
    }
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.headerTitle}>{t('qr.title')}</Text>
          {step !== 'PAID' && step !== 'PAYING' && (
            <TouchableOpacity
              onPress={onClose}
              style={styles.closeBtn}
              accessibilityRole="button"
              accessibilityLabel={t('qr.close')}
            >
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          )}
        </View>
        {renderBody()}
        <SpendingPinSheet
          visible={resetSheetOpen}
          mode="RESET"
          phoneE164={phone}
          maskedPhone={phone.replace(/(\+\d{2})(\d{3})(\d+)(\d{4})$/, '$1 $2 ****$4')}
          onClose={() => setResetSheetOpen(false)}
          onDone={() => {
            setResetSheetOpen(false);
            setFailure(null);
            fetchPinStatus().then(setPinStatus).catch(() => setPinStatus(null));
          }}
        />
      </SafeAreaView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.white },
  flex: { flex: 1, padding: spacing.base },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  headerTitle: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold, color: colors.heading },
  closeBtn: { minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  closeText: { fontSize: typography.fontSize.xl, color: colors.heading },
  centerFill: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.md },
  scrollBody: { padding: spacing.base, gap: spacing.sm },
  cameraFrame: { flex: 1, borderRadius: radius.card, overflow: 'hidden', backgroundColor: colors.heading },
  camera: { flex: 1 },
  heading: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  merchant: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.semibold, color: colors.heading },
  bodyText: { fontSize: typography.fontSize.base, lineHeight: typography.lineHeight.base, color: colors.body, textAlign: 'center' },
  hintText: { fontSize: typography.fontSize.sm, color: colors.muted, marginVertical: spacing.md, textAlign: 'center' },
  subtle: { fontSize: typography.fontSize.sm, color: colors.subtle },
  errorText: { fontSize: typography.fontSize.base, color: colors.alpa, fontWeight: typography.fontWeight.medium },
  balanceCard: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.base,
    gap: spacing.xs,
    marginVertical: spacing.sm,
  },
  balanceValue: { fontSize: typography.fontSize.xxl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  fieldLabel: { fontSize: typography.fontSize.sm, color: colors.muted, fontWeight: typography.fontWeight.medium },
  amountValue: { fontSize: 40, lineHeight: 48, fontWeight: typography.fontWeight.bold, color: colors.heading },
  amountBad: { color: colors.alpa },
  keypad: { marginVertical: spacing.sm, gap: spacing.sm },
  keypadRow: { flexDirection: 'row', gap: spacing.sm },
  // 56px targets (design §05).
  keyBtn: {
    flex: 1,
    height: 56,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
  },
  keyText: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.semibold, color: colors.heading },
  pinField: { gap: spacing.xs, marginVertical: spacing.sm },
  pinInput: {
    height: 56,
    borderWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.base,
    fontSize: typography.fontSize.xl,
    letterSpacing: 8,
    color: colors.heading,
  },
  primaryBtn: {
    minHeight: 56,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
    paddingHorizontal: spacing.xl,
    marginTop: spacing.sm,
  },
  primaryBtnText: { color: colors.white, fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold },
  secondaryBtn: {
    minHeight: 56,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.xl,
    marginTop: spacing.sm,
  },
  secondaryBtnText: { color: colors.heading, fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.semibold },
  btnDisabled: { opacity: 0.4 },
  paidTitle: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.hadir },
  codeValue: {
    fontSize: 56,
    lineHeight: 64,
    fontWeight: typography.fontWeight.bold,
    letterSpacing: 8,
    color: colors.heading,
    fontFamily: typography.fontFamily.mono,
  },
});
