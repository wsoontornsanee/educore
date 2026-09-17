/**
 * Staff Profile Modal (spec/14 §6, TASK-036).
 *
 * Displays staff user details and the SSO Linked Accounts management section.
 */
import React from 'react';
import {
  Modal,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { LinkedAccountsSection } from '../components/LinkedAccountsSection';
import { colors, radius, spacing, typography } from '../theme/tokens';
import type { UserProfile } from '../types';

interface StaffProfileModalProps {
  visible: boolean;
  user: UserProfile;
  onClose: () => void;
}

export const StaffProfileModal: React.FC<StaffProfileModalProps> = ({
  visible,
  user,
  onClose,
}) => {
  return (
    <Modal visible={visible} animationType="slide" transparent={false} onRequestClose={onClose}>
      <SafeAreaView style={styles.safeArea}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>PROFIL PENGGUNA</Text>
          <TouchableOpacity
            style={styles.closeButton}
            onPress={onClose}
            hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
          >
            <Text style={styles.closeButtonText}>Tutup</Text>
          </TouchableOpacity>
        </View>

        <ScrollView contentContainerStyle={styles.content}>
          {/* User Info Card */}
          <View style={styles.infoCard}>
            <View style={styles.brandBar} />
            <Text style={styles.userName}>{user.full_name}</Text>
            <Text style={styles.userRole}>
              {user.roles?.map((r) => r.role.toUpperCase()).join(' · ') || 'GURU / STAF'}
            </Text>
            <View style={styles.detailRow}>
              <Text style={styles.detailLabel}>Email:</Text>
              <Text style={styles.detailValue}>{user.email || '—'}</Text>
            </View>
            <View style={styles.detailRow}>
              <Text style={styles.detailLabel}>Nomor HP:</Text>
              <Text style={styles.detailValue}>{user.phone_e164 || '—'}</Text>
            </View>
          </View>

          {/* Linked SSO Accounts Section */}
          <LinkedAccountsSection />
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderDark,
    backgroundColor: colors.white,
  },
  headerTitle: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    letterSpacing: 1,
  },
  closeButton: {
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
  },
  closeButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  content: {
    padding: spacing.base,
  },
  infoCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.card,
    padding: spacing.base,
    marginBottom: spacing.base,
  },
  brandBar: {
    height: 3,
    backgroundColor: colors.primary,
    marginBottom: spacing.md,
  },
  userName: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  userRole: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.medium,
    color: colors.muted,
    marginBottom: spacing.md,
    letterSpacing: 0.5,
  },
  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  detailLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  detailValue: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.medium,
    color: colors.heading,
  },
});
