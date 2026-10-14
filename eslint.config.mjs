import js from '@eslint/js';
import globals from 'globals';

export default [
  {
    files: ['app/siftarr/static/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      ...js.configs.recommended.rules,
      // bun build strips the comments that mark intentional empty catches;
      // frontend-src lint still enforces no-empty on the TypeScript sources.
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
];
