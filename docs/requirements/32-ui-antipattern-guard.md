# 需求 32：UI 反模式守卫（补上截图基线放弃后的回归防线）

> 状态：**草稿**（随 PR 合入后置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：前端 UI 重构收口——审计自陈「未做浏览器渲染实测」、R7 收口唯一未做项是截图基线，
> 两者合起来意味着前端视觉质量**没有回归防线**。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 32（**占位**，待确认） |
| 标题 | UI 反模式守卫（impeccable 确定性检测器接入 CI） |
| 优先级 | P2（防复发机制；补的是 R1~R7 之后的空白） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | 待确认（用户需给口径：项目里需求号与 issue 号绑定） |
| 关联 PR | 待开（分支已建、未推送） |
| 创建 / 更新 | 2026-10-08 |
| 分支 | `chore/32-ui-antipattern-guard`（**堆叠栈的第 4 层 / 栈顶**，基于 `test/31-mobile-and-fault-regression`） |

> 堆叠说明：本层是栈顶，共享文件（`package.json` / `package-lock.json` /
> `ci.yml` / `.gitignore`）取的是**全量终态**——即第 1 层与第 3 层的增量之上再叠加本层的增量。

## 2. 问题背景

`docs/web-frontend-audit.md` 自陈边界是「**静态源码审计，未做浏览器渲染实测**；
对比度/尺寸为**源码推导值**」；而 R7 收口记录的唯一未做项是**截图基线**
（`docs/web-frontend-redesign-directions.md` 记明理由是「中文字体/渲染跨环境不稳定，另行提案」）。

两者合起来 == 前端视觉质量**没有回归防线**：R1~R7 把 79 项发现改完了，
但**没有任何机器校验阻止新代码把它们带回来**。

## 3. 需求分析

- 需要的是**可复现、零成本、二值判定**的尺子，能在每次 PR 上重跑；
- 期望中的截图基线死在「跨环境中文字体渲染不稳定」——这是一条**像素比对**特有的约束；
- 因此判据应当**不碰像素**（判定代码与渲染属性），从而绕开当初放弃截图基线的那个理由；
- 侧信道要求：零 LLM、零 API key（与项目既有门禁口径一致：
  `ci.yml` 开头即声明「自动轨仅离线单测，零 LLM 调用、零 API key、零外部服务」）。

## 4. 当前设计（代码位置）

| 位置 | 现状 |
|---|---|
| `tools/` | 4 个守卫：`check_results_whitelist.py`（152 行）、`check_frontend_boundary.py`（53 行）、`check_tailwind_tokens.py`（123 行）、`check_md_tables.py`（69 行） |
| `tests/test_arm7_artifact_governance.py` | 守卫单测范式：`importlib.util.spec_from_file_location` 按路径加载 `tools/` 下的守卫（`tools/` 不是包） |
| `.github/workflows/ci.yml` | `frontend` job：`npm ci` → `tsc --noEmit` → Tailwind token 守卫 → `vite build` |
| `web/frontend/` | 无任何视觉质量检测手段 |

## 5. 优化方案

**（1）`tools/check_ui_patterns.py`（新增，170 行）**

调用 impeccable（`pbakaus/impeccable`，Apache-2.0）的 `detect` 子命令 ——
**59 条确定性规则，不需要 LLM、不需要 API key**。

- CLI 解析顺序：`web/frontend/node_modules/impeccable/cli/bin/cli.js` →
  `.bin` 垫片 → PATH 上的 `impeccable`。用 `node <cli.js>` 直调而不是 `.bin` 垫片，
  因为后者在 Windows 上是 `.cmd` / `.ps1`，跨平台 `subprocess` 调用不可移植；
- 扫描目标：`web/frontend/src`（与 `check_tailwind_tokens.py` / `check_frontend_boundary.py` 同口径）；
- 输出解析：`detect --json` → 数组，逐条取 `antipattern` / `name` / `severity` / `file` / `line` / `snippet`；
  路径收敛为**仓库相对的正斜杠**（CI 在 Linux、本机在 Windows，绝对路径不可比）。

**三类判据，任一即违规**：

