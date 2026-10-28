# DeepResearch 项目约束（CONTRIBUTING）

> **版本**：v2.0（2026-10-26 全面修订；上一版 v1.1 2026-10-11）
> **效力**：本文是项目**硬约束**。所有开发（人类、AI 助手、脚本、临时提交）一律适用；违反即视为缺陷，需按 §15 纠偏。
> **冲突处理**：**规则**以本文为准；**实时状态**以 `docs/project-status.md` 为准（D-01）；**决策依据**以 `docs/decisions/` ADR 为准；**排期**以 `docs/roadmap.md` 为准；**对外数字**以 `docs/figures.md` 为准。
> **适用范围**：DeepResearch / 爱考据 iKaoju 全仓库（含 `research_engine/`、`web/`、`migrations/`、`docs/`、CI 与部署配置）。
> **变更方式**：本文属于治理文档，修改须走 `docs/<issue>-constraints` 分支 + PR + 主理人确认，并更新 §17 变更记录。

---

## 0. 文档体系：同一事实只允许一个权威源

| 文档 | 职责 | 不承担 |
| --- | --- | --- |
| 本文 `CONTRIBUTING.md` | 规则、流程、门禁、红线 | 实时状态、排期、设计细节 |
| `docs/project-status.md` | **实时状态看板**（D-01） | 设计要求、排期 |
| `docs/roadmap.md` | 批次排期与依赖 | 状态勾选（引用看板） |
| `docs/requirements/*.md` | 需求设计、DoD、变更记录 | 实时状态 |
| `docs/decisions/*.md` | 决策背景、方案、取舍（ADR） | 实施进度 |
| `docs/operations/*.md` | 部署、发布、回滚、恢复手册 | 需求设计 |
| `docs/figures.md`（2026-10-26 建立） | **对外数字唯一表**（含采集命令与日期） | 内部指标解释 |
| `docs/action-plan-*.md` | 一次性行动提案 | 排期与状态 |

- 同一数字/结论只允许在一个文档里“定义”，其它处**引用 + 标注日期**；禁止复制出第二份会漂移的副本。
- 过期内容必须就地标记 `⚠️ 已被 <新文档/决策> 取代（日期）`，禁止静默保留旧口径。
- README / 简历 / 面试材料只引用 `figures.md`，不得自行维护数字。

---

## 1. 核心原则（十条）

1. **先文档后代码**：B/C 类改动先有 Issue + 需求文档；A 类小改至少先在 PR 里说明。
2. **一需求一分支一 PR**：从 `dev` 拉分支，squash 合回 `dev`；**禁止直推 `dev` / `master`**。
3. **可逆优先**：破坏性、外部、删除类操作先 review 再执行；git 操作保持可回滚。
4. **诚实基线**：数字必须带来源与日期；失败不粉饰；统计上不可判定就写“不可判定”。
5. **冻结产物只读**：`research_engine/eval/results/` 历史产物不删除、不移动、不回填（D-16 / D-18）。
6. **人审 AI**：AI 助手不自动合并、不替主理人做产品/合规决策；PR 前必须人工确认。
7. **最小改动**：一次只解决一个问题；顺手重构必须拆成独立 PR。
8. **默认关闭**：新功能/新协议默认关，开启是显式运营决策；关闭态零漂移。
9. **可归因**：任何失败路径必须有结构化 `failure_reason` / 错误码，不得静默吞异常或伪装成功。
10. **一处变更、多处同步**：需求 / ADR / 看板 / 路线图 / 数字表相关处同步更新。

---

## 2. 当前阶段与产品边界

### 2.1 当前阶段（2026-10）

**L3-A/B 开放注册内测**：个人主体、邮箱开放注册、单机 compose；staging 已部署；域名 `ikaoju.cn` 已备案并上线（ICP 备案号见页脚）。
**当前不是公开产品**：无对外付费、无 SLA 承诺；注册开放但规模与成本受配额、月度预算与运营能力约束。
**开放注册的风险控制（2026-10-27 登记）**：已有注册总开关、每日注册上限、一次性邮箱域名黑名单；**邮箱验证与 CAPTCHA 尚未上线**，G7 合规未落档前不得公开推广、投放或收费；转「完整产品」前必须关闭该风险（见 ADR-0018 与 ADR-0017 §4）。

### 2.2 当前允许的产品面

