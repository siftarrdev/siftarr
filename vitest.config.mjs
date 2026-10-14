import { defineConfig } from 'vitest/config';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  resolve: {
    alias: {
      // Browser import paths resolve to the TypeScript sources, not generated output.
      '/static/js': fileURLToPath(new URL('./frontend-src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['frontend-tests/unit/**/*.test.js'],
  },
});
