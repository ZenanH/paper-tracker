import type { NextConfig } from 'next';

const config: NextConfig = {
  output: 'standalone',
  outputFileTracingIncludes: { '/api/*': ['./catalog/catalog.sqlite'] },
  outputFileTracingExcludes: { '/*': ['./.local/**/*', './paper-tracker-data/**/*', './.env*'] },
  poweredByHeader: false,
  compress: true,
  serverExternalPackages: ['undici'],
  async headers() {
    return [{ source: '/:path*', headers: [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'Referrer-Policy', value: 'same-origin' },
      { key: 'X-Frame-Options', value: 'DENY' },
      { key: 'Content-Security-Policy', value: "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" }
    ] }];
  }
};
export default config;
