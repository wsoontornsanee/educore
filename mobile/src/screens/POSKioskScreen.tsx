/**
 * Tablet POS Kiosk Screen (spec/07 §3-§7, spec/12, spec/17).
 *
 * 2-column landscape POS interface for canteen operators:
 * - Card Tap Area with student photo, name, balance & daily limit before item confirmation (WAL-019).
 * - Categorized product catalog with nutrition & allergen tags (WAL-024).
 * - Real-time spend rule enforcement & blocked category guardrails (WAL-010 to WAL-013).
 * - Rapid checkout ≤3s with offline-first queue fallback (WAL-015, WAL-016, WAL-018).
 * - Digital receipt slip & void action within allowable window (WAL-020, WAL-025).
 */
import React, { useState, useEffect, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  TextInput,
  Modal,
  Alert,
  ActivityIndicator,
  useWindowDimensions,
} from 'react-native';
import { colors, typography, spacing } from '../theme/tokens.ts';
import type {
  POSCartItem,
  POSProduct,
  POSReceipt,
  POSSessionData,
  POSStudent,
} from '../types/index.ts';
import {
  checkoutPOSTransaction,
  checkStudentSpendRules,
  fetchPosSession,
  getCachedSession,
  syncPosDeltas,
  voidPOSTransaction,
} from '../services/pos.ts';
import {
  getPendingPosCount,
  syncPendingPosTransactions,
} from '../services/posOfflineQueue.ts';
import { OfflineQrModal } from '../components/OfflineQrModal.tsx';

interface POSKioskScreenProps {
  terminalId?: number;
  onBack?: () => void;
}

