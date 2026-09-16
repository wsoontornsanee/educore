import React, { useState } from 'react';
import {
  ActivityIndicator, KeyboardAvoidingView, Platform, SafeAreaView,
  StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import { login } from '../services/auth';
import { requestOtp, verifyOtp } from '../services/parentAuth';
import { colors, radius, spacing, typography } from '../theme/tokens';
import { UserProfile } from '../types';

interface LoginScreenProps {
  onLoginSuccess: (user: UserProfile) => void;
}

type Role = 'GURU' | 'WALI';
type WaliStep = 'PHONE' | 'CODE';

export const LoginScreen: React.FC<LoginScreenProps> = ({ onLoginSuccess }) => {
  const [role, setRole] = useState<Role>('GURU');

  // Guru (teacher) state — unchanged behavior from the original component.
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');

  // Wali (parent) state.
  const [waliStep, setWaliStep] = useState<WaliStep>('PHONE');
  const [phone, setPhone] = useState('');
  const [challengeId, setChallengeId] = useState<number | null>(null);
  const [code, setCode] = useState('');

  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleGuruSubmit = async () => {
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
      setErrorMsg(
        err?.response?.data?.non_field_errors?.[0] ||
        err?.response?.data?.error ||
        err?.response?.data?.detail ||
        'Kredensial tidak valid atau akun terkunci. Periksa kembali data Anda.'
      );
    } finally {
      setLoading(false);
    }
  };

  const handleRequestOtp = async () => {
    if (!phone.trim()) {
      setErrorMsg('Nomor HP wajib diisi.');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const result = await requestOtp(phone.trim());
      setChallengeId(result.challenge_id);
      setWaliStep('CODE');
    } catch (err: any) {
      setErrorMsg(err?.response?.data?.error || 'Gagal mengirim kode OTP.');
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    if (!challengeId || code.length !== 6) {
      setErrorMsg('Masukkan 6 digit kode OTP.');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const result = await verifyOtp(challengeId, code);
      onLoginSuccess(result.user);
    } catch (err: any) {
      setErrorMsg(err?.response?.data?.error || 'Kode OTP salah atau kedaluwarsa.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.container}>
        <View style={styles.card}>
          <View style={styles.brandAccent} />
          <View style={styles.header}>
            <Text style={styles.brandTitle}>EDUCORE</Text>
            <Text style={styles.portalTitle}>
              {role === 'GURU' ? 'Portal Guru & Presensi' : 'Portal Wali Murid'}
            </Text>
          </View>

          <View style={styles.roleToggle}>
            <TouchableOpacity
              style={[styles.roleTab, role === 'GURU' && styles.roleTabActive]}
              onPress={() => { setRole('GURU'); setErrorMsg(null); }}
            >
              <Text style={[styles.roleTabText, role === 'GURU' && styles.roleTabTextActive]}>GURU</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.roleTab, role === 'WALI' && styles.roleTabActive]}
              onPress={() => { setRole('WALI'); setErrorMsg(null); }}
            >
              <Text style={[styles.roleTabText, role === 'WALI' && styles.roleTabTextActive]}>WALI MURID</Text>
            </TouchableOpacity>
          </View>

          {errorMsg && (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{errorMsg}</Text>
            </View>
          )}

          {role === 'GURU' ? (
            <>
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
              <TouchableOpacity style={styles.submitButton} onPress={handleGuruSubmit} disabled={loading} activeOpacity={0.85}>
                {loading ? <ActivityIndicator color={colors.white} /> : <Text style={styles.submitButtonText}>MASUK KE PORTAL</Text>}
              </TouchableOpacity>
            </>
          ) : waliStep === 'PHONE' ? (
            <>
              <View style={styles.formGroup}>
                <Text style={styles.label}>NOMOR HP</Text>
                <TextInput
                  style={styles.input}
                  placeholder="08123456789"
                  placeholderTextColor={colors.subtle}
                  value={phone}
                  onChangeText={setPhone}
                  keyboardType="phone-pad"
                />
              </View>
              <TouchableOpacity style={styles.submitButton} onPress={handleRequestOtp} disabled={loading} activeOpacity={0.85}>
                {loading ? <ActivityIndicator color={colors.white} /> : <Text style={styles.submitButtonText}>KIRIM KODE OTP</Text>}
              </TouchableOpacity>
            </>
          ) : (
            <>
              <View style={styles.formGroup}>
                <Text style={styles.label}>KODE OTP (6 DIGIT)</Text>
                <TextInput
                  style={styles.input}
                  placeholder="123456"
                  placeholderTextColor={colors.subtle}
                  value={code}
                  onChangeText={setCode}
                  keyboardType="number-pad"
                  maxLength={6}
                />
              </View>
              <TouchableOpacity style={styles.submitButton} onPress={handleVerifyOtp} disabled={loading} activeOpacity={0.85}>
                {loading ? <ActivityIndicator color={colors.white} /> : <Text style={styles.submitButtonText}>VERIFIKASI & MASUK</Text>}
              </TouchableOpacity>
              <TouchableOpacity onPress={() => { setWaliStep('PHONE'); setCode(''); }} style={styles.footer}>
                <Text style={styles.footerText}>Ubah nomor HP / kirim ulang kode</Text>
              </TouchableOpacity>
            </>
          )}

          {role === 'GURU' && (
            <View style={styles.footer}>
              <Text style={styles.footerText}>Bantuan akses atau reset akun? Hubungi Tata Usaha (TU) sekolah.</Text>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.surface },
  container: { flex: 1, justifyContent: 'center', padding: spacing.base },
  card: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border,
    padding: spacing.xl, borderRadius: radius.card,
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 0,
  },
  brandAccent: { height: 4, backgroundColor: colors.primary, marginBottom: spacing.lg },
  header: { marginBottom: spacing.lg },
  brandTitle: { fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold, color: colors.primary, letterSpacing: 2 },
  portalTitle: { fontSize: typography.fontSize.xxl, fontWeight: typography.fontWeight.bold, color: colors.heading, marginTop: 4 },
  roleToggle: { flexDirection: 'row', marginBottom: spacing.base, borderWidth: 1, borderColor: colors.borderDark },
  roleTab: { flex: 1, paddingVertical: spacing.sm, alignItems: 'center', backgroundColor: colors.white },
  roleTabActive: { backgroundColor: colors.primary },
  roleTabText: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.body },
  roleTabTextActive: { color: colors.white },
  errorBox: { backgroundColor: colors.alpaLight, borderWidth: 1, borderColor: colors.alpa, padding: spacing.sm, marginBottom: spacing.base, borderRadius: radius.card },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.medium },
  formGroup: { marginBottom: spacing.base },
  label: { fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold, color: colors.body, marginBottom: 6, letterSpacing: 0.5 },
  input: {
    borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.input,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm + 2,
    fontSize: typography.fontSize.base, color: colors.heading, backgroundColor: colors.white,
  },
  submitButton: { backgroundColor: colors.primary, borderRadius: radius.button, paddingVertical: spacing.md, alignItems: 'center', justifyContent: 'center', marginTop: spacing.sm },
  submitButtonText: { color: colors.white, fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, letterSpacing: 1 },
  footer: { marginTop: spacing.xl, alignItems: 'center' },
  footerText: { fontSize: typography.fontSize.xs, color: colors.muted, textAlign: 'center', lineHeight: 16 },
});
