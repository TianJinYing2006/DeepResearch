/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#070a0f',
        panel: '#0d121b',
        line: '#202938',
        cyan: '#66e3ff',
        mint: '#65f0bb',
      },
      boxShadow: {
        glow: '0 0 50px rgba(102, 227, 255, 0.08)',
      },
    },
  },
  plugins: [],
}
