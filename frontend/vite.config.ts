import { defineConfig } from 'vitest/config';

export default defineConfig({
  server: { proxy: { '/api': { target: 'http://backend:8000', rewrite: path => path.replace(/^\/api/, '') } } },
  test: { environment: 'jsdom', passWithNoTests: true },
});
