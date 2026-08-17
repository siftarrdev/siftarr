import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ['**/*.ts'],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        project: '../tsconfig.json',
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/ban-ts-comment': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['**/dashboard/*.ts'],
    rules: {
      '@typescript-eslint/ban-ts-comment': ['warn', { 'ts-nocheck': false }],
      'no-var': 'off',
      '@typescript-eslint/no-dynamic-delete': 'off',
    },
  },
  {
    ignores: ['eslint.config.mjs'],
  },
);
