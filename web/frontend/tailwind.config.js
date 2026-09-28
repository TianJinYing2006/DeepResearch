/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#070a0f',
        panel: '#0d121b',
        line: '#202938',
        // 品牌强调色。注意：键名不得使用 Tailwind 内置色板名（cyan/slate/emerald…）——
        // 内置色板是 {50..950} 色阶对象，用字符串覆盖会整体替换掉它，
        // 导致 text-cyan-200 / bg-cyan-300 之类带档位的类全部静默失效。
        // 详见 docs/requirements/11-tailwind-color-token-fix.md
        brand: { 200: '#a5f3fc', 300: '#66e3ff', 400: '#22d3ee' },
        mint: '#65f0bb',
      },
      boxShadow: {
        glow: '0 0 50px rgba(102, 227, 255, 0.08)',
      },
    },
  },
  plugins: [],
}