| # | 判据 | 说明 |
|---|---|---|
| A | 检测器报告任何**主要发现** | 逐条打印 `文件:行 [规则/严重度] 名称 — 命中 \`片段\`` |
| B | 检测器**跑不起来**（没安装 / 找不到 node / 执行失败） | **绝不放行** |
| C | 输出**不是**合法 JSON 数组 | 防止工具改版后静默变成空转 |

**（2）`tests/test_ui_pattern_guard.py`（新增，216 行）**

按项目既有范式（`importlib` 按路径加载 + `tmp_path`/`monkeypatch`），重心是
**证明守卫抓得住违规**，而不是只测 happy path：

- 假 CLI 用 `[sys.executable, <临时脚本>]` 注入（跨平台一致、不需要 chmod、也不需要真装检测器）；
- 覆盖三类判据各自的用例，含「工具缺失」「命令不存在（`OSError`）」「输出为空 / 非 JSON / 非数组」
  「畸形条目缺字段仍须出违规」；
- 「活体校验」用例 `test_real_repository_is_clean_when_tool_present` 在检测器缺席时 **skip 而非 fail**
  （pytest 跑在 `lint-and-test` job，那里没有 `npm ci`；真正的门禁在 `frontend` job）；
- `test_guard_is_executable_and_exits_nonzero_on_failure` 断言守卫**不会以「静默成功且无输出」的形态通过**。

**（3）误报治理：走检测器自带机制**

```bash
npx impeccable ignores add-value <rule> <value> --file <glob> --reason "<理由>"
```

写入 `.impeccable/config.json`（共享）或 `.impeccable/config.local.json`（本地）。
**不在守卫里维护第二份豁免名单** —— 那会变成第二个真相源。

**（4）接线**

- `impeccable` 入 `web/frontend` devDependencies（唯一用它的地方）；
- `.github/workflows/ci.yml` 的 `frontend` job 在 Tailwind 守卫之后新增
  `Guard: UI 反模式（impeccable detect）`（位置在 `npm ci` 之后、`build` 之前）；
- `.gitignore`：忽略 `.impeccable/config.local.json`（本地作用域，设计上不入库），
  同时登记 `.dsh/skills/`（DSH 本地技能根，与 `.opencode/skills/` 同源的手工副本，
  上游部分仓库未声明 LICENSE ⇒ 只在本机使用）。

## 6. 设计策略

- **「工具缺失绝不放行」是刻意的**：一个「检测器没装就 exit 0」的守卫，会在 CI 配置漂移时
  变成**永远绿的摆设**。项目已有的教训写在 `tools/check_results_whitelist.py` 的文档串里 ——
  单点裁判必须经变异测试验证**非空转**。因此 B/C 两类判据与 A 同等对待；
- **判据以 JSON 内容为准，而非退出码**：实测 impeccable `detect` 的退出码语义是
  `0` = 干净、`2` = 有发现、`1` = 扫描失败；但工具改版可能改语义，
  故以内容为准；只对「非零退出且 stdout 全空」单独判定为**执行失败**
  （否则排障时会去查 JSON 解析，而真因在 stderr —— 实测假 CLI 崩溃即此形态）；
- **不重复既有能力**：`docs/web-frontend-audit.md` 记的 `frontend-design-audit`（LLM 判断型）
  产出的是**一次性**的 79 项发现报告，无法在每次 PR 上重跑；
  本守卫补的是**确定性、可重跑**的那一半，二者互补不替代；
- **这是截图基线的替代品，不是补充**：确定性规则判定代码与渲染属性、**根本不碰像素**
  ⇒ 当初放弃截图基线的理由（跨环境中文字体渲染不稳定）对它不成立。

## 7. 验收标准（DoD）

- [x] `tools/check_ui_patterns.py` 落地，三类判据（发现 / 工具不可用 / 输出变形）齐备
- [x] `tests/test_ui_pattern_guard.py` 落地，**17 passed**（重心在证明抓得住违规，含变异类用例）
- [x] `.impeccable/config.json` 建立；误报治理走检测器自带机制，守卫内不维护第二份名单
- [x] `impeccable` 入 devDependencies；`package-lock.json` 同步（锁与 `package.json` 一致，无残留）
- [x] `ci.yml` `frontend` job 新增守卫步骤（位于 `npm ci` 之后、`build` 之前）
- [x] `.gitignore` 增 `.impeccable/config.local.json` 与 `.dsh/skills/`
- [x] **守卫建成即抓到一条真违规**：`web/frontend/src/index.css:187` 的
      `.report-prose blockquote` 命中 `side-tab`（`border-l-2`），已登记豁免（见 §8）
