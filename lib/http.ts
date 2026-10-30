import { gzipSync } from 'node:zlib';
import { hash } from './security.ts';
export function jsonResponse(request: Request, data: unknown, options: { status?: number; privateCache?: boolean; scope?: string; headers?: Record<string, string>; maximum?: number } = {}) {
  const body = Buffer.from(JSON.stringify(data));
  if (body.byteLength > (options.maximum || 64 * 1024)) throw new Error('Response budget exceeded');
  const headers = new Headers({ 'content-type': 'application/json; charset=utf-8', 'cache-control': options.privateCache ? 'private, no-cache' : 'no-store', vary: 'Accept-Encoding', ...options.headers });
  if (options.privateCache) {
    const etag = `W/"${hash(`${options.scope || ''}:${request.url}:${body.toString()}`)}"`;
    headers.set('etag', etag);
    if (request.headers.get('if-none-match')?.split(',').map(s => s.trim()).includes(etag)) return new Response(null, { status: 304, headers });
  }
  const encodings = (request.headers.get('accept-encoding') || '').split(',').map(s => s.trim().split(';'));
  const gzip = encodings.find(([name]) => name === 'gzip');
  const wildcard = encodings.find(([name]) => name === '*');
  const accepts = (gzip || wildcard) && !(gzip || wildcard)!.slice(1).some(s => /^\s*q\s*=\s*0(?:\.0*)?\s*$/.test(s));
  if (accepts && body.length >= 1024) { headers.set('content-encoding', 'gzip'); return new Response(new Uint8Array(gzipSync(body, { level: 4 })), { status: options.status || 200, headers }); }
  return new Response(new Uint8Array(body), { status: options.status || 200, headers });
}
