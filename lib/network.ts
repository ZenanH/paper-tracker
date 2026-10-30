import { setTimeout as delay } from 'node:timers/promises';
import { lookup } from 'node:dns/promises';
import { BlockList, isIP } from 'node:net';
import { Agent, request as httpRequest } from 'undici';
import { config } from './config.ts';
import { AppError } from './security.ts';

const privateIPs = new BlockList();
for (const [ip, bits] of [['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16], ['172.16.0.0', 12], ['192.168.0.0', 16], ['192.0.0.0', 24], ['192.0.2.0', 24], ['198.18.0.0', 15], ['198.51.100.0', 24], ['203.0.113.0', 24], ['224.0.0.0', 4], ['240.0.0.0', 4]] as const) privateIPs.addSubnet(ip, bits, 'ipv4');
for (const [ip, bits] of [['::', 128], ['::1', 128], ['fc00::', 7], ['fe80::', 10], ['ff00::', 8], ['2001:db8::', 32], ['64:ff9b::', 96], ['2002::', 16]] as const) privateIPs.addSubnet(ip, bits, 'ipv6');
export function publicIP(address: string): boolean {
  const raw = address.toLowerCase();
  if (raw.startsWith('::ffff:')) {
    const tail = raw.slice(7);
    if (isIP(tail) === 4) return !privateIPs.check(tail, 'ipv4');
    return false;
  }
  const family = isIP(address);
  return family !== 0 && !privateIPs.check(address, family === 6 ? 'ipv6' : 'ipv4');
}
export function aiURL(base: string): URL {
  let url: URL;
  try { url = new URL(base.trim().replace(/\/+$/, '') + '/chat/completions'); }
  catch { throw new AppError('请填写有效的 AI API 根地址。'); }
  const allowed = config().allowedOrigins.includes(url.origin);
  if (url.username || url.password || url.search || url.hash || (!allowed && url.protocol !== 'https:') || !['http:', 'https:'].includes(url.protocol)) throw new AppError('AI 地址需要公网 HTTPS；内网服务须在宿主机精确授权 origin。');
  const host = url.hostname.replace(/^\[|\]$/g, '');
  if (!allowed && (host === 'localhost' || host.endsWith('.localhost') || (isIP(host) && !publicIP(host)))) throw new AppError('AI 目标地址未获授权。');
  return url;
}
export class ExternalError extends Error {
  constructor(public code: string, public retryable: boolean, public retryAt?: number) { super(code); }
}
export async function boundedJSON(response: Response, maximum = 8 * 1024 * 1024): Promise<{ data: unknown; bytes: number }> {
  if (!response.body) throw new ExternalError('empty_response', true);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = []; let bytes = 0;
  try {
    while (true) { const next = await reader.read(); if (next.done) break; bytes += next.value.byteLength; if (bytes > maximum) throw new ExternalError('response_too_large', false); chunks.push(next.value); }
    return { data: JSON.parse(Buffer.concat(chunks).toString('utf8')), bytes };
  } finally { await reader.cancel().catch(() => {}); }
}
let aiCooldownUntil = 0;
let aiInFlight = 0;
async function acquireAI(signal?: AbortSignal) {
  while (true) {
    signal?.throwIfAborted();
    if (Date.now() >= aiCooldownUntil && aiInFlight < aiConcurrency()) {
      aiInFlight++;
      return () => { aiInFlight--; };
    }
    await delay(100, undefined, { signal });
  }
}
export function aiConcurrency() { return Date.now() < aiCooldownUntil ? 1 : 3; }
export async function aiRequest(base: string, key: string, body: unknown, signal?: AbortSignal, protocol: 'chat' | 'responses' = 'chat'): Promise<unknown> {
  const url = aiURL(base);
  if (protocol === 'responses') url.pathname = url.pathname.replace(/chat\/completions$/, 'responses');
  const allowed = config().allowedOrigins.includes(url.origin);
  const release = await acquireAI(signal);
  const agent = new Agent({ connect: { lookup(hostname, options, callback) {
    lookup(hostname, { all: true }).then(addresses => {
      if (!addresses.length || (!allowed && addresses.some(a => !publicIP(a.address)))) { callback(new Error('AI 目标地址未获授权。'), '', 4); return; }
      if (options.all) callback(null, addresses);
      else { const addr = addresses.find(a => !options.family || a.family === options.family) || addresses[0]; callback(null, addr.address, addr.family); }
    }, error => callback(error, '', 4));
  } } });
  try {
    const response = await httpRequest(url, {
      dispatcher: agent, method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${key}`, 'accept-encoding': 'identity' },
      body: JSON.stringify(body), headersTimeout: 90_000, bodyTimeout: 90_000,
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(90_000)]) : AbortSignal.timeout(90_000)
    });
    if (response.statusCode === 429) aiCooldownUntil = Math.max(aiCooldownUntil, retryAfter(response.headers['retry-after']) || Date.now() + 60_000);
    if (response.statusCode !== 200) {
      await response.body.dump();
      throw new ExternalError(response.statusCode === 429 ? 'ai_rate_limited' : response.statusCode === 401 || response.statusCode === 403 ? 'ai_credentials_rejected' : `ai_http_${response.statusCode}`, response.statusCode === 429 || response.statusCode >= 500, retryAfter(response.headers['retry-after']));
    }
    const buffers: Buffer[] = []; let bytes = 0;
    for await (const chunk of response.body) { bytes += chunk.length; if (bytes > 256 * 1024) { response.body.destroy(); throw new ExternalError('ai_response_too_large', false); } buffers.push(Buffer.from(chunk)); }
    try { return JSON.parse(Buffer.concat(buffers).toString('utf8')); }
    catch { throw new ExternalError('ai_invalid_json', true); }
  } catch (e) {
    if (e instanceof AppError || e instanceof ExternalError) throw e;
    throw new ExternalError('ai_connection_failed', true);
  } finally { try { await agent.close(); } finally { release(); } }
}
export function retryAfter(value: string | string[] | null | undefined): number | undefined {
  const str = Array.isArray(value) ? value[0] : value;
  if (!str) return undefined;
  const seconds = Number(str);
  const date = Number.isFinite(seconds) ? Date.now() + Math.max(0, seconds) * 1000 : Date.parse(str);
  return Number.isFinite(date) ? date : undefined;
}