export const POSKioskScreen: React.FC<POSKioskScreenProps> = ({
  terminalId = 1,
  onBack,
}) => {
  const { width } = useWindowDimensions();
  const isTablet = width >= 768;

  // Session & Data
  const [session, setSession] = useState<POSSessionData | null>(getCachedSession());
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [pendingSyncCount, setPendingSyncCount] = useState(0);

  // Active Student (from Card Tap / Selection)
  const [activeStudent, setActiveStudent] = useState<POSStudent | null>(null);
  const [cardInput, setCardInput] = useState('');
  const [showStudentSelector, setShowStudentSelector] = useState(false);

  // Catalog & Filter
  const [selectedCategory, setSelectedCategory] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  // Cart
  const [cart, setCart] = useState<POSCartItem[]>([]);
  const [checkoutLoading, setCheckoutLoading] = useState(false);

  // Receipt & Void
  const [receipt, setReceipt] = useState<POSReceipt | null>(null);
  const [lastReceipt, setLastReceipt] = useState<POSReceipt | null>(null);
  const [voidLoading, setVoidLoading] = useState(false);
  const [showOfflineQr, setShowOfflineQr] = useState(false);

  // Bootstrap session
  useEffect(() => {
    loadSession();
    updatePendingCount();
  }, [terminalId]);

  const loadSession = async () => {
    setLoading(true);
    try {
      const data = await fetchPosSession(terminalId);
      setSession(data);
    } catch {
      // Offline fallback: try cached session
      const cached = getCachedSession();
      if (cached) {
        setSession(cached);
      }
    } finally {
      setLoading(false);
    }
  };

  const updatePendingCount = async () => {
    const count = await getPendingPosCount();
    setPendingSyncCount(count);
  };

  const handleSyncQueue = async () => {
    if (!session) return;
    setSyncing(true);
    try {
      const res = await syncPendingPosTransactions(session.terminal_id);
      await syncPosDeltas(session.terminal_id, session.sync_cursor);
      await updatePendingCount();
      Alert.alert(
        'Sinkronisasi Selesai',
        `Berhasil menyinkronkan ${res.succeeded} transaksi offline.`
      );
    } catch (e: any) {
      Alert.alert('Gagal Sinkronisasi', e?.message || 'Koneksi jaringan bermasalah.');
    } finally {
      setSyncing(false);
    }
  };

  // Student resolution via card UID or NIS
  const handleCardTap = (input: string) => {
    const trimmed = input.trim().toUpperCase();
    if (!trimmed || !session) return;

    const found = session.roster.find(
      (s) =>
        (s.card_uid && s.card_uid.toUpperCase() === trimmed) ||
        (s.nis && s.nis.toUpperCase() === trimmed) ||
        (s.nisn && s.nisn === trimmed) ||
        String(s.id) === trimmed
    );

    if (found) {
      setActiveStudent(found);
      setCardInput('');
      setShowStudentSelector(false);
    } else {
      Alert.alert('Kartu Tidak Dikenali', `Tidak ditemukan siswa dengan ID/kartu "${trimmed}".`);
    }
  };

  // Cart Management
  const handleAddToCart = (product: POSProduct) => {
    setCart((prev) => {
      // A product is identified by SKU: the server's catalog has no numeric id.
      const existingIndex = prev.findIndex((i) => i.product.sku === product.sku);
      if (existingIndex >= 0) {
        return prev.map((item, index) => (index === existingIndex ? { ...item, qty: item.qty + 1 } : item));
      }
      return [...prev, { product, qty: 1, unit_price: Number(product.price) }];
    });
  };

  const handleUpdateQty = (sku: string, delta: number) => {
    setCart((prev) => {
      return prev
        .map((item) => {
          if (item.product.sku === sku) {
            const newQty = item.qty + delta;
            return newQty > 0 ? { ...item, qty: newQty } : null;
          }
          return item;
        })
        .filter(Boolean) as POSCartItem[];
    });
  };

  const handleClearCart = () => {
    setCart([]);
  };

  // Filtered Catalog
  const categories = useMemo(() => {
    if (!session) return ['ALL'];
    const set = new Set<string>();
    session.catalog.forEach((p) => {
      if (p.category) set.add(p.category);
    });
    return ['ALL', ...Array.from(set)];
  }, [session]);

  const filteredCatalog = useMemo(() => {
    if (!session) return [];
    return session.catalog.filter((p) => {
      const matchCat = selectedCategory === 'ALL' || p.category === selectedCategory;
      const matchSearch =
        !searchQuery ||
        p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.sku.toLowerCase().includes(searchQuery.toLowerCase());
      return matchCat && matchSearch && p.is_active !== false;
    });
  }, [session, selectedCategory, searchQuery]);

  const cartSubtotal = useMemo(() => {
    return cart.reduce((sum, item) => sum + item.unit_price * item.qty, 0);
  }, [cart]);

  // Spend Rule Live Check
  const ruleCheck = useMemo(() => {
    if (!activeStudent || cart.length === 0) return { allowed: true };
    return checkStudentSpendRules(activeStudent, cart);
  }, [activeStudent, cart]);

  // Rapid Checkout
  const handleCheckout = async (qrToken?: string) => {
    if (!session) return;
    if (!activeStudent) {
      Alert.alert('Tap Kartu Siswa', 'Silakan tempelkan kartu atau pilih siswa terlebih dahulu.');
      return;
    }
    if (cart.length === 0) {
      Alert.alert('Keranjang Kosong', 'Tambahkan item ke keranjang belanja.');
      return;
    }
    if (!ruleCheck.allowed) {
      Alert.alert('Aturan Belanja Terlanggar', ruleCheck.reason || 'Transaksi ditolak.');
      return;
    }

    setCheckoutLoading(true);
    const startTime = Date.now();
    try {
      const res = await checkoutPOSTransaction({
        terminalId: session.terminal_id,
        student: activeStudent,
        cartItems: cart,
        merchantName: session.merchant_name,
        terminalName: session.terminal_name,
        qrToken,
      });

      const elapsedMs = Date.now() - startTime;
      console.log(`[POS Kiosk] Checkout selesai dalam ${elapsedMs}ms (target ≤3000ms).`);

      setReceipt(res);
      setLastReceipt(res);
      setCart([]);
      setActiveStudent({ ...activeStudent, balance: res.balance_after || 0 });
      await updatePendingCount();
    } catch (e: any) {
      Alert.alert('Gagal Transaksi', e?.message || 'Terjadi kesalahan sistem.');
    } finally {
      setCheckoutLoading(false);
    }
  };

  // Void Last Transaction
  const handleVoidLast = async () => {
    if (!lastReceipt) return;

    Alert.prompt
      ? Alert.prompt(
          'Batalkan Transaksi (Void)',
          `Batalkan transaksi #${lastReceipt.transaction_id} sebesar Rp ${lastReceipt.total.toLocaleString(
            'id-ID'
          )}? Masukkan alasan pembatalan:`,
          [
            { text: 'Batal', style: 'cancel' },
            {
              text: 'Ya, Batalkan',
              style: 'destructive',
              onPress: async (reason) => {
                await executeVoid(reason || 'Pembatalan oleh kasir');
              },
            },
          ]
        )
      : executeVoid('Pembatalan oleh kasir');
  };

  const executeVoid = async (reason: string) => {
    if (!lastReceipt) return;
    setVoidLoading(true);
    try {
      await voidPOSTransaction(lastReceipt.transaction_id, reason);
      Alert.alert('Transaksi Dibatalkan', 'Saldo siswa berhasil dikembalikan.');
      setLastReceipt(null);
      if (activeStudent) {
        setActiveStudent({
          ...activeStudent,
          balance: Number(activeStudent.balance) + lastReceipt.total,
        });
      }
    } catch (e: any) {
      Alert.alert('Gagal Void', e?.message || 'Batas waktu pembatalan telah habis.');
    } finally {
      setVoidLoading(false);
    }
  };

  if (loading && !session) {
    return (
      <View style={styles.centerContainer}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={styles.loadingText}>Memuat Terminal POS Kantin...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Top Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          {onBack && (
            <TouchableOpacity onPress={onBack} style={styles.backButton}>
              <Text style={styles.backButtonText}>← Kembali</Text>
            </TouchableOpacity>
          )}
          <View>
            <Text style={styles.headerTitle}>
              {session?.merchant_name || 'Kantin EduCore'}
            </Text>
            <Text style={styles.headerSubtitle}>
              {session?.terminal_name || 'POS Kiosk Tablet'} • Rp 50.000 Floor Limit
            </Text>
          </View>
        </View>

        <View style={styles.headerRight}>
          {pendingSyncCount > 0 && (
            <TouchableOpacity
              onPress={handleSyncQueue}
              disabled={syncing}
              style={styles.syncBadge}
            >
              {syncing ? (
                <ActivityIndicator size="small" color={colors.white} />
              ) : (
                <Text style={styles.syncBadgeText}>
                  🔄 {pendingSyncCount} Offline • Sync
                </Text>
              )}
            </TouchableOpacity>
          )}
          {lastReceipt && (
            <TouchableOpacity
              onPress={handleVoidLast}
              disabled={voidLoading}
              style={styles.voidButton}
            >
              <Text style={styles.voidButtonText}>
                {voidLoading ? 'Memproses...' : `Void #${lastReceipt.transaction_id}`}
              </Text>
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* Main Kiosk Content */}
      <View style={[styles.mainLayout, isTablet ? styles.landscapeRow : styles.portraitCol]}>
        {/* Left Column: Product Catalog */}
        <View style={styles.catalogSection}>
          {/* Search & Category Filter */}
          <View style={styles.catalogControls}>
            <TextInput
              style={styles.searchInput}
              placeholder="Cari menu / scan barcode..."
              value={searchQuery}
              onChangeText={setSearchQuery}
              placeholderTextColor={colors.subtle}
            />
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.categoryScroll}>
              {categories.map((cat) => (
                <TouchableOpacity
                  key={cat}
                  onPress={() => setSelectedCategory(cat)}
                  style={[
                    styles.categoryTab,
                    selectedCategory === cat && styles.categoryTabActive,
                  ]}
                >
                  <Text
                    style={[
                      styles.categoryTabText,
                      selectedCategory === cat && styles.categoryTabTextActive,
                    ]}
                  >
                    {cat}
                  </Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>

          {/* Product Grid */}
          <ScrollView contentContainerStyle={styles.productGrid}>
            {filteredCatalog.map((product) => {
              const calories = product.nutrition?.calories;
              const isHealthy = product.nutrition?.is_healthy;
              return (
                <TouchableOpacity
                  key={product.sku}
                  style={styles.productCard}
                  onPress={() => handleAddToCart(product)}
                  activeOpacity={0.7}
                >
                  <View style={styles.productBadgeRow}>
                    {isHealthy && (
                      <View style={styles.healthyBadge}>
                        <Text style={styles.healthyBadgeText}>🌱 Sehat</Text>
                      </View>
                    )}
                    {calories !== undefined && (
                      <Text style={styles.caloriesBadgeText}>{calories} kcal</Text>
                    )}
                  </View>
                  <Text style={styles.productName} numberOfLines={2}>
                    {product.name}
                  </Text>
                  <Text style={styles.productPrice}>
                    Rp {Number(product.price).toLocaleString('id-ID')}
                  </Text>
                  {product.allergens && product.allergens.length > 0 && (
                    <Text style={styles.allergenText} numberOfLines={1}>
                      ⚠️ {product.allergens.join(', ')}
                    </Text>
                  )}
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </View>

        {/* Right Column: Student Card & Active Cart */}
        <View style={styles.cartSection}>
          {/* Student Card Tap Area (WAL-019) */}
          <View style={styles.studentCardContainer}>
            {activeStudent ? (
              <View style={styles.studentInfoRow}>
                <View style={styles.avatarBox}>
                  <Text style={styles.avatarText}>
                    {(activeStudent.full_name || '?').charAt(0)}
                  </Text>
                </View>
                <View style={styles.studentDetails}>
                  <Text style={styles.studentName} numberOfLines={1}>
                    {activeStudent.full_name}
                  </Text>
                  {!!(activeStudent.nis || activeStudent.nisn) && (
                    <Text style={styles.studentNis}>NIS: {activeStudent.nis || activeStudent.nisn}</Text>
                  )}
                  <View style={styles.balanceRow}>
                    <Text style={styles.balanceLabel}>Saldo:</Text>
                    <Text style={styles.balanceValue}>
                      Rp {Number(activeStudent.balance).toLocaleString('id-ID')}
                    </Text>
                  </View>
                  {activeStudent.daily_limit !== null && (
                    <Text style={styles.limitInfoText}>
                      Limit: Rp {Number(activeStudent.daily_limit).toLocaleString('id-ID')}
                    </Text>
                  )}
                </View>
                <TouchableOpacity
                  onPress={() => setActiveStudent(null)}
                  style={styles.changeStudentBtn}
                >
                  <Text style={styles.changeStudentText}>Ganti</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <View style={styles.tapPromptContainer}>
                <Text style={styles.tapPromptTitle}>💳 Tap Kartu Pelajar (NFC / Barcode)</Text>
                <Text style={styles.tapPromptDesc}>
                  Tempelkan kartu siswa pada reader atau masukkan NIS/NISN di bawah ini.
                </Text>
                <View style={styles.tapInputRow}>
                  <TextInput
                    style={styles.cardTextInput}
                    placeholder="Ketik NIS / UID kartu..."
                    value={cardInput}
                    onChangeText={setCardInput}
                    onSubmitEditing={() => handleCardTap(cardInput)}
                    placeholderTextColor={colors.subtle}
                  />
                  <TouchableOpacity
                    style={styles.cardSubmitBtn}
                    onPress={() => handleCardTap(cardInput)}
                  >
                    <Text style={styles.cardSubmitBtnText}>Pilih</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={styles.browseStudentBtn}
                    onPress={() => setShowStudentSelector(true)}
                  >
                    <Text style={styles.browseStudentBtnText}>Daftar</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
          </View>

          {/* Cart Items List */}
          <View style={styles.cartItemsHeader}>
            <Text style={styles.cartSectionTitle}>Keranjang ({cart.length} Item)</Text>
            {cart.length > 0 && (
              <TouchableOpacity onPress={handleClearCart}>
                <Text style={styles.clearCartText}>Kosongkan</Text>
              </TouchableOpacity>
            )}
          </View>

          <ScrollView style={styles.cartItemsScroll}>
            {cart.length === 0 ? (
              <View style={styles.emptyCartContainer}>
                <Text style={styles.emptyCartText}>Keranjang belanja masih kosong.</Text>
                <Text style={styles.emptyCartSubtext}>
                  Pilih produk dari katalog di sebelah kiri untuk menambahkan.
                </Text>
              </View>
            ) : (
              cart.map((item) => (
                <View key={item.product.sku} style={styles.cartItemRow}>
                  <View style={styles.cartItemInfo}>
                    <Text style={styles.cartItemName} numberOfLines={1}>
                      {item.product.name}
                    </Text>
                    <Text style={styles.cartItemPrice}>
                      Rp {item.unit_price.toLocaleString('id-ID')}
                    </Text>
                  </View>
                  <View style={styles.qtyControlRow}>
                    <TouchableOpacity
                      onPress={() => handleUpdateQty(item.product.sku, -1)}
                      style={styles.qtyBtn}
                    >
                      <Text style={styles.qtyBtnText}>-</Text>
                    </TouchableOpacity>
                    <Text style={styles.qtyValue}>{item.qty}</Text>
                    <TouchableOpacity
                      onPress={() => handleUpdateQty(item.product.sku, 1)}
                      style={styles.qtyBtn}
                    >
                      <Text style={styles.qtyBtnText}>+</Text>
                    </TouchableOpacity>
                  </View>
                  <Text style={styles.cartItemTotal}>
                    Rp {(item.unit_price * item.qty).toLocaleString('id-ID')}
                  </Text>
                </View>
              ))
            )}
          </ScrollView>

          {/* Spend Rule Warning Banner */}
          {!ruleCheck.allowed && (
            <View style={styles.ruleWarningBanner}>
              <Text style={styles.ruleWarningText}>⚠️ {ruleCheck.reason}</Text>
            </View>
          )}

          {/* Cart Footer & Checkout Button */}
          <View style={styles.cartFooter}>
            <View style={styles.subtotalRow}>
              <Text style={styles.subtotalLabel}>Total Pembayaran</Text>
              <Text style={styles.subtotalValue}>
                Rp {cartSubtotal.toLocaleString('id-ID')}
              </Text>
            </View>

            <TouchableOpacity
              style={[
                styles.checkoutButton,
                (!activeStudent || cart.length === 0 || !ruleCheck.allowed) &&
                  styles.checkoutButtonDisabled,
              ]}
              disabled={
                !activeStudent ||
                cart.length === 0 ||
                !ruleCheck.allowed ||
                checkoutLoading
              }
              onPress={() => handleCheckout()}
            >
              {checkoutLoading ? (
                <ActivityIndicator color={colors.white} />
              ) : (
                <Text style={styles.checkoutButtonText}>
                  Bayar Sekarang • Rp {cartSubtotal.toLocaleString('id-ID')}
                </Text>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              style={[
                styles.qrButton,
                (!activeStudent || cart.length === 0 || !ruleCheck.allowed) &&
                  styles.qrButtonDisabled,
              ]}
              disabled={
                !activeStudent ||
                cart.length === 0 ||
                !ruleCheck.allowed ||
                checkoutLoading
              }
              onPress={() => setShowOfflineQr(true)}
              accessibilityRole="button"
            >
              <Text style={styles.qrButtonText}>Bayar dengan QR (Offline)</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>

      {/* Offline QR payment (spec 18 QRS-022/023) */}
      <OfflineQrModal
        visible={showOfflineQr}
        terminalId={session?.terminal_id ?? terminalId}
        totalLabel={`Rp ${cartSubtotal.toLocaleString('id-ID')}`}
        onClose={() => setShowOfflineQr(false)}
        onPaid={(qrToken) => {
          setShowOfflineQr(false);
          handleCheckout(qrToken);
        }}
      />

      {/* Digital Receipt Modal (WAL-020) */}
      <Modal visible={!!receipt} transparent animationType="fade">
        <View style={styles.modalBackdrop}>
          <View style={styles.receiptModal}>
            <Text style={styles.receiptTitle}>STRUK PEMBELIAN KANTIN</Text>
            <Text style={styles.receiptMerchant}>
              {receipt?.merchant_name || 'Kantin EduCore'}
            </Text>
            <Text style={styles.receiptMeta}>
              Terminal: {receipt?.terminal_name || 'POS Kiosk'} •{' '}
              {receipt?.offline_created ? 'OFFLINE' : 'ONLINE'}
            </Text>
            <Text style={styles.receiptMeta}>
              ID: {receipt?.client_transaction_id}
            </Text>
            <View style={styles.divider} />

            <View style={styles.receiptStudentRow}>
              <Text style={styles.receiptStudentLabel}>Siswa:</Text>
              <Text style={styles.receiptStudentName}>{receipt?.student_name}</Text>
            </View>
            <View style={styles.divider} />

            <ScrollView style={styles.receiptItemsScroll}>
              {receipt?.items.map((i, idx) => (
                <View key={idx} style={styles.receiptItemRow}>
                  <Text style={styles.receiptItemName}>
                    {i.qty}x {i.name}
                  </Text>
                  <Text style={styles.receiptItemPrice}>
                    Rp {i.total.toLocaleString('id-ID')}
                  </Text>
                </View>
              ))}
            </ScrollView>

            <View style={styles.divider} />
            <View style={styles.receiptTotalRow}>
              <Text style={styles.receiptTotalLabel}>TOTAL</Text>
              <Text style={styles.receiptTotalValue}>
                Rp {receipt?.total.toLocaleString('id-ID')}
              </Text>
            </View>
            <View style={styles.receiptBalanceRow}>
              <Text style={styles.receiptBalanceLabel}>Sisa Saldo Dompet:</Text>
              <Text style={styles.receiptBalanceValue}>
                Rp {Number(receipt?.balance_after || 0).toLocaleString('id-ID')}
              </Text>
            </View>

            <TouchableOpacity
              style={styles.closeReceiptButton}
              onPress={() => setReceipt(null)}
            >
              <Text style={styles.closeReceiptButtonText}>Selesai / Struk Baru</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Roster Selection Modal */}
      <Modal visible={showStudentSelector} transparent animationType="slide">
        <View style={styles.modalBackdrop}>
          <View style={styles.rosterModal}>
            <Text style={styles.rosterModalTitle}>Daftar Siswa Terdaftar</Text>
            <ScrollView style={styles.rosterListScroll}>
              {session?.roster.map((s) => (
                <TouchableOpacity
                  key={s.id}
                  style={styles.rosterStudentItem}
                  onPress={() => {
                    setActiveStudent(s);
                    setShowStudentSelector(false);
                  }}
                >
                  <View>
                    <Text style={styles.rosterStudentName}>{s.full_name}</Text>
                    <Text style={styles.rosterStudentNis}>
                      {s.nis ? `NIS: ${s.nis} • ` : ''}Saldo: Rp{' '}
                      {Number(s.balance).toLocaleString('id-ID')}
                    </Text>
                  </View>
                  <Text style={styles.selectArrow}>Pilih →</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
            <TouchableOpacity
              style={styles.closeRosterBtn}
              onPress={() => setShowStudentSelector(false)}
            >
              <Text style={styles.closeRosterBtnText}>Tutup</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
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
    backgroundColor: colors.surface,
  },
  loadingText: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.base,
    color: colors.muted,
  },
  header: {
    height: 64,
    backgroundColor: colors.primary,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  backButton: {
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.sm,
    backgroundColor: 'rgba(255,255,255,0.2)',
  },
  backButtonText: {
    color: colors.white,
    fontWeight: '600',
  },
  headerTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: '700',
    color: colors.white,
  },
  headerSubtitle: {
    fontSize: typography.fontSize.xs,
    color: 'rgba(255,255,255,0.8)',
  },
  headerRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  syncBadge: {
    backgroundColor: colors.offline,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  syncBadgeText: {
    color: colors.white,
    fontSize: typography.fontSize.xs,
    fontWeight: '700',
  },
  voidButton: {
    backgroundColor: 'rgba(0,0,0,0.3)',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.4)',
  },
  voidButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.xs,
    fontWeight: '600',
  },
  mainLayout: {
    flex: 1,
  },
  landscapeRow: {
    flexDirection: 'row',
  },
  portraitCol: {
    flexDirection: 'column',
  },
  catalogSection: {
    flex: 1.4,
    borderRightWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.white,
  },
  catalogControls: {
    padding: spacing.md,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  searchInput: {
    height: 42,
    borderWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.md,
    fontSize: typography.fontSize.base,
    backgroundColor: colors.surface,
    marginBottom: spacing.sm,
  },
  categoryScroll: {
    flexDirection: 'row',
  },
  categoryTab: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    marginRight: spacing.sm,
    backgroundColor: colors.surfaceAlt,
  },
  categoryTabActive: {
    backgroundColor: colors.heading,
  },
  categoryTabText: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    fontWeight: '600',
  },
  categoryTabTextActive: {
    color: colors.white,
  },
  productGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    padding: spacing.sm,
    gap: spacing.sm,
  },
  productCard: {
    width: '31%',
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    justifyContent: 'space-between',
    minHeight: 110,
  },
  productBadgeRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: spacing.xs,
  },
  healthyBadge: {
    backgroundColor: colors.hadirLight,
    paddingHorizontal: 4,
    paddingVertical: 2,
  },
  healthyBadgeText: {
    fontSize: 10,
    color: colors.hadir,
    fontWeight: '700',
  },
  caloriesBadgeText: {
    fontSize: 10,
    color: colors.muted,
  },
  productName: {
    fontSize: typography.fontSize.sm,
    fontWeight: '600',
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  productPrice: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.primary,
  },
  allergenText: {
    fontSize: 10,
    color: colors.alpa,
    marginTop: 2,
  },
  cartSection: {
    flex: 1,
    backgroundColor: colors.surface,
    display: 'flex',
    flexDirection: 'column',
  },
  studentCardContainer: {
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  studentInfoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  avatarBox: {
    width: 52,
    height: 52,
    backgroundColor: colors.primaryLight,
    borderWidth: 1,
    borderColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: {
    fontSize: typography.fontSize.xl,
    fontWeight: '700',
    color: colors.primary,
  },
  studentDetails: {
    flex: 1,
  },
  studentName: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.heading,
  },
  studentNis: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  balanceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    marginTop: 2,
  },
  balanceLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
  },
  balanceValue: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.hadir,
  },
  limitInfoText: {
    fontSize: 11,
    color: colors.muted,
  },
  changeStudentBtn: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    backgroundColor: colors.surfaceAlt,
  },
  changeStudentText: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    fontWeight: '600',
  },
  tapPromptContainer: {
    paddingVertical: spacing.xs,
  },
  tapPromptTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.heading,
  },
  tapPromptDesc: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginVertical: spacing.xs,
  },
  tapInputRow: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  cardTextInput: {
    flex: 1,
    height: 38,
    borderWidth: 1,
    borderColor: colors.borderDark,
    paddingHorizontal: spacing.sm,
    fontSize: typography.fontSize.sm,
    backgroundColor: colors.white,
  },
  cardSubmitBtn: {
    backgroundColor: colors.heading,
    paddingHorizontal: spacing.md,
    justifyContent: 'center',
  },
  cardSubmitBtnText: {
    color: colors.white,
    fontWeight: '600',
    fontSize: typography.fontSize.xs,
  },
  browseStudentBtn: {
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: spacing.md,
    justifyContent: 'center',
  },
  browseStudentBtnText: {
    color: colors.body,
    fontWeight: '600',
    fontSize: typography.fontSize.xs,
  },
  cartItemsHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceAlt,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  cartSectionTitle: {
    fontSize: typography.fontSize.sm,
    fontWeight: '700',
    color: colors.heading,
  },
  clearCartText: {
    fontSize: typography.fontSize.xs,
    color: colors.alpa,
    fontWeight: '600',
  },
  cartItemsScroll: {
    flex: 1,
    backgroundColor: colors.white,
  },
  emptyCartContainer: {
    padding: spacing.xl,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyCartText: {
    fontSize: typography.fontSize.base,
    fontWeight: '600',
    color: colors.muted,
  },
  emptyCartSubtext: {
    fontSize: typography.fontSize.xs,
    color: colors.subtle,
    textAlign: 'center',
    marginTop: spacing.xs,
  },
  cartItemRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  cartItemInfo: {
    flex: 1,
  },
  cartItemName: {
    fontSize: typography.fontSize.sm,
    fontWeight: '600',
    color: colors.heading,
  },
  cartItemPrice: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  qtyControlRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    marginHorizontal: spacing.sm,
  },
  qtyBtn: {
    width: 28,
    height: 28,
    backgroundColor: colors.surfaceAlt,
    alignItems: 'center',
    justifyContent: 'center',
  },
  qtyBtnText: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.body,
  },
  qtyValue: {
    fontSize: typography.fontSize.sm,
    fontWeight: '700',
    minWidth: 20,
    textAlign: 'center',
  },
  cartItemTotal: {
    fontSize: typography.fontSize.sm,
    fontWeight: '700',
    color: colors.heading,
    minWidth: 70,
    textAlign: 'right',
  },
  ruleWarningBanner: {
    backgroundColor: colors.alpaLight,
    padding: spacing.sm,
    borderTopWidth: 1,
    borderColor: colors.alpa,
  },
  ruleWarningText: {
    fontSize: typography.fontSize.xs,
    color: colors.alpa,
    fontWeight: '600',
  },
  cartFooter: {
    padding: spacing.md,
    backgroundColor: colors.white,
    borderTopWidth: 1,
    borderColor: colors.border,
  },
  subtotalRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  subtotalLabel: {
    fontSize: typography.fontSize.base,
    color: colors.muted,
  },
  subtotalValue: {
    fontSize: typography.fontSize.xl,
    fontWeight: '700',
    color: colors.primary,
  },
  checkoutButton: {
    backgroundColor: colors.hadir,
    height: 52,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkoutButtonDisabled: {
    backgroundColor: colors.subtle,
  },
  checkoutButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: '700',
  },
  qrButton: {
    height: 48,
    marginTop: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.primary,
  },
  qrButtonDisabled: {
    opacity: 0.4,
  },
  qrButtonText: {
    color: colors.primary,
    fontSize: typography.fontSize.base,
    fontWeight: '700',
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  receiptModal: {
    width: '100%',
    maxWidth: 420,
    backgroundColor: colors.white,
    padding: spacing.lg,
    maxHeight: '85%',
  },
  receiptTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: '700',
    textAlign: 'center',
    color: colors.heading,
  },
  receiptMerchant: {
    fontSize: typography.fontSize.base,
    fontWeight: '600',
    textAlign: 'center',
    color: colors.muted,
  },
  receiptMeta: {
    fontSize: typography.fontSize.xs,
    textAlign: 'center',
    color: colors.subtle,
    marginTop: 2,
  },
  divider: {
    height: 1,
    backgroundColor: colors.borderDark,
    marginVertical: spacing.sm,
  },
  receiptStudentRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  receiptStudentLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
  },
  receiptStudentName: {
    fontSize: typography.fontSize.sm,
    fontWeight: '700',
    color: colors.heading,
  },
  receiptItemsScroll: {
    maxHeight: 180,
  },
  receiptItemRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 3,
  },
  receiptItemName: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
  },
  receiptItemPrice: {
    fontSize: typography.fontSize.sm,
    fontWeight: '600',
    color: colors.heading,
  },
  receiptTotalRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  receiptTotalLabel: {
    fontSize: typography.fontSize.base,
    fontWeight: '700',
    color: colors.heading,
  },
  receiptTotalValue: {
    fontSize: typography.fontSize.xl,
    fontWeight: '700',
    color: colors.primary,
  },
  receiptBalanceRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.xs,
  },
  receiptBalanceLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  receiptBalanceValue: {
    fontSize: typography.fontSize.xs,
    fontWeight: '700',
    color: colors.hadir,
  },
  closeReceiptButton: {
    backgroundColor: colors.primary,
    height: 46,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.md,
  },
  closeReceiptButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.base,
    fontWeight: '700',
  },
  rosterModal: {
    width: '100%',
    maxWidth: 480,
    backgroundColor: colors.white,
    padding: spacing.md,
    maxHeight: '75%',
  },
  rosterModalTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: '700',
    marginBottom: spacing.md,
    color: colors.heading,
  },
  rosterListScroll: {
    maxHeight: 320,
  },
  rosterStudentItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  rosterStudentName: {
    fontSize: typography.fontSize.base,
    fontWeight: '600',
    color: colors.heading,
  },
  rosterStudentNis: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  selectArrow: {
    color: colors.primary,
    fontWeight: '700',
  },
  closeRosterBtn: {
    backgroundColor: colors.surfaceAlt,
    height: 42,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.md,
  },
  closeRosterBtnText: {
    color: colors.body,
    fontWeight: '600',
  },
});
