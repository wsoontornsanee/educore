/**
 * Which server the app talks to.
 *
 * Kept free of Expo/React Native imports so it runs under `node --test`; the entry point (App.tsx)
 * feeds it the build-time values. A build that names no server is a build bug and fails at startup:
 * silently falling back to a simulator address would ship an app that can never log in.
 */

export class ApiConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ApiConfigError';
  }
}

export interface ApiConfigInput {
  /** `process.env.EXPO_PUBLIC_API_URL`, inlined at bundle time (set per EAS build profile). */
  envUrl?: string;
  /** `expo.extra.apiUrl` from the app config, if a build sets one there. */
  extraUrl?: string;
  /** `__DEV__`: only a development bundle may fall back to a local server. */
  isDev: boolean;
  /** `Platform.OS`: the Android emulator reaches the host at 10.0.2.2, the iOS simulator at localhost. */
  platform: string;
}

const API_PATH = '/api/v1';
// Deliberately not `new URL()`: React Native's URL polyfill leaves parts of it unimplemented.
const URL_SHAPE = /^(https?):\/\/[^\s/?#]+(\/[^\s?#]*)?$/i;

export function devApiBase(platform: string): string {
  return `http://${platform === 'android' ? '10.0.2.2' : 'localhost'}:8000${API_PATH}`;
}

/**
 * The API base URL for this build, without a trailing slash.
 *
 * Precedence: `envUrl`, then `extraUrl`, then (development bundles only) the local dev server.
 * Anything else throws `ApiConfigError`, as does a URL that is not `http(s)://host/api/v1`
 * (callers pass paths like `/pos/sessions/` and rely on the base ending in `/api/v1`) or that is
 * plain `http` outside development (tokens and card data would travel in clear).
 */
export function resolveApiBase(input: ApiConfigInput): string {
  const configured = (input.envUrl || input.extraUrl || '').trim();
  if (!configured) {
    if (input.isDev) return devApiBase(input.platform);
    throw new ApiConfigError(
      'No API URL configured for this build. Set EXPO_PUBLIC_API_URL in the EAS build profile (see eas.json).',
    );
  }

  const url = configured.replace(/\/+$/, '');
  const match = URL_SHAPE.exec(url);
  if (!match) {
    throw new ApiConfigError(`API URL is not a valid http(s) URL: ${configured}`);
  }
  if (!url.endsWith(API_PATH)) {
    throw new ApiConfigError(`API URL must end with ${API_PATH} (got ${configured}).`);
  }
  if (match[1].toLowerCase() === 'http' && !input.isDev) {
    throw new ApiConfigError(`API URL must use https outside development (got ${configured}).`);
  }
  return url;
}
