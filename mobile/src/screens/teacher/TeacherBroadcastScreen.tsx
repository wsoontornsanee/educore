/**
 * Teacher Broadcast Screen (spec/09 TCH-011, teacher mobile shell Tab 3).
 *
 * Compose and send an announcement to a class group.
 * Gated by school policy flag `teacher_can_broadcast` (default ON) and
 * rate limit 5/day/class (server-enforced).
 */
import React, { useState } from 'react';
import {
  ActivityIndicator,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { api } from '../../services/api';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import { useLocale } from '../../i18n/LocaleContext';

interface TeacherBroadcastScreenProps {
  onBack: () => void;
}

export const TeacherBroadcastScreen: React.FC<TeacherBroadcastScreenProps> = ({ onBack }) => {
  const { t, locale } = useLocale();
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSend = async () => {
    if (!title.trim() || !body.trim()) {
      setError('Judul dan isi pengumuman wajib diisi.');
      return;
    }
    setSending(true);
    setError(null);
    try {
      await api.post('/teacher/broadcasts/', {
        title: title.trim(),
        body: body.trim(),
      });
      setSent(true);
    } catch (err: any) {
      setError(
        err?.response?.data?.error ||
        err?.message ||
        'Gagal mengirim pengumuman. Coba lagi.',
      );
    } finally {
      setSending(false);
    }
  };

  if (sent) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centerContainer}>
          <Text style={styles.successTitle}>Pengumuman Terkirim</Text>
          <Text style={styles.successBody}>
            Pengumuman "{title}" telah dikirim ke kelas.
          </Text>
          <TouchableOpacity
            style={styles.backBtn}
            onPress={() => { setSent(false); setTitle(''); setBody(''); }}
            accessibilityRole="button"
          >
            <Text style={styles.backBtnText}>BUAT PENGUMUMAN LAIN</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.heading}>Buat Pengumuman</Text>
        <Text style={styles.subheading}>
          Pengumuman akan dikirim ke wali murid kelas yang Anda ajar.
        </Text>

        {error && (
          <View style={styles.errorBox}>
            <Text style={styles.errorText}>{error}</Text>
          </View>
        )}

        <Text style={styles.label}>Judul</Text>
        <TextInput
          style={styles.input}
          value={title}
          onChangeText={setTitle}
          placeholder="Contoh: Perubahan Jadwal Ulangan"
          placeholderTextColor={colors.subtle}
        />

        <Text style={styles.label}>Isi Pengumuman</Text>
        <TextInput
          style={[styles.input, styles.textArea]}
          value={body}
          onChangeText={setBody}
          placeholder="Tulis pengumuman di sini..."
          placeholderTextColor={colors.subtle}
          multiline
          numberOfLines={5}
        />

        <TouchableOpacity
          style={[styles.sendButton, sending && styles.sendButtonDisabled]}
          onPress={handleSend}
          disabled={sending}
          accessibilityRole="button"
          accessibilityLabel="Kirim pengumuman"
        >
          {sending ? (
            <ActivityIndicator size="small" color={colors.white} />
          ) : (
            <Text style={styles.sendButtonText}>KIRIM PENGUMUMAN</Text>
          )}
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
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.xl,
  },
  heading: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  subheading: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    marginBottom: spacing.lg,
  },
  label: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
    marginBottom: spacing.xs,
    marginTop: spacing.md,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    padding: spacing.md,
    fontSize: typography.fontSize.base,
    color: colors.heading,
    minHeight: 48,
  },
  textArea: {
    minHeight: 120,
    textAlignVertical: 'top',
  },
  errorBox: {
    backgroundColor: colors.alpaLight,
    borderWidth: 1,
    borderColor: colors.alpa,
    padding: spacing.sm,
    marginBottom: spacing.md,
  },
  errorText: {
    color: colors.alpa,
    fontSize: typography.fontSize.sm,
  },
  sendButton: {
    backgroundColor: colors.primary,
    minHeight: 48,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: spacing.xl,
  },
  sendButtonDisabled: {
    backgroundColor: colors.subtle,
  },
  sendButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    letterSpacing: 1,
  },
  successTitle: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.hadir,
    marginBottom: spacing.md,
  },
  successBody: {
    fontSize: typography.fontSize.base,
    color: colors.body,
    textAlign: 'center',
    marginBottom: spacing.xl,
  },
  backBtn: {
    backgroundColor: colors.primary,
    minHeight: 48,
    paddingHorizontal: spacing.xl,
    justifyContent: 'center',
    alignItems: 'center',
  },
  backBtnText: {
    color: colors.white,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
  },
});
