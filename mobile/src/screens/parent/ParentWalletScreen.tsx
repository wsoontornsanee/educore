/**
 * Parent Digital Canteen Wallet Screen (spec/07 WAL-001, WAL-005, WAL-008, WAL-009..011, spec/08 PAR-006..008, PAR-010, PAR-015, spec/17 §8.1).
 * 
 * Comprehensive wallet management screen for parents:
 * - Real-time student balance & active/frozen status badge
 * - Top-up flow via Virtual Account (BCA, Mandiri, BNI, BRI) and QRIS with fee breakdown
 * - Top-up intent detail polling (every 3 seconds) with settlement feedback
 * - Daily spend limit stepper with Rp 5.000 increments & "Tanpa Batas" toggle
 * - Category blocking switches (Minuman Manis, Camilan, Makanan Cepat Saji)
 * - Allowed purchase time window toggles
 * - 1-minute disclaimer notice banner
 * - Auto-topup settings configuration
 * - Direct shortcut card to Nutrition analytics
 * - Itemized canteen transaction history
 * - 5 mandatory screen states (LOADING, EMPTY, STALE, OFFLINE, ERROR)
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import {
  CATEGORY_BLOCK_CHOICES,
  DAILY_LIMIT_INCREMENT,
  TOPUP_PRESETS,
  VA_BANKS,
  VABankCode,
  createTopupIntent,
  fetchAutoTopupConfig,
  fetchSpendRules,
  fetchTopupIntent,
  fetchWallet,
  fetchWalletTransactions,
  formatRupiah,
  pollTopupIntent,
  updateAutoTopupConfig,
  updateSpendRules,
} from '../../services/wallet';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type {
  ChildSummary,
  WalletAutoTopupConfig,
  WalletData,
  WalletSpendRule,
  WalletTopupIntentItem,
  WalletTransactionItem,
} from '../../types';

interface ParentWalletScreenProps {
  child: ChildSummary;
  onNavigateNutrition?: () => void;
}

export const ParentWalletScreen: React.FC<ParentWalletScreenProps> = ({
  child,
  onNavigateNutrition,
}) => {
  // Screen state
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Data
  const [wallet, setWallet] = useState<WalletData | null>(null);
  const [transactions, setTransactions] = useState<WalletTransactionItem[]>([]);
  const [spendRule, setSpendRule] = useState<WalletSpendRule | null>(null);
  const [autoTopup, setAutoTopup] = useState<WalletAutoTopupConfig | null>(null);

  // Spend Rule Editing State
  const [noDailyLimit, setNoDailyLimit] = useState(false);
  const [dailyLimitNum, setDailyLimitNum] = useState(25000);
  const [blockedCats, setBlockedCats] = useState<string[]>([]);
  const [enableTimeWindow, setEnableTimeWindow] = useState(false);
  const [windowStart, setWindowStart] = useState('09:30');
  const [windowEnd, setWindowEnd] = useState('13:30');
  const [savingRules, setSavingRules] = useState(false);
  const [rulesSuccessMsg, setRulesSuccessMsg] = useState<string | null>(null);

  // Auto-topup Editing State
  const [autoTopupActive, setAutoTopupActive] = useState(false);
  const [autoThreshold, setAutoThreshold] = useState('20000');
  const [autoAmount, setAutoAmount] = useState('50000');
  const [savingAutoTopup, setSavingAutoTopup] = useState(false);
  const [autoTopupSuccessMsg, setAutoTopupSuccessMsg] = useState<string | null>(null);

  // Top-Up Modal State
  const [topupModalVisible, setTopupModalVisible] = useState(false);
  const [topupAmount, setTopupAmount] = useState<number>(50000);
  const [customAmountInput, setCustomAmountInput] = useState('');
  const [topupMethod, setTopupMethod] = useState<'VA' | 'QRIS'>('VA');
  const [selectedBank, setSelectedBank] = useState<VABankCode>('BCA');
  const [creatingIntent, setCreatingIntent] = useState(false);
  const [activeIntent, setActiveIntent] = useState<WalletTopupIntentItem | null>(null);
  const [pollingActive, setPollingActive] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);
  const [topupError, setTopupError] = useState<string | null>(null);

  const pollingRef = useRef<boolean>(false);

  // Load all wallet data
  const loadData = async (forceRefresh = false) => {
    if (!forceRefresh) setLoading(true);
    setErrorMessage(null);

    try {
      const [walletRes, txRes, rulesRes, autoRes] = await Promise.all([
        fetchWallet(child.student_id),
        fetchWalletTransactions(child.student_id),
        fetchSpendRules(child.student_id),
        fetchAutoTopupConfig(child.student_id),
      ]);

      setWallet(walletRes.wallet);
      setTransactions(txRes.transactions);
      setSpendRule(rulesRes.rules);
      setAutoTopup(autoRes.config);

      const isOffline =
        walletRes.isOfflineCached || txRes.isOfflineCached || rulesRes.isOfflineCached;
      setOffline(isOffline);
      setCachedAt(walletRes.lastUpdated || null);

      // Initialize spend rule form
      if (rulesRes.rules) {
        if (rulesRes.rules.daily_limit === null) {
          setNoDailyLimit(true);
          setDailyLimitNum(25000);
        } else {
          setNoDailyLimit(false);
          const limitVal = parseFloat(rulesRes.rules.daily_limit);
          setDailyLimitNum(isNaN(limitVal) ? 25000 : limitVal);
        }
        setBlockedCats(rulesRes.rules.blocked_categories || []);
        if (rulesRes.rules.allowed_window_start && rulesRes.rules.allowed_window_end) {
          setEnableTimeWindow(true);
          setWindowStart(rulesRes.rules.allowed_window_start.substring(0, 5));
          setWindowEnd(rulesRes.rules.allowed_window_end.substring(0, 5));
        } else {
          setEnableTimeWindow(false);
        }
      } else {
        setNoDailyLimit(false);
        setDailyLimitNum(25000);
        setBlockedCats([]);
        setEnableTimeWindow(false);
      }

      // Initialize auto topup form
      if (autoRes.config) {
        setAutoTopupActive(autoRes.config.is_active);
        setAutoThreshold(
          autoRes.config.threshold_amount
            ? String(Math.round(parseFloat(autoRes.config.threshold_amount)))
            : '20000'
        );
        setAutoAmount(
          autoRes.config.topup_amount
            ? String(Math.round(parseFloat(autoRes.config.topup_amount)))
            : '50000'
        );
      }
    } catch (err: any) {
      setErrorMessage(
        err?.message || 'Gagal memuat data dompet. Periksa koneksi internet Anda.'
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
    return () => {
      pollingRef.current = false;
    };
  }, [child.student_id]);

  const handleRefresh = () => {
    setRefreshing(true);
    loadData(true);
  };

  // Daily limit stepper handlers
  const handleDecrementLimit = () => {
    setDailyLimitNum((prev) => Math.max(DAILY_LIMIT_INCREMENT, prev - DAILY_LIMIT_INCREMENT));
  };

  const handleIncrementLimit = () => {
    setDailyLimitNum((prev) => prev + DAILY_LIMIT_INCREMENT);
  };

  const handleToggleCategory = (cat: string) => {
    setBlockedCats((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat]
    );
  };

  // Save spend rules
  const handleSaveSpendRules = async () => {
    setSavingRules(true);
    setRulesSuccessMsg(null);
    try {
      const payload = {
        daily_limit: noDailyLimit ? null : dailyLimitNum.toFixed(2),
        blocked_categories: blockedCats,
        allowed_window_start: enableTimeWindow ? `${windowStart}:00` : null,
        allowed_window_end: enableTimeWindow ? `${windowEnd}:00` : null,
      };
      const updated = await updateSpendRules(child.student_id, payload);
      setSpendRule(updated);
      setRulesSuccessMsg('Aturan batas belanja berhasil disimpan.');
      setTimeout(() => setRulesSuccessMsg(null), 4000);
    } catch (err: any) {
      alert(err?.message || 'Gagal menyimpan aturan belanja.');
    } finally {
      setSavingRules(false);
    }
  };

  // Save auto top-up config
  const handleSaveAutoTopup = async () => {
    setSavingAutoTopup(true);
    setAutoTopupSuccessMsg(null);
    try {
      const payload = {
        is_active: autoTopupActive,
        threshold_amount: parseFloat(autoThreshold || '0').toFixed(2),
        topup_amount: parseFloat(autoAmount || '0').toFixed(2),
        method: 'VA' as const,
        bank: selectedBank,
      };
      const updated = await updateAutoTopupConfig(child.student_id, payload);
      setAutoTopup(updated);
      setAutoTopupSuccessMsg('Pengaturan Top-Up Otomatis berhasil disimpan.');
      setTimeout(() => setAutoTopupSuccessMsg(null), 4000);
    } catch (err: any) {
      alert(err?.message || 'Gagal menyimpan konfigurasi top-up otomatis.');
    } finally {
      setSavingAutoTopup(false);
    }
  };

  // Topup Intent Submission
  const handleCreateTopup = async () => {
    const finalAmount = customAmountInput ? parseFloat(customAmountInput) : topupAmount;
    if (isNaN(finalAmount) || finalAmount < 10000) {
      setTopupError('Jumlah nominal top-up minimal Rp 10.000.');
      return;
    }

    setCreatingIntent(true);
    setTopupError(null);
    try {
      const intent = await createTopupIntent(child.student_id, {
        method: topupMethod,
        amount: finalAmount.toFixed(2),
        bank: topupMethod === 'VA' ? selectedBank : undefined,
        provider: 'MOCK',
      });
      setActiveIntent(intent);
      startPolling(intent.id);
    } catch (err: any) {
      setTopupError(err?.message || 'Gagal membuat tagihan top-up.');
    } finally {
      setCreatingIntent(false);
    }
  };

  // Settlement Polling (3s interval per spec)
  const startPolling = async (intentId: number) => {
    pollingRef.current = true;
    setPollingActive(true);

    try {
      const settledIntent = await pollTopupIntent(
        child.student_id,
        intentId,
        {
          intervalMs: 3000,
          maxAttempts: 40,
          onUpdate: (latest) => {
            if (pollingRef.current) {
              setActiveIntent(latest);
            }
          },
        }
      );

      if (settledIntent.status === 'SETTLED') {
        // Auto refresh wallet balance and transactions
        await loadData(true);
      }
    } catch {
      // Continue without crashing
    } finally {
      setPollingActive(false);
    }
  };

  const handleCloseTopupModal = () => {
    pollingRef.current = false;
    setPollingActive(false);
    setActiveIntent(null);
    setTopupError(null);
    setCopySuccess(false);
    setTopupModalVisible(false);
  };

  const handleCopyVA = () => {
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 3000);
  };

  // 1. Loading State
  if (loading && !wallet) {
    return (
      <SafeAreaView style={styles.centerContainer}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={styles.loadingText}>Memuat dompet kantin...</Text>
      </SafeAreaView>
    );
  }

  // 2. Error State
  if (errorMessage && !wallet) {
    return (
      <SafeAreaView style={styles.centerContainer}>
        <View style={styles.errorCard}>
          <Text style={styles.errorTitle}>Gagal Memuat Data</Text>
          <Text style={styles.errorBody}>{errorMessage}</Text>
          <TouchableOpacity
            style={styles.retryButton}
            onPress={() => loadData(true)}
            accessibilityRole="button"
          >
            <Text style={styles.retryButtonText}>Coba Lagi</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  const isFrozen = wallet?.status === 'FROZEN' || wallet?.status === 'BLOCKED';
  const effectiveAmount = customAmountInput ? parseFloat(customAmountInput) : topupAmount;
  const adminFee = 0; // Fee breakdown itemization
  const totalPayment = isNaN(effectiveAmount) ? 0 : effectiveAmount + adminFee;

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            colors={[colors.primary]}
          />
        }
      >
        {/* Offline / Stale Banner */}
        <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

        {/* Student Info Header */}
        <View style={styles.headerBox}>
          <Text style={styles.childName}>{child.full_name}</Text>
          <Text style={styles.childMeta}>
            {child.class_name ? `${child.class_name} • ` : ''}
            {child.school_name || 'Sekolah EduCore'} • NIS: {child.nis || '-'}
          </Text>
        </View>

        {/* Hero Balance Card (WAL-001, WAL-008) */}
        <View style={styles.balanceHeroCard}>
          <View style={styles.balanceRowTop}>
            <View>
              <Text style={styles.balanceLabel}>Saldo Dompet Digital Siswa</Text>
              <Text style={styles.balanceValue}>{formatRupiah(wallet?.balance)}</Text>
            </View>
            <View
              style={[
                styles.statusBadge,
                isFrozen ? styles.statusBadgeFrozen : styles.statusBadgeActive,
              ]}
            >
              <View
                style={[
                  styles.statusDot,
                  isFrozen ? styles.statusDotFrozen : styles.statusDotActive,
                ]}
              />
              <Text
                style={[
                  styles.statusBadgeText,
                  isFrozen ? styles.statusTextFrozen : styles.statusTextActive,
                ]}
              >
                {isFrozen ? 'Dibekukan' : 'Aktif'}
              </Text>
            </View>
          </View>

          {isFrozen && (
            <View style={styles.frozenNoticeBox}>
              <Text style={styles.frozenNoticeText}>
                ⚠️ Dompet saat ini dibekukan oleh sekolah atau admin keuangan. Hubungi pihak sekolah untuk informasi pembukaan kembali.
              </Text>
            </View>
          )}

          <View style={styles.heroActionRow}>
            <TouchableOpacity
              style={[styles.primaryActionBtn, isFrozen && styles.actionBtnDisabled]}
              onPress={() => setTopupModalVisible(true)}
              disabled={isFrozen}
              accessibilityRole="button"
            >
              <Text style={styles.primaryActionBtnText}>+ Isi Saldo</Text>
            </TouchableOpacity>

            <View style={styles.dailyLimitIndicator}>
              <Text style={styles.dailyLimitIndicatorLabel}>Batas Belanja Harian:</Text>
              <Text style={styles.dailyLimitIndicatorValue}>
                {wallet?.daily_limit ? formatRupiah(wallet.daily_limit) : 'Tanpa Batas'}
              </Text>
            </View>
          </View>
        </View>

        {/* Nutrition Quick-Glance Shortcut Card (PAR-010) */}
        {onNavigateNutrition && (
          <TouchableOpacity
            style={styles.nutritionShortcutCard}
            onPress={onNavigateNutrition}
            activeOpacity={0.8}
            accessibilityRole="button"
          >
            <View style={styles.nutritionShortcutIconBox}>
              <Text style={styles.nutritionShortcutIcon}>🥗</Text>
            </View>
            <View style={styles.nutritionShortcutTextBox}>
              <Text style={styles.nutritionShortcutTitle}>Pantauan Gizi & Kalori Siswa</Text>
              <Text style={styles.nutritionShortcutSubtitle}>
                Lihat asupan kalori, batas gula, dan riwayat menu sehat kantin.
              </Text>
            </View>
            <Text style={styles.nutritionShortcutArrow}>→</Text>
          </TouchableOpacity>
        )}

        {/* Spending Controls Section (WAL-009..011, spec/08 §4) */}
        <View style={styles.sectionCard}>
          <Text style={styles.sectionTitle}>Pengendalian Belanja (Spend Controls)</Text>
          <Text style={styles.sectionSubtitle}>
            Atur limit harian dan larangan kategori makanan agar anak jajan secara sehat dan terencana.
          </Text>

          {/* 1-Minute Disclaimer Notice Banner */}
          <View style={styles.disclaimerBanner}>
            <Text style={styles.disclaimerIcon}>ℹ️</Text>
            <Text style={styles.disclaimerText}>
              Perubahan aturan batas belanja akan efektif dalam waktu 1 menit di seluruh terminal kasir kantin.
            </Text>
          </View>

          {/* Tanpa Batas Toggle */}
          <View style={styles.switchRow}>
            <View style={styles.switchLabelContainer}>
              <Text style={styles.switchTitle}>Tanpa Batas Harian</Text>
              <Text style={styles.switchDesc}>
                Siswa dapat berbelanja tanpa batas maksimal saldo per hari
              </Text>
            </View>
            <Switch
              value={noDailyLimit}
              onValueChange={setNoDailyLimit}
              trackColor={{ false: colors.borderDark, true: colors.primaryLight }}
              thumbColor={noDailyLimit ? colors.primary : colors.surfaceAlt}
            />
          </View>

          {/* Daily Limit Stepper */}
          {!noDailyLimit && (
            <View style={styles.stepperContainer}>
              <Text style={styles.stepperLabel}>Batas Maksimal Belanja per Hari</Text>
              <View style={styles.stepperRow}>
                <TouchableOpacity
                  style={styles.stepperBtn}
                  onPress={handleDecrementLimit}
                  accessibilityRole="button"
                  accessibilityLabel="Kurangi batas belanja"
                >
                  <Text style={styles.stepperBtnText}>−</Text>
                </TouchableOpacity>

                <View style={styles.stepperDisplay}>
                  <Text style={styles.stepperValueText}>{formatRupiah(dailyLimitNum)}</Text>
                  <Text style={styles.stepperStepLabel}>(Kelipatan Rp 5.000)</Text>
                </View>

                <TouchableOpacity
                  style={styles.stepperBtn}
                  onPress={handleIncrementLimit}
                  accessibilityRole="button"
                  accessibilityLabel="Tambah batas belanja"
                >
                  <Text style={styles.stepperBtnText}>+</Text>
                </TouchableOpacity>
              </View>
            </View>
          )}

          {/* Category Blocking Switches */}
          <View style={styles.categoryBlockSection}>
            <Text style={styles.categoryBlockHeading}>Blokir Kategori Makanan & Minuman</Text>
            <Text style={styles.categoryBlockSub}>
              Pencegahan otomatis di mesin POS kasir kantin untuk barang dalam kelompok ini:
            </Text>

            {CATEGORY_BLOCK_CHOICES.map((cat) => {
              const isBlocked = blockedCats.includes(cat);
              return (
                <View key={cat} style={styles.catSwitchRow}>
                  <Text style={styles.catLabel}>{cat}</Text>
                  <Switch
                    value={isBlocked}
                    onValueChange={() => handleToggleCategory(cat)}
                    trackColor={{ false: colors.borderDark, true: colors.alpaLight }}
                    thumbColor={isBlocked ? colors.alpa : colors.surfaceAlt}
                  />
                </View>
              );
            })}
          </View>

          {/* Allowed Time Window Switches */}
          <View style={styles.timeWindowSection}>
            <View style={styles.switchRow}>
              <View style={styles.switchLabelContainer}>
                <Text style={styles.switchTitle}>Batasi Jam Belanja</Text>
                <Text style={styles.switchDesc}>Hanya izinkan transaksi pada jam istirahat sekolah</Text>
              </View>
              <Switch
                value={enableTimeWindow}
                onValueChange={setEnableTimeWindow}
                trackColor={{ false: colors.borderDark, true: colors.primaryLight }}
                thumbColor={enableTimeWindow ? colors.primary : colors.surfaceAlt}
              />
            </View>

            {enableTimeWindow && (
              <View style={styles.timeInputRow}>
                <View style={styles.timeInputBox}>
                  <Text style={styles.timeLabel}>Mulai:</Text>
                  <TextInput
                    style={styles.timeInput}
                    value={windowStart}
                    onChangeText={setWindowStart}
                    placeholder="09:30"
                    maxLength={5}
                  />
                </View>
                <Text style={styles.timeDash}>—</Text>
                <View style={styles.timeInputBox}>
                  <Text style={styles.timeLabel}>Selesai:</Text>
                  <TextInput
                    style={styles.timeInput}
                    value={windowEnd}
                    onChangeText={setWindowEnd}
                    placeholder="13:30"
                    maxLength={5}
                  />
                </View>
              </View>
            )}
          </View>

          {/* Save Spend Rules Button */}
          {rulesSuccessMsg && (
            <View style={styles.successBox}>
              <Text style={styles.successText}>✓ {rulesSuccessMsg}</Text>
            </View>
          )}

          <TouchableOpacity
            style={styles.saveRulesBtn}
            onPress={handleSaveSpendRules}
            disabled={savingRules}
            accessibilityRole="button"
          >
            {savingRules ? (
              <ActivityIndicator size="small" color={colors.white} />
            ) : (
              <Text style={styles.saveRulesBtnText}>Simpan Aturan Belanja</Text>
            )}
          </TouchableOpacity>
        </View>

        {/* Auto Top-up Configuration (WAL-005, WAL-006) */}
        <View style={styles.sectionCard}>
          <Text style={styles.sectionTitle}>Top-Up Otomatis (Auto Top-Up)</Text>
          <Text style={styles.sectionSubtitle}>
            Isi saldo secara otomatis melalui tagihan VA ketika saldo siswa menipis di bawah ambang batas.
          </Text>

          <View style={styles.switchRow}>
            <View style={styles.switchLabelContainer}>
              <Text style={styles.switchTitle}>Aktifkan Top-Up Otomatis</Text>
              <Text style={styles.switchDesc}>Otomatis buat tagihan VA saat saldo kurang</Text>
            </View>
            <Switch
              value={autoTopupActive}
              onValueChange={setAutoTopupActive}
              trackColor={{ false: colors.borderDark, true: colors.hadirLight }}
              thumbColor={autoTopupActive ? colors.hadir : colors.surfaceAlt}
            />
          </View>

          {autoTopupActive && (
            <View style={styles.autoInputsContainer}>
              <View style={styles.inputGroup}>
                <Text style={styles.inputGroupLabel}>Bila saldo berada di bawah:</Text>
                <TextInput
                  style={styles.textInputInstitutional}
                  value={autoThreshold}
                  onChangeText={setAutoThreshold}
                  keyboardType="numeric"
                  placeholder="20000"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.inputGroupLabel}>Top-up otomatis sebesar:</Text>
                <TextInput
                  style={styles.textInputInstitutional}
                  value={autoAmount}
                  onChangeText={setAutoAmount}
                  keyboardType="numeric"
                  placeholder="50000"
                />
              </View>
            </View>
          )}

          {autoTopupSuccessMsg && (
            <View style={styles.successBox}>
              <Text style={styles.successText}>✓ {autoTopupSuccessMsg}</Text>
            </View>
          )}

          <TouchableOpacity
            style={styles.saveAutoTopupBtn}
            onPress={handleSaveAutoTopup}
            disabled={savingAutoTopup}
            accessibilityRole="button"
          >
            {savingAutoTopup ? (
              <ActivityIndicator size="small" color={colors.white} />
            ) : (
              <Text style={styles.saveAutoTopupBtnText}>Simpan Pengaturan Auto Top-Up</Text>
            )}
          </TouchableOpacity>
        </View>

        {/* Itemized Transaction History (WAL-008) */}
        <View style={styles.sectionCard}>
          <Text style={styles.sectionTitle}>Riwayat Transaksi Kantin</Text>
          <Text style={styles.sectionSubtitle}>
            Daftar pembelian, pengisian saldo, dan penyesuaian transaksi kantin siswa.
          </Text>

          {transactions.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyIcon}>💳</Text>
              <Text style={styles.emptyTitle}>Belum Ada Transaksi</Text>
              <Text style={styles.emptySub}>
                Transaksi belanja kantin atau pengisian saldo akan muncul di sini.
              </Text>
            </View>
          ) : (
            transactions.map((tx) => {
              const isCredit = tx.type === 'TOPUP' || tx.type === 'REFUND';
              const formattedDate = new Date(tx.occurred_at).toLocaleString('id-ID', {
                dateStyle: 'medium',
                timeStyle: 'short',
              });

              return (
                <View key={tx.id} style={styles.txRow}>
                  <View style={styles.txLeft}>
                    <View
                      style={[
                        styles.txTypeBadge,
                        isCredit ? styles.txTypeBadgeCredit : styles.txTypeBadgeDebit,
                      ]}
                    >
                      <Text
                        style={[
                          styles.txTypeBadgeText,
                          isCredit ? styles.txTypeTextCredit : styles.txTypeTextDebit,
                        ]}
                      >
                        {tx.type}
                      </Text>
                    </View>
                    <Text style={styles.txReference}>
                      {tx.reference || (tx.type === 'TOPUP' ? 'Top-up Saldo' : 'Belanja Kantin')}
                    </Text>
                    <Text style={styles.txDate}>{formattedDate}</Text>
                  </View>

                  <View style={styles.txRight}>
                    <Text
                      style={[
                        styles.txAmount,
                        isCredit ? styles.txAmountCredit : styles.txAmountDebit,
                      ]}
                    >
                      {isCredit ? `+${formatRupiah(tx.amount)}` : `-${formatRupiah(tx.amount)}`}
                    </Text>
                    <Text style={styles.txBalanceAfter}>
                      Saldo: {formatRupiah(tx.balance_after)}
                    </Text>
                  </View>
                </View>
              );
            })
          )}
        </View>
      </ScrollView>

      {/* Top-up Modal (WAL-005, PAR-006, PAR-007, PAR-008) */}
      <Modal
        visible={topupModalVisible}
        transparent={true}
        animationType="slide"
        onRequestClose={handleCloseTopupModal}
      >
        <SafeAreaView style={styles.modalOverlay}>
          <View style={styles.modalContainer}>
            {/* Modal Header */}
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                {activeIntent ? 'Instruksi Pembayaran' : 'Isi Saldo Dompet Siswa'}
              </Text>
              <TouchableOpacity
                style={styles.modalCloseBtn}
                onPress={handleCloseTopupModal}
                accessibilityRole="button"
                accessibilityLabel="Tutup"
              >
                <Text style={styles.modalCloseText}>✕</Text>
              </TouchableOpacity>
            </View>

            <ScrollView contentContainerStyle={styles.modalBody}>
              {!activeIntent ? (
                /* Step 1: Selection & Form */
                <>
                  {/* Amount Preset Buttons */}
                  <Text style={styles.modalLabel}>Pilih Nominal Top-Up:</Text>
                  <View style={styles.presetGrid}>
                    {TOPUP_PRESETS.map((p) => {
                      const isSelected = !customAmountInput && topupAmount === p;
                      return (
                        <TouchableOpacity
                          key={p}
                          style={[styles.presetBtn, isSelected && styles.presetBtnSelected]}
                          onPress={() => {
                            setTopupAmount(p);
                            setCustomAmountInput('');
                          }}
                          accessibilityRole="button"
                        >
                          <Text
                            style={[
                              styles.presetBtnText,
                              isSelected && styles.presetBtnTextSelected,
                            ]}
                          >
                            {formatRupiah(p)}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>

                  {/* Custom Nominal Input */}
                  <Text style={styles.modalLabel}>Atau Masukkan Nominal Lain (Min. Rp 10.000):</Text>
                  <TextInput
                    style={styles.textInputInstitutional}
                    value={customAmountInput}
                    onChangeText={setCustomAmountInput}
                    keyboardType="numeric"
                    placeholder="Contoh: 75000"
                  />

                  {/* Payment Method Switcher */}
                  <Text style={[styles.modalLabel, { marginTop: spacing.base }]}>
                    Metode Pembayaran:
                  </Text>
                  <View style={styles.methodTabRow}>
                    <TouchableOpacity
                      style={[
                        styles.methodTab,
                        topupMethod === 'VA' && styles.methodTabActive,
                      ]}
                      onPress={() => setTopupMethod('VA')}
                      accessibilityRole="button"
                    >
                      <Text
                        style={[
                          styles.methodTabText,
                          topupMethod === 'VA' && styles.methodTabTextActive,
                        ]}
                      >
                        Virtual Account
                      </Text>
                    </TouchableOpacity>

                    <TouchableOpacity
                      style={[
                        styles.methodTab,
                        topupMethod === 'QRIS' && styles.methodTabActive,
                      ]}
                      onPress={() => setTopupMethod('QRIS')}
                      accessibilityRole="button"
                    >
                      <Text
                        style={[
                          styles.methodTabText,
                          topupMethod === 'QRIS' && styles.methodTabTextActive,
                        ]}
                      >
                        QRIS (Instan)
                      </Text>
                    </TouchableOpacity>
                  </View>

                  {/* VA Bank Selector */}
                  {topupMethod === 'VA' && (
                    <View style={styles.bankSelectorContainer}>
                      <Text style={styles.bankSelectorLabel}>Pilih Bank Tujuan VA:</Text>
                      <View style={styles.bankGrid}>
                        {VA_BANKS.map((b) => {
                          const isBankSelected = selectedBank === b;
                          return (
                            <TouchableOpacity
                              key={b}
                              style={[
                                styles.bankBtn,
                                isBankSelected && styles.bankBtnSelected,
                              ]}
                              onPress={() => setSelectedBank(b)}
                              accessibilityRole="button"
                            >
                              <Text
                                style={[
                                  styles.bankBtnText,
                                  isBankSelected && styles.bankBtnTextSelected,
                                ]}
                              >
                                {b}
                              </Text>
                            </TouchableOpacity>
                          );
                        })}
                      </View>
                    </View>
                  )}

                  {/* Fee Breakdown Display */}
                  <View style={styles.feeBreakdownBox}>
                    <View style={styles.feeRow}>
                      <Text style={styles.feeLabel}>Nominal Top-Up:</Text>
                      <Text style={styles.feeValue}>{formatRupiah(effectiveAmount)}</Text>
                    </View>
                    <View style={styles.feeRow}>
                      <Text style={styles.feeLabel}>Biaya Layanan:</Text>
                      <Text style={styles.feeValue}>Rp 0 (Gratis)</Text>
                    </View>
                    <View style={styles.feeDivider} />
                    <View style={styles.feeRow}>
                      <Text style={styles.feeTotalLabel}>Total Pembayaran:</Text>
                      <Text style={styles.feeTotalValue}>{formatRupiah(totalPayment)}</Text>
                    </View>
                  </View>

                  {topupError && (
                    <View style={styles.modalErrorBox}>
                      <Text style={styles.modalErrorText}>{topupError}</Text>
                    </View>
                  )}

                  <TouchableOpacity
                    style={styles.submitTopupBtn}
                    onPress={handleCreateTopup}
                    disabled={creatingIntent}
                    accessibilityRole="button"
                  >
                    {creatingIntent ? (
                      <ActivityIndicator size="small" color={colors.white} />
                    ) : (
                      <Text style={styles.submitTopupBtnText}>
                        Lanjutkan Pembayaran ({formatRupiah(totalPayment)})
                      </Text>
                    )}
                  </TouchableOpacity>
                </>
              ) : (
                /* Step 2: Active Payment Intent Details & Polling Feedback */
                <View style={styles.intentContainer}>
                  {activeIntent.status === 'SETTLED' ? (
                    <View style={styles.settledBox}>
                      <Text style={styles.settledIcon}>✓</Text>
                      <Text style={styles.settledTitle}>Top-Up Berhasil!</Text>
                      <Text style={styles.settledSub}>
                        Saldo sebesar {formatRupiah(activeIntent.amount)} telah berhasil ditambahkan ke dompet anak.
                      </Text>
                      <TouchableOpacity
                        style={styles.doneBtn}
                        onPress={handleCloseTopupModal}
                        accessibilityRole="button"
                      >
                        <Text style={styles.doneBtnText}>Selesai</Text>
                      </TouchableOpacity>
                    </View>
                  ) : (
                    <>
                      <View style={styles.paymentInfoCard}>
                        <Text style={styles.paymentInfoMethod}>
                          {activeIntent.method === 'VA'
                            ? `Virtual Account ${activeIntent.va_bank}`
                            : 'Pembayaran QRIS'}
                        </Text>
                        <Text style={styles.paymentInfoAmount}>
                          {formatRupiah(activeIntent.amount)}
                        </Text>
                      </View>

                      {activeIntent.method === 'VA' ? (
                        <View style={styles.vaDisplayBox}>
                          <Text style={styles.vaLabel}>Nomor Virtual Account:</Text>
                          <Text style={styles.vaNumber}>{activeIntent.va_number}</Text>
                          <TouchableOpacity
                            style={styles.copyBtn}
                            onPress={handleCopyVA}
                            accessibilityRole="button"
                          >
                            <Text style={styles.copyBtnText}>
                              {copySuccess ? '✓ Tersalin!' : 'Salin Nomor VA'}
                            </Text>
                          </TouchableOpacity>
                        </View>
                      ) : (
                        <View style={styles.qrisDisplayBox}>
                          <Text style={styles.qrisLabel}>Kode QRIS Pembayaran:</Text>
                          <View style={styles.qrisPlaceholder}>
                            <Text style={styles.qrisPlaceholderText}>[ QRIS CODE ]</Text>
                            <Text style={styles.qrisPayloadString}>
                              {activeIntent.qris_payload || '00020101021226580016ID.GO.BI.QRIS...'}
                            </Text>
                          </View>
                          <Text style={styles.qrisHelp}>
                            Scan kode QRIS di atas dengan aplikasi mobile banking atau e-wallet apa saja.
                          </Text>
                        </View>
                      )}

                      {/* Polling Indicator */}
                      <View style={styles.pollingNoticeBox}>
                        <ActivityIndicator size="small" color={colors.primary} />
                        <Text style={styles.pollingNoticeText}>
                          Menunggu konfirmasi pembayaran dari bank... (Cek otomatis setiap 3 detik)
                        </Text>
                      </View>

                      <TouchableOpacity
                        style={styles.cancelTopupBtn}
                        onPress={handleCloseTopupModal}
                        accessibilityRole="button"
                      >
                        <Text style={styles.cancelTopupBtnText}>Tutup / Bayar Nanti</Text>
                      </TouchableOpacity>
                    </>
                  )}
                </View>
              )}
            </ScrollView>
          </View>
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.xl,
    backgroundColor: colors.surface,
  },
  loadingText: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.base,
    color: colors.muted,
  },
  scroll: {
    flex: 1,
  },
  scrollContent: {
    padding: spacing.base,
    paddingBottom: spacing.xxl * 2,
  },
  headerBox: {
    marginBottom: spacing.base,
  },
  childName: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  childMeta: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    marginTop: spacing.xs,
  },

  // Hero Balance Card (Strict 0px radius)
  balanceHeroCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.card,
    padding: spacing.base,
    marginBottom: spacing.base,
  },
  balanceRowTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  balanceLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    fontWeight: typography.fontWeight.medium,
  },
  balanceValue: {
    fontSize: typography.fontSize.xxl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginTop: spacing.xs,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderWidth: 1,
    borderRadius: radius.badge,
  },
  statusBadgeActive: {
    backgroundColor: colors.hadirLight,
    borderColor: colors.hadir,
  },
  statusBadgeFrozen: {
    backgroundColor: colors.alpaLight,
    borderColor: colors.alpa,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 0,
    marginRight: spacing.xs,
  },
  statusDotActive: {
    backgroundColor: colors.hadir,
  },
  statusDotFrozen: {
    backgroundColor: colors.alpa,
  },
  statusBadgeText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
  },
  statusTextActive: {
    color: colors.hadir,
  },
  statusTextFrozen: {
    color: colors.alpa,
  },
  frozenNoticeBox: {
    backgroundColor: colors.alpaLight,
    borderLeftWidth: 4,
    borderLeftColor: colors.alpa,
    padding: spacing.sm,
    marginTop: spacing.sm,
  },
  frozenNoticeText: {
    fontSize: typography.fontSize.xs,
    color: colors.alpa,
    lineHeight: typography.lineHeight.sm,
  },
  heroActionRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: spacing.base,
    paddingTop: spacing.base,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  primaryActionBtn: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.button,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  actionBtnDisabled: {
    opacity: 0.5,
  },
  primaryActionBtnText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },
  dailyLimitIndicator: {
    alignItems: 'flex-end',
  },
  dailyLimitIndicatorLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  dailyLimitIndicatorValue: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },

  // Nutrition Shortcut Card (PAR-010)
  nutritionShortcutCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F0FDF4',
    borderWidth: 1,
    borderColor: '#86EFAC',
    borderRadius: radius.card,
    padding: spacing.base,
    marginBottom: spacing.base,
    minHeight: 44,
  },
  nutritionShortcutIconBox: {
    marginRight: spacing.md,
  },
  nutritionShortcutIcon: {
    fontSize: 24,
  },
  nutritionShortcutTextBox: {
    flex: 1,
  },
  nutritionShortcutTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: '#166534',
  },
  nutritionShortcutSubtitle: {
    fontSize: typography.fontSize.xs,
    color: '#15803D',
    marginTop: 2,
  },
  nutritionShortcutArrow: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: '#166534',
    marginLeft: spacing.sm,
  },

  // Section Cards
  sectionCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.card,
    padding: spacing.base,
    marginBottom: spacing.base,
  },
  sectionTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  sectionSubtitle: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: spacing.xs,
    marginBottom: spacing.md,
    lineHeight: typography.lineHeight.sm,
  },

  // Disclaimer Banner
  disclaimerBanner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: '#EFF6FF',
    borderLeftWidth: 4,
    borderLeftColor: '#3B82F6',
    borderRadius: radius.none,
    padding: spacing.sm,
    marginBottom: spacing.base,
  },
  disclaimerIcon: {
    marginRight: spacing.xs,
    fontSize: 14,
  },
  disclaimerText: {
    flex: 1,
    fontSize: typography.fontSize.xs,
    color: '#1E40AF',
    lineHeight: typography.lineHeight.sm,
  },

  // Switches and Stepper
  switchRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    minHeight: 44,
  },
  switchLabelContainer: {
    flex: 1,
    paddingRight: spacing.md,
  },
  switchTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.medium,
    color: colors.heading,
  },
  switchDesc: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  stepperContainer: {
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  stepperLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
    color: colors.body,
    marginBottom: spacing.sm,
  },
  stepperRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepperBtn: {
    width: 48,
    height: 44,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    justifyContent: 'center',
    alignItems: 'center',
  },
  stepperBtnText: {
    fontSize: 22,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  stepperDisplay: {
    minWidth: 160,
    alignItems: 'center',
    marginHorizontal: spacing.md,
  },
  stepperValueText: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  stepperStepLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },

  // Category Blocking
  categoryBlockSection: {
    marginTop: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingBottom: spacing.md,
  },
  categoryBlockHeading: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  categoryBlockSub: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
    marginBottom: spacing.sm,
  },
  catSwitchRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    minHeight: 44,
  },
  catLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
  },

  // Time Window
  timeWindowSection: {
    marginTop: spacing.sm,
  },
  timeInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.sm,
    paddingBottom: spacing.sm,
  },
  timeInputBox: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  timeLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    marginRight: spacing.xs,
  },
  timeInput: {
    width: 70,
    height: 44,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.input,
    textAlign: 'center',
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.medium,
    color: colors.heading,
    backgroundColor: colors.surface,
  },
  timeDash: {
    marginHorizontal: spacing.md,
    fontSize: typography.fontSize.lg,
    color: colors.muted,
  },

  // Action Buttons
  saveRulesBtn: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.md,
    borderRadius: radius.button,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: spacing.base,
  },
  saveRulesBtnText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },
  saveAutoTopupBtn: {
    backgroundColor: colors.heading,
    paddingVertical: spacing.md,
    borderRadius: radius.button,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: spacing.base,
  },
  saveAutoTopupBtnText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },

  // Auto Top-up inputs
  autoInputsContainer: {
    marginTop: spacing.md,
  },
  inputGroup: {
    marginBottom: spacing.md,
  },
  inputGroupLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginBottom: spacing.xs,
  },
  textInputInstitutional: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.input,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    minHeight: 44,
    fontSize: typography.fontSize.base,
    color: colors.heading,
    backgroundColor: colors.white,
  },

  // Feedback Boxes
  successBox: {
    backgroundColor: colors.hadirLight,
    borderWidth: 1,
    borderColor: colors.hadir,
    padding: spacing.sm,
    marginTop: spacing.sm,
  },
  successText: {
    color: colors.hadir,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
  },

  // Transactions list
  txRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  txLeft: {
    flex: 1,
    paddingRight: spacing.sm,
  },
  txTypeBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
    borderRadius: radius.badge,
    marginBottom: spacing.xs,
  },
  txTypeBadgeCredit: {
    backgroundColor: colors.hadirLight,
  },
  txTypeBadgeDebit: {
    backgroundColor: colors.surfaceAlt,
  },
  txTypeBadgeText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
  },
  txTypeTextCredit: {
    color: colors.hadir,
  },
  txTypeTextDebit: {
    color: colors.body,
  },
  txReference: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.medium,
    color: colors.heading,
  },
  txDate: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  txRight: {
    alignItems: 'flex-end',
    justifyContent: 'center',
  },
  txAmount: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },
  txAmountCredit: {
    color: colors.hadir,
  },
  txAmountDebit: {
    color: colors.heading,
  },
  txBalanceAfter: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  emptyContainer: {
    alignItems: 'center',
    paddingVertical: spacing.xl,
  },
  emptyIcon: {
    fontSize: 36,
    marginBottom: spacing.sm,
  },
  emptyTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  emptySub: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    textAlign: 'center',
    marginTop: 2,
  },

  // Error Card
  errorCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.alpa,
    padding: spacing.lg,
    borderRadius: radius.card,
    alignItems: 'center',
  },
  errorTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.alpa,
    marginBottom: spacing.sm,
  },
  errorBody: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    textAlign: 'center',
    marginBottom: spacing.lg,
  },
  retryButton: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xl,
    borderRadius: radius.button,
    minHeight: 44,
    justifyContent: 'center',
  },
  retryButtonText: {
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
  },

  // Modal Styles (Strict 0px radius)
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    padding: spacing.base,
  },
  modalContainer: {
    backgroundColor: colors.white,
    borderRadius: radius.modal,
    borderWidth: 1,
    borderColor: colors.borderDark,
    maxHeight: '90%',
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.base,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  modalTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  modalCloseBtn: {
    padding: spacing.sm,
    minHeight: 44,
    justifyContent: 'center',
  },
  modalCloseText: {
    fontSize: 20,
    color: colors.muted,
    fontWeight: typography.fontWeight.bold,
  },
  modalBody: {
    padding: spacing.base,
  },
  modalLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
    color: colors.body,
    marginBottom: spacing.xs,
  },
  presetGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  presetBtn: {
    flex: 1,
    minWidth: '45%',
    minHeight: 44,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
  },
  presetBtnSelected: {
    backgroundColor: colors.primaryLight,
    borderColor: colors.primary,
  },
  presetBtnText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
    color: colors.body,
  },
  presetBtnTextSelected: {
    color: colors.primary,
    fontWeight: typography.fontWeight.bold,
  },
  methodTabRow: {
    flexDirection: 'row',
    marginBottom: spacing.md,
    gap: spacing.sm,
  },
  methodTab: {
    flex: 1,
    minHeight: 44,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    backgroundColor: colors.surfaceAlt,
    justifyContent: 'center',
    alignItems: 'center',
  },
  methodTabActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  methodTabText: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    fontWeight: typography.fontWeight.medium,
  },
  methodTabTextActive: {
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
  },
  bankSelectorContainer: {
    marginBottom: spacing.md,
  },
  bankSelectorLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginBottom: spacing.xs,
  },
  bankGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  bankBtn: {
    flex: 1,
    minWidth: '22%',
    minHeight: 44,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    backgroundColor: colors.surfaceAlt,
    justifyContent: 'center',
    alignItems: 'center',
  },
  bankBtnSelected: {
    backgroundColor: colors.primaryLight,
    borderColor: colors.primary,
  },
  bankBtnText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.body,
  },
  bankBtnTextSelected: {
    color: colors.primary,
  },
  feeBreakdownBox: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.card,
    padding: spacing.md,
    marginVertical: spacing.md,
  },
  feeRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 2,
  },
  feeLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  feeValue: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    fontWeight: typography.fontWeight.medium,
  },
  feeDivider: {
    height: 1,
    backgroundColor: colors.border,
    marginVertical: spacing.xs,
  },
  feeTotalLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  feeTotalValue: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  modalErrorBox: {
    backgroundColor: colors.alpaLight,
    borderWidth: 1,
    borderColor: colors.alpa,
    padding: spacing.sm,
    marginBottom: spacing.sm,
  },
  modalErrorText: {
    color: colors.alpa,
    fontSize: typography.fontSize.xs,
  },
  submitTopupBtn: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.md,
    borderRadius: radius.button,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: spacing.sm,
  },
  submitTopupBtnText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },

  // Active Intent Display
  intentContainer: {
    paddingVertical: spacing.sm,
  },
  paymentInfoCard: {
    backgroundColor: colors.surface,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.card,
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  paymentInfoMethod: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
  },
  paymentInfoAmount: {
    fontSize: typography.fontSize.xxl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginTop: spacing.xs,
  },
  vaDisplayBox: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.borderDark,
    padding: spacing.md,
    borderRadius: radius.card,
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  vaLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  vaNumber: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
    letterSpacing: 2,
    marginVertical: spacing.sm,
  },
  copyBtn: {
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.lg,
    minHeight: 44,
    justifyContent: 'center',
  },
  copyBtnText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  qrisDisplayBox: {
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  qrisLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginBottom: spacing.xs,
  },
  qrisPlaceholder: {
    width: 200,
    height: 200,
    borderWidth: 2,
    borderColor: colors.heading,
    borderRadius: radius.none,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.surface,
    padding: spacing.sm,
  },
  qrisPlaceholderText: {
    fontSize: 20,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  qrisPayloadString: {
    fontSize: 8,
    color: colors.muted,
    textAlign: 'center',
    marginTop: spacing.xs,
  },
  qrisHelp: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    textAlign: 'center',
    marginTop: spacing.sm,
  },
  pollingNoticeBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surfaceAlt,
    padding: spacing.md,
    borderRadius: radius.none,
    marginBottom: spacing.md,
    gap: spacing.sm,
  },
  pollingNoticeText: {
    flex: 1,
    fontSize: typography.fontSize.xs,
    color: colors.body,
    lineHeight: typography.lineHeight.sm,
  },
  cancelTopupBtn: {
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.button,
    paddingVertical: spacing.md,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  cancelTopupBtnText: {
    fontSize: typography.fontSize.base,
    color: colors.muted,
    fontWeight: typography.fontWeight.medium,
  },
  settledBox: {
    alignItems: 'center',
    paddingVertical: spacing.lg,
  },
  settledIcon: {
    fontSize: 48,
    color: colors.hadir,
    marginBottom: spacing.sm,
  },
  settledTitle: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  settledSub: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    textAlign: 'center',
    marginTop: spacing.xs,
    marginBottom: spacing.xl,
  },
  doneBtn: {
    backgroundColor: colors.hadir,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xxl,
    borderRadius: radius.button,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  doneBtnText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
  },
});
