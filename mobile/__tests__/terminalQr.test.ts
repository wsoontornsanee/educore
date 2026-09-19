/**
 * Canteen QR Charge offline terminal signer (spec 18 QRS-022/023).
 *
 * KNOWN_TOKEN was produced by the backend's own `apps.wallet.qr_offline._sign`; the client must match
 * it byte for byte or the server rejects every token it mints with BAD_SIGNATURE.
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  OFFLINE_TOKEN_TTL_SECONDS,
  base64UrlEncode,
  describePairingFailure,
  getTerminalPairing,
  mintOfflineQrToken,
  pairTerminal,
  randomNonce,
  signOfflineTokenPayload,
  unpairTerminal,
} from '../src/services/terminalQr.ts';
import { api } from '../src/services/api.ts';

const SECRET = 'test-secret-Zk3p';
const PAYLOAD = {
  terminal_device_id: 'POS-KANTIN-01',
  key_id: 'potk_AbC123xyz',
  nonce: '00112233445566778899aabbccddeeff',
  minted_at: '2026-09-19T03:00:00.123456+00:00',
  expires_at: '2026-09-19T03:02:00.123456+00:00',
};
const KNOWN_TOKEN =
  'eyJleHBpcmVzX2F0IjoiMjAyNi0wOS0xOVQwMzowMjowMC4xMjM0NTYrMDA6MDAiLCJrZXlfaWQiOiJwb3RrX0FiQzEyM3h5eiIsIm1pbnRlZF9hdCI6IjIwMjYtMDktMTlUMDM6MDA6MDAuMTIzNDU2KzAwOjAwIiwibm9uY2UiOiIwMDExMjIzMzQ0NTU2Njc3ODg5OWFhYmJjY2RkZWVmZiIsInRlcm1pbmFsX2RldmljZV9pZCI6IlBPUy1LQU5USU4tMDEifQ.' +
  '4b11ba7d3effa7a39b8656dad35eb20dba407892196dc21ea29a24413676cef1';

function decodePayload(token: string): any {
  const b64 = token.split('.')[0].replace(/-/g, '+').replace(/_/g, '/');
  return JSON.parse(Buffer.from(b64, 'base64').toString('utf8'));
}

describe('offline terminal QR signer', () => {
  it('reproduces the backend signature byte for byte', () => {
    assert.strictEqual(signOfflineTokenPayload(PAYLOAD, SECRET), KNOWN_TOKEN);
  });

  it('is independent of the key order the payload was built in', () => {
    const shuffled = {
      nonce: PAYLOAD.nonce,
      terminal_device_id: PAYLOAD.terminal_device_id,
      expires_at: PAYLOAD.expires_at,
      key_id: PAYLOAD.key_id,
      minted_at: PAYLOAD.minted_at,
    };
    assert.strictEqual(signOfflineTokenPayload(shuffled, SECRET), KNOWN_TOKEN);
  });

  it('a different secret gives a different signature over the same payload', () => {
    const other = signOfflineTokenPayload(PAYLOAD, 'another-secret');
    assert.strictEqual(other.split('.')[0], KNOWN_TOKEN.split('.')[0]);
    assert.notStrictEqual(other.split('.')[1], KNOWN_TOKEN.split('.')[1]);
  });

  it('base64url-encodes every remainder length without padding', () => {
    for (const s of ['', 'a', 'ab', 'abc', 'abcd', 'abcde', '???>>>~~~']) {
      const expected = Buffer.from(s).toString('base64url');
      assert.strictEqual(base64UrlEncode(new TextEncoder().encode(s)), expected);
    }
  });

  it('mints a token the server can parse: fields, +00:00 offsets and the default 120 s window', () => {
    const now = new Date('2026-09-19T03:00:00.123Z');
    const { token, expiresAt } = mintOfflineQrToken(
      { deviceId: 'POS-KANTIN-01', keyId: 'potk_AbC123xyz', secret: SECRET },
      { now, nonce: 'ab'.repeat(16) },
    );
    const payload = decodePayload(token);
    assert.deepStrictEqual(Object.keys(payload), ['expires_at', 'key_id', 'minted_at', 'nonce', 'terminal_device_id']);
    assert.strictEqual(payload.minted_at, '2026-09-19T03:00:00.123+00:00');
    assert.strictEqual(payload.expires_at, '2026-09-19T03:02:00.123+00:00');
    assert.strictEqual(new Date(payload.expires_at).getTime() - new Date(payload.minted_at).getTime(), OFFLINE_TOKEN_TTL_SECONDS * 1000);
    assert.strictEqual(expiresAt, '2026-09-19T03:02:00.123Z');
    assert.ok(token.length < 512, 'must fit the guardian scanner limit');
    assert.match(token, /^[A-Za-z0-9_-]+\.[0-9a-f]{64}$/);
  });

  it('honours a custom ttl and never reuses a nonce', () => {
    const pairing = { deviceId: 'D', keyId: 'K', secret: SECRET };
    const now = new Date('2026-09-19T03:00:00Z');
    const a = decodePayload(mintOfflineQrToken(pairing, { now, ttlSeconds: 30 }).token);
    const b = decodePayload(mintOfflineQrToken(pairing, { now, ttlSeconds: 30 }).token);
    assert.strictEqual(new Date(a.expires_at).getTime() - now.getTime(), 30_000);
    assert.notStrictEqual(a.nonce, b.nonce);
    assert.match(a.nonce, /^[0-9a-f]{32}$/);
  });

  it('randomNonce is 128 bits of hex', () => {
    assert.match(randomNonce(), /^[0-9a-f]{32}$/);
  });
});

describe('terminal pairing', () => {
  const realGet = api.get;
  const realPost = api.post;
  beforeEach(async () => {
    api.get = realGet;
    api.post = realPost;
    await unpairTerminal(7);
    await unpairTerminal(8);
  });

  it('fetches the device id, issues a key and stores both with the secret', async () => {
    const calls: string[] = [];
    (api as any).get = async (url: string) => {
      calls.push(`GET ${url}`);
      return { data: { device_id: 'POS-KANTIN-01' } };
    };
    (api as any).post = async (url: string, body: any) => {
      calls.push(`POST ${url}`);
      assert.deepStrictEqual(body, {});
      return { data: { key_id: 'potk_new', secret: 'S3cret', status: 'ACTIVE' } };
    };
    const pairing = await pairTerminal(7);
    assert.deepStrictEqual(calls, ['GET /pos/terminals/7/', 'POST /pos/terminals/7/session-key/issue/']);
    assert.strictEqual(pairing.deviceId, 'POS-KANTIN-01');
    assert.strictEqual(pairing.keyId, 'potk_new');
    const stored = await getTerminalPairing(7);
    assert.strictEqual(stored?.secret, 'S3cret');
    assert.strictEqual(stored?.terminalId, 7);
  });

  it('a failed issue leaves the terminal unpaired', async () => {
    (api as any).get = async () => ({ data: { device_id: 'POS-KANTIN-01' } });
    (api as any).post = async () => {
      const err: any = new Error('forbidden');
      err.response = { status: 403, data: {} };
      throw err;
    };
    await assert.rejects(() => pairTerminal(7), (e: any) => describePairingFailure(e) === 'FORBIDDEN');
    assert.strictEqual(await getTerminalPairing(7), null);
  });

  it('keeps pairings per terminal and unpair drops only that one', async () => {
    (api as any).get = async () => ({ data: { device_id: 'D' } });
    (api as any).post = async () => ({ data: { key_id: 'K', secret: 'S' } });
    await pairTerminal(7);
    await pairTerminal(8);
    await unpairTerminal(7);
    assert.strictEqual(await getTerminalPairing(7), null);
    assert.ok(await getTerminalPairing(8));
  });

  it('classifies failures for the pairing screen', () => {
    assert.strictEqual(describePairingFailure({ response: { status: 403 } }), 'FORBIDDEN');
    assert.strictEqual(describePairingFailure({ response: { status: 404 } }), 'NOT_FOUND');
    assert.strictEqual(describePairingFailure({ isNetworkError: true }), 'NETWORK');
    assert.strictEqual(describePairingFailure({ response: { status: 500 } }), 'UNKNOWN');
  });
});
