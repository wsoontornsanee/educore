/**
 * Profile Tab Screen — PAR-013, PAR-014, PAR-018.
 *
 * Sections:
 *  1. User info card (name, masked phone)
 *  2. Anak terhubung — read-only linked children list
 *  3. Notifikasi — per-category toggle + channel chips + quiet hours
 *  4. Bahasa / Language — segmented control (id-ID / en-US)
 *  5. Keamanan / Security — biometric unlock toggle
 *  6. Keluar / Logout
 *
 * Design: 0px radii (spec/17), #C8102E accent, >=44dp touch targets (PAR-016).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { ParentClinicHistoryScreen } from './ParentClinicHistoryScreen';
import { ParentPickupScreen } from './ParentPickupScreen';
import { useLocale } from '../../i18n/LocaleContext';
import { t } from '../../i18n/strings';
import {
  authenticateBiometric,
  getBiometricTypeLabel,
  hasBiometricHardware,
  isBiometricAvailable,
} from '../../services/biometric';
import {
  PARENT_NOTIFICATION_CATEGORIES,
  buildPrefMap,
  fetchNotificationPrefs,
  getEffectivePref,
  saveNotificationPref,
} from '../../services/notificationPrefs';
import {
  getBiometricEnabled,
  setBiometricEnabled,
} from '../../services/storage';
import { LinkedAccountsSection } from '../../components/LinkedAccountsSection';
import { SpendingPinSheet, SpendingPinSheetMode } from '../../components/SpendingPinSheet';
import { fetchPinStatus } from '../../services/qrCharge';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { ChildSummary, NotificationChannelType, NotificationLocale, UserProfile } from '../../types/index';

const AVAILABLE_CHANNELS: NotificationChannelType[] = ['WHATSAPP', 'PUSH', 'SMS'];

interface ParentProfileScreenProps {
  user: UserProfile;
  allChildren: ChildSummary[];
  onLogout: () => void;
}

export const ParentProfileScreen: React.FC<ParentProfileScreenProps> = ({
  user,
  allChildren,
  onLogout,
}) => {
  const { locale, setLocale } = useLocale();

  // Notification preferences state
  const [prefMap, setPrefMap] = useState<Record<string, any>>({});
  const [prefsLoading, setPrefsLoading] = useState(true);
  const [savingCategory, setSavingCategory] = useState<string | null>(null);

  // Global quiet hours — single window applied across categories (UI simplification)
  const [quietStart, setQuietStart] = useState('21:00');
  const [quietEnd, setQuietEnd] = useState('06:00');

  // Biometric state
  const [bioHardwareAvailable, setBioHardwareAvailable] = useState(false);
  const [bioEnabled, setBioEnabled] = useState(false);
  const [bioLabel, setBioLabel] = useState('Biometrik');

  // Guardian spending PIN (QR Charge): shown only once a PIN exists — first-time setup happens
  // in the QR pay flow. `pinSheet` is the open change/reset sheet, if any.
  const [pinIsSet, setPinIsSet] = useState(false);
  const [pinSheet, setPinSheet] = useState<SpendingPinSheetMode | null>(null);

  useEffect(() => {
    fetchPinStatus()
      .then((status) => setPinIsSet(status.is_set))
      .catch(() => setPinIsSet(false));
  }, []);

  // Clinic visit history modal — which child's history is currently shown, if any
  const [clinicHistoryChild, setClinicHistoryChild] = useState<ChildSummary | null>(null);
  // Pickup authorisation modal — which child's authorisations are being managed, if any
  const [pickupChild, setPickupChild] = useState<ChildSummary | null>(null);

  // Load data on mount
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const prefs = await fetchNotificationPrefs();
        if (!cancelled) {
          const map = buildPrefMap(prefs);
          setPrefMap(map);
          // Use the first pref's quiet hours as the global window
          const firstPref = prefs[0];
          if (firstPref) {
            setQuietStart(firstPref.quiet_hours_start);
            setQuietEnd(firstPref.quiet_hours_end);
          }
        }
      } catch {
        // Silently degrade — prefs show as defaults
      } finally {
        if (!cancelled) setPrefsLoading(false);
      }

      // Biometric
      const hasHw = await hasBiometricHardware();
      const enabled = await getBiometricEnabled();
      const label = await getBiometricTypeLabel(locale);
      if (!cancelled) {
        setBioHardwareAvailable(hasHw);
        setBioEnabled(enabled);
        setBioLabel(label);
      }
    })();

    return () => { cancelled = true; };
  }, []);

  // Toggle a notification category on/off
  const handleToggleCategory = useCallback(
    async (category: string, enabled: boolean) => {
      const current = getEffectivePref(prefMap, category);
      const updated = { ...current, enabled, quiet_hours_start: quietStart, quiet_hours_end: quietEnd };
      setSavingCategory(category);
      try {
        const saved = await saveNotificationPref(updated);
        setPrefMap((prev) => ({ ...prev, [category]: saved }));
      } catch {
        Alert.alert(t('common.error', locale), t('notif.save_error', locale));
      } finally {
        setSavingCategory(null);
      }
    },
    [prefMap, quietStart, quietEnd, locale]
  );

  // Toggle a channel for a category
  const handleToggleChannel = useCallback(
    async (category: string, channel: NotificationChannelType, active: boolean) => {
      const current = getEffectivePref(prefMap, category);
      const channels = active
        ? [...new Set([...current.channels, channel])]
        : current.channels.filter((c) => c !== channel);
      const updated = { ...current, channels, quiet_hours_start: quietStart, quiet_hours_end: quietEnd };
      setSavingCategory(category);
      try {
        const saved = await saveNotificationPref(updated);
        setPrefMap((prev) => ({ ...prev, [category]: saved }));
      } catch {
        Alert.alert(t('common.error', locale), t('notif.save_error', locale));
      } finally {
        setSavingCategory(null);
      }
    },
    [prefMap, quietStart, quietEnd, locale]
  );

  // Biometric toggle
  const handleBiometricToggle = useCallback(
    async (enable: boolean) => {
      if (enable) {
        const available = await isBiometricAvailable();
        if (!available) {
          Alert.alert(
            t('bio.not_available', locale),
            t('bio.not_enrolled', locale)
          );
          return;
        }
        const result = await authenticateBiometric(t('bio.enroll_prompt', locale));
        if (!result.success) return; // User cancelled — don't enable
        await setBiometricEnabled(true);
        setBioEnabled(true);
      } else {
        await setBiometricEnabled(false);
        setBioEnabled(false);
      }
    },
    [locale]
  );

  // Mask phone: +6281234567890 -> +62 812 ****7890
  const maskedPhone = user.phone_e164
    ? user.phone_e164.replace(/(\+\d{2})(\d{3})(\d+)(\d{4})$/, '$1 $2 ****$4')
    : '';

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>

        {/* 1. User info card */}
        <View style={styles.card}>
          <Text style={styles.userName}>{user.full_name}</Text>
          <Text style={styles.userPhone}>{maskedPhone}</Text>
        </View>

        {/* 2. Linked children */}
        <Text style={styles.sectionHeader}>{t('profile.section.children', locale)}</Text>
        <View style={styles.card}>
          {allChildren.length === 0 ? (
            <Text style={styles.emptyText}>—</Text>
          ) : (
            allChildren.map((child) => (
              <View key={child.student_id} style={styles.childRow}>
                <Text style={styles.childName}>{child.full_name}</Text>
                {child.financial_responsible && (
                  <View style={styles.badge}>
                    <Text style={styles.badgeText}>Keuangan</Text>
                  </View>
                )}
              </View>
            ))
          )}
        </View>

        {/* 2b. Child health / clinic visit history (Notion: Guardian Read Scoping for Clinic Visit History) */}
        {allChildren.length > 0 && (
          <>
            <Text style={styles.sectionHeader}>{t('profile.section.health', locale)}</Text>
            <View style={styles.card}>
              {allChildren.map((child) => (
                <TouchableOpacity
                  key={child.student_id}
                  style={styles.healthRow}
                  onPress={() => setClinicHistoryChild(child)}
                  accessibilityRole="button"
                  accessibilityLabel={`${t('profile.health.view_history', locale)} ${child.full_name}`}
                >
                  <Text style={styles.childName}>{child.full_name}</Text>
                  <Text style={styles.healthLink}>{t('profile.health.view_history', locale)}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </>
        )}

        {/* 2c. Pickup authorisation (ATT-015): who may collect each child */}
        {allChildren.length > 0 && (
          <>
            <Text style={styles.sectionHeader}>{t('profile.section.pickup', locale)}</Text>
            <View style={styles.card}>
              {allChildren.map((child) => (
                <TouchableOpacity
                  key={child.student_id}
                  style={styles.healthRow}
                  onPress={() => setPickupChild(child)}
                  accessibilityRole="button"
                  accessibilityLabel={`${t('profile.pickup.manage', locale)} ${child.full_name}`}
                >
                  <Text style={styles.childName}>{child.full_name}</Text>
                  <Text style={styles.healthLink}>{t('profile.pickup.manage', locale)}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </>
        )}

        {/* 3. Notification preferences */}
        <Text style={styles.sectionHeader}>{t('profile.section.notif', locale)}</Text>

        {/* Global quiet hours */}
        <View style={styles.card}>
          <Text style={styles.rowLabel}>{t('notif.quiet_hours', locale)}</Text>
          <View style={styles.quietHoursRow}>
            <View style={styles.quietHoursField}>
              <Text style={styles.quietHoursFieldLabel}>{t('notif.quiet_start', locale)}</Text>
              <TouchableOpacity
                style={styles.timeButton}
                onPress={() => {
                  // Simple text-based time picker via Alert prompt (no native picker needed for MVP)
                  Alert.prompt
                    ? Alert.prompt(
                        t('notif.quiet_start', locale),
                        'Format HH:MM (misal: 21:00)',
                        (val) => { if (val) setQuietStart(val); },
                        'plain-text',
                        quietStart
                      )
                    : null;
                }}
                accessibilityRole="button"
                accessibilityLabel={`${t('notif.quiet_start', locale)} ${quietStart}`}
              >
                <Text style={styles.timeButtonText}>{quietStart}</Text>
              </TouchableOpacity>
            </View>
            <View style={styles.quietHoursField}>
              <Text style={styles.quietHoursFieldLabel}>{t('notif.quiet_end', locale)}</Text>
              <TouchableOpacity
                style={styles.timeButton}
                onPress={() => {
                  Alert.prompt
                    ? Alert.prompt(
                        t('notif.quiet_end', locale),
                        'Format HH:MM (misal: 06:00)',
                        (val) => { if (val) setQuietEnd(val); },
                        'plain-text',
                        quietEnd
                      )
                    : null;
                }}
                accessibilityRole="button"
                accessibilityLabel={`${t('notif.quiet_end', locale)} ${quietEnd}`}
              >
                <Text style={styles.timeButtonText}>{quietEnd}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>

        {prefsLoading ? (
          <ActivityIndicator color={colors.primary} style={{ marginVertical: spacing.lg }} />
        ) : (
          PARENT_NOTIFICATION_CATEGORIES.map((category) => {
            const pref = getEffectivePref(prefMap, category);
            const isSaving = savingCategory === category;
            return (
              <View key={category} style={styles.card}>
                <View style={styles.prefRow}>
                  <View style={styles.prefLabelContainer}>
                    <Text style={styles.rowLabel}>{t(`cat.${category}`, locale)}</Text>
                  </View>
                  {isSaving ? (
                    <ActivityIndicator size="small" color={colors.primary} />
                  ) : (
                    <Switch
                      value={pref.enabled}
                      onValueChange={(val) => handleToggleCategory(category, val)}
                      trackColor={{ false: colors.borderDark, true: colors.primary }}
                      thumbColor={colors.white}
                      accessibilityLabel={`${t(`cat.${category}`, locale)} notifikasi`}
                    />
                  )}
                </View>
                {pref.enabled && (
                  <View style={styles.channelRow}>
                    {AVAILABLE_CHANNELS.map((channel) => {
                      const active = pref.channels.includes(channel);
                      return (
                        <TouchableOpacity
                          key={channel}
                          style={[styles.channelChip, active && styles.channelChipActive]}
                          onPress={() => handleToggleChannel(category, channel, !active)}
                          accessibilityRole="checkbox"
                          accessibilityState={{ checked: active }}
                          accessibilityLabel={t(`channel.${channel}`, locale)}
                        >
                          <Text style={[styles.channelChipText, active && styles.channelChipTextActive]}>
                            {t(`channel.${channel}`, locale)}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                )}
              </View>
            );
          })
        )}

        {/* 4. Language */}
        <Text style={styles.sectionHeader}>{t('profile.section.language', locale)}</Text>
        <View style={styles.card}>
          <View style={styles.segmentedControl}>
            {(['id-ID', 'en-US'] as NotificationLocale[]).map((lang) => (
              <TouchableOpacity
                key={lang}
                style={[styles.segmentButton, locale === lang && styles.segmentButtonActive]}
                onPress={() => setLocale(lang)}
                accessibilityRole="radio"
                accessibilityState={{ selected: locale === lang }}
                accessibilityLabel={t(lang === 'id-ID' ? 'lang.id' : 'lang.en', locale)}
              >
                <Text
                  style={[styles.segmentButtonText, locale === lang && styles.segmentButtonTextActive]}
                >
                  {t(lang === 'id-ID' ? 'lang.id' : 'lang.en', locale)}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 5. Security / Biometric + spending PIN */}
        {(bioHardwareAvailable || pinIsSet) && (
          <>
            <Text style={styles.sectionHeader}>{t('profile.section.security', locale)}</Text>
            {pinIsSet && (
              <View style={styles.card}>
                <TouchableOpacity
                  style={styles.prefRow}
                  onPress={() => setPinSheet('CHANGE')}
                  accessibilityRole="button"
                >
                  <View style={styles.prefLabelContainer}>
                    <Text style={styles.rowLabel}>{t('pin.change', locale)}</Text>
                    <Text style={styles.rowSub}>{t('pin.change_sub', locale)}</Text>
                  </View>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.prefRow}
                  onPress={() => setPinSheet('RESET')}
                  accessibilityRole="button"
                >
                  <View style={styles.prefLabelContainer}>
                    <Text style={styles.rowLabel}>{t('pin.forgot', locale)}</Text>
                  </View>
                </TouchableOpacity>
              </View>
            )}
            {bioHardwareAvailable && (
              <View style={styles.card}>
                <View style={styles.prefRow}>
                  <View style={styles.prefLabelContainer}>
                    <Text style={styles.rowLabel}>
                      {t('bio.toggle', locale)} ({bioLabel})
                    </Text>
                    <Text style={styles.rowSub}>{t('bio.toggle_sub', locale)}</Text>
                  </View>
                  <Switch
                    value={bioEnabled}
                    onValueChange={handleBiometricToggle}
                    trackColor={{ false: colors.borderDark, true: colors.primary }}
                    thumbColor={colors.white}
                    accessibilityLabel={`${t('bio.toggle', locale)} ${bioLabel}`}
                  />
                </View>
              </View>
            )}
          </>
        )}

        {/* 6. Linked SSO Accounts */}
        <LinkedAccountsSection />

        {/* 7. Logout */}
        <TouchableOpacity
          style={styles.logoutButton}
          onPress={onLogout}
          accessibilityRole="button"
          accessibilityLabel={t('profile.logout', locale)}
        >
          <Text style={styles.logoutText}>{t('profile.logout', locale)}</Text>
        </TouchableOpacity>

        <View style={{ height: spacing.xxl }} />
      </ScrollView>

      <Modal
        visible={!!clinicHistoryChild}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setClinicHistoryChild(null)}
      >
        {clinicHistoryChild && (
          <ParentClinicHistoryScreen
            child={clinicHistoryChild}
            onClose={() => setClinicHistoryChild(null)}
          />
        )}
      </Modal>

      <Modal
        visible={!!pickupChild}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setPickupChild(null)}
      >
        {pickupChild && <ParentPickupScreen child={pickupChild} onClose={() => setPickupChild(null)} />}
      </Modal>

      <SpendingPinSheet
        visible={pinSheet !== null}
        mode={pinSheet ?? 'CHANGE'}
        phoneE164={user.phone_e164}
        maskedPhone={maskedPhone}
        onClose={() => setPinSheet(null)}
        onDone={(mode) => {
          setPinSheet(null);
          Alert.alert(mode === 'CHANGE' ? t('pin.done_change', locale) : t('pin.done_reset', locale));
        }}
      />
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  scroll: { paddingHorizontal: spacing.base, paddingTop: spacing.lg },
  sectionHeader: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginTop: spacing.lg,
    marginBottom: spacing.sm,
    marginLeft: spacing.xs,
  },
  card: {
    backgroundColor: colors.white,
    borderRadius: radius.card, // 0px
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.base,
    marginBottom: spacing.sm,
  },
  userName: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  userPhone: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
  },
  emptyText: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
  },
  childRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.xs,
  },
  healthRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    minHeight: 44,
    paddingVertical: spacing.xs,
  },
  healthLink: {
    fontSize: typography.fontSize.sm,
    color: colors.primary,
    fontWeight: typography.fontWeight.medium,
  },
  childName: {
    flex: 1,
    fontSize: typography.fontSize.base,
    color: colors.body,
  },
  badge: {
    backgroundColor: colors.primaryLight,
    borderRadius: radius.badge,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: typography.fontSize.xs,
    color: colors.primary,
    fontWeight: typography.fontWeight.medium,
  },
  prefRow: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 44,
  },
  prefLabelContainer: {
    flex: 1,
    marginRight: spacing.sm,
  },
  rowLabel: {
    fontSize: typography.fontSize.base,
    color: colors.body,
    fontWeight: typography.fontWeight.medium,
  },
  rowSub: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  channelRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    marginTop: spacing.sm,
    gap: spacing.xs,
  },
  channelChip: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.badge,
    minHeight: 32,
    justifyContent: 'center',
    alignItems: 'center',
  },
  channelChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  channelChipText: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    fontWeight: typography.fontWeight.medium,
  },
  channelChipTextActive: {
    color: colors.white,
  },
  quietHoursRow: {
    flexDirection: 'row',
    marginTop: spacing.sm,
    gap: spacing.base,
  },
  quietHoursField: {
    flex: 1,
  },
  quietHoursFieldLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginBottom: spacing.xs,
  },
  timeButton: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.input,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.sm,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  timeButtonText: {
    fontSize: typography.fontSize.base,
    color: colors.heading,
    fontWeight: typography.fontWeight.medium,
  },
  segmentedControl: {
    flexDirection: 'row',
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    overflow: 'hidden',
  },
  segmentButton: {
    flex: 1,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.white,
  },
  segmentButtonActive: {
    backgroundColor: colors.primary,
  },
  segmentButtonText: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    fontWeight: typography.fontWeight.medium,
  },
  segmentButtonTextActive: {
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
  },
  logoutButton: {
    marginTop: spacing.xl,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.primary,
    borderRadius: radius.button,
    paddingVertical: spacing.md,
  },
  logoutText: {
    fontSize: typography.fontSize.base,
    color: colors.primary,
    fontWeight: typography.fontWeight.bold,
  },
});
