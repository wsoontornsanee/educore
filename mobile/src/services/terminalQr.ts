/**
 * Canteen QR Charge — offline terminal signer (spec 18 §6, QRS-022/023).
 *
 * A canteen terminal that has lost connectivity cannot ask the server for a QR session, so it signs
 * its own single-use SESSION token with a per-terminal secret. The wire format is fixed by the
 * backend (`apps/wallet/qr_offline.py`, `_sign`) and must be reproduced byte for byte:
 *
 *   token   = "<payload_b64>.<hmac_hex>"
 *   payload = {"terminal_device_id", "key_id", "nonce", "minted_at", "expires_at"}   (keys sorted, no spaces)
 *   b64     = base64url without padding
 *   hmac    = HMAC-SHA256(secret, payload_b64)
 *
 * Pairing (`pairTerminal`) is the only step that needs the network: it fetches the terminal's
 * `device_id` and asks the server to issue a key. The secret is returned exactly once, so it goes
 * straight into secure storage and is never shown, logged or sent back.
 *
 * The stored key deliberately survives an operator logout (like the biometric flag): the terminal
 * must keep signing while the cashier's session is expired, and the secret belongs to the device,
 * not to the person holding it. `unpairTerminal` is the explicit way to drop it.
 */
import { hmac } from '@noble/hashes/hmac.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex, utf8ToBytes } from '@noble/hashes/utils.js';
import { api } from './api.ts';
import { getItem, removeItem, setItem } from './storage.ts';

/** Server default; the token is single-use and the student is standing at the counter. */
export const OFFLINE_TOKEN_TTL_SECONDS = 120;

const PAIRING_KEY_PREFIX = 'educore_pos_terminal_qr_key';

export interface TerminalQrPairing {
  terminalId: number;
  /** `POSTerminal.device_id` — the payload's `terminal_device_id`, which the server matches exactly. */
  deviceId: string;
  keyId: string;
  secret: string;
  pairedAt: string;
}

export interface OfflineQrToken {
  token: string;
  /** ISO time after which the server refuses this token (plus its clock-skew tolerance). */
  expiresAt: string;
}

export type PairingFailure = 'FORBIDDEN' | 'NOT_FOUND' | 'NETWORK' | 'UNKNOWN';

function pairingKey(terminalId: number): string {
  return `${PAIRING_KEY_PREFIX}:${terminalId}`;
}

// ─── Encoding (must match Python's json.dumps(sort_keys, separators) + urlsafe_b64encode) ──────────

const B64URL = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

/** base64url, no padding. Written out so the output never depends on `btoa` being present. */
export function base64UrlEncode(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | ((bytes[i + 1] ?? 0) << 8) | (bytes[i + 2] ?? 0);
    out += B64URL[(n >> 18) & 63] + B64URL[(n >> 12) & 63];
    if (i + 1 < bytes.length) out += B64URL[(n >> 6) & 63];
    if (i + 2 < bytes.length) out += B64URL[n & 63];
  }
  return out;
}

/** Python `datetime.isoformat()` with a UTC offset — the form the server parses with `fromisoformat`. */
function isoUtc(date: Date): string {
  return date.toISOString().replace('Z', '+00:00');
}

export interface OfflineTokenPayload {
  terminal_device_id: string;
  key_id: string;
  nonce: string;
  minted_at: string;
  expires_at: string;
}

/** Sign an already-built payload. Exposed so tests can pin the output to the backend's `_sign`. */
export function signOfflineTokenPayload(payload: OfflineTokenPayload, secret: string): string {
  // Keys are inserted in sorted order, which is what sort_keys=True produces; all values are ASCII.
  const ordered = {
    expires_at: payload.expires_at,
    key_id: payload.key_id,
    minted_at: payload.minted_at,
    nonce: payload.nonce,
    terminal_device_id: payload.terminal_device_id,
  };
  const payloadB64 = base64UrlEncode(utf8ToBytes(JSON.stringify(ordered)));
  const signature = bytesToHex(hmac(sha256, utf8ToBytes(secret), utf8ToBytes(payloadB64)));
  return `${payloadB64}.${signature}`;
}