研究主链路（规划/多跳检索/critic/写作/校验/修复/渲染）、证据链（快照/引文/定位/分级）、RAG 多租户、账号与会话（密码/TOTP/Passkey）、配额与限流、内容安全与申诉、报告分享、导出（md/json/pdf/docx）、HITL（澄清/计划审批/追问）、MCP 双向、可观测（OTel/Prometheus/Sentry）、备份与恢复、管理 CLI。

### 2.3 当前禁止（未拍板前一律不做）

- 未经 §2.1 口径批准的注册方式变更、对外付费与计费、对外 SLA/赔付承诺；
- 对外多租户或开放 API 商业化、第三方平台分发；
- 未成年人相关场景、跨境数据传输、境外服务默认开启；
- 移动端/桌面客户端、K8s/多区域/自动扩缩容；
- 在 G7 合规未落档前进行公开推广、投放、对外收费或宣布「完整产品」。

### 2.4 向“完整产品”演进

主理人已明确：**可以在需求完善之后转为完整产品**。转段必须同时满足：

1. 需求侧：目标用户、核心场景、付费/成本模型写成需求文档并冻结；
2. 合规侧：ICP / 公安联网 / 生成式 AI 手续书面确认并落档（G7）；
3. 质量侧：固定证据池上的确定性指标达标 + 5~10 人内测判据通过；
4. 安全侧：P0/P1 安全问题闭环（反代信任、MFA 限流、安全头、备份加密、脱敏）；
5. 治理侧：新增 ADR 重划边界，主理人书面拍板。

### 2.5 与历史决策的关系

- ADR-0001「不做完整产品」是 2026-08 的历史定位；L3 已实质扩展产品面。**边界以本文 §2 为准**。
- ADR-0009（L3 架构）已于 **2026-10-26 追认转正**；产品边界由 **ADR-0017** 重划，ADR-0001 §1.1 的旧边界条款已被取代（前端硬边界①继续有效）。
- **ADR-0017 已落地；任何越过 §2.3（禁止面）的功能一律不做；进入 §2.4 需重新拍板并新增 ADR**。

---

## 3. 开发流程

### 3.1 分支模型

```
master   保护主干，只收批次/里程碑合并，禁止直推
  ▲ 批次收口：merge dev + tag vX.Y
dev      集成分支，所有 PR 的目标，禁止直推
  ▲ squash merge（PR 前先 rebase dev 解冲突）
feature/<issue>-<slug> | fix/<issue>-<slug> | docs/<slug> | chore/<slug>
```

- 命名示例：`feature/188-evidence-chain`、`fix/257-passkey-stepup`、`docs/0101-constraints-v2`。
- 合并方式：squash 合 `dev`；合后删分支；需求文档状态置「已合」。
- **发布节奏**：每批次（约 2~4 周）或一个里程碑收口一次 —— `dev → master`、annotated tag `v0.x`、CHANGELOG、回滚演练登记（§9.6）。

### 3.2 Issue

- 每个需求/优化/bug = 1 个 Issue，编号与需求文档编号一致。
- Issue 必含：需求文档链接、目标摘要、DoD 勾选项、风险与影响面。

### 3.3 PR

- 标题：`<type>(<scope>): <简述>`，type ∈ `feat | fix | refactor | docs | test | chore | perf | build | ci`。
- 描述必含 6 项：① 关联需求文档 + `Closes #<issue>`；② 改动摘要（模块/文件）；③ 自测命令与真实输出；④ DoD 逐条勾选；⑤ 风险与影响范围；⑥ 回滚方式（如适用）。
- 门禁：CI 全绿（§6.1）+ 确定性回归门禁（§6.2）+ 人工确认。
- 涉及可观测的改动附 trace 截图；涉及 UI 的附窄屏/桌面截图或 e2e 证据。

### 3.4 AI 执行 pre-flight（每次动手前核对）

1. 当前在 `feature|fix|docs|chore/<...>` 分支，不是 `dev` / `master`；
2. 有对应 Issue + 需求文档（B/C 类必须有）；
3. 改动范围与需求文档「当前设计/优化方案」匹配；
4. 明确本次**不做什么**，以及如何验证；
5. PR 前停下等主理人确认，**不自动合并**。

### 3.5 提交信息

