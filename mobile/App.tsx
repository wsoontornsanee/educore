/**
 * EduCore Guru Mobile App Entry Point.
 */
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, SafeAreaView, StyleSheet, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { checkAuth, logout } from './src/services/auth';
import { todayWib } from './src/services/localDate';
import { isParent, isStaff } from './src/services/roleRouting';
import { fetchSubstitutionSlot } from './src/services/agenda';
import { track } from './src/services/analytics';
import { initAnalyticsQueueDb } from './src/services/analyticsQueue';
import { initQueueDb } from './src/services/offlineQueue';
import {
  deactivatePushTokenAsync,
  registerForPushNotificationsAsync,
  subscribeToNotificationReceived,
  subscribeToNotificationResponseReceived,
  getInitialNotificationResponse,
} from './src/services/pushNotifications';
import { getBiometricEnabled } from './src/services/storage';
import { authenticateBiometric } from './src/services/biometric';
import { LocaleProvider } from './src/i18n/LocaleContext';
import { LoginScreen } from './src/screens/LoginScreen';
import { AgendaScreen } from './src/screens/AgendaScreen';
import { RollCallScreen } from './src/screens/RollCallScreen';
import { SubstitutionModal } from './src/screens/SubstitutionModal';
import { ParentShell, ParentTab } from './src/screens/parent/ParentShell';
import { ParentHomeScreen } from './src/screens/parent/ParentHomeScreen';
import { ParentAttendanceScreen } from './src/screens/parent/ParentAttendanceScreen';
import { ParentInvoicesScreen } from './src/screens/parent/ParentInvoicesScreen';
import { ParentWalletScreen } from './src/screens/parent/ParentWalletScreen';
import { ParentAcademicScreen } from './src/screens/parent/ParentAcademicScreen';
import { ParentMessagesScreen } from './src/screens/parent/ParentMessagesScreen';
import { ParentProfileScreen } from './src/screens/parent/ParentProfileScreen';
import { POSKioskScreen } from './src/screens/POSKioskScreen';
import { ParentNutritionDashboardScreen } from './src/screens/ParentNutritionDashboardScreen';

import { initPosQueueDb } from './src/services/posOfflineQueue';
import { colors } from './src/theme/tokens';
import { StudentRosterItem, TimetableSlotItem, UserProfile } from './src/types';


