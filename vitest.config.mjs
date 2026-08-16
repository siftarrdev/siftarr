import { defineConfig } from 'vitest/config';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  resolve: {
    alias: {
      '/static/js': fileURLToPath(new URL('./app/siftarr/static/js', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['frontend-tests/unit/**/*.test.js'],
  },
});
