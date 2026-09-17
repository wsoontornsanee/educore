/**
 * Parent Nutrition & Daily Intake Analytics Dashboard Screen (spec/07 WAL-024, spec/08 PAR-003, PAR-010, PAR-015, spec/17 §7.4).
 * 
 * Provides parents/guardians with complete visibility into their child's dietary intake:
 * - Child switcher (PAR-003)
 * - Period filter (Hari Ini, 7 Hari Terakhir, 30 Hari Terakhir)
 * - 4 Hero KPI cards (Total Kalori, Asupan Gula, Pilihan Sehat, Alergen)
 * - Visual daily intake trend bar chart
 * - Allergen alerts and safety advice
 * - Chronological itemized food intake log
 * - 5 mandatory screen states (LOADING, EMPTY, STALE, OFFLINE, ERROR)
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  AllergenAlertInfo,
  calculateDailyAverageCalories,
  calculateHealthyRatio,
  calculateSugarStatus,
  fetchStudentNutritionSummary,
  getAllergenAlertDetails,
  getDateRangeForPeriod,
} from '../services/nutrition.ts';
import { colors, radius, spacing, typography } from '../theme/tokens.ts';
import type {
  DailyNutritionBreakdown,
  LinkedStudentProfile,
  NutritionItem,
  NutritionPeriodFilter,
  StudentNutritionSummary,
} from '../types/index.ts';

interface ParentNutritionDashboardScreenProps {
  initialStudentId?: number;
  linkedStudents?: LinkedStudentProfile[];
  onBack?: () => void;
}

export const ParentNutritionDashboardScreen: React.FC<ParentNutritionDashboardScreenProps> = ({
  initialStudentId = 1,
  linkedStudents = [
    {
      id: 1,
      full_name: 'Fathir Ahmad Pratama',
      nis: '2026001',
      nisn: '0071234561',
      class_name: 'Kelas VII-A',
      school_name: 'SMP Al-Hikmah 1',
    },
    {
      id: 2,
      full_name: 'Aisyah Putri Pratama',
      nis: '2026042',
      nisn: '0098765432',
      class_name: 'Kelas IV-B',
      school_name: 'SD Al-Hikmah Nusantara',
    },
  ],
  onBack,
}) => {
  const [selectedStudent, setSelectedStudent] = useState<LinkedStudentProfile>(
    linkedStudents.find((s) => s.id === initialStudentId) || linkedStudents[0]
  );
  const [childModalVisible, setChildModalVisible] = useState(false);
  const [period, setPeriod] = useState<NutritionPeriodFilter>('WEEK');

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isOfflineCached, setIsOfflineCached] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [summary, setSummary] = useState<StudentNutritionSummary | null>(null);

  const loadData = async (forceRefresh = false) => {
    if (!forceRefresh) setLoading(true);
    setErrorMsg(null);

    const { fromDate, toDate } = getDateRangeForPeriod(period);

    try {
      const res = await fetchStudentNutritionSummary(
        selectedStudent.id,
        fromDate,
        toDate,
        forceRefresh
      );
      setSummary(res.summary);
      setIsOfflineCached(res.isOfflineCached);
      setLastUpdated(res.lastUpdated);
    } catch (err: any) {
      setErrorMsg(
        err?.message || 'Gagal memuat ringkasan nutrisi siswa. Silakan periksa koneksi internet Anda.'
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [selectedStudent.id, period]);

  const onPullRefresh = () => {
    setRefreshing(true);
    loadData(true);
  };

  const handleSelectStudent = (student: LinkedStudentProfile) => {
    setSelectedStudent(student);
    setChildModalVisible(false);
  };

  const formatRelativeTime = (isoString: string | null): string => {
    if (!isoString) return 'Baru saja';
    try {
      const diffMs = Date.now() - new Date(isoString).getTime();
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return 'Baru saja';
      if (diffMins < 60) return `${diffMins} menit lalu`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours} jam lalu`;
      return new Date(isoString).toLocaleDateString('id-ID');
    } catch {
      return 'Baru saja';
    }
  };

  const { daysCount } = getDateRangeForPeriod(period);
  const avgCalories = summary ? calculateDailyAverageCalories(summary, daysCount) : 0;
  const sugarStatus = summary
    ? calculateSugarStatus(summary.total_sugar_g, daysCount)
    : { status: 'NORMAL', avgSugarPerDay: 0, message: '' };
  const healthyRatio = summary
    ? calculateHealthyRatio(summary.healthy_items_count, summary.total_items)
    : { percentage: 0, label: '' };
  const allergenAlerts: AllergenAlertInfo[] = summary
    ? getAllergenAlertDetails(summary.allergens)
    : [];

  const maxDailyCalories = summary?.daily_breakdown?.reduce(
    (max, d) => Math.max(max, d.total_calories),
    100
  ) || 500;

  return (
    <SafeAreaView style={styles.safeArea}>
      {/* Top Application Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          {onBack && (
            <TouchableOpacity
              onPress={onBack}
              style={styles.backButton}
              accessibilityLabel="Kembali"
              accessibilityRole="button"
            >
              <Text style={styles.backButtonText}>←</Text>
            </TouchableOpacity>
          )}
          <View>
            <Text style={styles.headerSubtitle}>PORTAL ORANG TUA · KANTIN</Text>
            <Text style={styles.headerTitle}>Nutrisi & Asupan Harian</Text>
          </View>
        </View>

        {/* Network / Offline Mode Indicator */}
        <View
          style={[
            styles.statusPill,
            isOfflineCached ? styles.statusPillOffline : styles.statusPillOnline,
          ]}
        >
          <View
            style={[
              styles.statusDot,
              isOfflineCached ? styles.statusDotOffline : styles.statusDotOnline,
            ]}
          />
          <Text style={styles.statusPillText}>
            {isOfflineCached ? 'LURING' : 'ONLINE'}
          </Text>
        </View>
      </View>

      {/* Child Switcher (spec/08 PAR-003) */}
      <TouchableOpacity
        style={styles.childSwitcher}
        onPress={() => setChildModalVisible(true)}
        accessibilityRole="button"
        accessibilityLabel={`Pilih anak: ${selectedStudent.full_name}`}
      >
        <View style={styles.childAvatar}>
          <Text style={styles.childAvatarText}>
            {selectedStudent.full_name
              .split(' ')
              .map((n) => n[0])
              .slice(0, 2)
              .join('')
              .toUpperCase()}
          </Text>
        </View>
        <View style={styles.childInfo}>
          <Text style={styles.childName}>{selectedStudent.full_name}</Text>
          <Text style={styles.childMeta}>
            NISN: {selectedStudent.nisn} • {selectedStudent.class_name || 'Siswa'}
          </Text>
        </View>
        <Text style={styles.childSwitchArrow}>Ganti Anak ▼</Text>
      </TouchableOpacity>

      {/* Period Filter Selector */}
      <View style={styles.periodTabs}>
        <TouchableOpacity
          style={[styles.periodTab, period === 'TODAY' && styles.periodTabActive]}
          onPress={() => setPeriod('TODAY')}
        >
          <Text
            style={[
              styles.periodTabText,
              period === 'TODAY' && styles.periodTabTextActive,
            ]}
          >
            Hari Ini
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.periodTab, period === 'WEEK' && styles.periodTabActive]}
          onPress={() => setPeriod('WEEK')}
        >
          <Text
            style={[
              styles.periodTabText,
              period === 'WEEK' && styles.periodTabTextActive,
            ]}
          >
            7 Hari Terakhir
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.periodTab, period === 'MONTH' && styles.periodTabActive]}
          onPress={() => setPeriod('MONTH')}
        >
          <Text
            style={[
              styles.periodTabText,
              period === 'MONTH' && styles.periodTabTextActive,
            ]}
          >
            30 Hari Terakhir
          </Text>
        </TouchableOpacity>
      </View>

      {/* Stale / Offline Notification Banner (spec/17 §8.1) */}
      {isOfflineCached && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineBannerIcon}>⚠️</Text>
          <View style={styles.offlineBannerContent}>
            <Text style={styles.offlineBannerTitle}>Mode Data Tersimpan (Luring)</Text>
            <Text style={styles.offlineBannerSub}>
              Diperbarui: {formatRelativeTime(lastUpdated)}
            </Text>
          </View>
          <TouchableOpacity
            style={styles.offlineRefreshBtn}
            onPress={() => loadData(true)}
          >
            <Text style={styles.offlineRefreshBtnText}>Segarkan</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* Main Body with 5 Screen States */}
      <ScrollView
        style={styles.scrollContainer}
        contentContainerStyle={styles.scrollContent}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onPullRefresh}
            colors={[colors.primary]}
          />
        }
      >
        {/* State 1: LOADING */}
        {loading && (
          <View style={styles.stateContainer}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.stateText}>Memuat analisis nutrisi siswa...</Text>
          </View>
        )}

        {/* State 2: ERROR */}
        {!loading && errorMsg && (
          <View style={styles.errorCard}>
            <Text style={styles.errorCardTitle}>Gagal Memuat Data Nutrisi</Text>
            <Text style={styles.errorCardMessage}>{errorMsg}</Text>
            <TouchableOpacity
              style={styles.retryButton}
              onPress={() => loadData(true)}
            >
              <Text style={styles.retryButtonText}>Coba Lagi</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* State 3: EMPTY */}
        {!loading && !errorMsg && (!summary || summary.total_items === 0) && (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyCardIcon}>🥗</Text>
            <Text style={styles.emptyCardTitle}>Belum Ada Riwayat Konsumsi</Text>
            <Text style={styles.emptyCardSub}>
              Tidak ditemukan transaksi pembelian makanan atau minuman di kantin untuk periode ini.
            </Text>
            <TouchableOpacity
              style={styles.emptyResetBtn}
              onPress={() => setPeriod('MONTH')}
            >
              <Text style={styles.emptyResetBtnText}>Lihat 30 Hari Terakhir</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* State 4 & 5: DATA LOADED (SUCCESS / STALE) */}
        {!loading && !errorMsg && summary && summary.total_items > 0 && (
          <View style={styles.dashboardBody}>
            {/* Freshness Subtitle */}
            <View style={styles.freshnessRow}>
              <Text style={styles.freshnessText}>
                Periode: {summary.from_date} s/d {summary.to_date} • Diperbarui:{' '}
                {formatRelativeTime(lastUpdated)}
              </Text>
            </View>

            {/* 4 Hero KPI Cards (2x2 Grid) */}
            <View style={styles.kpiGrid}>
              {/* Card 1: Total Kalori */}
              <View style={styles.kpiCard}>
                <Text style={styles.kpiLabel}>TOTAL KALORI</Text>
                <Text style={styles.kpiValue}>
                  {summary.total_calories.toLocaleString('id-ID')}{' '}
                  <Text style={styles.kpiUnit}>kkal</Text>
                </Text>
                <Text style={styles.kpiSub}>
                  Rata-rata ~{avgCalories.toLocaleString('id-ID')} kkal/hari
                </Text>
              </View>

              {/* Card 2: Asupan Gula */}
              <View style={styles.kpiCard}>
                <View style={styles.kpiHeaderRow}>
                  <Text style={styles.kpiLabel}>ASUPAN GULA</Text>
                  <View
                    style={[
                      styles.pillBadge,
                      sugarStatus.status === 'HIGH'
                        ? styles.badgeDanger
                        : sugarStatus.status === 'ELEVATED'
                        ? styles.badgeWarning
                        : styles.badgeSuccess,
                    ]}
                  >
                    <Text
                      style={[
                        styles.pillBadgeText,
                        sugarStatus.status === 'HIGH'
                          ? styles.badgeTextDanger
                          : sugarStatus.status === 'ELEVATED'
                          ? styles.badgeTextWarning
                          : styles.badgeTextSuccess,
                      ]}
                    >
                      {sugarStatus.status === 'HIGH'
                        ? 'TINGGI'
                        : sugarStatus.status === 'ELEVATED'
                        ? 'SEDANG'
                        : 'AMAN'}
                    </Text>
                  </View>
                </View>
                <Text style={styles.kpiValue}>
                  {parseFloat(summary.total_sugar_g).toLocaleString('id-ID', {
                    maximumFractionDigits: 1,
                  })}{' '}
                  <Text style={styles.kpiUnit}>gram</Text>
                </Text>
                <Text style={styles.kpiSub}>
                  ~{sugarStatus.avgSugarPerDay} g/hari (anjuran maks. 25g)
                </Text>
              </View>

              {/* Card 3: Pilihan Sehat */}
              <View style={styles.kpiCard}>
                <Text style={styles.kpiLabel}>PILIHAN SEHAT</Text>
                <Text style={styles.kpiValue}>
                  {healthyRatio.percentage}%
                </Text>
                <View style={styles.progressBarTrack}>
                  <View
                    style={[
                      styles.progressBarFill,
                      { width: `${Math.max(5, healthyRatio.percentage)}%` },
                    ]}
                  />
                </View>
                <Text style={styles.kpiSub}>{healthyRatio.label}</Text>
              </View>

              {/* Card 4: Alergen */}
              <View style={styles.kpiCard}>
                <Text style={styles.kpiLabel}>ALERGEN TERDETEKSI</Text>
                <Text
                  style={[
                    styles.kpiValue,
                    allergenAlerts.length > 0 ? styles.kpiDanger : styles.kpiSuccess,
                  ]}
                >
                  {allergenAlerts.length > 0
                    ? `${allergenAlerts.length} Jenis`
                    : 'Nihil (Aman)'}
                </Text>
                <Text style={styles.kpiSub}>
                  {allergenAlerts.length > 0
                    ? summary.allergens.join(', ')
                    : 'Tidak ada alergen umum'}
                </Text>
              </View>
            </View>

            {/* Allergen Warning Banner if Allergens Detected */}
            {allergenAlerts.length > 0 && (
              <View style={styles.allergenAlertCard}>
                <View style={styles.allergenAlertHeader}>
                  <Text style={styles.allergenAlertIcon}>⚠️</Text>
                  <Text style={styles.allergenAlertTitle}>
                    Peringatan Kandungan Alergen
                  </Text>
                </View>
                <Text style={styles.allergenAlertDesc}>
                  Siswa mengonsumsi makanan/minuman yang mengandung bahan berikut pada periode ini:
                </Text>
                <View style={styles.allergenTagsRow}>
                  {allergenAlerts.map((item, idx) => (
                    <View key={idx} style={styles.allergenTagPill}>
                      <Text style={styles.allergenTagText}>
                        • {item.name}
                      </Text>
                    </View>
                  ))}
                </View>
              </View>
            )}

            {/* Visual Daily Intake Trend Bar Chart */}
            <View style={styles.sectionCard}>
              <View style={styles.sectionHeader}>
                <Text style={styles.sectionTitle}>Tren Kalori Harian</Text>
                <Text style={styles.sectionSubtitle}>
                  Grafik asupan kalori per hari (kkal)
                </Text>
              </View>

              <View style={styles.chartContainer}>
                {summary.daily_breakdown.map((day: DailyNutritionBreakdown, idx: number) => {
                  const barHeightPct = Math.min(
                    100,
                    Math.max(12, Math.round((day.total_calories / maxDailyCalories) * 100))
                  );
                  const isHighCalorie = day.total_calories > 600;

                  return (
                    <View key={idx} style={styles.chartBarCol}>
                      <Text style={styles.chartBarValue}>
                        {day.total_calories > 0 ? day.total_calories : '-'}
                      </Text>
                      <View style={styles.chartBarTrack}>
                        <View
                          style={[
                            styles.chartBarFill,
                            { height: `${barHeightPct}%` },
                            isHighCalorie && styles.chartBarFillWarning,
                          ]}
                        />
                      </View>
                      <Text style={styles.chartBarLabel}>
                        {day.date.slice(5)}
                      </Text>
                    </View>
                  );
                })}
              </View>
            </View>

            {/* Itemized Food Intake Log */}
            <View style={styles.sectionCard}>
              <View style={styles.sectionHeader}>
                <Text style={styles.sectionTitle}>Riwayat Asupan Terperinci</Text>
                <Text style={styles.sectionSubtitle}>
                  Daftar produk makanan & minuman yang dikonsumsi di kantin
                </Text>
              </View>

              <View style={styles.itemsList}>
                {summary.items.map((item: NutritionItem, idx: number) => {
                  return (
                    <View key={idx} style={styles.itemRow}>
                      <View style={styles.itemLeft}>
                        <View style={styles.itemNameRow}>
                          <Text style={styles.itemName}>{item.name}</Text>
                          {item.is_healthy && (
                            <View style={styles.healthyBadge}>
                              <Text style={styles.healthyBadgeText}>SEHAT</Text>
                            </View>
                          )}
                        </View>
                        <Text style={styles.itemDate}>
                          {item.occurred_at
                            ? new Date(item.occurred_at).toLocaleString('id-ID', {
                                dateStyle: 'medium',
                                timeStyle: 'short',
                              })
                            : ''}
                        </Text>
                        {item.allergens && item.allergens.length > 0 && (
                          <Text style={styles.itemAllergenNote}>
                            Alergen: {item.allergens.join(', ')}
                          </Text>
                        )}
                      </View>

                      <View style={styles.itemRight}>
                        <Text style={styles.itemQtyPrice}>
                          {item.qty}x Rp{' '}
                          {parseFloat(item.unit_price).toLocaleString('id-ID')}
                        </Text>
                        <Text style={styles.itemNutrBadges}>
                          {item.calories} kkal • {item.sugar_g}g gula
                        </Text>
                      </View>
                    </View>
                  );
                })}
              </View>
            </View>
          </View>
        )}
      </ScrollView>

      {/* Child Switcher Modal (PAR-003) */}
      <Modal
        visible={childModalVisible}
        transparent
        animationType="fade"
        onRequestClose={() => setChildModalVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Pilih Data Anak</Text>
              <TouchableOpacity
                onPress={() => setChildModalVisible(false)}
                style={styles.modalCloseBtn}
              >
                <Text style={styles.modalCloseText}>✕</Text>
              </TouchableOpacity>
            </View>

            <FlatList
              data={linkedStudents}
              keyExtractor={(item) => String(item.id)}
              renderItem={({ item }) => {
                const isSelected = item.id === selectedStudent.id;
                return (
                  <TouchableOpacity
                    style={[
                      styles.modalChildItem,
                      isSelected && styles.modalChildItemActive,
                    ]}
                    onPress={() => handleSelectStudent(item)}
                  >
                    <View style={styles.modalChildAvatar}>
                      <Text style={styles.modalChildAvatarText}>
                        {item.full_name
                          .split(' ')
                          .map((n) => n[0])
                          .slice(0, 2)
                          .join('')
                          .toUpperCase()}
                      </Text>
                    </View>
                    <View style={styles.modalChildMeta}>
                      <Text style={styles.modalChildName}>{item.full_name}</Text>
                      <Text style={styles.modalChildDetails}>
                        NISN: {item.nisn} • {item.school_name || 'Sekolah'}
                      </Text>
                    </View>
                    {isSelected && <Text style={styles.checkIcon}>✓</Text>}
                  </TouchableOpacity>
                );
              }}
            />
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  header: {
    height: 56,
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.base,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  backButton: {
    padding: spacing.xs,
    marginRight: spacing.xs,
  },
  backButtonText: {
    fontSize: 22,
    fontWeight: '700',
    color: colors.heading,
  },
  headerSubtitle: {
    fontSize: 9,
    fontFamily: typography.fontFamily.mono,
    fontWeight: '600',
    color: colors.muted,
    letterSpacing: 0.5,
  },
  headerTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderWidth: 1,
    borderRadius: radius.none,
  },
  statusPillOnline: {
    backgroundColor: '#E6F4EE',
    borderColor: '#0E7A4F',
  },
  statusPillOffline: {
    backgroundColor: '#FEF3C7',
    borderColor: '#D97706',
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  statusDotOnline: {
    backgroundColor: '#0E7A4F',
  },
  statusDotOffline: {
    backgroundColor: '#D97706',
  },
  statusPillText: {
    fontSize: 10,
    fontFamily: typography.fontFamily.mono,
    fontWeight: '700',
    color: colors.heading,
  },
  childSwitcher: {
    backgroundColor: colors.white,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  childAvatar: {
    width: 36,
    height: 36,
    backgroundColor: colors.primaryLight,
    borderWidth: 1,
    borderColor: colors.primary,
    borderRadius: radius.none,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.sm,
  },
  childAvatarText: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.primary,
  },
  childInfo: {
    flex: 1,
  },
  childName: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  childMeta: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 1,
  },
  childSwitchArrow: {
    fontSize: typography.fontSize.xs,
    fontWeight: '600',
    color: colors.primary,
  },
  periodTabs: {
    flexDirection: 'row',
    backgroundColor: colors.white,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  periodTab: {
    flex: 1,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.none,
    alignItems: 'center',
  },
  periodTabActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  periodTabText: {
    fontSize: typography.fontSize.xs,
    fontWeight: '600',
    color: colors.body,
  },
  periodTabTextActive: {
    color: colors.white,
  },
  offlineBanner: {
    backgroundColor: '#FEF3C7',
    borderBottomWidth: 1,
    borderBottomColor: '#D97706',
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
  },
  offlineBannerIcon: {
    fontSize: 16,
  },
  offlineBannerContent: {
    flex: 1,
  },
  offlineBannerTitle: {
    fontSize: typography.fontSize.xs,
    fontWeight: '700',
    color: '#B56A00',
  },
  offlineBannerSub: {
    fontSize: 11,
    color: colors.body,
  },
  offlineRefreshBtn: {
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#D97706',
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.none,
  },
  offlineRefreshBtnText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#B56A00',
  },
  scrollContainer: {
    flex: 1,
  },
  scrollContent: {
    padding: spacing.base,
    paddingBottom: spacing.xxl,
  },
  stateContainer: {
    paddingVertical: 60,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stateText: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.base,
    color: colors.muted,
  },
  errorCard: {
    backgroundColor: '#FCE9E7',
    borderWidth: 1,
    borderColor: '#B3261E',
    padding: spacing.base,
    borderRadius: radius.none,
    marginVertical: spacing.lg,
  },
  errorCardTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: '#B3261E',
    marginBottom: spacing.xs,
  },
  errorCardMessage: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginBottom: spacing.md,
  },
  retryButton: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.base,
    alignSelf: 'flex-start',
    borderRadius: radius.none,
  },
  retryButtonText: {
    color: colors.white,
    fontWeight: '700',
    fontSize: typography.fontSize.sm,
  },
  emptyCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.xl,
    alignItems: 'center',
    borderRadius: radius.none,
    marginTop: spacing.lg,
  },
  emptyCardIcon: {
    fontSize: 48,
    marginBottom: spacing.sm,
  },
  emptyCardTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: '700',
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  emptyCardSub: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    textAlign: 'center',
    marginBottom: spacing.base,
  },
  emptyResetBtn: {
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    borderRadius: radius.none,
  },
  emptyResetBtnText: {
    fontSize: typography.fontSize.sm,
    fontWeight: '600',
    color: colors.heading,
  },
  dashboardBody: {
    gap: spacing.base,
  },
  freshnessRow: {
    marginBottom: spacing.xs,
  },
  freshnessText: {
    fontSize: 11,
    fontFamily: typography.fontFamily.mono,
    color: colors.muted,
  },
  kpiGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  kpiCard: {
    flexBasis: '48%',
    flexGrow: 1,
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    borderRadius: radius.none,
  },
  kpiHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  kpiLabel: {
    fontSize: 10,
    fontFamily: typography.fontFamily.mono,
    fontWeight: '700',
    color: colors.muted,
    letterSpacing: 0.5,
  },
  kpiValue: {
    fontSize: 22,
    fontWeight: '700',
    color: colors.heading,
    fontFamily: typography.fontFamily.mono,
    marginTop: 2,
  },
  kpiUnit: {
    fontSize: 12,
    fontWeight: '500',
    color: colors.muted,
  },
  kpiSub: {
    fontSize: 11,
    color: colors.muted,
    marginTop: 4,
  },
  kpiSuccess: {
    color: '#0E7A4F',
  },
  kpiDanger: {
    color: '#B3261E',
  },
  pillBadge: {
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radius.none,
  },
  badgeSuccess: {
    backgroundColor: '#E6F4EE',
  },
  badgeTextSuccess: {
    fontSize: 9,
    fontWeight: '700',
    color: '#0E7A4F',
  },
  badgeWarning: {
    backgroundColor: '#FDF1E0',
  },
  badgeTextWarning: {
    fontSize: 9,
    fontWeight: '700',
    color: '#B56A00',
  },
  badgeDanger: {
    backgroundColor: '#FCE9E7',
  },
  badgeTextDanger: {
    fontSize: 9,
    fontWeight: '700',
    color: '#B3261E',
  },
  progressBarTrack: {
    height: 4,
    backgroundColor: colors.surfaceAlt,
    marginTop: 6,
    overflow: 'hidden',
  },
  progressBarFill: {
    height: '100%',
    backgroundColor: '#0E7A4F',
  },
  allergenAlertCard: {
    backgroundColor: '#FFF5F6',
    borderWidth: 1,
    borderColor: '#F8C4CC',
    padding: spacing.base,
    borderRadius: radius.none,
  },
  allergenAlertHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  allergenAlertIcon: {
    fontSize: 16,
  },
  allergenAlertTitle: {
    fontSize: typography.fontSize.sm,
    fontWeight: '700',
    color: '#B3261E',
  },
  allergenAlertDesc: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    marginBottom: spacing.sm,
  },
  allergenTagsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  allergenTagPill: {
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#B3261E',
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  allergenTagText: {
    fontSize: 11,
    fontWeight: '600',
    color: '#B3261E',
  },
  sectionCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.base,
    borderRadius: radius.none,
  },
  sectionHeader: {
    marginBottom: spacing.md,
  },
  sectionTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.heading,
  },
  sectionSubtitle: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  chartContainer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    height: 140,
    paddingTop: 20,
    gap: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingBottom: 4,
  },
  chartBarCol: {
    flex: 1,
    alignItems: 'center',
    height: '100%',
    justifyContent: 'flex-end',
  },
  chartBarValue: {
    fontSize: 9,
    fontFamily: typography.fontFamily.mono,
    color: colors.muted,
    marginBottom: 2,
  },
  chartBarTrack: {
    width: '70%',
    height: '75%',
    backgroundColor: colors.surfaceAlt,
    justifyContent: 'flex-end',
  },
  chartBarFill: {
    width: '100%',
    backgroundColor: colors.primary,
  },
  chartBarFillWarning: {
    backgroundColor: '#D97706',
  },
  chartBarLabel: {
    fontSize: 9,
    fontFamily: typography.fontFamily.mono,
    color: colors.muted,
    marginTop: 4,
  },
  itemsList: {
    gap: spacing.sm,
  },
  itemRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  itemLeft: {
    flex: 1,
    marginRight: spacing.sm,
  },
  itemNameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap',
  },
  itemName: {
    fontSize: typography.fontSize.sm,
    fontWeight: '700',
    color: colors.heading,
  },
  healthyBadge: {
    backgroundColor: '#E6F4EE',
    paddingHorizontal: 4,
    paddingVertical: 1,
  },
  healthyBadgeText: {
    fontSize: 9,
    fontWeight: '700',
    color: '#0E7A4F',
  },
  itemDate: {
    fontSize: 11,
    color: colors.muted,
    marginTop: 2,
  },
  itemAllergenNote: {
    fontSize: 11,
    color: '#B3261E',
    marginTop: 2,
  },
  itemRight: {
    alignItems: 'flex-end',
  },
  itemQtyPrice: {
    fontSize: typography.fontSize.sm,
    fontFamily: typography.fontFamily.mono,
    fontWeight: '600',
    color: colors.heading,
  },
  itemNutrBadges: {
    fontSize: 11,
    fontFamily: typography.fontFamily.mono,
    color: colors.muted,
    marginTop: 2,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(22, 17, 15, 0.4)',
    justifyContent: 'center',
    padding: spacing.base,
  },
  modalCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.base,
    borderRadius: radius.none,
    maxHeight: '70%',
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.base,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingBottom: spacing.sm,
  },
  modalTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.heading,
  },
  modalCloseBtn: {
    padding: spacing.xs,
  },
  modalCloseText: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.muted,
  },
  modalChildItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  modalChildItemActive: {
    backgroundColor: colors.primaryLight,
  },
  modalChildAvatar: {
    width: 36,
    height: 36,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.sm,
  },
  modalChildAvatarText: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.heading,
  },
  modalChildMeta: {
    flex: 1,
  },
  modalChildName: {
    fontSize: typography.fontSize.sm,
    fontWeight: '700',
    color: colors.heading,
  },
  modalChildDetails: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 1,
  },
  checkIcon: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.primary,
  },
});
