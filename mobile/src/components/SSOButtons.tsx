/**
 * SSO Action Buttons Component — Google Workspace & Microsoft 365.
 *
 * Implements spec/17 institutional design (0px radius, >=44dp target, clean typography).
 */
import React from 'react';
import {
  ActivityIndicator,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SocialProvider } from '../services/sso';
import { colors, radius, spacing, typography } from '../theme/tokens';

interface SSOButtonsProps {
  onSelectProvider: (provider: SocialProvider) => void;
  loading?: boolean;
  activeProvider?: SocialProvider | null;
  disabled?: boolean;
}

export const SSOButtons: React.FC<SSOButtonsProps> = ({
  onSelectProvider,
  loading = false,
  activeProvider = null,
  disabled = false,
}) => {
  return (
    <View style={styles.container}>
      {/* Divider */}
      <View style={styles.dividerRow}>
        <View style={styles.dividerLine} />
        <Text style={styles.dividerText}>ATAU MASUK DENGAN SSO</Text>
        <View style={styles.dividerLine} />
      </View>

      {/* Google Button */}
      <TouchableOpacity
        style={[styles.button, styles.googleButton]}
        onPress={() => onSelectProvider('google')}
        disabled={disabled || loading}
        activeOpacity={0.8}
        accessibilityRole="button"
        accessibilityLabel="Masuk dengan Google Workspace"
      >
        <View style={styles.iconContainer}>
          <Text style={styles.googleIconText}>G</Text>
        </View>
        {loading && activeProvider === 'google' ? (
          <ActivityIndicator size="small" color={colors.heading} />
        ) : (
          <Text style={styles.buttonText}>Masuk dengan Google Workspace</Text>
        )}
      </TouchableOpacity>

      {/* Microsoft Button */}
      <TouchableOpacity
        style={[styles.button, styles.microsoftButton]}
        onPress={() => onSelectProvider('microsoft')}
        disabled={disabled || loading}
        activeOpacity={0.8}
        accessibilityRole="button"
        accessibilityLabel="Masuk dengan Microsoft 365"
      >
        <View style={styles.msIconGrid}>
          <View style={[styles.msSquare, { backgroundColor: '#F25022' }]} />
          <View style={[styles.msSquare, { backgroundColor: '#7FBA00' }]} />
          <View style={[styles.msSquare, { backgroundColor: '#00A4EF' }]} />
          <View style={[styles.msSquare, { backgroundColor: '#FFB900' }]} />
        </View>
        {loading && activeProvider === 'microsoft' ? (
          <ActivityIndicator size="small" color={colors.white} />
        ) : (
          <Text style={[styles.buttonText, styles.microsoftText]}>Masuk dengan Microsoft 365</Text>
        )}
      </TouchableOpacity>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    marginTop: spacing.md,
    width: '100%',
  },
  dividerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginVertical: spacing.md,
  },
  dividerLine: {
    flex: 1,
    height: 1,
    backgroundColor: colors.border,
  },
  dividerText: {
    paddingHorizontal: spacing.sm,
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.muted,
    letterSpacing: 0.5,
  },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44, // PAR-016 accessible touch floor
    paddingVertical: spacing.sm + 2,
    paddingHorizontal: spacing.md,
    borderRadius: radius.card, // 0px institutional
    borderWidth: 1,
    marginBottom: spacing.sm,
  },
  googleButton: {
    backgroundColor: colors.white,
    borderColor: colors.borderDark,
  },
  googleIconText: {
    fontSize: 16,
    fontWeight: '800',
    color: '#4285F4',
  },
  microsoftButton: {
    backgroundColor: '#2F2F2F',
    borderColor: '#1F1F1F',
  },
  iconContainer: {
    marginRight: spacing.sm,
    width: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  msIconGrid: {
    width: 16,
    height: 16,
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    alignContent: 'space-between',
    marginRight: spacing.sm,
  },
  msSquare: {
    width: 7,
    height: 7,
  },
  buttonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  microsoftText: {
    color: colors.white,
  },
});
