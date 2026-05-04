/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: 'class',
    content: [
        './app/siftarr/templates/**/*.html',
        './app/siftarr/static/js/**/*.js',
    ],
    theme: {
        extend: {
            colors: {
                brand: {
                    50:  '#eef6ff',
                    100: '#d9eaff',
                    200: '#bbd8ff',
                    300: '#8cbeff',
                    400: '#5599ff',
                    500: '#2e73fc',
                    600: '#1854f1',
                    700: '#103fde',
                    800: '#1434b4',
                    900: '#16308e',
                    950: '#111f56',
                },
                surface: {
                    DEFAULT: '#0f1117',
                    50:  '#f6f7f9',
                    100: '#eceef2',
                    200: '#d5d8e2',
                    800: '#1a1d27',
                    850: '#151720',
                    900: '#0f1117',
                    950: '#0a0b0f',
                },
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
                mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
            },
        },
    },
    plugins: [],
}
