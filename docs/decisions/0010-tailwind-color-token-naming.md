# ADR-0010：禁止用字符串覆盖 Tailwind 内置色板名

## 基本信息

- **编号**：0010
- **标题**：禁止用字符串覆盖 Tailwind 内置色板名；品牌强调色改用 `brand-*`
- **日期**：2026-09-29
- **状态**：已采纳
- **涉及模块**：`web/frontend/tailwind.config.js`、`web/frontend/src/`（3 个组件）、`tools/check_tailwind_tokens.py`、`.github/workflows/ci.yml`

## 背景

2026-09-29 前端结构盘点时核对构建产物 `dist/assets/index-*.css`，发现：
`text-emerald-200`、`.max-w-[1600px]`、`.animate-shimmer` 均在，唯独 **"cyan" 零出现**，
而源码里有 7 处代码位置、15 个 `*-cyan-*` 类名实例。

根因在 `web/frontend/tailwind.config.js`：

```js
theme: { extend: { colors: { cyan: '#66e3ff', ... } } }
```

Tailwind 内置 `cyan` 是 `{50..950}` **色阶对象**。`extend.colors.cyan` 赋值**字符串**会
把整个对象**替换**掉（不是合并），生成器拿到字符串后只能产出裸 `text-cyan`，
`text-cyan-200` / `bg-cyan-300` / `border-cyan-300` / `from-cyan-400` / `to-cyan-300`
一律无对应 CSS 规则。

## 问题 / 动机

这是**静默失效**，四道防线全部漏掉：

| 防线 | 是否拦住 | 原因 |
|---|---|---|
| `tsc -b` | 否 | 类名是字符串字面量，类型系统看不见 |
| `vite build` | 否 | 无法解析的类直接不生成，不报错 |
| Playwright E2E | 否 | E2E 只认 testid / 中文文案 / `article.report-prose`，不认色值 |
| 人眼 | 弱 | 深色底 + 低透明度（`bg-cyan-300/[0.05]`），缺了看不出来 |

缺陷能长期存活，说明**机制上缺一道闸**，而不是某次手误。因此本 ADR 的重点不是
改对这一次，而是让这类错误以后变成 CI 红灯。

## 方案

1. **改名**：`cyan: '#66e3ff'` → `brand: { 200: '#a5f3fc', 300: '#66e3ff', 400: '#22d3ee' }`，
   并把 7 处 15 个 `cyan-*` 类名改为 `brand-*`。
   - ⚠️ 未采用 `accent` 作为名字：Tailwind 内置 `accentColor` 工具类会从 `colors` 继承，
     定义 `colors.accent` 会同时生成 `accent-200/300/400` 作为 **accent-color 属性**类，语义打架。
2. **守卫**：新增 `tools/check_tailwind_tokens.py`，两类判据 ——
   - A：`theme.extend.colors` 中键名为内置色板名且值为字符串 ⇒ 违规；
   - B：源码使用自定义色阶的档位（如 `brand-500`）未在 config 中定义 ⇒ 违规。
3. **接 CI**：`frontend` job 在 `tsc --noEmit` 之后、`vite build` 之前执行该守卫。

**不动 Tailwind 版本**（锁 v3；`package.json` `^3.4.17` → lockfile 与 `node_modules` 实际 3.4.19，
npm `v3-lts=3.4.19` 已到顶）。**不做视觉改版**，色值不变（`#66e3ff` 仍是 300 档）。

## 理由与取舍

- **为什么改名而不是补色阶对象**：补成 `cyan: { 200:…, 300:…, 400:… }` 改动更小（只动 config），
  但仍在覆盖内置色板，将来有人用 `cyan-500` 会**再次**静默失效。改名彻底消除这一反模式，
  且语义更正确——这是品牌强调色，不是 cyan 色阶。代价是要改 7 处源码。
- **为什么升级 v4 不在选项内**：v4 的 `cyan` 是 `--color-cyan-*` 命名空间，字符串覆盖**同样丢档**，
  即升级治不了本缺陷；反而带来 OKLCH 全站视觉漂移 + 浏览器基线抬高（Safari 16.4+/Chrome 111+），
  且与 D-20「呈现层升级不扩展产品边界」冲突。升级 v4 的理由只能是 v4 本身。
- **为什么用正则解析 config 而不引 JS 解析器**：config 结构固定，零依赖、可离线跑，
  与既有 `tools/check_frontend_boundary.py` 同风格。

## 影响

- **视觉**：强调色**由不生效变为生效**。状态 chip、连接态、进度条、Token 卡片将出现青色
  （`#66e3ff` 系）。这是修复，不是回归。
- **回归面**：低。纯类名字符串替换，不改逻辑；E2E 选择器（testid / 中文文案）全部保留。
- **同步项**：本文档 + 需求 11 文档 + 守卫脚本 + CI 配置。

## 验证

| 项 | 结果 |
|---|---|
| `npm run build` | 通过（tsc -b + vite build，3.44s） |
| 源码 `grep -rn "cyan-" src/` | 0 |
| 产物 `dist/assets/*.css` | 9 个 brand 类名全部生成：`.bg-brand-300`、`.bg-brand-300\/\[0\.05\]`、`\/\[0\.07\]`、`\/\[0\.08\]`、`.border-brand-300\/15`、`\/20`、`.from-brand-400\/20`、`.text-brand-200`、`.to-brand-300` |
| 守卫正向 | exit 0（`brand` 档位齐全） |
| 守卫反向自测 | 判据 A：改回 `cyan: '#66e3ff'` ⇒ exit 1；判据 B：注入 `brand-500` ⇒ exit 1 |

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-09-29 | 初始记录：修复 cyan 静默失效，改名 brand，新增守卫与 CI 门禁 |
