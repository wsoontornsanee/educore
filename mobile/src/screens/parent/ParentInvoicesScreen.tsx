/**
 * Parent Invoices & Receipts: outstanding + paid invoice list and downloadable / shareable receipts (PAR-005, PAR-006, PAR-009, PAR-015).
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Share,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { fetchInvoicesForChild } from '../../services/invoices';
import { fetchPaymentsForChild, fetchPaymentReceipt } from '../../services/payments';
import { cacheGet, cacheSet } from '../../services/storage';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { PaymentScreen } from './PaymentScreen';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { ChildSummary, InvoiceItem, PaymentReceiptItem } from '../../types';

export type InvoicesSubTab = 'INVOICES' | 'RECEIPTS';

interface ParentInvoicesScreenProps {
  child: ChildSummary;
}

function formatCurrency(amount: string | number, currency: string = 'IDR'): string {
  const num = typeof amount === 'string' ? parseFloat(amount) : amount;
  if (isNaN(num)) return `${currency} ${amount}`;
  return `${currency} ${num.toLocaleString('id-ID')}`;
}

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '-';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('id-ID', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  } catch {
    return dateStr;
  }
}

export const ParentInvoicesScreen: React.FC<ParentInvoicesScreenProps> = ({ child }) => {
  const [activeTab, setActiveTab] = useState<InvoicesSubTab>('INVOICES');
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [invoices, setInvoices] = useState<InvoiceItem[]>([]);
  const [payingInvoiceIds, setPayingInvoiceIds] = useState<number[] | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  // Payments / Receipts state
  const [payments, setPayments] = useState<PaymentReceiptItem[]>([]);
  const [paymentsLoading, setPaymentsLoading] = useState(false);
  const [paymentsOffline, setPaymentsOffline] = useState(false);
  const [paymentsCachedAt, setPaymentsCachedAt] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<number | null>(null);

  const cacheKey = `educore_parent_invoices_${child.student_id}`;

  // Fetch Invoices
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await fetchInvoicesForChild(child.student_id);
        if (cancelled) return;
        setInvoices(result);
        setOffline(false);
        setCachedAt(null);
        await cacheSet(cacheKey, result);
      } catch {
        if (cancelled) return;
        const cached = await cacheGet<InvoiceItem[]>(cacheKey);
        if (cached) {
          setInvoices(cached.value);
          setCachedAt(cached.cachedAt);
        }
        setOffline(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id, reloadToken]);

  // Fetch Payments / Receipts
  useEffect(() => {
    if (activeTab !== 'RECEIPTS') return;
    let cancelled = false;
    (async () => {
      setPaymentsLoading(true);
      try {
        const res = await fetchPaymentsForChild(child.student_id);
        if (cancelled) return;
        setPayments(res.data);
        setPaymentsOffline(res.fromCache);
        setPaymentsCachedAt(res.lastUpdated);
      } catch {
        if (cancelled) return;
        setPaymentsOffline(true);
      } finally {
        if (!cancelled) setPaymentsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id, activeTab, reloadToken]);

  if (payingInvoiceIds) {
    return (
      <PaymentScreen
        invoiceIds={payingInvoiceIds}
        onDone={() => { setPayingInvoiceIds(null); setReloadToken((t) => t + 1); }}
      />
    );
  }

  const handleDownloadReceipt = async (item: PaymentReceiptItem) => {
    if (paymentsOffline) {
      Alert.alert('Mode Offline', 'Pengunduhan kwitansi PDF memerlukan koneksi internet aktif.');
      return;
    }
    try {
      setDownloadingId(item.id);
      let url = item.receipt_download_url;
      if (!url) {
        const detail = await fetchPaymentReceipt(item.id);
        url = detail.download_url;
      }
      if (url) {
        await Linking.openURL(url);
      } else {
        Alert.alert('Kwitansi', 'Tautan unduh kwitansi tidak tersedia.');
      }
    } catch (err: any) {
      Alert.alert('Gagal Mengunduh', err?.message || 'Terjadi kesalahan saat memuat dokumen kwitansi.');
    } finally {
      setDownloadingId(null);
    }
  };

  const handleShareReceipt = async (item: PaymentReceiptItem) => {
    try {
      const studentName = child.name || item.student_name || 'Siswa';
      const receiptNo = item.receipt_number || `RCP/${item.id}`;
      const amountStr = formatCurrency(item.amount, item.currency);
      const paidDate = formatDate(item.paid_at || item.settled_at || item.created_at);

      let shareMsg = `*KWITANSI PEMBAYARAN EDUCORE*\n\n` +
        `No. Kwitansi : ${receiptNo}\n` +
        `Nama Siswa   : ${studentName}\n` +
        `Tanggal      : ${paidDate}\n` +
        `Metode       : ${item.method} (${item.channel})\n` +
        `Total Bayar  : ${amountStr}\n` +
        `Status       : LUNAS\n`;

      if (item.receipt_download_url) {
        shareMsg += `\nUnduh Dokumen Resmi: ${item.receipt_download_url}\n`;
      }
      shareMsg += `\n_Bukti penerimaan pembayaran sah diterbitkan secara elektronik oleh EduCore._`;

      await Share.share({
        title: `Kwitansi ${receiptNo}`,
        message: shareMsg,
      });
    } catch (err: any) {
      Alert.alert('Gagal Membagikan', err?.message || 'Tidak dapat membagikan kwitansi.');
    }
  };

  const outstanding = invoices
    .filter((inv) => Number(inv.balance_due) > 0)
    .sort((a, b) => a.due_date.localeCompare(b.due_date));

  const handleQuickPay = () => {
    if (outstanding.length === 1) {
      setPayingInvoiceIds([outstanding[0].id]);
    } else if (outstanding.length > 1) {
      setPayingInvoiceIds([outstanding[0].id]);
    }
  };

  return (
    <View style={styles.root}>
      {/* Sub-tab switcher */}
      <View style={styles.tabBar}>
        <TouchableOpacity
          testID="tab-invoices"
          style={[styles.tabButton, activeTab === 'INVOICES' && styles.tabButtonActive]}
          onPress={() => setActiveTab('INVOICES')}
          activeOpacity={0.8}
        >
          <Text style={[styles.tabButtonText, activeTab === 'INVOICES' && styles.tabButtonTextActive]}>
            Tagihan
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          testID="tab-receipts"
          style={[styles.tabButton, activeTab === 'RECEIPTS' && styles.tabButtonActive]}
          onPress={() => setActiveTab('RECEIPTS')}
          activeOpacity={0.8}
        >
          <Text style={[styles.tabButtonText, activeTab === 'RECEIPTS' && styles.tabButtonTextActive]}>
            Bukti Bayar & Kwitansi
          </Text>
        </TouchableOpacity>
      </View>

      {activeTab === 'INVOICES' ? (
        <>
          <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

          {loading ? (
            <View style={styles.center}>
              <ActivityIndicator size="large" color={colors.primary} />
            </View>
          ) : (
            <>
              {outstanding.length > 0 && !offline && (
                <TouchableOpacity style={styles.payButton} onPress={handleQuickPay} activeOpacity={0.85}>
                  <Text style={styles.payButtonText}>
                    BAYAR {outstanding.length === 1 ? 'TAGIHAN INI' : `TAGIHAN TERLAMA (${outstanding[0].period})`}
                  </Text>
                </TouchableOpacity>
              )}

              <FlatList
                data={invoices}
                keyExtractor={(item) => String(item.id)}
                contentContainerStyle={styles.list}
                ListEmptyComponent={<Text style={styles.emptyText}>Belum ada tagihan.</Text>}
                renderItem={({ item }) => (
                  <View style={styles.row}>
                    <View style={styles.rowText}>
                      <Text style={styles.rowNumber}>{item.number} — {item.period}</Text>
                      <Text style={styles.rowDue}>Jatuh tempo: {item.due_date}</Text>
                    </View>
                    <View style={styles.rowAmounts}>
                      <Text style={styles.rowTotal}>{item.currency} {item.total}</Text>
                      <Text style={[styles.rowStatus, item.status === 'PAID' && styles.rowStatusPaid]}>{item.status}</Text>
                    </View>
                  </View>
                )}
              />
            </>
          )}
        </>
      ) : (
        <>
          <StaleOfflineBanner isOffline={paymentsOffline} lastSyncedAt={paymentsCachedAt} />

          {paymentsLoading ? (
            <View style={styles.center}>
              <ActivityIndicator size="large" color={colors.primary} />
            </View>
          ) : (
            <FlatList
              data={payments}
              keyExtractor={(item) => String(item.id)}
              contentContainerStyle={styles.list}
              ListEmptyComponent={<Text style={styles.emptyText}>Belum ada bukti pembayaran.</Text>}
              renderItem={({ item }) => {
                const receiptNo = item.receipt_number || `Kwitansi #${item.id}`;
                const isDownloading = downloadingId === item.id;
                return (
                  <View style={styles.receiptCard}>
                    <View style={styles.receiptHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.receiptNumber}>{receiptNo}</Text>
                        <Text style={styles.receiptDate}>
                          {formatDate(item.paid_at || item.settled_at || item.created_at)}
                        </Text>
                      </View>
                      <View style={styles.badgeSuccess}>
                        <Text style={styles.badgeSuccessText}>LUNAS</Text>
                      </View>
                    </View>

                    <View style={styles.receiptBody}>
                      <View style={styles.receiptRow}>
                        <Text style={styles.receiptLabel}>Total Dibayar:</Text>
                        <Text style={styles.receiptAmount}>{formatCurrency(item.amount, item.currency)}</Text>
                      </View>
                      <View style={styles.receiptRow}>
                        <Text style={styles.receiptLabel}>Metode Pembayaran:</Text>
                        <Text style={styles.receiptValue}>{item.method} ({item.channel})</Text>
                      </View>
                      <View style={styles.receiptRow}>
                        <Text style={styles.receiptLabel}>No. Referensi:</Text>
                        <Text style={styles.receiptValueMono}>{item.reference}</Text>
                      </View>

                      {item.allocations && item.allocations.length > 0 && (
                        <View style={styles.allocationsContainer}>
                          <Text style={styles.allocationsTitle}>Alokasi Tagihan:</Text>
                          {item.allocations.map((alloc) => (
                            <View key={alloc.id} style={styles.allocationRow}>
                              <Text style={styles.allocationInvoice}>
                                {alloc.invoice_number || `Tagihan #${alloc.invoice}`}
                              </Text>
                              <Text style={styles.allocationAmount}>
                                {formatCurrency(alloc.amount, alloc.currency)}
                              </Text>
                            </View>
                          ))}
                        </View>
                      )}
                    </View>

                    <View style={styles.receiptActions}>
                      <TouchableOpacity
                        testID={`btn-download-${item.id}`}
                        style={[
                          styles.actionDownloadBtn,
                          paymentsOffline && styles.actionBtnDisabled,
                        ]}
                        onPress={() => handleDownloadReceipt(item)}
                        disabled={paymentsOffline || isDownloading}
                        activeOpacity={0.8}
                      >
                        {isDownloading ? (
                          <ActivityIndicator size="small" color={colors.white} />
                        ) : (
                          <Text style={styles.actionDownloadText}>
                            {paymentsOffline ? 'Unduh Offline (Tidak Aktif)' : '📄 Unduh Kwitansi (PDF)'}
                          </Text>
                        )}
                      </TouchableOpacity>

                      <TouchableOpacity
                        testID={`btn-share-${item.id}`}
                        style={styles.actionShareBtn}
                        onPress={() => handleShareReceipt(item)}
                        activeOpacity={0.8}
                      >
                        <Text style={styles.actionShareText}>🔗 Bagikan</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                );
              }}
            />
          )}
        </>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },

  // Sub tabs
  tabBar: {
    flexDirection: 'row',
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  tabButton: {
    flex: 1,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderBottomWidth: 3,
    borderBottomColor: 'transparent',
    borderRadius: 0,
  },
  tabButtonActive: {
    borderBottomColor: colors.primary,
  },
  tabButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
    color: colors.muted,
  },
  tabButtonTextActive: {
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },

  payButton: {
    backgroundColor: colors.primary,
    margin: spacing.base,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: 0,
  },
  payButtonText: {
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
    letterSpacing: 1,
  },
  list: { paddingHorizontal: spacing.base, paddingVertical: spacing.md },
  emptyText: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    textAlign: 'center',
    marginTop: spacing.xl,
  },

  // Invoices row
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 0,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  rowText: { flex: 1 },
  rowNumber: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowDue: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: 2 },
  rowAmounts: { alignItems: 'flex-end' },
  rowTotal: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowStatus: { fontSize: typography.fontSize.xs, color: colors.alpa, marginTop: 2 },
  rowStatusPaid: { color: colors.hadir },

  // Receipt Card
  receiptCard: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 0,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  receiptHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingBottom: spacing.sm,
    marginBottom: spacing.sm,
  },
  receiptNumber: {
    fontSize: typography.fontSize.md,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  receiptDate: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  badgeSuccess: {
    backgroundColor: '#DCFCE7',
    borderWidth: 1,
    borderColor: '#16A34A',
    borderRadius: 0,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  badgeSuccessText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: '#16A34A',
  },

  receiptBody: {
    marginBottom: spacing.md,
  },
  receiptRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginVertical: 3,
  },
  receiptLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  receiptValue: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.medium,
    color: colors.heading,
  },
  receiptValueMono: {
    fontSize: typography.fontSize.xs,
    fontFamily: 'monospace',
    color: colors.heading,
  },
  receiptAmount: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },

  allocationsContainer: {
    marginTop: spacing.sm,
    paddingTop: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: '#F1F5F9',
  },
  allocationsTitle: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.muted,
    marginBottom: 4,
  },
  allocationRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 2,
  },
  allocationInvoice: {
    fontSize: typography.fontSize.xs,
    color: colors.heading,
  },
  allocationAmount: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.heading,
  },

  receiptActions: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  actionDownloadBtn: {
    flex: 2,
    minHeight: 44,
    backgroundColor: colors.primary,
    borderRadius: 0,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
  },
  actionDownloadText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.white,
  },
  actionShareBtn: {
    flex: 1,
    minHeight: 44,
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.primary,
    borderRadius: 0,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
  },
  actionShareText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  actionBtnDisabled: {
    opacity: 0.5,
    backgroundColor: colors.muted,
  },
});