- Conventional Commits，中文摘要，body 写“动机 / 改了什么 / 怎么验证 / 风险”。
- 禁止把多个不相关改动塞进一个 commit；禁止 `--no-verify` 跳过钩子。

---

## 4. 需求文档规范

- 位置：`docs/requirements/<编号>-<slug>.md`；模板 `docs/requirements/模板.md`（10 字段：元信息 / 问题背景 / 需求分析 / 当前设计 / 优化方案 / 设计策略 / 验收标准 / 影响范围与风险 / 测试策略 / 变更记录）。
- 状态流转：**草稿 → 进行中 → 自测 → 待合 → 已合 → 已废弃**。
- B/C 类改动**必须**有需求文档；A 类（文档/配置/测试）可免，但 PR 描述须说明理由。
- 需求文档必须引用代码位置（`file:line`）、给出可勾选 DoD、写明回归面与降级策略。
- **飞书同步（2026-10-26 降级）**：日常需求只维护仓库；仅在**重大决策 / 里程碑 / 对外发布**时同步飞书（文件夹 token `MYR6fazL5la0ardJdUecOBkVnd8`）。飞书只放架构/方案/结论，**不放密钥、不放真实 IP**。

---

## 5. ADR 规范

### 5.1 何时必须写

- 架构级改动（模块拆分、状态机/图拓扑、存储选型、部署架构）；
- 线上缺陷修复（P0/P1 事故、数据损坏、安全事件）；
- 改判定口径 / 指标口径 / 评分器版本；
- 产品边界、阶段或对外承诺变化；
- 删除公共模块、公共 API、公共配置项。

### 5.2 何时可免

局部实现细节、文案/样式、补充测试、默认值微调（PR 说明即可）。

### 5.3 规则

- 编号**唯一、递增**，命名 `NNNN-slug.md`；发现重号必须以“后到者改号”方式修正，并在索引标注。
- 索引 `docs/decisions/README.md` **必须同步更新**（编号、标题、状态、日期）；状态 ∈ 草稿 / 已采纳 / 已取代 / 已废弃；被取代必须交叉链接。
- 模板 `docs/decisions/TEMPLATE.md` 七段：基本信息 / 背景 / 问题动机 / 方案 / 理由与取舍 / 影响 / 变更记录。
- 必须写“放弃的方案 + 代价 + 成本账”；不得只写结论。
- 与需求文档互链；同一决策不得在 ADR 与需求文档各写一份互相漂移的版本。

---

## 6. 质量与测试约束

### 6.1 硬门禁（CI 阻塞，缺一不可）

| 门禁 | 内容 | 执行 |
| --- | --- | --- |
| Lint | `ruff check .`（E/F/I/UP，本地豁免见 `pyproject.toml`） | `lint-and-test` |
| 单测 | `pytest tests/ -q` 零依赖 lane | 3.11 / 3.12 / 3.13 矩阵 |
| 集成 | 真实 PostgreSQL + Redis：RunStore/Worker/保留期/连接池/通知/告警/申诉/快照/检查点/恢复/HITL/导出 | `infra` |
| 迁移 | compose 校验 + 迁移幂等（第二次 `applied=0`）+ `migrations/checks/*.sql` 结构断言 | `infra` |
| 恢复 | 备份 → 清库 → 恢复 → 数据相等 + backup-verify 新鲜/过期双路径 | `infra` |
| 前端 | `npm ci` + `tsc --noEmit` + `vitest run` + `vite build` | `frontend` |
| 守卫 | `check_results_whitelist.py` / `check_frontend_boundary.py` / `check_public_ip.py` / `check_doc_consistency.py` / `check_tailwind_tokens.py` / `check_ui_patterns.py` | CI |
| 浏览器 | Playwright E2E（desktop + mobile project） | `e2e` |
| 镜像 | `docker build -f Dockerfile.api` | `infra` |

> 注：`tests/test_doc_consistency.py` 需要完整 tag 历史；`git clone --depth 1` 的本机克隆会失败并提示 `fetch-depth: 0`，先 `git fetch --unshallow --tags` 再跑（CI 已固定 `fetch-depth: 0`）。

### 6.2 确定性回归门禁（eval 固定证据池，同口径才比较）

改动若触及检索/证据/校验/写作路径，必须对**固定证据池**复算（`--eval-only`，零图成本）并比较以下**确定性指标**；任一下降 > 1pp 即阻断：

