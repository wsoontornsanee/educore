"""Tests for ESC/POS thermal receipt rendering (spec/07 WAL-020, spec/12).

Covers: the pure encoder (command framing, width clamping, CP437, barcode),
receipt rendering for COMPLETED/REJECTED/VOIDED transactions, and the API
wiring (receipt bytes in the checkout response + offline batch report, the
reprint endpoint, cross-tenant 404).
"""
import base64
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.wallet.escpos import (
    WIDTH_58MM,
    WIDTH_80MM,
    EscposBuilder,
    format_idr,
    render_pos_receipt,
)
from apps.wallet.models import POSTransaction, POSTransactionStatus
from apps.wallet.services import (
    attach_receipts_to_batch_report,
    get_or_create_wallet,
    process_pos_transaction,
    process_offline_pos_batch,
    topup_wallet,
    void_pos_transaction,
)
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class EscposBuilderTests(TestCase):
    def test_init_prefix_and_cut_suffix(self):
        data = EscposBuilder(WIDTH_58MM).init().line('Halo').cut().bytes()
        self.assertTrue(data.startswith(b'\x1b@'))
        self.assertTrue(data.endswith(b'\x1dV\x42'))

    def test_line_width_clamped_to_58mm(self):
        data = EscposBuilder(WIDTH_58MM).line('x' * 100).bytes()
        text_line = data.rstrip(b'\n').lstrip(b'\x1b@')
        self.assertLessEqual(len(text_line), WIDTH_58MM)

    def test_line_width_80mm_allows_48_columns(self):
        data = EscposBuilder(WIDTH_80MM).init().line('x' * 48).bytes()
        body = data[2:].rstrip(b'\n')  # strip the 2-byte init prefix (ESC @)
        self.assertEqual(len(body), WIDTH_80MM)

    def test_invalid_width_rejected(self):
        with self.assertRaises(ValueError):
            EscposBuilder(72)

    def test_invalid_justification_rejected(self):
        with self.assertRaises(ValueError):
            EscposBuilder().justify(3)

    def test_centered_uses_justification_command(self):
        data = EscposBuilder().init().centered('Judul').bytes()
        self.assertIn(b'\x1ba\x01', data)

    def test_two_column_pads_to_exact_width(self):
        data = EscposBuilder(WIDTH_58MM).two_column('Total', 'Rp 15.000').bytes()
        body = data.rstrip(b'\n')
        self.assertEqual(len(body), WIDTH_58MM)
        self.assertTrue(body.endswith(b'Rp 15.000'))

    def test_two_column_overflow_moves_value_to_own_row(self):
        data = EscposBuilder(WIDTH_58MM).two_column('N' * 40, 'Rp 1').bytes()
        lines = data.rstrip(b'\n').split(b'\n')
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith(b'Rp 1'))

    def test_cp437_indonesian_text(self):
        data = EscposBuilder().line('Pembayaran Berhasil').bytes()
        self.assertIn('Pembayaran Berhasil'.encode('cp437'), data)

    def test_unencodable_character_transliterated_not_crash(self):
        data = EscposBuilder().line('Emoji \U0001f600 sini').bytes()
        self.assertTrue(len(data) > 0)

    def test_barcode_code128_framing(self):
        data = EscposBuilder().barcode_code128('TX-123').bytes()
        # GS k m=73 len payload
        payload = b'{B' + b'TX-123'
        self.assertIn(b'\x1dkI' + bytes([len(payload)]) + payload, data)

    def test_feed_multi_line_uses_esc_d(self):
        data = EscposBuilder().feed(3).bytes()
        self.assertEqual(data, b'\x1bd\x03')


class FormatIdrTests(TestCase):
    def test_indonesian_grouping_no_decimals(self):
        self.assertEqual(format_idr(Decimal('1500000.00')), 'Rp 1.500.000')
        self.assertEqual(format_idr(Decimal('15000.00')), 'Rp 15.000')
        self.assertEqual(format_idr(Decimal('0.00')), 'Rp 0')


class RenderReceiptTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-topup')
        self.items = [{'sku': self.product.sku, 'name': self.product.name,
                       'qty': 2, 'unit_price': '15000.00', 'category': 'FOOD'}]

    def test_completed_receipt_structure(self):
        pos_tx = process_pos_transaction(self.terminal, self.fx['student'], self.items, 'rcpt-1')
        data = render_pos_receipt(pos_tx)
        self.assertTrue(data.startswith(b'\x1b@'))
        self.assertTrue(data.endswith(b'\x1dV\x42'))
        text = data.decode('cp437', 'replace')
        self.assertIn('Kantin Sehat', text)
        self.assertIn('Dimas', text)
        self.assertIn('30.000', text)  # 2 x 15000 in Indonesian grouping
        # Barcode of the client transaction id is present.
        payload = b'{B' + b'rcpt-1'
        self.assertIn(b'\x1dkI' + bytes([len(payload)]) + payload, data)

    def test_receipt_line_width_never_exceeds_paper(self):
        long_items = [{'sku': 'X', 'name': 'Produk Dengan Nama Sangat Panjang Sekali Sampai Terpotong',
                       'qty': 3, 'unit_price': '5000.00', 'category': 'FOOD'}]
        pos_tx = process_pos_transaction(self.terminal, self.fx['student'], long_items, 'rcpt-2')
        data = render_pos_receipt(pos_tx, WIDTH_58MM)
        for line in data.split(b'\n'):
            self.assertLessEqual(len(line), WIDTH_58MM)

    def test_rejected_receipt_marks_refusal(self):
        from apps.wallet.services import set_spend_rule
        set_spend_rule(self.fx['student'], blocked_categories=['FOOD'])
        with self.assertRaises(Exception):
            process_pos_transaction(self.terminal, self.fx['student'], self.items, 'rcpt-3')
        logged = POSTransaction.all_tenants.get(client_transaction_id='rcpt-3')
        self.assertEqual(logged.status, POSTransactionStatus.REJECTED)
        text = render_pos_receipt(logged).decode('cp437', 'replace')
        self.assertIn('TRANSAKSI DITOLAK', text)
        self.assertIn('Saldo tidak berkurang', text)

    def test_voided_receipt_marks_cancellation(self):
        pos_tx = process_pos_transaction(self.terminal, self.fx['student'], self.items, 'rcpt-4')
        voided = void_pos_transaction(pos_tx, 'test void')
        text = render_pos_receipt(voided).decode('cp437', 'replace')
        self.assertIn('DIBATALKAN', text)
        self.assertIn('test void', text)

    def test_no_guardian_pii_in_receipt(self):
        pos_tx = process_pos_transaction(self.terminal, self.fx['student'], self.items, 'rcpt-5')
        text = render_pos_receipt(pos_tx).decode('cp437', 'replace')
        self.assertNotIn('nik', text.lower())
        self.assertNotIn('nisn', text.lower())  # NIS prints, NISN (PII) does not


class ReceiptApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('200000'), 'CASH', 'seed-topup')
        self.client.force_authenticate(user=self.fx['finance_user'])

    def _checkout(self, client_tx_id):
        return self.client.post('/api/v1/pos/transactions/', {
            'terminal_id': self.terminal.id,
            'student_id': self.fx['student'].id,
            'items': [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1,
                       'unit_price': '15000.00'}],
            'client_transaction_id': client_tx_id,
        }, format='json')

    def test_checkout_response_carries_escpos_bytes(self):
        res = self._checkout('esc-1')
        self.assertEqual(res.status_code, 201, res.content)
        raw = base64.b64decode(res.json()['receipt_escpos_base64'])
        self.assertTrue(raw.startswith(b'\x1b@'))
        self.assertIn('Kantin Sehat'.encode('cp437'), raw)

    def test_reprint_endpoint_returns_raw_bytes(self):
        res = self._checkout('esc-2')
        tx_id = res.json()['id']
        res2 = self.client.get(f'/api/v1/pos/transactions/{tx_id}/receipt/')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2['Content-Type'], 'application/vnd.escpos.raw')
        raw = b''.join(res2.streaming_content) if res2.streaming else res2.content
        self.assertTrue(raw.startswith(b'\x1b@'))
        self.assertTrue(raw.endswith(b'\x1dV\x42'))

    def test_reprint_cross_tenant_404(self):
        res = self._checkout('esc-3')
        tx_id = res.json()['id']
        from apps.wallet.tests.test_wallet_core import build_wallet_fixture as bwf
        fx_b = bwf(foundation_name="Yayasan ESC B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res2 = self.client.get(f'/api/v1/pos/transactions/{tx_id}/receipt/')
        self.assertEqual(res2.status_code, 404)

    def test_reprint_requires_auth(self):
        res = self._checkout('esc-4')
        tx_id = res.json()['id']
        anon = APIClient()
        res2 = anon.get(f'/api/v1/pos/transactions/{tx_id}/receipt/')
        self.assertIn(res2.status_code, (401, 403))


class OfflineBatchReceiptTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('200000'), 'CASH', 'seed-topup')
        self.client.force_authenticate(user=self.fx['finance_user'])

    def test_batch_report_carries_receipts_per_result(self):
        batch = [
            {'client_transaction_id': 'off-1', 'student_id': self.fx['student'].id,
             'items': [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1,
                        'unit_price': '15000.00'}],
             'occurred_at': '2026-09-17T03:00:00Z'},
            {'client_transaction_id': 'off-2', 'student_id': self.fx['student'].id,
             'items': [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1,
                        'unit_price': '15000.00'}],
             'occurred_at': '2026-09-17T03:05:00Z'},
        ]
        res = self.client.post('/api/v1/pos/transactions/batch/', {'terminal_id': self.terminal.id,
                                                                   'transactions': batch}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        report = res.json()
        for entry in report['results']:
            self.assertIn('receipt_escpos_base64', entry)
            raw = base64.b64decode(entry['receipt_escpos_base64'])
            self.assertTrue(raw.startswith(b'\x1b@'))
