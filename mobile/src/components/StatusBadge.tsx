/**
 * Institutional Status Badge (spec/17 Design Tokens).
 * 
 * Strict 0px border radius, high-contrast label with dot indicator.
 */
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { colors, radius, spacing, typography } from '../theme/tokens';
import { AttendanceStatus } from '../types';

export type BadgeType = AttendanceStatus | 'GATE' | 'SUBSTITUTE' | 'PENDING' | 'SYNCED' | 'FAILED';

interface StatusBadgeProps {
  type: BadgeType;
  label?: string;
  size?: 'sm' | 'md';
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ type, label, size = 'md' }) => {
  let bgColor = colors.surfaceAlt;
  let textColor = colors.body;
  let dotColor = colors.muted;
  let defaultLabel = type;

  switch (type) {
    case 'HADIR':
      bgColor = colors.hadirLight;
      textColor = colors.hadir;
      dotColor = colors.hadir;
      defaultLabel = 'Hadir';
      break;
    case 'SAKIT':
      bgColor = colors.sakitLight;
      textColor = colors.sakit;
      dotColor = colors.sakit;
      defaultLabel = 'Sakit';
      break;
    case 'IZIN':
      bgColor = colors.izinLight;
      textColor = colors.izin;
      dotColor = colors.izin;
      defaultLabel = 'Izin';
      break;
    case 'ALPA':
      bgColor = colors.alpaLight;
      textColor = colors.alpa;
      dotColor = colors.alpa;
      defaultLabel = 'Alpa';
      break;
    case 'GATE':
      bgColor = colors.gateLight;
      textColor = colors.gate;
      dotColor = colors.gate;
      defaultLabel = 'Gate';
      break;
    case 'SUBSTITUTE':
      bgColor = colors.substituteLight;
      textColor = colors.substitute;
      dotColor = colors.substitute;
      defaultLabel = 'Guru Pengganti';
      break;
    case 'PENDING':
      bgColor = colors.offlineLight;
      textColor = colors.offline;
      dotColor = colors.offline;
      defaultLabel = 'Menunggu';
      break;
    case 'SYNCED':
      bgColor = colors.hadirLight;
      textColor = colors.hadir;
      dotColor = colors.hadir;
      defaultLabel = 'Tersinkron';
      break;
    case 'FAILED':
      bgColor = colors.alpaLight;
      textColor = colors.alpa;
      dotColor = colors.alpa;
      defaultLabel = 'Gagal';
      break;
  }

  const isSmall = size === 'sm';

  return (
    <View
      style={[
        styles.badge,
        {
          backgroundColor: bgColor,
          paddingVertical: isSmall ? 2 : spacing.xs,
          paddingHorizontal: isSmall ? spacing.xs : spacing.sm,
        },
      ]}
    >
      <View style={[styles.dot, { backgroundColor: dotColor }]} />
      <Text
        style={[
          styles.text,
          {
            color: textColor,
            fontSize: isSmall ? typography.fontSize.xs : typography.fontSize.sm,
          },
        ]}
      >
        {label || defaultLabel}
      </Text>
    </View>
  );
};

const styles = StyleSheet.create({
  badge: {
    borderRadius: radius.badge,
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    borderWidth: 1,
    borderColor: 'transparent',
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginRight: 6,
  },
  text: {
    fontWeight: typography.fontWeight.semibold,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
});
