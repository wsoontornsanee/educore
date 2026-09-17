/**
 * EduCore Guru Mobile App Entry Point.
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, SafeAreaView, StyleSheet, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { checkAuth, logout } from './src/services/auth';
import { todayWib } from './src/services/localDate';
import { isParent } from './src/services/roleRouting';
import { initQueueDb } from './src/services/offlineQueue';
import {
  deactivatePushTokenAsync,
  registerForPushNotificationsAsync,
  subscribeToNotificationReceived,
} from './src/services/pushNotifications';
import { LoginScreen } from './src/screens/LoginScreen';
import { AgendaScreen } from './src/screens/AgendaScreen';
import { RollCallScreen } from './src/screens/RollCallScreen';
import { SubstitutionModal } from './src/screens/SubstitutionModal';
import { ParentShell, ParentTab } from './src/screens/parent/ParentShell';
import { ParentHomeScreen } from './src/screens/parent/ParentHomeScreen';
import { ParentAttendanceScreen } from './src/screens/parent/ParentAttendanceScreen';
import { ParentInvoicesScreen } from './src/screens/parent/ParentInvoicesScreen';
import { ParentWalletScreen } from './src/screens/parent/ParentWalletScreen';
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

  const isCanteenOperator = currentUser?.roles?.some((r) => r.role === 'canteen_operator');
  const todayStr = todayWib();

  useEffect(() => {
    const bootstrap = async () => {
      await initQueueDb();
      await initPosQueueDb();
      const authState = await checkAuth();
      if (authState.authenticated && authState.user) {
        setCurrentUser(authState.user);
        // Register push tokens in background
        registerForPushNotificationsAsync().catch(() => {});
      }
      setCheckingAuth(false);
    };

    bootstrap();

    // Subscribe to push notification events
    const sub = subscribeToNotificationReceived((notification) => {
      const data = notification?.request?.content?.data;
      if (data?.type === 'SUBSTITUTE_ASSIGNED' && data?.slot) {
        setSubModalSlot(data.slot);
      }
    });

    return () => {
      if (sub && typeof sub.remove === 'function') {
        sub.remove();
      }
    };
  }, []);

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
        <ParentShell activeTab={parentTab} onTabChange={setParentTab} onLogout={handleLogout}>
          {({ selectedChild, allChildren }) =>
            parentTab === 'HOME' ? (
              <ParentHomeScreen child={selectedChild} onNavigateTab={setParentTab} />
            ) : parentTab === 'ATTENDANCE' ? (
              <ParentAttendanceScreen child={selectedChild} />
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