- `existence_rate`（引用存在率）；
- `quote_located_rate`（逐字引文可定位率，L0~L2）；
- `retrieval_hit_rate`（检索命中率）。

“同口径” = 同数据集 / 同模型 / 同开关 / 同 `SCORER_VERSION`。不同口径只记录，不比较。

### 6.3 观测项（不阻塞，但必须落盘）

`coverage`、`citation_accuracy`（LLM judge）、`insufficient_ratio`、`steps`、单轮成本、端到端耗时 p50/p90。

### 6.4 统计纪律

- 样本量不足以达到 MDE 时，**不得**宣称质量提升或下降；只允许陈述“点估计 + 不可判定”。
- LLM judge 指标必须标注裁判模型、温度、是否独立于主链路；同族模型不得冒充独立裁判。
- 对外数字只引用 `figures.md`，且必须写明口径与日期。

### 6.5 测试纪律（禁止“假绿”）

- 测试必须断言行为与契约，不得只 mock 关键路径后断言 mock 被调用。
- E2E 打的是 `dist/` 构建产物：改前端后必须**先 build 再跑**，否则是假绿。
- 视觉基线只在 CI Linux（Ubuntu + 官方 Chromium + Noto CJK）生成；本地非 Linux 平台跳过并如实标注。
- 零依赖 lane 必须不受本机 `.env` 影响：`conftest` 需同时清空 `DR_DATABASE_URL` / `DR_REDIS_URL` / `QDRANT_URL` / **`DR_TEST_DATABASE_URL` / `DR_TEST_REDIS_URL`**（当前缺后两项，属待修缺陷）。
- 新增 `FailureReason` 枚举值必须同步更新硬断言（`tests/test_arm1_run_status.py` 与 `tests/test_arm3_python_version_gate.py`），否则 CI 红。
- 取消/超时不得走 `_recover_from_exception`（会污染故障可归因率）；超时走 `RUN_FINISHED + stop_reason=timeout`。
- 修复 bug 必须先有能复现的失败用例（或明确记录“无法复现”的原因）。

### 6.6 覆盖率、类型与依赖

- 覆盖率：先接入观测（PR 可见变化，待接入），暂不设阻断门槛；设门槛需 ADR。
- 类型检查：渐进式 mypy（新代码 + 核心模块白名单，待接入），不做全仓一次性迁移。
- 依赖：`pip-audit` 进 CI（待接入；首期只报告不阻塞，高危项必须有处置记录）；新增依赖“外科式”——最小集、写理由、`pip check`、更新 lock。

---

## 7. 评测纪律

- **成本敏感**：真实 API 评测走 `eval-manual.yml` 手动触发（当前 20 题约 ¥0.5 / 40~60 分钟 + Qdrant）；不得进自动 CI。
- **两阶段管道**：`--run-only` 存 raw / `--eval-only` 零成本复算；评测器与主链路解耦。
- **产物治理**：白名单判据 = “引用即入库”（D-10/D-13）；`tools/check_results_whitelist.py` 必须通过；不得手动删除/移动/改名历史产物（D-16）。
- **冻结与不回填**：after 基线以 3 轮结案、不续跑（D-18）；历史 raw/eval 只读，禁止回填新字段（D-07/D-14）。
- **口径断点**：改指标定义/评分器必须 bump `SCORER_VERSION` 并登记断点，跨断点不得直接比较。
- **新实验前置**：若改动会改变证据面，立项时**先拍板是否需要 before 基线**（改动前采集，错过不可逆）。
- 早期评测器 `eval/retrieval_eval.py`、`citation_eval.py`、`report_eval.py` 未被主链路调用，不得作为当前口径依据；去留需需求文档 + PR。

---

## 8. 安全与合规

### 8.1 密钥与凭据

- 只允许存在于 `.env`（本地）与 GitHub Secrets；**不进仓库、不进飞书、不进日志、不进截图、不进 Issue/PR 正文**。
- 泄露即轮换（API key / 令牌 / 部署密钥），并在 `docs/operations/` 留痕；不得“先观察”。
- 备份私钥/口令离线保管，只从环境变量注入，禁止写入命令行或脚本。

### 8.2 公开仓脱敏（2026-10-26 新增，重点）