/** 128 random bits as hex. Never falls back to Math.random: a guessable nonce is refused, not tolerated. */
export function randomNonce(): string {
  let bytes: Uint8Array | null = null;
  try {
    // expo-crypto is the source on device; Node (tests) exposes the same Web Crypto API.
    bytes = require('expo-crypto').getRandomBytes(16);
  } catch {
    const webCrypto = (globalThis as any).crypto;
    if (webCrypto && typeof webCrypto.getRandomValues === 'function') {
      bytes = webCrypto.getRandomValues(new Uint8Array(16));
    }
  }
  if (!bytes) throw new Error('No secure random source available to mint a QR token.');
  return bytesToHex(bytes);
}

/**
 * Mint one single-use session token, entirely offline. `now` and `nonce` are injectable for tests;
 * production callers pass neither.
 */
export function mintOfflineQrToken(
  pairing: Pick<TerminalQrPairing, 'deviceId' | 'keyId' | 'secret'>,
  options: { now?: Date; ttlSeconds?: number; nonce?: string } = {},
): OfflineQrToken {
  const now = options.now ?? new Date();
  const ttl = options.ttlSeconds ?? OFFLINE_TOKEN_TTL_SECONDS;
  const expiresAt = new Date(now.getTime() + ttl * 1000);
  const payload: OfflineTokenPayload = {
    terminal_device_id: pairing.deviceId,
    key_id: pairing.keyId,
    nonce: options.nonce ?? randomNonce(),
    minted_at: isoUtc(now),
    expires_at: isoUtc(expiresAt),
  };
  return { token: signOfflineTokenPayload(payload, pairing.secret), expiresAt: expiresAt.toISOString() };
}

// ─── Pairing + storage ───────────────────────────────────────────────────────────────────────────

export async function getTerminalPairing(terminalId: number): Promise<TerminalQrPairing | null> {
  const raw = await getItem(pairingKey(terminalId));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (
      parsed && typeof parsed.deviceId === 'string' && typeof parsed.keyId === 'string' &&
      typeof parsed.secret === 'string' && parsed.terminalId === terminalId
    ) {
      return parsed as TerminalQrPairing;
    }
  } catch {
    // corrupt value: treated as not paired, the operator re-pairs
  }
  return null;
}

export async function unpairTerminal(terminalId: number): Promise<void> {
  await removeItem(pairingKey(terminalId));
}

export function describePairingFailure(err: any): PairingFailure {
  const status = err?.response?.status;
  if (status === 403 || status === 401) return 'FORBIDDEN';
  if (status === 404) return 'NOT_FOUND';
  if (err?.isNetworkError) return 'NETWORK';
  return 'UNKNOWN';
}

/**
 * Pair (or re-pair) this device with `terminalId`. Needs the network and a user who may manage
 * hardware (`hardware.write`). Re-pairing rotates the key: the server keeps the old one verifiable
 * for its grace period, so sales already signed offline still sync.
 *
 * The secret is written to secure storage before this resolves. `storage.setItem` degrades to an
 * in-memory map when the keychain refuses the write, so on such a device the pairing would not
 * survive an app restart; the terminal then reads as unpaired and the operator pairs again (which
 * rotates the key, harmlessly).
 */
export async function pairTerminal(terminalId: number): Promise<TerminalQrPairing> {
  const terminal = await api.get<{ device_id: string }>(`/pos/terminals/${terminalId}/`);
  const issued = await api.post<{ key_id: string; secret: string }>(
    `/pos/terminals/${terminalId}/session-key/issue/`,
    {},
  );
  const pairing: TerminalQrPairing = {
    terminalId,
    deviceId: terminal.data.device_id,
    keyId: issued.data.key_id,
    secret: issued.data.secret,
    pairedAt: new Date().toISOString(),
  };
  await setItem(pairingKey(terminalId), JSON.stringify(pairing));
  return pairing;
}