- [x] 守卫在本仓库 exit 0；`ruff check .` All checks passed
- [x] `tsc --noEmit` = 0；`vite build` = 0；`vitest run` = 25 passed（本层未改单测）
- [x] E2E 全量 **63 passed**（栈顶、对全新构建的 dist）
- [ ] CI 全量**真跑**零回归 —— 未推送，CI 未触发
- [ ] 推送分支并开 PR，回填 PR 号
- [ ] 全量 pytest 套件重跑（本轮只跑了本守卫的单测 17 passed）
- [ ] 正式需求编号与关联 Issue 确认（现用 32 占位）

## 8. 影响范围与风险

**模块**：`tools/check_ui_patterns.py`（新）、`tests/test_ui_pattern_guard.py`（新）、
`.impeccable/config.json`（新）、`web/frontend/package.json`、`web/frontend/package-lock.json`、
`.github/workflows/ci.yml`、`.gitignore`。

### 建成即抓到的第一条真违规（已登记豁免，可一行回退）

```
web/frontend/src/index.css:187  [side-tab/warning] Side-tab accent border — 命中 border-l-2
```

该行实际是 `.report-prose blockquote`，`border-l-2 border-stamp-blue/50` 是排版上经典的
**引文左边线**，而规则原文写的是「border on one side of a **card**」。判定为**误报**，
按「blockquote 左侧引文线属文档排版、非卡片装饰」登记进 `.impeccable/config.json`。

⚠️ 该豁免是**一行命令可回退**的设计判断（`impeccable ignores remove-value side-tab "*"`），
不是技术结论 —— 若认为应改样式而非豁免，删掉这条即可。

值得注意的是：**59 条规则扫完整个前端只命中 1 条**，这本身是对 R1~R7 重构的强背书。

### 其他风险

- **工具链依赖**：守卫依赖 `web/frontend/node_modules` 中的检测器。
  因此它只能放在 `frontend` job（那里先跑 `npm ci`），**不能**放进 `lint-and-test` job；
  本守卫的单测相应地做了「检测器缺席时 skip」处理；
- **检测器为外部开源工具**（Apache-2.0，npm 包 `impeccable@4.1.0`，仓库 `pbakaus/impeccable`）。
  规则集可能随版本变化 ⇒ 升级时需重跑并复核豁免清单是否仍然需要；
- **本地环境的 npm 缓存位置**：沙箱受限时 `npm` 默认缓存目录不可写会报 `EPERM`，
  需 `--cache` 指向可写位置。**CI 无此问题**（无沙箱），故未写进脚本，仅此处登记；
- **扫描范围**是 `web/frontend/src`：`index.html` 与后端托管的静态资源不在内。

## 9. 测试策略

- **守卫单测**（`tests/test_ui_pattern_guard.py`，17 条）：以「守卫会不会漏」为重心——
  A 类（发现必须被报出、文件:行/规则/片段齐全、空结果才可通过、判据以内容为准而非退出码）、
  B 类（检测器缺失必须违规而非放行、CLI 崩溃、命令不存在）、
  C 类（输出为空 / 非 JSON / 非数组）、纯函数（路径收敛、畸形条目）；
- **活体校验**：`test_real_repository_is_clean_when_tool_present` 在检测器在位时断言本仓库零发现，
  缺席时 skip；
- **CI**：`frontend` job 的守卫步骤；零 LLM、零 API key、零外部服务；
- **误报回归**：由 `.impeccable/config.json` 承担，改动用 `impeccable ignores` 管理，
  不写进本守卫的测试。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-08 | 新增 | 审计自陈未做渲染实测 + R7 放弃截图基线 ⇒ 视觉质量无回归防线 | impeccable 确定性检测器接入为第 5 个守卫 + 17 条单测 + CI 步骤；建成即抓到 1 条真违规并登记豁免 | 待开（本地 commit `0affd6d`，分支 `chore/32-ui-antipattern-guard`） |