- **tracked 文件零真实主机 IP / 内网主机名 / SSH 目标 / 凭据 / 端口映射**；部署实参用占位符或 GitHub Secrets/变量。
  已公开的品牌域名（如 `ikaoju.cn`）不属敏感项，但**不得与源站 IP 同时出现**。
- **本地部署实参允许保存**（方便开发），但只能放 `local-artifacts/`、`.env`、`.workbuddy/` 等已忽略路径；**不得提交**。
- 已知存量违规（待收口）：`.github/workflows/deploy-staging.yml`、`docs/operations/release-runbook.md`、`docs/project-status.md`、`docs/requirements/37-*.md`、`tools/deploy.sh` 含真实 IP。
- 处置：tracked 文件脱敏 + 部署 workflow 改用 `secrets.STAGING_HOST` + 新增 `tools/check_public_ip.py` CI 守卫 + 安全组最小放行；历史 commit 默认不重写，凭据泄露时才评估重写与轮换。
- 本约束同样适用于截图、PPT、博客与对外材料。

### 8.3 应用安全

- 认证/会话/CSRF/MFA/Passkey 改动必须过安全用例（限流、爆破、重认证、会话固定）。
- 不改弱默认：`DR_ENV=staging|production` 的 fail-fast 校验不得绕过。
- 输出 gate（审核/脱敏）与导出、分享、历史回放同口径；新增格式必须复用同一装配。
- 多租户隔离必须“下推过滤 + 后置兜底”双层；新增查询不得只靠调用方自觉。
- SSRF/上传/注入防线（`safe_fetch`、magic bytes、不可见 Unicode 剥离）不得为便利关闭。

### 8.4 数据与合规

- PII 最小化；日志/事件/导出不落原始密钥与完整正文（除经审核的导出通道）。
- 保留期、注销、申诉 SLA、审计日志 append-only 不得绕过。
- G7 未落档前：不公开推广、不投放、不承诺 SLA；开放注册按 §2.1 的风险控制口径执行，新增注册面须先登记风险。
- 数据流向登记表（`production-readiness.md` §4）新增外部服务必须同步登记。

---

## 9. 运维与发布

1. **CD 事实**：合入 `dev` 且 CI 绿 ⇒ 自动部署 staging（能合 dev 即具备部署能力，故 dev 上禁止未审代码）。
2. **部署通道**：GitHub Actions → 服务器 forced command（仅允许 `/opt/deepresearch/tools/deploy.sh`；no-pty / no-port-forwarding）；host key 固定防 MITM；禁止在服务器手改代码。
3. **发布前**（T-0）：CI 6/6 绿 → 迁移人工评审 → 新鲜备份 + 可读校验（失败即中止）→ 配置 diff（CORS/预算/并发/限流/境外开关/webhook）→ 容量检查（连接预算）。
4. **迁移**：只前向、不写回滚脚本；新结构必须兼容旧代码（旧版本忽略新表）；`migrations/checks/*.sql` 必须同步。
5. **发布后**：5 分钟冒烟（健康/登录/提交/尾随/取消/导出）+ 15~30 分钟观察（5xx、worker 心跳、备份、告警）+ 发布记录。
6. **回滚**：应用回滚镜像（DB 不动）；数据级回退走备份恢复；每批次至少演练一次并登记 runbook §9；镜像按 git SHA 固定，禁止 `latest` 覆盖。
7. **备份**：每日 cron + 异地（COS）+ 加密 + 定期恢复演练（至少每季度一次，记录断言结果）。
8. **运维实参**（IP/域名/口令）按 §8.2 只存本地，不入 git。

---

## 10. 仓库与产物边界

- 仓库只放：产品代码、`docs/`、迁移、必要配置、lock 文件、产品资源（小体积）。
- **素材与个人材料迁出**：`icon-lab/`（图标设计）、`ppt-report/`（汇报材料）不再作为产品内容；`面试准备/`、`taste-skill/`、`local-artifacts/` 等不得入库（未跟踪的加入 `.gitignore`）。存量迁移走一次性 ADR，**不重写历史**。
- 评测产物：沿用白名单机制；新增产物必须在 `.gitignore` 登记**紧邻独立注释行**的出处，并跑 `check_results_whitelist.py`；禁止手动删/移/改名。
- 禁止入库：`node_modules/`、`dist/`、`test-results/`、`.env`、`__pycache__`、日志、`reports/` 原始压测产物、`.tmp/`。
- 单文件 > 1MB 的二进制需 PR 说明理由；截图优先压缩或放 `docs/` 下的必要位置。
- 迁移到 LFS/外置存储需 ADR（受 D-16 约束）。

