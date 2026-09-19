/**
 * Teacher Profile Screen (spec/09, teacher mobile shell Tab 4).
 *
 * Shows user info and logout. Notification preferences can be added
 * as a follow-up (reuses PAR-013 notificationPrefs.ts logic).
 */
import React from 'react';
import { SafeAreaView, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import { useLocale } from '../../i18n/LocaleContext';
import { rolesLabel } from '../../services/roleLabels';
import type { UserProfile } from '../../types';

interface TeacherProfileScreenProps {
  user: UserProfile;
  onLogout: () => void;
}

export const TeacherProfileScreen: React.FC<TeacherProfileScreenProps> = ({ user, onLogout }) => {
  const { t, locale } = useLocale();

  // All held roles, senior first: a teacher who is also a guardian shows both, not whichever the API listed first.
  const roleLabel = rolesLabel(user.roles, locale);

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* User Info Card */}
        <View style={styles.card}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>
              {user.full_name.charAt(0).toUpperCase()}
            </Text>
          </View>
          <Text style={styles.name}>{user.full_name}</Text>
          <Text style={styles.role}>{roleLabel}</Text>
          <Text style={styles.contact}>{user.phone_e164}</Text>
          {user.email && <Text style={styles.contact}>{user.email}</Text>}
        </View>

        {/* Logout */}
        <TouchableOpacity
          style={styles.logoutButton}
          onPress={onLogout}
          accessibilityRole="button"
          accessibilityLabel="Keluar dari aplikasi"
        >
          <Text style={styles.logoutText}>KELUAR</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  content: {
    padding: spacing.base,
  },
  card: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    alignItems: 'center',
    marginBottom: spacing.lg,
  },
  avatar: {
    width: 64,
    height: 64,
    backgroundColor: colors.primaryLight,
    borderWidth: 2,
    borderColor: colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  avatarText: {
    fontSize: typography.fontSize.xxl,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  name: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  role: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    marginBottom: spacing.sm,
  },
  contact: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginBottom: spacing.xs,
  },
  logoutButton: {
    backgroundColor: colors.alpa,
    minHeight: 48,
    justifyContent: 'center',
    alignItems: 'center',
  },
  logoutText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    letterSpacing: 1,
  },
});
