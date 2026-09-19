/**
 * Clinic officer: find the student for a visit by name or NIS (spec/10 LIF-001, step one of the 60-second flow).
 * Nothing typed or found here is kept on the device.
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { MIN_SEARCH_LENGTH, searchStudents } from '../../services/clinicStaff.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import type { StudentLookupItem } from '../../types/index.ts';

const DEBOUNCE_MS = 300;

interface StudentSearchStepProps {
  onPick: (student: StudentLookupItem) => void;
}

export const StudentSearchStep: React.FC<StudentSearchStepProps> = ({ onPick }) => {
  const { t } = useLocale();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<StudentLookupItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const searchable = query.trim().length >= MIN_SEARCH_LENGTH;

  useEffect(() => {
    if (!searchable) {
      setResults([]);
      setError(false);
      setLoading(false);
      return;
    }
    // A slower earlier response must not overwrite the answer to what is typed now.
    let current = true;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const found = await searchStudents(query);
        if (!current) return;
        setResults(found);
        setError(false);
      } catch {
        if (current) setError(true);
      } finally {
        if (current) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [query, searchable]);

  return (
    <View style={styles.root}>
      <Text style={styles.label}>{t('clinicstaff.search.label')}</Text>
      <TextInput
        style={styles.input}
        value={query}
        onChangeText={setQuery}
        placeholder={t('clinicstaff.search.placeholder')}
        autoFocus
        autoCorrect={false}
        autoCapitalize="none"
        returnKeyType="search"
        accessibilityLabel={t('clinicstaff.search.label')}
      />
      {!searchable && <Text style={styles.hint}>{t('clinicstaff.search.hint')}</Text>}
      {loading && <ActivityIndicator style={styles.spinner} color={colors.primary} />}
      {error && (
        <Text style={styles.error} accessibilityRole="alert">{t('clinicstaff.search.error')}</Text>
      )}
      {searchable && !loading && !error && results.length === 0 && (
        <Text style={styles.hint}>{t('clinicstaff.search.empty')}</Text>
      )}
      <FlatList
        data={results}
        keyExtractor={(item) => String(item.id)}
        keyboardShouldPersistTaps="handled"
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.row} onPress={() => onPick(item)} accessibilityRole="button">
            <Text style={styles.name}>{item.name}</Text>
            <Text style={styles.meta}>{[item.nis, item.school_name].filter(Boolean).join(' · ')}</Text>
          </TouchableOpacity>
        )}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, padding: spacing.base },
  label: { fontSize: typography.fontSize.xs, color: colors.muted, fontWeight: typography.fontWeight.bold },
  input: {
    marginTop: spacing.xs, minHeight: 44, borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.input,
    backgroundColor: colors.white, paddingHorizontal: spacing.md, fontSize: typography.fontSize.base, color: colors.heading,
  },
  hint: { marginTop: spacing.md, color: colors.muted, fontSize: typography.fontSize.sm },
  error: { marginTop: spacing.md, color: colors.alpa, fontSize: typography.fontSize.sm },
  spinner: { marginTop: spacing.md },
  row: {
    minHeight: 44, marginTop: spacing.sm, padding: spacing.md, backgroundColor: colors.white,
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.card,
  },
  name: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading },
  meta: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: 2 },
});