---

## 11. 本机环境铁律

- Python **3.11 ~ 3.13**；不在区间会在 import 时直接报错（运行时闸）。使用项目专属 venv，按 lock 安装。
- 改 `.py` ⇒ 必须 `ruff check .`；测试必须用项目 venv + 干净 `--basetemp`。
- 零依赖测试：`DR_DATABASE_URL=` / `DR_REDIS_URL=` 置空；集成 lane 用 `DR_TEST_DATABASE_URL` / `DR_TEST_REDIS_URL` 显式 opt-in。
- E2E 必须显式 `DR_PYTHON=<项目 venv python>`，且先 build `dist/`。
- git：**禁用 `git stash` / `rebase` / force push**；本机存在 ref 静默不落盘的坑，必要时用 `git add` + `write-tree` + `commit-tree` + 直接写 `.git/refs/heads/<branch>` 的 workaround，**绝不因此退回直推 dev**。
- 网络：pip/npm 访问需清代理；git 推送走 `.workbuddy/git_push.sh`（环境特定脚本，不入库）。
- 编码统一 UTF-8；路径含中文时注意 PowerShell 传参。

---

## 12. AI 协作规则

### 12.1 允许

只读审查与问题定位；在分支上改代码/文档/测试；运行测试与守卫；给出方案与取舍；整理会议/评审结论。

### 12.2 必须先问

动产品边界、改判定/指标口径、删除或移动公共模块与产物、改安全/认证/配额/审核逻辑、新增外部服务或依赖、涉及成本的操作（评测跑批、云资源）、对外发布与合规表述。

### 12.3 禁止

- 直推 `dev`/`master`、自动合并 PR、绕过 CI 或守卫；
- 修改/删除/回填冻结评测产物；
- 伪造测试结果、声称没跑过的验证、把“没验证”写成“已通过”；
- 大爆炸重构、无关改动混入同一 PR；
- 在 tracked 文件写入真实 IP/域名/密钥；
- 替主理人做产品/合规/预算决策。

### 12.4 必须报告

改动文件清单；执行的命令与**真实输出**（失败就贴失败）；未验证项与原因；风险与回滚方式；与需求文档的偏差。

### 12.5 节奏

一次只做一件事；大改先给计划再动手；不确定时停下来问，不猜。

---

## 13. 文档写作纪律

- 中文；结论先行；能用表格就用表格；章节可跳读。
- 数字必须带**来源 + 日期 + 口径**；推测与事实分开标注。
- 已知限制、失败案例、被否方案必须写，不得只写成功面。
- 引用代码给 `file:line`；引用文档给相对路径。
- 过期内容标 `⚠️ 已被取代`；不得静默删除历史结论。
- 改 Markdown 表格后跑 `tools/check_md_tables.py`。
- 对外不吹指标：不可判定就写不可判定；不得把“点估计”写成“提升”。

---

## 14. 执行清单

### 14.1 开工前（每个 B/C 项）

- [ ] Issue 已建，编号 = 需求编号
- [ ] 需求文档已写（10 字段），DoD 可勾选
- [ ] 从 `dev` 拉了正确命名的分支
- [ ] 明确本次范围与**不做什么**
- [ ] 涉及边界/口径/删除？→ 先出 ADR 并等拍板

### 14.2 PR 前

- [ ] `ruff check .` 通过
- [ ] `pytest tests/ -q` 通过（零依赖口径）
- [ ] 涉及前端的：`tsc --noEmit` / `vitest run` / `npm run build` / e2e
- [ ] 全部 CI 守卫通过（清单见 §6 表）
- [ ] 涉及检索/证据/校验/写作：固定证据池复算，确定性指标未下降
- [ ] PR 描述 6 项齐全；DoD 勾选
- [ ] 主理人确认后才合并

### 14.3 发布前

- [ ] CI 6/6 绿；迁移人工评审；备份可读；配置 diff；容量检查
- [ ] 回滚镜像 tag 已确认；观察窗口责任人明确
- [ ] 发布记录模板填写

### 14.4 安全事件

