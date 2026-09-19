/**
 * Parent: pickup authorisations for one child (spec/05 ATT-015). A guardian authorises a named person for a
 * time window, shows them the QR, sees the status and can revoke. Photo upload is a separate item.
 * The list holds a phone number and a live QR token, so nothing here is written to device storage.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator, Alert, FlatList, Modal, SafeAreaView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import QRCode from 'react-native-qrcode-svg';
import {
  createPickupAuthorization, fetchPickupAuthorizations, isLive, pickupErrorCode, revokePickupAuthorization,
  type WindowPreset,
} from '../../services/pickup.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type { ChildSummary, PickupAuthorizationItem } from '../../types/index.ts';

interface ParentPickupScreenProps {
  child: ChildSummary;
  onClose: () => void;
}

const PRESETS: WindowPreset[] = ['TODAY', 'WEEK', 'MONTH'];

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('id-ID', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

export const ParentPickupScreen: React.FC<ParentPickupScreenProps> = ({ child, onClose }) => {
  const { t } = useLocale();
  const [items, setItems] = useState<PickupAuthorizationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [qrFor, setQrFor] = useState<PickupAuthorizationItem | null>(null);

  const [personName, setPersonName] = useState('');
  const [relation, setRelation] = useState('');
  const [phone, setPhone] = useState('');
  const [preset, setPreset] = useState<WindowPreset>('TODAY');
  const [oneTime, setOneTime] = useState(true);

  const explain = useCallback((error: unknown) => (
    pickupErrorCode(error) === 'PICKUP_NOT_GUARDIAN' ? t('pickup.error.not_guardian') : t('pickup.error.generic')
  ), [t]);

  const load = useCallback(async () => {
    setLoading(true);
    setMessage(null);
    try {
      setItems(await fetchPickupAuthorizations(child.student_id));
    } catch (error) {
      setMessage(explain(error));
    } finally {
      setLoading(false);
    }
  }, [child.student_id, explain]);

  useEffect(() => { load(); }, [load]);

  const submit = async () => {
    if (!personName.trim()) {
      setMessage(t('pickup.name_required'));
      return;
    }
    setSubmitting(true); // one request per tap: the create call is not idempotent
    setMessage(null);
    try {
      const created = await createPickupAuthorization({
        studentId: child.student_id, personName, relation, phone, preset, oneTime,
      });
      setItems((current) => [created, ...current]);
      setAdding(false);
      setPersonName(''); setRelation(''); setPhone('');
      if (created.qr_token) setQrFor(created);
    } catch (error) {
      setMessage(explain(error));
    } finally {
      setSubmitting(false);
    }
  };

  const revoke = (item: PickupAuthorizationItem) => {
    Alert.alert(t('pickup.revoke'), t('pickup.revoke_confirm'), [
      { text: t('common.cancel'), style: 'cancel' },
      {
        text: t('pickup.revoke'),
        style: 'destructive',
        onPress: async () => {
          try {
            const updated = await revokePickupAuthorization(item.id);
            setItems((current) => current.map((row) => (row.id === updated.id ? updated : row)));
            if (qrFor?.id === item.id) setQrFor(null);
          } catch (error) {
            setMessage(explain(error));
          }
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>{t('pickup.title')}</Text>
        <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel={t('common.close')} style={styles.closeButton}>
          <Text style={styles.closeText}>{t('common.close')}</Text>
        </TouchableOpacity>
      </View>
      <Text style={styles.childName}>{child.full_name}</Text>

      {message && (
        <View style={styles.errorCard} accessibilityRole="alert">
          <Text style={styles.errorText}>{message}</Text>
        </View>
      )}

      {loading ? (
        <ActivityIndicator size="large" color={colors.primary} style={styles.loader} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          ListHeaderComponent={
            adding ? (
              <View style={styles.form}>
                <Field label={t('pickup.person_name')} value={personName} onChange={setPersonName} />
                <Field label={t('pickup.relation')} value={relation} onChange={setRelation} />
                <Field label={t('pickup.phone')} value={phone} onChange={setPhone} keyboardType="phone-pad" />
                <Text style={styles.label}>{t('pickup.validity')}</Text>
                <View style={styles.presetRow}>
                  {PRESETS.map((key) => (
                    <TouchableOpacity
                      key={key}
                      onPress={() => setPreset(key)}
                      style={[styles.preset, preset === key && styles.presetActive]}
                      accessibilityRole="button"
                      accessibilityState={{ selected: preset === key }}
                    >
                      <Text style={[styles.presetText, preset === key && styles.presetTextActive]}>{t(`pickup.preset.${key}`)}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                <View style={styles.switchRow}>
                  <Text style={styles.label}>{t('pickup.one_time')}</Text>
                  <Switch value={oneTime} onValueChange={setOneTime} />
                </View>
                {!oneTime && <Text style={styles.hint}>{t('pickup.one_time_hint')}</Text>}
                <TouchableOpacity style={styles.primary} onPress={submit} disabled={submitting} accessibilityRole="button">
                  {submitting ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>{t('pickup.submit')}</Text>}
                </TouchableOpacity>
              </View>
            ) : (
              <TouchableOpacity style={styles.primary} onPress={() => setAdding(true)} accessibilityRole="button">
                <Text style={styles.primaryText}>{t('pickup.add')}</Text>
              </TouchableOpacity>
            )
          }
          ListEmptyComponent={<Text style={styles.empty}>{t('pickup.empty')}</Text>}
          renderItem={({ item }) => (
            <View style={styles.card}>
              <View style={styles.cardTop}>
                <Text style={styles.person}>{item.person_name}</Text>
                <Text style={[styles.badge, isLive(item) ? styles.badgeLive : styles.badgeDone]}>{t(`pickup.status.${item.status}`)}</Text>
              </View>
              {!!item.relation && <Text style={styles.meta}>{item.relation}</Text>}
              <Text style={styles.meta}>{formatDateTime(item.valid_from)} – {formatDateTime(item.valid_to)}</Text>
              {isLive(item) && (
                <View style={styles.actions}>
                  {!!item.qr_token && (
                    <TouchableOpacity onPress={() => setQrFor(item)} style={styles.action} accessibilityRole="button">
                      <Text style={styles.actionText}>{t('pickup.show_qr')}</Text>
                    </TouchableOpacity>
                  )}
                  <TouchableOpacity onPress={() => revoke(item)} style={styles.action} accessibilityRole="button">
                    <Text style={[styles.actionText, styles.danger]}>{t('pickup.revoke')}</Text>
                  </TouchableOpacity>
                </View>
              )}
            </View>
          )}
        />
      )}

      <Modal visible={!!qrFor} animationType="fade" transparent onRequestClose={() => setQrFor(null)}>
        <View style={styles.backdrop}>
          <View style={styles.qrCard}>
            <Text style={styles.person}>{qrFor?.person_name}</Text>
            {!!qrFor?.qr_token && <QRCode value={qrFor.qr_token} size={240} ecl="M" />}
            <Text style={styles.hint}>{t('pickup.qr_hint')}</Text>
            <TouchableOpacity style={styles.primary} onPress={() => setQrFor(null)} accessibilityRole="button">
              <Text style={styles.primaryText}>{t('common.close')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
};

const Field: React.FC<{ label: string; value: string; onChange: (v: string) => void; keyboardType?: 'phone-pad' }> = ({
  label, value, onChange, keyboardType,
}) => (
  <View style={styles.field}>
    <Text style={styles.label}>{label}</Text>
    <TextInput style={styles.input} value={value} onChangeText={onChange} keyboardType={keyboardType} accessibilityLabel={label} />
  </View>
);

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: spacing.base },
  headerTitle: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  closeButton: { minHeight: 44, justifyContent: 'center' },
  closeText: { color: colors.primary, fontWeight: typography.fontWeight.bold },
  childName: { paddingHorizontal: spacing.base, color: colors.muted, fontSize: typography.fontSize.base },
  loader: { marginTop: spacing.xl },
  list: { padding: spacing.base, gap: spacing.sm },
  errorCard: { backgroundColor: colors.alpaLight, margin: spacing.base, padding: spacing.md, borderRadius: radius.card },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.sm },
  empty: { textAlign: 'center', color: colors.muted, marginTop: spacing.xl },
  form: { backgroundColor: colors.white, borderRadius: radius.card, borderWidth: 1, borderColor: colors.border, padding: spacing.md, marginBottom: spacing.md },
  field: { marginBottom: spacing.sm },
  label: { fontSize: typography.fontSize.sm, color: colors.body, fontWeight: typography.fontWeight.medium },
  input: { borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.card, padding: spacing.sm, minHeight: 44, color: colors.heading },
  presetRow: { flexDirection: 'row', gap: spacing.sm, marginVertical: spacing.sm },
  preset: { flex: 1, minHeight: 44, justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.card },
  presetActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  presetText: { color: colors.body },
  presetTextActive: { color: colors.white, fontWeight: typography.fontWeight.bold },
  switchRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', minHeight: 44 },
  hint: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: spacing.xs },
  primary: { minHeight: 44, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.primary, borderRadius: radius.card, marginTop: spacing.sm },
  primaryText: { color: colors.white, fontWeight: typography.fontWeight.bold },
  card: { backgroundColor: colors.white, borderRadius: radius.card, borderWidth: 1, borderColor: colors.border, padding: spacing.md },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  person: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading },
  meta: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: 2 },
  badge: { fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold, paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.card },
  badgeLive: { color: colors.hadir, backgroundColor: colors.hadirLight },
  badgeDone: { color: colors.muted, backgroundColor: colors.surfaceAlt },
  actions: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.sm },
  action: { minHeight: 44, justifyContent: 'center' },
  actionText: { color: colors.primary, fontWeight: typography.fontWeight.bold },
  danger: { color: colors.alpa },
  backdrop: { flex: 1, backgroundColor: 'rgba(15,23,42,0.6)', justifyContent: 'center', padding: spacing.xl },
  qrCard: { backgroundColor: colors.white, borderRadius: radius.card, padding: spacing.xl, alignItems: 'center', gap: spacing.md },
});
