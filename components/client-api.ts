export class APIError extends Error { constructor(message: string, public status: number, public code: string) { super(message); } }
type CacheEntry = { etag: string; data: unknown; bytes: number };
const cache = new Map<string, CacheEntry>();
export async function api<T>(path: string, options?: { body?: unknown; signal?: AbortSignal; uncached?: boolean }): Promise<T> {
  const url = `/api/${path}`, old = !options?.body && !options?.uncached ? cache.get(url) : undefined;
  const headers: Record<string, string> = {};
  if (options?.body !== undefined) headers['content-type'] = 'application/json';
  if (old?.etag) headers['if-none-match'] = old.etag;
  const timeout = AbortSignal.timeout(options?.body === undefined ? 45000 : 90000);
  const signal = options?.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const response = await fetch(url, { method: options?.body === undefined ? 'GET' : 'POST', body: options?.body === undefined ? undefined : JSON.stringify(options.body), headers, signal, cache: 'no-store' });
  if (response.status === 304 && old) { cache.delete(url); cache.set(url, old); return old.data as T; }
  const text = await response.text();
  let data: unknown;
  try { data = JSON.parse(text); } catch { throw new APIError('Invalid server response. Please try again.', response.status, 'invalid_response'); }
  if (!response.ok) { const error = data as { error?: string; code?: string }; throw new APIError((error.error && !/[\u3400-\u9fff]/.test(error.error) ? error.error : ({ ai_connection_failed: 'Cannot connect to the AI provider. Check DNS, proxy, and allowed origins.', ai_credentials_rejected: 'The provider rejected the API key or denied access.', ai_http_400: 'The provider rejected the model or request parameters.', ai_http_404: 'API endpoint or model not found.', ai_rate_limited: 'The provider rate limit or quota was reached.', reprocess_confirmation_required: 'This change will reprocess existing unread papers and may use API credit. Continue?', origin_mismatch: 'Request origin does not match APP_URL.', service_unavailable: 'Service unavailable. Check the data directory and server configuration.' } as Record<string, string>)[error.code || ''] || 'Request failed. Check your configuration and try again.'), response.status, error.code || 'request_failed'); }
  const etag = response.headers.get('etag');
  if (etag) {
    cache.delete(url); cache.set(url, { etag, data, bytes: new TextEncoder().encode(text).byteLength });
    while (cache.size > 10 || [...cache.values()].reduce((sum, e) => sum + e.bytes, 0) > 1024 * 1024) cache.delete(cache.keys().next().value!);
  }
  return data as T;
}
