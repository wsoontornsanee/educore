/**
 * Sync Status Floating Pill (spec/17 Design Tokens).
 * 
 * Strict 0px border radius.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, radius, spacing, typography } from '../theme/tokens';

interface SyncStatusPillProps {
  pendingCount: number;
  onPress?: () => void;
}

export const SyncStatusPill: React.FC<SyncStatusPillProps> = ({ pendingCount, onPress }) => {
  if (pendingCount <= 0) return null;

  return (
    <TouchableOpacity
      style={styles.pill}
      onPress={onPress}
      activeOpacity={0.85}
      disabled={!onPress}
    >
      <View style={styles.badge}>
        <Text style={styles.badgeText}>{pendingCount}</Text>
      </View>
      <Text style={styles.text}>Menunggu Sinkron</Text>
    </TouchableOpacity>
  );
};

const styles = StyleSheet.create({
  pill: {
    backgroundColor: colors.heading,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.sm,
    borderRadius: radius.card,
    borderWidth: 1,
    borderColor: colors.borderDark,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.15,
    shadowRadius: 0,
    elevation: 3,
  },
  badge: {
    backgroundColor: colors.primary,
    borderRadius: radius.badge,
    minWidth: 18,
    height: 18,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 4,
    marginRight: spacing.xs,
  },
  badgeText: {
    color: colors.white,
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
  },
  text: {
    color: colors.white,
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.medium,
  },
});
