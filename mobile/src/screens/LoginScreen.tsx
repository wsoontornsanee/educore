/**
 * Login Screen for Teacher Mobile (spec/02 §3, spec/17).
 * 
 * Supports dual phone/email identifier login against DualAuthBackend.
 * Strict 0px border radius institutional design.
 */
import React, { useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { login } from '../services/auth';
import { colors, radius, spacing, typography } from '../theme/tokens';
import { UserProfile } from '../types';

interface LoginScreenProps {
  onLoginSuccess: (user: UserProfile) => void;
}

export const LoginScreen: React.FC<LoginScreenProps> = ({ onLoginSuccess }) => {
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!identifier.trim() || !password) {
      setErrorMsg('Nomor HP / Email dan password wajib diisi.');
      return;
    }

    setLoading(true);
    setErrorMsg(null);

    try {
      const result = await login(identifier.trim(), password);
      onLoginSuccess(result.user);
    } catch (err: any) {
      const message =
        err?.response?.data?.non_field_errors?.[0] ||
        err?.response?.data?.error ||
        err?.response?.data?.detail ||
        'Kredensial tidak valid atau akun terkunci. Periksa kembali data Anda.';
      setErrorMsg(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.container}
      >
        <View style={styles.card}>
          {/* Brand Accent Bar */}
          <View style={styles.brandAccent} />

          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.brandTitle}>EDUCORE</Text>
            <Text style={styles.portalTitle}>Portal Guru & Presensi</Text>
            <Text style={styles.subtitle}>
              Sistem Operasi Akademik & Presensi Kelas
            </Text>
          </View>

          {/* Error Banner */}
          {errorMsg && (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{errorMsg}</Text>
            </View>
          )}

          {/* Form */}
          <View style={styles.formGroup}>
            <Text style={styles.label}>NOMOR HP ATAU EMAIL</Text>
            <TextInput
              style={styles.input}
              placeholder="08123456789 atau guru@sekolah.sch.id"
              placeholderTextColor={colors.subtle}
              value={identifier}
              onChangeText={setIdentifier}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
            />
          </View>

          <View style={styles.formGroup}>
            <Text style={styles.label}>PASSWORD</Text>
            <TextInput
              style={styles.input}
              placeholder="••••••••••••"
              placeholderTextColor={colors.subtle}
              value={password}
              onChangeText={setPassword}
              secureTextEntry
            />
          </View>

          <TouchableOpacity
            style={styles.submitButton}
            onPress={handleSubmit}
            disabled={loading}
            activeOpacity={0.85}
          >
            {loading ? (
              <ActivityIndicator color={colors.white} />
            ) : (
              <Text style={styles.submitButtonText}>MASUK KE PORTAL</Text>
            )}
          </TouchableOpacity>

          <View style={styles.footer}>
            <Text style={styles.footerText}>
              Bantuan akses atau reset akun? Hubungi Tata Usaha (TU) sekolah.
            </Text>
          </View>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  container: {
    flex: 1,
    justifyContent: 'center',
    padding: spacing.base,
  },
  card: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.xl,
    borderRadius: radius.card,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 0,
  },
  brandAccent: {
    height: 4,
    backgroundColor: colors.primary,
    marginBottom: spacing.lg,
  },
  header: {
    marginBottom: spacing.xl,
  },
  brandTitle: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
    letterSpacing: 2,
  },
  portalTitle: {
    fontSize: typography.fontSize.xxl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginTop: 4,
  },
  subtitle: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    marginTop: 4,
  },
  errorBox: {
    backgroundColor: colors.alpaLight,
    borderWidth: 1,
    borderColor: colors.alpa,
    padding: spacing.sm,
    marginBottom: spacing.base,
    borderRadius: radius.card,
  },
  errorText: {
    color: colors.alpa,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
  },
  formGroup: {
    marginBottom: spacing.base,
  },
  label: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.body,
    marginBottom: 6,
    letterSpacing: 0.5,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.input,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    fontSize: typography.fontSize.base,
    color: colors.heading,
    backgroundColor: colors.white,
  },
  submitButton: {
    backgroundColor: colors.primary,
    borderRadius: radius.button,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.sm,
  },
  submitButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    letterSpacing: 1,
  },
  footer: {
    marginTop: spacing.xl,
    alignItems: 'center',
  },
  footerText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    textAlign: 'center',
    lineHeight: 16,
  },
});
