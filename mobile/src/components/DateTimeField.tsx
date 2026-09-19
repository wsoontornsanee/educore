/**
 * A labelled date-and-time field. iOS shows the system compact date-time control inline; Android has no combined
 * dialog, so a tap opens the date dialog and then the time dialog.
 */
import React from 'react';
import { Platform, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import DateTimePicker, { DateTimePickerAndroid, type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import { colors, radius, spacing, typography } from '../theme/tokens.ts';

interface DateTimeFieldProps {
  label: string;
  value: Date;
  onChange: (next: Date) => void;
}

function formatDateTime(value: Date): string {
  return value.toLocaleString('id-ID', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export const DateTimeField: React.FC<DateTimeFieldProps> = ({ label, value, onChange }) => {
  const openAndroid = () => {
    DateTimePickerAndroid.open({
      value,
      mode: 'date',
      onChange: (event: DateTimePickerEvent, day?: Date) => {
        if (event.type !== 'set' || !day) return;
        DateTimePickerAndroid.open({
          value: day,
          mode: 'time',
          is24Hour: true,
          onChange: (timeEvent: DateTimePickerEvent, time?: Date) => {
            if (timeEvent.type !== 'set' || !time) return;
            const next = new Date(day);
            next.setHours(time.getHours(), time.getMinutes(), 0, 0);
            onChange(next);
          },
        });
      },
    });
  };

  return (
    <View style={styles.row}>
      <Text style={styles.label}>{label}</Text>
      {Platform.OS === 'ios' ? (
        <DateTimePicker
          value={value}
          mode="datetime"
          display="compact"
          locale="id-ID"
          onChange={(_event: DateTimePickerEvent, next?: Date) => { if (next) onChange(next); }}
          accessibilityLabel={label}
        />
      ) : (
        <TouchableOpacity onPress={openAndroid} style={styles.button} accessibilityRole="button" accessibilityLabel={label}>
          <Text style={styles.buttonText}>{formatDateTime(value)}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', minHeight: 44 },
  label: { fontSize: typography.fontSize.sm, color: colors.body, fontWeight: typography.fontWeight.medium },
  button: { minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.sm, borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.card },
  buttonText: { color: colors.heading, fontSize: typography.fontSize.sm },
});
