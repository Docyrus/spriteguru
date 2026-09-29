import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The engine serves the built studio at `/`; in dev, Vite proxies /api (HTTP and WebSocket)
// to an engine started with SPRITEGURU_DEV=1 on port 8777. The logo comes straight from the
// repo's brand/ kit, so the dev server may read that folder too.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: '../src/spriteguru/studio_dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    fs: { allow: ['.', '../brand'] },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8777',
        ws: true,
        changeOrigin: false,
      },
    },
  },
});
