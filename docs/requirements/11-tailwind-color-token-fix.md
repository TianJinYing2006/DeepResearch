# 需求 11：前端色阶 token 覆盖缺陷修复（cyan 静默失效）

> 状态：**草稿**。本文件是修复需求载体；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 前置事实（2026-09-29 实测）：`tailwindcss` 锁在 v3 —— `package.json` 写 `^3.4.17`，
> `package-lock.json`(v3) 与 `node_modules` 实际均为 **3.4.19**；CI 用 `npm ci`（`ci.yml:171,213`），严格按 lockfile。
> npm dist-tags：`latest=4.3.3`、`v3-lts=3.4.19` ⇒ 3.x 分支已到顶。
> 飞书镜像：待同步（本文件为 bug 修复类需求，命名按本地 `11-tailwind-color-token-fix`）。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 11 |
| 标题 | 前端色阶 token 覆盖缺陷修复（cyan 静默失效） |
| 优先级 | P1 |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | [#56](https://github.com/TianJinYing2006/DeepResearch/issues/56)（GitHub；PR 与 Issue 共用编号空间，故 Issue 号与需求编号不一致，映射以本行为准） |
| 关联 PR |  |
| 关联文档 | `docs/decisions/0010-tailwind-color-token-naming.md`（ADR-0010） |
| 创建 / 更新 | 2026-09-29 |

## 2. 问题背景

### 2.1 现状一句话

> **强调色 `cyan` 在构建产物里完全不存在——15 个类名全部静默失效，而构建、类型检查、E2E 没有一道拦得住。**

### 2.2 发现路径

2026-09-29 前端结构盘点时核对 `dist/assets/index-*.css`：`text-emerald-200`、`.max-w-[1600px]`、
`.animate-shimmer` 均在，唯独 **"cyan" 零出现**，而源码里有 15 处 `*-cyan-*` 用法。

### 2.3 为什么危险（四道防线全漏）

| 防线 | 是否拦住 | 原因 |
|---|---|---|
| `tsc -b` | 否 | 类名是字符串字面量，类型系统看不见 |
| `vite build` | 否 | Tailwind 对无法解析的类不报错，直接不生成 |
| Playwright E2E | 否 | E2E 只认 testid / `article.report-prose` / 中文文案，不认色值 |
| 人眼 | 弱 | 深色底 + 低透明度（`bg-cyan-300/[0.05]`），缺了看不出来 |

## 3. 需求分析

### 3.1 目标

1. 让强调色**按原设计真正渲染出来**（色值不变）；
2. 建立**不会复发**的机制：守卫脚本 + CI 门禁。

### 3.2 量化成功定义

- `dist/assets/*.css` 中可 grep 到新 token 的 200 / 300 / 400 三档类；
- 7 处代码位置的 15 个类名实例 100% 有对应 CSS 规则；
- 新增守卫脚本可复现「失败 → 修复 → 通过」。

## 4. 当前设计

### 4.1 根因

`web/frontend/tailwind.config.js:6-12`：

```js
colors: {
  ink: '#070a0f',
  panel: '#0d121b',
  line: '#202938',
  cyan: '#66e3ff',   // ← 根因
  mint: '#65f0bb',
}
```

Tailwind 内置 `cyan` 是**色阶对象** `{50..950}`。`theme.extend.colors.cyan` 赋值**字符串**会把整个
对象顶掉（是替换而非合并），生成器拿到字符串后只能产出裸 `text-cyan`，
`text-cyan-200` / `bg-cyan-300` / `border-cyan-300` / `from-cyan-400` / `to-cyan-300` 一律无对应规则。

### 4.2 受影响代码位置（7 处 / 15 个类名实例）

| 位置 | 类名实例 |
|---|---|
| `src/App.tsx:705` | `from-cyan-400/20`、`text-cyan-200` |
| `src/App.tsx:836` | `border-cyan-300/20`、`bg-cyan-300/[0.08]`、`text-cyan-200` |
| `src/App.tsx:849` | `border-cyan-300/15`、`bg-cyan-300/[0.05]`、`text-cyan-200`、`bg-cyan-300`（dotClass） |
| `src/App.tsx:859` | `border-cyan-300/15`、`bg-cyan-300/[0.07]`、`text-cyan-200` |
| `src/components/AccountPanel.tsx:657` | `to-cyan-300` |
| `src/components/ProgressBar.tsx:28` | `bg-cyan-300` |
| `src/components/ProgressBar.tsx:62` | `to-cyan-300` |

### 4.3 同类隐患（当前零使用，非 bug）

`mint`、`ink`、`panel`、`line`、`shadow-glow` 在 `src/` 下 grep **无任何使用**（`panel` 的匹配项全是
`AccountPanel` 组件名，`line` 的匹配项全是 `inline-flex` 之类）。属 dead config，不构成渲染缺陷，
但 `mint` 同样是「字符串覆盖内置色板名」写法，修复后应一并清理或改用非冲突命名。

## 5. 优化方案

### 方案 A：保留 `cyan` 名，补成色阶对象

```js
cyan: { 200: '#a5f3fc', 300: '#66e3ff', 400: '#22d3ee' }
```

- 优：只动 config，源码零改动
- 劣：仍覆盖内置 `cyan`；将来有人用 `cyan-500` 会**再次**静默失效

### 方案 B（推荐）：改名，彻底避开内置色板

```js
brand: { 200: '#a5f3fc', 300: '#66e3ff', 400: '#22d3ee' }
```

并把 7 处 15 个 `cyan-*` 类名改为 `brand-*`。

> ⚠️ **命名坑**：不要用 `accent` —— Tailwind 内置 `accentColor` 工具类会从 `colors` 继承，
> 定义 `colors.accent` 会同时生成 `accent-200/300/400` 作为 **accent-color 属性**类，语义打架。
> `brand` 不撞任何内置工具类。

- 优：消除「字符串覆盖内置色板」这一反模式；语义正确（这是品牌强调色，不是 cyan 色阶）
- 劣：要改 7 处源码（3 个文件）

## 6. 设计策略

- **不动 Tailwind 版本**。升级 v4 **不能修复本缺陷** —— v4 的 `cyan` 是 `--color-cyan-*` 命名空间，
  字符串覆盖同样丢档；反而带来 OKLCH 全站视觉漂移 + 浏览器基线抬高（Safari 16.4+/Chrome 111+），
  且与 D-20「呈现层升级不扩展产品边界」冲突。**升级 v4 的理由只能是 v4 本身，不能是本缺陷。**
- **不做视觉改版**。本次只让既有强调色按原设计生效，色值不变（`#66e3ff` 仍是 300 档）。
- **防复发优先于一次性修复**：新增守卫脚本，把这类错误变成 CI 红灯。

## 7. 验收标准（DoD）

- [x] `tailwind.config.js` 中不再存在「键为内置色板名、值为字符串」的写法（改名为 `brand` 色阶对象）
- [x] 7 处 15 个类名全部改为新 token，`grep -rn "cyan-" web/frontend/src` 结果为 0
- [x] `npm run build` 通过（tsc -b + vite build 3.44s），`dist/assets/*.css` 生成 9 个 brand 类名，覆盖 200/300/400 三档（含 3 个透明度变体）
- [x] 新增 `tools/check_tailwind_tokens.py`：判据 A（内置色板被字符串覆盖）+ 判据 B（使用未定义档位），违规即非零退出；已做反向自测
- [x] 该守卫接入 CI `frontend` job（`tsc --noEmit` 之后、`vite build` 之前）
- [ ] Playwright E2E 10/10 仍绿（E2E 不认色值，但需确认无回归）—— **运行中，结果待回填**
- [x] `docs/decisions/0010-tailwind-color-token-naming.md` 记录「禁覆盖内置色板」约定

## 8. 影响范围与风险

| 项 | 说明 |
|---|---|
| 动到文件 | `tailwind.config.js` + 3 个 tsx（7 处）+ 新增 1 个 tools 脚本 + CI yml |
| 视觉变化 | 强调色**由不生效变为生效**：状态 chip、连接态、进度条、Token 卡片将出现青色 —— 这是**修复**不是回归 |
| 回归面 | 低。纯类名字符串替换，不改逻辑；E2E 选择器全部保留 |
| 主要风险 | 人工漏改某处 ⇒ 由「源码 grep 为 0」+「dist CSS grep 到三档」双校验兜住 |

## 9. 测试策略

| 项 | 命令 / 预期 |
|---|---|
| 构建 | `cd web/frontend && npm run build` → 退出码 0 |
| 产物校验 | grep 新 token 三档类名于 `dist/assets/*.css` → 均能命中 |
| 源码校验 | `grep -rn "cyan-" web/frontend/src` → 0 命中 |
| 守卫自测 | 临时改回字符串写法 → 脚本非零退出 → 改回 → 零退出 |
| E2E | `npm run e2e`（`DR_DEMO=1`）→ 10/10 |

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-29 | bug | 需求立项 | 结构盘点发现 cyan 色阶静默失效（7 处 / 15 个类名） | — |
