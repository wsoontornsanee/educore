/**
 * Clinic officer: record a visit for the chosen student (spec/10 LIF-001..004). The health alert comes first,
 * above the fold, so allergies are seen before treatment or medication is decided (LIF-002).
 * Nothing entered here is written to the device; the draft lives in component state only.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, ScrollView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import {
  CLINIC_OUTCOMES,
  classifySubmitFailure,
  dispensableStock,
  fetchMedicationStock,
  fetchStudentHealthProfile,
  recordClinicVisit,
  validateVisitDraft,
} from '../../services/clinicStaff.ts';
import type { ClinicOutcome, VisitDraft, VisitDraftIssue } from '../../services/clinicStaff.ts';
import { todayWib } from '../../services/localDate.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type {
  ClinicVisitItem, HealthProfileItem, MedicationStockItem, StudentLookupItem,
} from '../../types/index.ts';

interface VisitFormStepProps {
  student: StudentLookupItem;
  onChangeStudent: () => void;
  onSaved: (visit: ClinicVisitItem) => void;
}

export const VisitFormStep: React.FC<VisitFormStepProps> = ({ student, onChangeStudent, onSaved }) => {
  const { t } = useLocale();
  const today = todayWib();

  const [profile, setProfile] = useState<HealthProfileItem | null>(null);
  const [profileFailed, setProfileFailed] = useState(false);
  const [stock, setStock] = useState<MedicationStockItem[] | null>(null);
  const [stockFailed, setStockFailed] = useState(false);

  const [complaint, setComplaint] = useState('');
  const [treatment, setTreatment] = useState('');
  const [temperature, setTemperature] = useState('');
  const [pulse, setPulse] = useState('');
  const [outcome, setOutcome] = useState<ClinicOutcome | null>(null);
  const [medication, setMedication] = useState<MedicationStockItem | null>(null);
  const [quantity, setQuantity] = useState('');
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [consentNote, setConsentNote] = useState('');

  const [attempted, setAttempted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const inFlight = useRef(false);

  const loadContext = useCallback(async () => {
    setProfileFailed(false);
    setStockFailed(false);
    const [profileResult, stockResult] = await Promise.allSettled([
      fetchStudentHealthProfile(student.id),
      fetchMedicationStock(),
    ]);
    if (profileResult.status === 'fulfilled') setProfile(profileResult.value); else setProfileFailed(true);
    if (stockResult.status === 'fulfilled') setStock(stockResult.value); else setStockFailed(true);
  }, [student.id]);

  useEffect(() => { loadContext(); }, [loadContext]);

  const draft: VisitDraft = {
    student, complaint, treatment, temperature, pulse, outcome, medication, quantity, consentConfirmed, consentNote,
  };
  const issues = validateVisitDraft(draft, today);
  const issueText = (issue: VisitDraftIssue) =>
    attempted && issues.includes(issue) ? <Text style={styles.issue}>{t(`clinicstaff.issue.${issue}`)}</Text> : null;
  const medicationOptions = stock ? dispensableStock(stock, student.school, today) : [];

  const save = async () => {
    setAttempted(true);
    if (issues.length > 0 || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    setFailure(null);
    try {
      onSaved(await recordClinicVisit(draft));
    } catch (error) {
      const result = classifySubmitFailure(error);
      setFailure(result.kind === 'rejected' && result.message
        ? `${t('clinicstaff.submit.rejected')} ${result.message}`
        : t('clinicstaff.submit.unknown'));
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.root} keyboardShouldPersistTaps="handled">
      <View style={styles.studentRow}>
        <View style={styles.studentText}>
          <Text style={styles.studentName}>{student.name}</Text>
          <Text style={styles.meta}>{[student.nis, student.school_name].filter(Boolean).join(' · ')}</Text>
        </View>
        <TouchableOpacity onPress={onChangeStudent} accessibilityRole="button" style={styles.linkButton}>
          <Text style={styles.linkText}>{t('clinicstaff.change_student')}</Text>
        </TouchableOpacity>
      </View>

      <HealthAlert profile={profile} failed={profileFailed} onRetry={loadContext} />

      <Label text={t('clinicstaff.complaint')} required />
      <TextInput
        style={[styles.input, styles.multiline]} value={complaint} onChangeText={setComplaint} multiline
        accessibilityLabel={t('clinicstaff.complaint')}
      />
      {issueText('complaint')}

      <Label text={t('clinicstaff.treatment')} />
      <TextInput
        style={[styles.input, styles.multiline]} value={treatment} onChangeText={setTreatment} multiline
        accessibilityLabel={t('clinicstaff.treatment')}
      />

      <View style={styles.pair}>
        <View style={styles.pairItem}>
          <Label text={t('clinicstaff.temperature')} />
          <TextInput
            style={styles.input} value={temperature} onChangeText={setTemperature} keyboardType="decimal-pad"
            accessibilityLabel={t('clinicstaff.temperature')}
          />
          {issueText('temperature')}
        </View>
        <View style={styles.pairItem}>
          <Label text={t('clinicstaff.pulse')} />
          <TextInput
            style={styles.input} value={pulse} onChangeText={setPulse} keyboardType="number-pad"
            accessibilityLabel={t('clinicstaff.pulse')}
          />
          {issueText('pulse')}
        </View>
      </View>

      <Label text={t('clinic.outcome')} required />
      <View style={styles.chips}>
        {CLINIC_OUTCOMES.map((value) => (
          <Chip
            key={value} label={t(`clinic.outcome.${value}`)} selected={outcome === value}
            onPress={() => setOutcome(value)}
          />
        ))}
      </View>
      {issueText('outcome')}

      <Label text={t('clinicstaff.medication')} />
      {stock === null && !stockFailed && <ActivityIndicator color={colors.primary} />}
      {stockFailed && (
        <TouchableOpacity onPress={loadContext} accessibilityRole="button" style={styles.linkButton}>
          <Text style={styles.issue}>{t('clinic.load_error')} {t('common.retry')}</Text>
        </TouchableOpacity>
      )}
      {stock !== null && (
        <View style={styles.chips}>
          <Chip
            label={t('clinicstaff.medication.none')} selected={medication === null}
            onPress={() => { setMedication(null); setQuantity(''); setConsentConfirmed(false); setConsentNote(''); }}
          />
          {medicationOptions.map((item) => (
            <Chip
              key={item.id} label={`${item.name} (${item.quantity} ${item.unit})`} selected={medication?.id === item.id}
              onPress={() => setMedication(item)}
            />
          ))}
        </View>
      )}
      {stock !== null && medicationOptions.length === 0 && (
        <Text style={styles.meta}>{t('clinicstaff.medication.unavailable')}</Text>
      )}
      {issueText('medication_expired')}

      {medication && (
        <View>
          <Label text={t('clinicstaff.quantity')} required />
          <TextInput
            style={styles.input} value={quantity} onChangeText={setQuantity} keyboardType="number-pad"
            accessibilityLabel={t('clinicstaff.quantity')}
          />
          {issueText('quantity')}
          {issueText('quantity_over_stock')}
          <View style={styles.switchRow}>
            <Text style={styles.switchLabel}>{t('clinicstaff.consent.confirm')}</Text>
            <Switch
              value={consentConfirmed} onValueChange={setConsentConfirmed}
              accessibilityLabel={t('clinicstaff.consent.confirm')}
            />
          </View>
          <TextInput
            style={styles.input} value={consentNote} onChangeText={setConsentNote}
            placeholder={t('clinicstaff.consent.note')} accessibilityLabel={t('clinicstaff.consent.note')}
          />
        </View>
      )}

      {failure && <Text style={styles.failure} accessibilityRole="alert">{failure}</Text>}
      <Text style={styles.privacy}>{t('clinicstaff.privacy.record')}</Text>
      <TouchableOpacity
        style={[styles.save, saving && styles.saveDisabled]} onPress={save} disabled={saving}
        accessibilityRole="button" accessibilityState={{ disabled: saving }}
      >
        <Text style={styles.saveText}>{saving ? t('clinicstaff.saving') : t('clinicstaff.save')}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
};

const HealthAlert: React.FC<{ profile: HealthProfileItem | null; failed: boolean; onRetry: () => void }> = ({
  profile, failed, onRetry,
}) => {
  const { t } = useLocale();
  if (failed) {
    return (
      <View style={[styles.alertBox, styles.alertWarn]} accessibilityRole="alert">
        <Text style={styles.alertTitle}>{t('clinicstaff.alert.title')}</Text>
        <Text style={styles.alertText}>{t('clinicstaff.alert.error')}</Text>
        <TouchableOpacity onPress={onRetry} accessibilityRole="button" style={styles.linkButton}>
          <Text style={styles.linkText}>{t('common.retry')}</Text>
        </TouchableOpacity>
      </View>
    );
  }
  if (!profile) return <ActivityIndicator color={colors.primary} style={styles.spinner} />;

  const rows: Array<[string, string[]]> = [
    [t('clinic.allergies'), profile.allergies],
    [t('clinic.chronic_conditions'), profile.chronic_conditions],
    [t('clinicstaff.alert.medications'), profile.medications],
  ];
  const filled = rows.filter(([, values]) => values.length > 0);
  return (
    <View style={[styles.alertBox, profile.has_medical_alert ? styles.alertWarn : styles.alertOk]}>
      <Text style={styles.alertTitle}>{t('clinicstaff.alert.title')}</Text>
      {filled.length === 0 && <Text style={styles.alertText}>{t('clinicstaff.alert.none')}</Text>}
      {filled.map(([label, values]) => (
        <Text key={label} style={styles.alertText}>
          <Text style={styles.alertLabel}>{label}: </Text>{values.join(', ')}
        </Text>
      ))}
      {!!profile.blood_type && (
        <Text style={styles.alertText}>
          <Text style={styles.alertLabel}>{t('clinicstaff.alert.blood_type')}: </Text>{profile.blood_type}
        </Text>
      )}
    </View>
  );
};

const Label: React.FC<{ text: string; required?: boolean }> = ({ text, required }) => (
  <Text style={styles.label}>{text}{required ? ' *' : ''}</Text>
);

const Chip: React.FC<{ label: string; selected: boolean; onPress: () => void }> = ({ label, selected, onPress }) => (
  <TouchableOpacity
    style={[styles.chip, selected && styles.chipSelected]} onPress={onPress}
    accessibilityRole="button" accessibilityState={{ selected }}
  >
    <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text>
  </TouchableOpacity>
);

const styles = StyleSheet.create({
  root: { padding: spacing.base, paddingBottom: spacing.xxl },
  studentRow: { flexDirection: 'row', alignItems: 'center' },
  studentText: { flex: 1 },
  studentName: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold, color: colors.heading },
  meta: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: 2 },
  linkButton: { minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.sm },
  linkText: { color: colors.primary, fontWeight: typography.fontWeight.bold, fontSize: typography.fontSize.sm },
  spinner: { marginVertical: spacing.md },
  alertBox: { marginTop: spacing.md, padding: spacing.md, borderWidth: 1, borderRadius: radius.card },
  alertWarn: { backgroundColor: colors.izinLight, borderColor: colors.izin },
  alertOk: { backgroundColor: colors.hadirLight, borderColor: colors.hadir },
  alertTitle: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  alertText: { fontSize: typography.fontSize.base, color: colors.heading, marginTop: spacing.xs },
  alertLabel: { fontWeight: typography.fontWeight.bold },
  label: {
    marginTop: spacing.md, fontSize: typography.fontSize.xs, color: colors.muted, fontWeight: typography.fontWeight.bold,
  },
  input: {
    marginTop: spacing.xs, minHeight: 44, borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.input,
    backgroundColor: colors.white, paddingHorizontal: spacing.md, fontSize: typography.fontSize.base, color: colors.heading,
  },
  multiline: { minHeight: 72, textAlignVertical: 'top', paddingTop: spacing.sm },
  pair: { flexDirection: 'row', gap: spacing.md },
  pairItem: { flex: 1 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  chip: {
    minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.md, borderWidth: 1,
    borderColor: colors.borderDark, borderRadius: radius.button, backgroundColor: colors.white,
  },
  chipSelected: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: typography.fontSize.sm, color: colors.body },
  chipTextSelected: { color: colors.white, fontWeight: typography.fontWeight.bold },
  switchRow: { flexDirection: 'row', alignItems: 'center', marginTop: spacing.md },
  switchLabel: { flex: 1, fontSize: typography.fontSize.base, color: colors.body, paddingRight: spacing.md },
  issue: { marginTop: spacing.xs, color: colors.alpa, fontSize: typography.fontSize.sm },
  failure: {
    marginTop: spacing.md, padding: spacing.md, backgroundColor: colors.alpaLight, color: colors.alpa,
    fontSize: typography.fontSize.sm, borderRadius: radius.card,
  },
  privacy: { marginTop: spacing.md, fontSize: typography.fontSize.xs, color: colors.muted },
  save: {
    marginTop: spacing.md, minHeight: 48, justifyContent: 'center', alignItems: 'center',
    backgroundColor: colors.primary, borderRadius: radius.button,
  },
  saveDisabled: { opacity: 0.6 },
  saveText: { color: colors.white, fontWeight: typography.fontWeight.bold, fontSize: typography.fontSize.base },
});
