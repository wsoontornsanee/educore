/**
 * Mandatory Stale / Offline Banner (spec/17 Design Tokens).
 * 
 * Informs the teacher when network is disconnected or cached data is active.
 * Strict 0px border radius.
 */
import React from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, radius, spacing, typography } from '../theme/tokens';

interface StaleOfflineBannerProps {
  isOffline: boolean;
  pendingCount?: number;
  lastSyncedAt?: string | null;
  onSyncPress?: () => void;
  isSyncing?: boolean;
}

export const StaleOfflineBanner: React.FC<StaleOfflineBannerProps> = ({
  isOffline,
  pendingCount = 0,
  lastSyncedAt,
  onSyncPress,
  isSyncing = false,
}) => {
  if (!isOffline && pendingCount === 0) {
    return null;
  }

  return (
    <View style={styles.container}>
      <View style={styles.left}>
        <View style={styles.iconBox}>
          <Text style={styles.icon}>⚡</Text>
        </View>
        <View style={styles.textContainer}>
          <Text style={styles.title}>
            {isOffline ? 'Mode Offline Aktif' : 'Presensi Menunggu Sinkron'}
          </Text>
          <Text style={styles.subtitle}>
            {pendingCount > 0
              ? `${pendingCount} sesi presensi tersimpan lokal di HP ini.`
              : 'Perubahan akan otomatis dikirim saat sinyal pulih.'}
            {lastSyncedAt ? ` (Sinkron: ${lastSyncedAt})` : ''}
          </Text>
        </View>
      </View>

      {onSyncPress && pendingCount > 0 && !isOffline && (
        <TouchableOpacity
          style={styles.syncButton}
          onPress={onSyncPress}
          disabled={isSyncing}
          activeOpacity={0.8}
        >
          {isSyncing ? (
            <ActivityIndicator size="small" color={colors.white} />
          ) : (
            <Text style={styles.syncButtonText}>Sinkronkan</Text>
          )}
        </TouchableOpacity>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    backgroundColor: colors.offlineLight,
    borderBottomWidth: 1,
    borderTopWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderRadius: radius.card,
  },
  left: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
  },
  iconBox: {
    marginRight: spacing.sm,
  },
  icon: {
    fontSize: 16,
    color: colors.offline,
  },
  textContainer: {
    flex: 1,
  },
  title: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  subtitle: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    marginTop: 1,
  },
  syncButton: {
    backgroundColor: colors.heading,
    borderRadius: radius.button,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    marginLeft: spacing.sm,
  },
  syncButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    textTransform: 'uppercase',
  },
});
