import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const backendOrigin = process.env.VAN_DASHBOARD_BACKEND_ORIGIN ?? 'http://vanpi.lan:8788';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
        configure(proxy) {
          // Flask's CSRF guard compares Origin and Host. During local Vite
          // development, make both describe the trusted backend origin.
          proxy.on('proxyReq', (proxyRequest) => {
            const origin = new URL(backendOrigin);
            proxyRequest.setHeader('Host', origin.host);
            proxyRequest.setHeader('Origin', origin.origin);
            proxyRequest.setHeader('Referer', `${origin.origin}/`);
          });
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    restoreMocks: true,
    clearMocks: true,
  },
});
