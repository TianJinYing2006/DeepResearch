/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 方向 B「研究档案」令牌（docs/web-frontend-redesign-directions.md）。
        // ⚠️ 键名刻意避开 Tailwind 内置色板名 —— 用字符串覆盖内置色板会把整档
        // {50..950} 静默替换掉（见 docs/requirements/11-tailwind-color-token-fix.md，
        // 守卫工具 tools/check_tailwind_tokens.py）。
        paper: '#F4F6F3',
        sheet: '#FFFFFF',
        rule: '#D9DEDA',
        ink: '#171B19',
        'ink-muted': '#5E6862',
        'stamp-blue': '#1E5AD8',
        'stamp-red': '#B5432E',
        'stamp-green': '#2E7D5B',
        'stamp-amber': '#9A6B1F',
      },
    },
  },
  plugins: [],
}