export default function App() {
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [activeSlot, setActiveSlot] = useState<TimetableSlotItem | null>(null);
  const [subModalSlot, setSubModalSlot] = useState<TimetableSlotItem | null>(null);
  const [parentTab, setParentTab] = useState<ParentTab>('HOME');
  const [posMode, setPosMode] = useState(false);
  const [nutritionMode, setNutritionMode] = useState(false);
  const [deepLinkChildId, setDeepLinkChildId] = useState<number | null>(null);
  const [deepLinkDate, setDeepLinkDate] = useState<string | null>(null);
  // PAR-018: biometric gate — true means we need biometric auth before showing the shell
  const [biometricBlocked, setBiometricBlocked] = useState(false);
  const [biometricChecked, setBiometricChecked] = useState(false);

  const isCanteenOperator = currentUser?.roles?.some((r) => r.role === 'canteen_operator');
  const todayStr = todayWib();

  const parseNumericId = (value: unknown): number | null => {
    if (value === null || value === undefined) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };

  const handleNotificationData = async (data: any, user: UserProfile | null) => {
    if (!data) return;
    if ((data.type === 'ARRIVAL' || data.type === 'DEPARTURE') && isParent(user)) {
      setParentTab('ATTENDANCE');
      setDeepLinkChildId(parseNumericId(data.student_id));
      setDeepLinkDate(typeof data.date === 'string' ? data.date : null);
    } else if (data.type === 'SUBSTITUTE_ASSIGNED' && isStaff(user)) {
      const subId = parseNumericId(data.substitution_id);
      if (subId) {
        try {
          const slotItem = await fetchSubstitutionSlot(subId);
          setActiveSlot(null);
          setSubModalSlot(slotItem);
        } catch (err) {
          console.warn('Failed to fetch substitution slot for modal auto-open:', err);
        }
      }
    }
  };

  // Mirrors `currentUser` for use inside the notification-handling closures
  // below, which are set up once (empty-deps effect) and would otherwise
  // only ever see the user from an existing-session restore, never a fresh
  // in-session login via handleLoginSuccess.
  const currentUserRef = useRef<UserProfile | null>(null);
  useEffect(() => {
    currentUserRef.current = currentUser;
  }, [currentUser]);

  useEffect(() => {
    const bootstrap = async () => {
      await initQueueDb();
      await initPosQueueDb();
      await initAnalyticsQueueDb();
      const authState = await checkAuth();
      if (authState.authenticated && authState.user) {
        setCurrentUser(authState.user);
        currentUserRef.current = authState.user;
        // Register push tokens in background
        registerForPushNotificationsAsync().catch(() => {});
        track('app_open');

        // Cold-start: app was launched by tapping a notification.
        const initialData = await getInitialNotificationResponse();
        if (initialData) {
          await handleNotificationData(initialData, currentUserRef.current);
        }

        // PAR-018: check if biometric gate is enabled
        const bioEnabled = await getBiometricEnabled();
        if (bioEnabled) {
          setBiometricBlocked(true);
        }
      }
      setBiometricChecked(true);
      setCheckingAuth(false);
    };

    bootstrap();

    // Foreground: notification arrived while the app is already open. This
    // fires on mere delivery, before any user interaction — it must never
    // trigger deep-link navigation or auto-open modals (see design doc §4).
    const sub = subscribeToNotificationReceived((_notification) => {
      track('notification_opened');
    });

    // Tap: user taps a notification while the app is backgrounded or foregrounded.
    const responseSub = subscribeToNotificationResponseReceived(async (data) => {
      await handleNotificationData(data, currentUserRef.current);
      track('notification_opened');
    });

    return () => {
      if (sub && typeof sub.remove === 'function') {
        sub.remove();
      }
      if (responseSub && typeof responseSub.remove === 'function') {
        responseSub.remove();
      }
    };
  }, []);

  // PAR-018: attempt biometric auth when blocked
  useEffect(() => {
    if (!biometricBlocked) return;
    (async () => {
      const result = await authenticateBiometric('Masuk ke EduCore');
      if (result.success) {
        setBiometricBlocked(false);
      } else {
        // Auth failed or cancelled: log the user out for safety
        await handleLogout();
        setBiometricBlocked(false);
      }
    })();
  }, [biometricBlocked]);

  const handleLoginSuccess = (user: UserProfile) => {
    setCurrentUser(user);
    registerForPushNotificationsAsync().catch(() => {});
  };

  const handleLogout = async () => {
    await deactivatePushTokenAsync();
    await logout();
    setCurrentUser(null);
    setActiveSlot(null);
  };


  if (checkingAuth) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  // PAR-018: biometric gate loading — show spinner while prompt is in progress
  if (biometricBlocked) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  // Sample student roster generator for slots
  const getMockRosterForSlot = (slot: TimetableSlotItem): StudentRosterItem[] => {
    if (slot.roster && slot.roster.length > 0) return slot.roster;
    // Generate 32 sample students with 2 gate exceptions per TCH-001/003
    return Array.from({ length: 32 }, (_, idx) => {
      const id = idx + 1;
      const isAbsentAtGate = id === 5 || id === 18;
      return {
        student_id: id,
        full_name: `Siswa Contoh ${id}`,
        nis: `2026${String(id).padStart(3, '0')}`,
        nisn: `00${String(id).padStart(8, '0')}`,
        gate_status: isAbsentAtGate ? 'NO_SCAN' : 'IN',
        prefill_status: isAbsentAtGate ? 'ALPA' : 'HADIR',
        is_gate_prefill: isAbsentAtGate,
        medical_flags: id === 12 ? ['ASMA'] : [],
      };
    });
  };

  return (
    <LocaleProvider>
      <View style={styles.root}>
        <StatusBar style="dark" />
        {!currentUser ? (
          <LoginScreen onLoginSuccess={handleLoginSuccess} />
        ) : nutritionMode ? (
          <ParentNutritionDashboardScreen
            onBack={() => {
              if (nutritionMode) {
                setNutritionMode(false);
              } else {
                handleLogout();
              }
            }}
          />
        ) : isParent(currentUser) ? (
          <ParentShell
            activeTab={parentTab}
            onTabChange={setParentTab}
            onLogout={handleLogout}
            deepLinkChildId={deepLinkChildId}
          >
            {({ selectedChild, allChildren }) =>
              parentTab === 'HOME' ? (
                <ParentHomeScreen child={selectedChild} onNavigateTab={setParentTab} />
              ) : parentTab === 'ATTENDANCE' ? (
                <ParentAttendanceScreen child={selectedChild} highlightDate={deepLinkDate} />
              ) : parentTab === 'ACADEMIC' ? (
                <ParentAcademicScreen
                  key={selectedChild.student_id}
                  child={selectedChild}
                  onNavigateInvoices={() => setParentTab('INVOICES')}
                />
              ) : parentTab === 'MESSAGES' ? (
                <ParentMessagesScreen key={selectedChild.student_id} child={selectedChild} />
              ) : parentTab === 'WALLET' ? (
                <ParentWalletScreen
                  key={selectedChild.student_id}
                  child={selectedChild}
                  onNavigateNutrition={() => setParentTab('NUTRITION')}
                />
              ) : parentTab === 'NUTRITION' ? (
                <ParentNutritionDashboardScreen
                  key={selectedChild.student_id}
                  initialStudentId={selectedChild.student_id}
                  linkedStudents={allChildren.map((c) => ({
                    id: c.student_id,
                    full_name: c.full_name,
                    nis: c.nis,
                    nisn: c.nisn,
                    class_name: c.class_name,
                    school_name: c.school_name,
                  }))}
                  onBack={() => setParentTab('HOME')}
                />
              ) : parentTab === 'PROFILE' ? (
                // PAR-013/PAR-014/PAR-018: Profile tab
                <ParentProfileScreen
                  user={currentUser}
                  allChildren={allChildren}
                  onLogout={handleLogout}
                />
              ) : (
                // Keyed on the child so switching children mid-payment remounts the
                // screen instead of leaving the previous child's VA/amount on screen.
                <ParentInvoicesScreen key={selectedChild.student_id} child={selectedChild} />
              )
            }
          </ParentShell>
        ) : isCanteenOperator || posMode ? (
          <POSKioskScreen
            onBack={() => {
              if (posMode) {
                setPosMode(false);
              } else {
                handleLogout();
              }
            }}
          />
        ) : activeSlot ? (
          <RollCallScreen
            slot={activeSlot}
            dateStr={todayStr}
            initialRoster={getMockRosterForSlot(activeSlot)}
            onBack={() => setActiveSlot(null)}
            onSaved={() => setActiveSlot(null)}
          />
        ) : (
          <AgendaScreen
            user={currentUser}
            onSelectSlot={(slot) => setActiveSlot(slot)}
            onOpenSubstitution={(slot) => setSubModalSlot(slot)}
            onLogout={handleLogout}
          />
        )}

        {/* Substitution Modal */}
        <SubstitutionModal
          visible={!!subModalSlot}
          slot={subModalSlot}
          onClose={() => setSubModalSlot(null)}
          onResolved={() => {
            setSubModalSlot(null);
          }}
        />
      </View>
    </LocaleProvider>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  center: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.surface,
  },
});
