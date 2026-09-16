/**
 * Authenticated API Client for EduCore Mobile
 * 
 * Uses standard Fetch API (native to React Native, Expo, and Node.js).
 * Injects JWT Bearer tokens and handles automatic 401 token refresh.
 */
import { clearAuth, getTokens, saveTokens } from './storage.ts';

export const DEFAULT_API_BASE = 'http://10.0.2.2:8000/api/v1';

let currentBaseUrl = DEFAULT_API_BASE;

export function setApiBaseUrl(url: string): void {
  currentBaseUrl = url.replace(/\/+$/, '');
}

export function getApiBaseUrl(): string {
  return currentBaseUrl;
}

export interface ApiResponse<T = any> {
  data: T;
  status: number;
  headers: Record<string, string>;
}

export interface RequestOptions {
  headers?: Record<string, string>;
  timeout?: number;
  _retry?: boolean;
}

// Concurrency lock for refresh token calls
let isRefreshing = false;
let failedQueue: Array<{
  resolve: (token: string) => void;
  reject: (error: any) => void;
}> = [];

function processQueue(error: any, token: string | null = null) {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else if (token) {
      prom.resolve(token);
    }
  });
  failedQueue = [];
}

async function request<T>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: any,
  options: RequestOptions = {}
): Promise<ApiResponse<T>> {
  const url = path.startsWith('http') ? path : `${currentBaseUrl}${path.startsWith('/') ? '' : '/'}${path}`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    ...(options.headers || {}),
  };

  const { access } = await getTokens();
  if (access && !headers.Authorization) {
    headers.Authorization = `Bearer ${access}`;
  }

  const fetchOptions: RequestInit = {
    method,
    headers,
  };

  if (body !== undefined && method !== 'GET') {
    fetchOptions.body = typeof body === 'string' ? body : JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, fetchOptions);
  } catch (netErr: any) {
    const error: any = new Error(netErr.message || 'Network Error');
    error.isNetworkError = true;
    throw error;
  }

  // Handle 401 Unauthorized with automatic token refresh
  if (response.status === 401 && !options._retry && !path.includes('auth/token')) {
    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({
          resolve: async (newToken: string) => {
            options._retry = true;
            options.headers = { ...options.headers, Authorization: `Bearer ${newToken}` };
            try {
              const retryRes = await request<T>(method, path, body, options);
              resolve(retryRes);
            } catch (retryErr) {
              reject(retryErr);
            }
          },
          reject,
        });
      });
    }

    isRefreshing = true;
    options._retry = true;

    try {
      const { refresh } = await getTokens();
      if (!refresh) {
        throw new Error('No refresh token available');
      }

      const refreshRes = await fetch(`${currentBaseUrl}/auth/token/refresh/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ refresh }),
      });

      if (!refreshRes.ok) {
        throw new Error('Refresh token rejected');
      }

      const refreshData = await refreshRes.json();
      const newAccess = refreshData.access;
      const newRefresh = refreshData.refresh || refresh;
      await saveTokens(newAccess, newRefresh);

      processQueue(null, newAccess);

      headers.Authorization = `Bearer ${newAccess}`;
      return request<T>(method, path, body, options);
    } catch (refreshErr) {
      processQueue(refreshErr, null);
      await clearAuth();
      const err: any = new Error('Authentication expired');
      err.response = { status: 401 };
      throw err;
    } finally {
      isRefreshing = false;
    }
  }

  let responseData: any = null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    responseData = await response.json();
  } else {
    responseData = await response.text();
  }

  if (!response.ok) {
    const error: any = new Error(
      responseData?.error || responseData?.detail || `HTTP Error ${response.status}`
    );
    error.response = {
      status: response.status,
      data: responseData,
    };
    throw error;
  }

  return {
    data: responseData as T,
    status: response.status,
    headers: Object.fromEntries((response.headers as any).entries()),
  };
}

export const apiClient = {
  get: <T = any>(path: string, options?: RequestOptions) => request<T>('GET', path, undefined, options),
  post: <T = any>(path: string, body?: any, options?: RequestOptions) => request<T>('POST', path, body, options),
  put: <T = any>(path: string, body?: any, options?: RequestOptions) => request<T>('PUT', path, body, options),
  patch: <T = any>(path: string, body?: any, options?: RequestOptions) => request<T>('PATCH', path, body, options),
  delete: <T = any>(path: string, options?: RequestOptions) => request<T>('DELETE', path, undefined, options),
};
