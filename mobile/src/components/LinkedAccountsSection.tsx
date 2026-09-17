/**
 * Linked Accounts Section Component (spec/14 §6, TASK-036).
 *
 * Allows staff (and guardians) to inspect, link, and unlink their Google Workspace
 * and Microsoft 365 SSO accounts.
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  fetchSSOLinks,
  linkSSO,
  signInWithProvider,
  SocialLinkItem,
  SocialProvider,
  unlinkSSO,
} from '../services/sso';
import { colors, radius, spacing, typography } from '../theme/tokens';

export const LinkedAccountsSection: React.FC = () => {
  const [links, setLinks] = useState<SocialLinkItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionProvider, setActionProvider] = useState<SocialProvider | null>(null);

  const loadLinks = async () => {
    try {
      const data = await fetchSSOLinks();
      setLinks(data);
    } catch {
      // Silently handle if offline or unlinked
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLinks();
  }, []);

  const getLinkForProvider = (provider: SocialProvider) => {
    return links.find((l) => l.provider === provider);
  };

  const handleLink = async (provider: SocialProvider) => {
    setActionProvider(provider);
    try {
      const { idToken } = await signInWithProvider(provider);
      await linkSSO(provider, idToken);
      await loadLinks();
      Alert.alert(
        'Akun Ditautkan',
        `Akun ${provider === 'google' ? 'Google Workspace' : 'Microsoft 365'} berhasil ditautkan ke profil Anda.`
      );
    } catch (err: any) {
      const code = err?.response?.data?.code;
      if (code === 'ACCOUNT_ALREADY_LINKED') {
        Alert.alert(
          'Gagal Menautkan',
          'Akun ini sudah ditautkan ke pengguna EduCore lain.'
        );
      } else {
        Alert.alert(
          'Gagal Menautkan',
          err?.response?.data?.error || 'Gagal menautkan akun SSO. Periksa kembali koneksi Anda.'
        );
      }
    } finally {
      setActionProvider(null);
    }
  };

  const handleUnlink = (provider: SocialProvider) => {
    const providerName = provider === 'google' ? 'Google Workspace' : 'Microsoft 365';
    Alert.alert(
      'Putuskan Tautan Akun?',
      `Anda tidak akan dapat masuk menggunakan ${providerName} sebelum menautkannya kembali.`,
      [
        { text: 'Batal', style: 'cancel' },
        {
          text: 'Putuskan',
          style: 'destructive',
          onPress: async () => {
            setActionProvider(provider);
            try {
              await unlinkSSO(provider);
              await loadLinks();
              Alert.alert('Tautan Diputuskan', `Tautan ${providerName} berhasil diputuskan.`);
            } catch (err: any) {
              Alert.alert(
                'Gagal',
                err?.response?.data?.error || 'Gagal memutuskan tautan akun.'
              );
            } finally {
              setActionProvider(null);
            }
          },
        },
      ]
    );
  };

  const renderProviderRow = (
    provider: SocialProvider,
    label: string,
    badgeColor: string
  ) => {
    const link = getLinkForProvider(provider);
    const isLinked = !!link;
    const isBusy = actionProvider === provider;

    return (
      <View style={styles.row}>
        <View style={styles.providerInfo}>
          <View style={styles.nameBadgeRow}>
            <View style={[styles.dot, { backgroundColor: badgeColor }]} />
            <Text style={styles.providerName}>{label}</Text>
          </View>
          <Text style={styles.statusText}>
            {isLinked ? (link.email ? `Terhubung (${link.email})` : 'Terhubung') : 'Belum terhubung'}
          </Text>
        </View>

        <TouchableOpacity
          style={[
            styles.actionButton,
            isLinked ? styles.unlinkButton : styles.linkButton,
          ]}
          onPress={() => (isLinked ? handleUnlink(provider) : handleLink(provider))}
          disabled={isBusy}
          activeOpacity={0.8}
        >
          {isBusy ? (
            <ActivityIndicator
              size="small"
              color={isLinked ? colors.alpa : colors.primary}
            />
          ) : (
            <Text
              style={[
                styles.actionButtonText,
                isLinked ? styles.unlinkButtonText : styles.linkButtonText,
              ]}
            >
              {isLinked ? 'Putuskan' : 'Tautkan'}
            </Text>
          )}
        </TouchableOpacity>
      </View>
    );
  };

  return (
    <View style={styles.container}>
      <Text style={styles.sectionTitle}>AKUN TERHUBUNG (SSO)</Text>
      <Text style={styles.sectionSubtitle}>
        Tautkan akun Google Workspace atau Microsoft 365 sekolah untuk masuk secara cepat dengan satu klik.
      </Text>

      {loading ? (
        <ActivityIndicator style={{ paddingVertical: spacing.md }} color={colors.primary} />
      ) : (
        <View style={styles.list}>
          {renderProviderRow('google', 'Google Workspace', '#4285F4')}
          <View style={styles.divider} />
          {renderProviderRow('microsoft', 'Microsoft 365', '#00A4EF')}
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    marginVertical: spacing.md,
  },
  sectionTitle: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.muted,
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  sectionSubtitle: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    lineHeight: 16,
    marginBottom: spacing.sm,
  },
  list: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.card,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.md,
    minHeight: 48,
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
  },
  providerInfo: {
    flex: 1,
    marginRight: spacing.sm,
  },
  nameBadgeRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: spacing.xs + 2,
  },
  providerName: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  statusText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  actionButton: {
    minHeight: 44, // PAR-016 touch target
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: radius.card,
  },
  linkButton: {
    backgroundColor: colors.white,
    borderColor: colors.primary,
  },
  linkButtonText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  unlinkButton: {
    backgroundColor: colors.alpaLight,
    borderColor: colors.alpa,
  },
  unlinkButtonText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.alpa,
  },
});
