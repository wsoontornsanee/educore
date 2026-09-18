/**
 * EduCore Design Tokens (spec/17-design-system-and-ui.md)
 * 
 * Strict 0px border radius, Indonesian institutional typography,
 * and high-contrast accessibility standards.
 */

export const colors = {
  // Brand
  primary: '#C8102E', // Flag-saturated merah
  primaryDark: '#9B0D23',
  primaryLight: '#FDF2F4',

  // Neutrals
  heading: '#0F172A',
  body: '#334155',
  muted: '#64748B',
  subtle: '#6B7280',    // PAR-016: was #94A3B8 (2.56:1 on white → FAIL). Darkened to pass WCAG AA 4.5:1 (4.69:1).
  border: '#E2E8F0',
  borderDark: '#CBD5E1',
  surface: '#F8FAFC',
  surfaceAlt: '#F1F5F9',
  white: '#FFFFFF',

  // Attendance Status (Semantic Palette)
  hadir: '#15803D',      // PAR-016: was #16A34A (3.30:1 on white → FAIL for normal text). Darkened to pass WCAG AA 4.5:1.
  hadirLight: '#DCFCE7',
  sakit: '#2563EB',      // Sick / Medical / Info — passes 5.17:1 on white
  sakitLight: '#DBEAFE',
  izin: '#B45309',       // PAR-016: was #D97706 (3.19:1 on white → FAIL for normal text). Darkened to pass WCAG AA 4.5:1.
  izinLight: '#FEF3C7',
  alpa: '#DC2626',       // Absent / Unexcused / Danger — passes 4.83:1 on white
  alpaLight: '#FEE2E2',

  // Special Indicators
  gate: '#0D9488',       // Gate-scan prefilled badge (Teal)
  gateLight: '#CCFBF1',
  substitute: '#7C3AED', // Substitute assignment badge (Purple)
  substituteLight: '#EDE9FE',
  offline: '#D97706',    // Offline / Stale banner
  offlineLight: '#FFFBEB',
};

export const typography = {
  fontFamily: {
    sans: 'System',
    mono: 'Courier',
  },
  fontSize: {
    xs: 11,
    sm: 13,
    base: 15,
    lg: 17,
    xl: 20,
    xxl: 24,
  },
  lineHeight: {
    xs: 14,
    sm: 18,
    base: 22,
    lg: 24,
    xl: 28,
    xxl: 32,
  },
  fontWeight: {
    regular: '400' as const,
    medium: '500' as const,
    semibold: '600' as const,
    bold: '700' as const,
  },
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  base: 16,
  lg: 20,
  xl: 24,
  xxl: 32,
};

/**
 * Institutional Square styling rule:
 * Border radius is strictly 0px across all mobile components.
 */
export const radius = {
  none: 0,
  card: 0,
  button: 0,
  badge: 0,
  input: 0,
  modal: 0,
};