- [ ] 先止血（关闭入口/轮换凭据/回滚），再定位
- [ ] 24h 内出 ADR（背景/影响/修复/预防）
- [ ] 补能复现的测试或守卫；同类问题第二次必须机器化拦截

---

## 15. 违规与纠偏

- 发现违规：立即停止、保留证据（Issue/日志/commit）、评估可逆性、按最小面回滚。
- 可逆操作（代码/文档/配置）优先 revert；不可逆操作（数据删除、发布、外发）先备份再处置。
- 违规修复必须留痕：Issue + ADR（若架构/安全相关）+ 看板登记。
- 同类违规出现第二次：必须新增 CI 守卫或流程卡点，不得只靠人记。

---

## 16. 约束执行矩阵

| 约束 | 执行手段 | 失败后果 |
| --- | --- | --- |
| 分支/PR 流程 | 人工 review + GitHub 保护（如已启用） | 打回重做 |
| 需求文档/ADR | PR 描述检查 + 主理人 review | 不合并 |
| Lint/单测/集成/前端 | CI 6 job | 红，不合并 |
| 评测产物白名单 | `check_results_whitelist.py` | CI 红 |
| 前端边界 | `check_frontend_boundary.py` | CI 红 |
| 色板 token | `check_tailwind_tokens.py` | CI 红 |
| UI 反模式 | `check_ui_patterns.py` | CI 红 |
| 迁移幂等/结构 | infra job | 红 |
| 备份可恢复 | infra 演练 | 红 |
| 真实 IP 脱敏 | `check_public_ip.py`（待建） | CI 红 |
| 确定性质量回归 | 固定证据池复算脚本（待固化） | 阻断合并 |
| 发布/回滚 | runbook + 记录模板 | 事故复盘 |
| 环境铁律 | 人工 + 文档 | 返工 |

---

## 17. 变更记录

| 版本 | 日期 | 变更 | 依据 |
| --- | --- | --- | --- |
| v1.0 | 2026-09-03 | 初版：流程、分支、Issue、PR、需求文档、ADR、AI pre-flight、合规 | W3 重建基线 |
| v1.1 | 2026-10-11 | 随 OTel 迁移更新可观测相关口径 | PR #181 |
| v2.0 | 2026-10-26 | 全面修订：文档层级、当前阶段与产品边界（L3-A/B→完整产品路线）、批次发布与 tag、确定性质量门禁、评测纪律、公开仓脱敏、仓库边界、本机环境铁律、AI 协作规则、执行矩阵 | 2026-10-26 全方位评审 + 主理人确认（边界/脱敏/飞书/门禁/仓库/发布六项） |
| v2.1 | 2026-10-26 | 执行 §17 第 1 项：ADR-0017 落地、ADR-0009 转正、ADR 索引补齐与 0012 重号修正；§2.5 改为“已落地”口径 | `ADR-0017`、`docs/decisions/README.md` |
| v2.2 | 2026-10-26 | 执行 §17 第 2 项：建立 `docs/figures.md` 并回填 README 数字（测试 1290 / E2E 92 / ADR 17 / 迁移 27 / checks 23） | `docs/figures.md` |

### 本文生效后需同步的待办

1. ✅ **已完成（2026-10-26）**：新增 ADR-0017、ADR-0009 转正、ADR 索引补齐并修正 0012 重号（原 OTel ADR → 0016）。
2. ✅ **已完成（2026-10-26）**：`docs/figures.md` 已建立并回填 README 数字；简历 / 面试材料由主理人改为引用。
3. tracked 文件脱敏 + 部署 workflow 改用 secrets + 新增 `tools/check_public_ip.py`。
4. 修 `tests/conftest.py` 清空 `DR_TEST_*`，使零依赖 lane 与 README 口径一致。
5. `icon-lab/`、`ppt-report/` 迁出（一次性 ADR）；`面试准备/`、`taste-skill/` 加入 `.gitignore`。
6. 发布批次收口：`dev → master` + `v0.2.0` tag + CHANGELOG + 回滚演练。

---

## 附：历史注记

- W1~W9 里程碑（critic 节点化、溯源、OTel、工具集、评测、开源材料、Web UI 重构）已完成；L3 生产化推进到 P8。
- 2026-09-03 仓库曾因外部同步进程破坏 `.git`，已重建基线；分支纪律自 W3 起严格执行。
