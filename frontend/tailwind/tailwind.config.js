/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    '../templates/**/*.html',
    '../../apps/**/templates/**/*.html',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#FFF5F6',
          100: '#FDE7EA',
          200: '#F8C4CC',
          600: '#C8102E', // PRIMARY BRAND
          700: '#A50D26', // Hover state
          800: '#8E0B20', // Active / pressed state
        },
        ink: {
          900: '#16110F', // Primary body text, high-contrast headings
          700: '#3A302C', // Secondary headings, active menu labels
          500: '#6B615C', // Muted text, helper labels, timestamps
        },
        line: '#E5DDD9', // Borders, card dividers, table row lines
        surface: {
          DEFAULT: '#FFFFFF', // Card background, modal surface, paper white
          alt: '#F7F4F2',     // App canvas background, neutral tags, zebra rows
        },
        semantic: {
          success: '#0E7A4F',
          'success-bg': '#E6F4EE',
          warning: '#B56A00',
          'warning-bg': '#FDF1E0',
          danger: '#B3261E',
          'danger-bg': '#FCE9E7',
          info: '#1B5FA8',
          'info-bg': '#E8F0FA',
        },
        attendance: {
          hadir: '#0E7A4F',
          'hadir-bg': '#E6F4EE',
          terlambat: '#B56A00',
          'terlambat-bg': '#FDF1E0',
          sakit: '#1B5FA8',
          'sakit-bg': '#E8F0FA',
          izin: '#6B615C',
          'izin-bg': '#F0ECE9',
          alpa: '#B3261E',
          'alpa-bg': '#FCE9E7',
          dispen: '#7C3AED',
          'dispen-bg': '#F3E8FF',
        },
      },
      fontFamily: {
        display: ['"Plus Jakarta Sans"', 'sans-serif'],
        sans: ['"IBM Plex Sans"', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '0px',
        none: '0px',
        sm: '4px',
        full: '9999px',
      },
      boxShadow: {
        flat: 'none',
        raised: '0 4px 12px rgba(22, 17, 15, 0.08)',
        overlay: '0 16px 32px rgba(22, 17, 15, 0.16)',
      },
      spacing: {
        '1': '4px',
        '2': '8px',
        '3': '12px',
        '4': '16px',
        '6': '24px',
        '8': '32px',
        '12': '48px',
        '16': '64px',
      },
    },
  },
  plugins: [],
};
