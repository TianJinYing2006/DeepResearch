# 行动清单：评审结论 → 可执行批次（2026-10-26）

> **定位**：一次性评审后的**行动提案**，不是需求文档、不是看板。
> 实时状态仍以 `docs/project-status.md` 为准（D-01）；正式排期仍以 `docs/roadmap.md` 为准。
> 已登记进 `docs/project-status.md`（2026-10-26）。
>
> **约束遵循声明**：本文件所有行动按 `CONTRIBUTING.md` 的硬约束组织 ——
> Issue → 从 dev 拉 `feature|fix/<issue>-*` 分支 → 开发 → PR（DoD + 自测 + 风险评估）→ review → squash 合 dev；
> 里程碑合 master 并打 tag；密钥只进 `.env` / GitHub Secrets；AI 执行遵守 pre-flight，PR 前需人工确认、不自动合并。
> 本文件**不删改任何评测产物**（D-16 / D-18）、**不改核心判定口径**（除非单列 C 类拍板）、**不扩展产品边界**（D-19 / D-20）。
>
> **数字基线（2026-10-26 本机实测，用于后续对比）**：
> 338 commits / dev 领先 master 30；1289 pytest 收集（142 个测试文件）；E2E 20 spec / 77 个 `test()`；Vitest 5 文件 / 47 例；
> engine ≈13.5K 行、backend ≈13.1K 行、tests ≈20.7K 行、frontend(ts/tsx) ≈10.3K 行；27 个迁移；43 篇需求；15 个 ADR 编号（含 0012 重号）；
> 覆盖 `coverage=39.8%`、`marker_ratio=78.7%`（after 基线口径）；tag 仅 `v0.1.0`。

**准入类型（每个行动项必须标注）**

| 类型 | 含义 | 开工前置 |
| --- | --- | --- |
| **A** | 文档 / 配置 / 测试内改动；不触产品边界、不改判定口径 | 分支 + PR + CI 绿 |
| **B** | 功能 / 行为改动 | Issue + 需求文档（`docs/requirements/模板.md` 10 字段）+ DoD |
| **C** | 边界 / 判定口径 / 冻结产物 / 删除类 | 先出 ADR + 主理人拍板；**未拍板不动** |

---

## 0. 先决：约束体系本身需要对齐（做任何事之前）

> 评审发现：项目约束文档与已交付事实已经**结构性冲突**。不先收口，后续每个行动都会“合规性打架”。

| ID | 事项 | 冲突证据 | 准入 | 产出 / 验收 | 估时 |
| --- | --- | --- | --- | --- | --- |
| C0-1 | **L3 已突破 ADR-0001 边界，但 ADR-0009 仍是草稿（✅ 已收口）** | `docs/decisions/0001-initial-design.md:34` 明确「不做登录、用户系统、任务队列、任务持久化、多租户」；`docs/requirements/9-web-ui-rewrite.md:420` 三条硬边界同口径；而 L3 已交付全部上述能力（27 迁移、30+ 后端模块、Passkey/MFA）；`docs/decisions/0009-l3-multiuser-production.md:8` 状态仍「草稿（P0 立项）……拍板前不进入实现」 | **C** | ✅ **2026-10-26 已落地**：新增 **ADR-0017**「产品边界重划与产品化路线」（ADR-0001 旧边界条款被取代、前端硬边界①保留），**ADR-0009 转「已采纳」**，ADR 索引补齐并修正 0012 重号（OTel ADR → 0016） | 0.5 天 |
| C0-2 | **公开仓泄露 staging 公网 IP（与本项目自己的安全口径冲突）** | `docs/operations/deployment.md:10` 规定「本文件进入公开仓库，不得写入真实主机 IP」；但 `docs/project-status.md:27`、`docs/requirements/37-mcp-bidirectional.md:155`、`docs/operations/release-runbook.md:89,502`、`.github/workflows/deploy-staging.yml:47,54`、`tools/deploy.sh:15,24` 均含真实 IP/主机名（本文件不复述具体值） | **C** | ① 安全组只放行必要端口 + 更换公网 IP 或前置 CDN/WAF（历史 commit 仍含旧 IP，改文件只能止血）；② 当前文件脱敏为占位符；③ CI 加 `tools/check_public_ip.py` 守卫（沿用现有守卫模式，零网络）；④ 评估是否重写历史（默认不重写，D-15 同源原则） | 1 天（含窗口） |
| C0-3 | **对外数字无唯一真相源** | README 旧数：996 测试 / 49 E2E / 28 需求 / 11 ADR / 232 提交（`README.md:473`、`README.md:342`、`README.md:514`）；实测 1290 / 92 / 42+模板 / 17 / 338 | **A** | ✅ 2026-10-26 已落地：建立 `docs/figures.md`（仓库/代码/测试/评测/性能/部署/断点七节），README 已回填；简历/面试材料待主理人改为引用 | 0.5 天 |
| C0-4 | **评审文档是否入看板** | 本文是新文档，按 D-01 只有 `project-status.md` 承担实时状态 | **A** | ✅ 2026-10-26 已登记（project-status 新增治理行） | 5 分钟 |

---

## 1. P0：上线前止血（目标 1 周内完成）

> 全部为**部署/配置/前端小改**，除 P0-7 外不改核心判定；每项独立可回滚。

| ID | 事项 | 证据 | 准入 | 验收判据 | 估时 |
| --- | --- | --- | --- | --- | --- |
| P0-1 | **反代信任与真实 IP**：staging/生产显式配置 `DR_TRUSTED_PROXY_CIDRS`（docker 网段），Caddy 覆写 `X-Forwarded-For`；或反代直连暴露时关闭 XFF 信任 | `docker-compose.staging.yml:360-361` 默认空；`web/backend/main.py:976-993` 仅信任 CIDR；登录限流 `main.py:299-304` 按 IP 计数 | **A**（配置） | 两个不同公网 IP 各打 10 次登录：互不挤占；审计日志 `ip` 为真实客户端；`X-Forwarded-*` 伪造不生效 | 0.5 天 |
| P0-2 | **MFA / Passkey 加固**：`/api/auth/mfa/verify` 加用户+IP 限流（5/min）与失败退避；Passkey 注册/换绑要求密码或 MFA step-up | `main.py:1434` 无 LIMITER（对比登录/提交有限流）；`main.py:1540+` 注册仅需会话 | **B** | 6 位码连续错误 10 次触发锁定；被劫持会话无密码不能绑定新 Passkey | 1 天 |
| P0-3 | **全局安全响应头**：Caddy 统一 `CSP`（`default-src 'self'; script-src 'self'; frame-ancestors 'none'`）、`HSTS`、`X-Content-Type-Options`、`Referrer-Policy`、`Permissions-Policy` | `main.py:3344` 仅分享下载单响应有 Referrer-Policy；`Caddyfile:28,41` 无 `header` 指令 | **A** | 浏览器控制台无 CSP 违规；分享页/工作台功能不回归；`securityheaders` 类检查达到 A 级等价项 | 0.5 天 |
| P0-4 | **备份与默认凭据**：`pg_dump` 改 `PGPASSFILE`/环境变量传参；生产强制加密（`BACKUP_RECIPIENT_CERT` 或 `BACKUP_ENCRYPT=1`）；轮换 compose 默认口令；备份文件 0600 | `tools/backup.sh:33` DSN 作 argv；`docker-compose.staging.yml:36,106,244,300` 默认 `deepresearch-local` | **A/B** | `ps aux | grep pg_dump` 看不到密码；无证书/口令时生产启动 fail-fast；恢复演练仍通过 | 0.5 天 |
| P0-5 | **运维端点来源限制**：`/metrics`（仅 `PROMETHEUS_ENABLED` 时挂载）与 `/api/ops/*` 只允许内网/反代白名单；文档与 Caddy 同步 | `main.py:3490`；`main.py:821-831` ops token 已有，但 `/metrics` 无鉴权 | **A** | 外网直访 `/metrics` 403/404；内网抓取正常 | 0.5 天 |
| P0-6 | **公开仓 IP 收口**（C0-2 的执行项） | 见 C0-2 | **C** | 当前 HEAD 无真实 IP；CI 守卫拦截新增；安全组仅 443/80 对公网 | 1 天 |
| P0-7 | **（评估，非阻塞）多租户 RLS 兜底**：给核心表加 PG RLS 或至少 CI 静态检查“每个 owner 查询都带 user_id” | `store.py:368-428,1010` 等仅应用层 `WHERE user_id` | **C** | 设计评审通过；不改变现有 API 语义 | 2 天 |

### 1b. 前端 P0（让“可审计”可见）

| ID | 事项 | 证据 | 准入 | 验收判据 | 估时 |
| --- | --- | --- | --- | --- | --- |
| P0-A | **分享页补只读证据栏**：引用编号可点开只读引用清单（来源/引文/偏移），带质量徽标；不扩大披露边界（只读、不提供追问/导出） | `web/frontend/src/components/SharedReportPage.tsx:60` 不传 `onCitationClick`；`components/ReportView.tsx:79` 无回调即原样返回 → `[来源:3]` 死文本 | **B** | 登录墙外可查看每条引用原文与定位状态；e2e 覆盖；无新增接口泄露面 | 1 天 |
| P0-B | **“信息不足”升级为一等公民**：正文标记样式 + 质量徽标显示覆盖率 + 导出元数据；修正 `[来源: 信息不足]` 被判为“有源”的逻辑 | 全前端 grep「信息不足」为 0；`ReportView.tsx:157` `!text.includes('[来源:')` | **B** | 含“信息不足”的小节在 UI 可见；质量卡显示 `marked_sections/section_count`；e2e 断言 | 1 天 |
| P0-C | **等待合同与离开保护**：提交前明示“预计 5~15 分钟 / 单次上限 ¥1.50”；运行中 `beforeunload` 拦截并提示“可刷新恢复，勿关标签”；starting 态可取消 | `web/frontend/src/App.tsx:300-312` beforeunload 只 flush 草稿；`features/launch/LaunchPanels.tsx:167` 仅 running/stopping 有停止 | **B** | 关页弹出确认；刷新后恢复；starting 可取消；e2e 覆盖 | 1 天 |

---

## 2. P1：质量证明（2~4 周，与真实用户验证并行）

> ⚠️ **W8 纪律**：Q-1/Q-2 会改判定/指标口径 ⇒ 立项时**先拍板是否需要 before 基线**（改动前采集，错过不可逆）；
> 改口径必须 bump `SCORER_VERSION` 并登记口径断点（当前 `w9.1`）；不重跑 D-18 已结案的 6 轮，只做零图复算。

| ID | 事项 | 证据 | 准入 | 验收判据 | 估时 |
| --- | --- | --- | --- | --- | --- |
| Q-1 | **修“多源印证”分片失效**：多批时喂料改为「本批引用行 + 独立来源候选池」，或改两阶段（先选 ID 再单独取证）；补**多批**回归测试 | `agents/validator.py:806-810` 只喂本批 finding 行；`validator.py:1050-1057` 要求 ≥2 独立来源；现有多源测试仅单批 | **C**（改判定口径） | 构造 >batch_size 引用 + 跨批第二来源，`multi_source_verified_count > 0`；固定证据池复算前后对比报告 | 2~3 天 |
| Q-2 | **拆 fidelity 口径**：分母剔除 `verification_failed`（或用 `verification_failed` 单列 `unverified_rate`），避免“未校验”混算成“不忠实” | `agents/validator.py:994-1016` 未校验仍 `verified=False`；`eval/metrics.py:221` 分母为 existence | **C**（改指标口径） | 指标定义文档 + 单测；新旧口径并列一版；`SCORER_VERSION` bump | 1~2 天 |
| Q-3 | **零图复算 + 对外口径更正**：对现有冻结证据池跑 `--eval-only`，把多源/fidelity 新口径落到数字；更新 README 的“能力与限制” | `README.md:409-432` 现有四指标不可判定结论 | **B** | 一份新旧口径对照表；README 同步；不删除/回填任何 history 产物 | 1 天 |
| Q-4 | **评测集扩容与头条指标更换**：50~100 题；头条改为确定性指标（引文可定位率 L0~L2、existence、retrieval_hit、insufficient_ratio、成本/时长 p50/p90）；LLM judge 降为附录 | 现 20 题 × 3 轮，MDE 10.66pp > 效应 5.1pp；`eval/run.py` 两阶段管道已具备 | **B**（成本敏感，`eval-manual.yml` 手动轨） | 新题集冻结 + 首轮报告；头条指标不依赖 LLM 裁判 | 1~2 周 |
| Q-5 | **人工抽检 30 份报告**：按“可用 / 部分可用 / 不可用”分级，记录失败模式 | 现无报告级可用性评级 | **A**（运营） | 抽检表 + 结论；与 `insufficient_ratio` 交叉验证 | 2 天 |
| Q-6 | **5~10 人内测 2 周**：按 `production-readiness.md` 的 L3-A 判据执行（任务丢失率 0、P0 事故 0、p50/p90、成本/人） | G7 合规未开始；内测判据未跑 | **B** | 一页《质量与体验报告》：回答“值不值得用 / 贵不贵 / 哪里最差” | 2 周（并行） |

---

## 3. P1：工程质量门禁（可并行，低成本优先）

| ID | 事项 | 证据 | 准入 | 验收判据 | 估时 |
| --- | --- | --- | --- | --- | --- |
| E-1 | CI 加 `pip-audit`（先只报告不阻塞，再按结果决定门槛） | `.github/workflows/ci.yml` 无依赖扫描 | **A** | 首轮报告入库；高危项有处置记录 | 0.5 天 |
| E-2 | CI 加覆盖率报告（`pytest --cov`，先观测后设门槛） | 同上，无 coverage | **A** | PR 可见覆盖率变化；不阻断 | 0.5 天 |
| E-3 | 渐进式类型检查：仅对新代码/核心模块启用 mypy（避免全仓债） | 无 mypy；`def` 类型标注约 78% | **A/B** | CI 只检查白名单目录；存量零新增错误 | 1 天 |
| E-4 | **发布工程**：里程碑合并 dev→master、打 `v0.2.0`、CHANGELOG、镜像 digest 固定、回滚演练 1 次 | 仅 `v0.1.0`；dev 领先 30；runbook §9 回滚未演练 | **B** | tag + release notes；回滚演练记录 | 1~2 天 |
| E-5 | **测试隔离修复**：`conftest` 同时清 `DR_TEST_DATABASE_URL/DR_TEST_REDIS_URL`（或明确文档化“集成 lane 需先起依赖”） | `tests/conftest.py:38-42` 只清 3 个变量；本机 `.env` 有 `DR_TEST_*` 时全量跑出 80 失败/错误 | **A** | 无依赖机器 `pytest tests/ -q` 全绿；README 两种模式的口径与实测一致 | 0.5 天 |
| E-6 | 文档一致性修复：README 数字、ADR 索引/重号、launch-readiness 的 superseded 标记 | `README.md:342,473`；`docs/decisions/README.md` 0009→0012；`docs/launch-readiness-review.md:18` | **A** | 机器核对脚本或人工清单全绿 | 0.5 天 |
| E-7 | 拆分 `main.py` / `store.py`（按 router→service→store 增量，禁止大爆炸） | `web/backend/main.py` 3056 行；`store.py` 3058 行 | **C**（架构级） | ADR + 分 3 个 PR；行为零变化（测试 + 冒烟） | 3~5 天 |
| E-8 | 配置面收敛：env 按 dev/staging/prod profile 化，校验开关矩阵从 2⁴ 收敛 | 121 config 字段 / ~197 env 名；`validator.py:54-77,929-937` 双实现 | **C** | 旧变量兼容期 + 文档；`config_snapshot` 不变 | 2~3 天 |

---

## 4. P2：收敛与战略（4~8 周，需主理人拍板）

| ID | 事项 | 说明 | 准入 |
| --- | --- | --- | --- |
| S-1 | **冻结新增功能 4 周** | 暂停 MCP 扩展、Passkey P1-3b/P1-4、PPT 导出、新 Agent 能力；只做 P0/P1 | C |
| S-2 | **战略二选一** | 产品路线：合规三件套 + 有资质审核服务 + 国内第二搜索源 + 邮件通道；作品集/开源路线：英文 README + 示例部署 + issue 模板 + 技术文章。判据 = Q-6 内测数据 | C |
| S-3 | **仓库瘦身** | ⚠️ 与 D-16「维持现状、不删任何产物」冲突 ⇒ 必须**新拍板**；默认只做 git 侧可回溯（LFS/外置），不动磁盘原始产物 | C |
| S-4 | 删除 3 个零引用的早期评测器 | `eval/retrieval_eval.py` / `citation_eval.py` / `report_eval.py`；低风险但需需求文档留痕 | B |
| S-5 | 工作区素材移出产品仓库 | `icon-lab/`、`ppt-report/` 为设计/汇报素材；移出前需评估 git 历史与引用（与 S-3 同批拍板） | C |

---

## 5. 明确不做（本轮冻结）

- 不新增 Agent 功能 / 协议（MCP server 扩张、新工具源、新图节点）。
- 不做 Passkey P1-3b/P1-4、PPT 导出、K8s/Helm、多实例编排、CDN/WAF 之外的基础设施。
- 不重写前端、不追求测试数量、不做全仓 mypy、不做大爆炸重构。
- 不删除/移动/回填任何 `research_engine/eval/results/` 产物（D-16 / D-18）。
- 不在未拍板前改 `research_engine/` 核心判定口径或前端承载研究逻辑（D-20 三条硬边界 + `tools/check_frontend_boundary.py`）。

---

## 6. 约束遵循矩阵

| 行动 | CONTRIBUTING | D-01 | D-16/18 | D-19/20 | ADR 要求 | 守卫/CI |
| --- | --- | --- | --- | --- | --- | --- |
| C0-1 边界重划 | 需 Issue + 需求文档 | 需登记 | — | ✅ 直接相关 | **必须新增 ADR** | ADR 索引检查 |
| C0-2 IP 收口 | 破坏性操作先 review | 需登记 | — | — | 安全类 ADR 可简化 | 新增 `check_public_ip.py` |
| P0-1~P0-5 | PR + CI | 需登记 | — | 不扩边界 | 配置类可免 | infra job |
| P0-A/B/C | 需求文档 + e2e | 需登记 | — | 前端不承载研究逻辑 | 前端类可免 | `check_frontend_boundary.py`、`check_tailwind_tokens.py`、impeccable |
| Q-1/Q-2 | 需求文档 + 先拍 before 基线 | 需登记 | 不重跑 6 轮、不回填 | 改判定需谨慎 | **必须 ADR** | eval 手动轨；`SCORER_VERSION` |
| Q-4 | 需求文档（成本预算） | 需登记 | 新基线新口径 | — | 可免 | `eval-manual.yml` |
| E-7/E-8 | 需求文档 | 需登记 | — | — | **必须 ADR** | 全量测试 + 冒烟 |
| S-3/S-5 | 需新拍板 | 需登记 | **冲突，需新决策** | — | **必须 ADR** | 白名单守卫 |

---

## 7. 执行与登记流程（每个 B/C 项）

1. 开 Issue（编号 = 需求文档编号），写目标 + DoD + 风险；
2. 新增/更新 `docs/requirements/<编号>-<slug>.md`（10 字段模板），同步飞书（双轨）；
3. `git checkout dev && git pull && git checkout -b feature/<issue>-<slug>`；
4. 开发 + 自测（`ruff check .`、`pytest tests/ -q`、相关 e2e/守卫；涉及 eval 的跑 `--eval-only`）；
5. PR 标题 `<type>(<scope>): <简述>`，描述含需求文档链接、`Closes #<issue>`、自测输出、DoD 勾选、风险；
6. CI 绿 + eval 不回归（引用准确率 ≥ 基线 −2%）+ 人工确认后 squash 合 dev；合后删分支、需求文档置「已合」；
7. 里程碑合并 master + tag（E-4 落地后按此执行）。

---

## 8. 附：评审证据索引（便于复核）

- 测试/规模：本机 `pytest --collect-only` = 1289；`web/frontend/e2e` 20 spec / 77 `test()`；`vitest` 47。
- 质量数字：`research_engine/eval/results/run_20260919_*` 的 `metrics.coverage / metrics.insufficient`。
- 安全/部署：`docker-compose.staging.yml:360-361`、`web/backend/main.py:299-304,976-993,1434,1540,3344,3490`、`Caddyfile:28,41`、`tools/backup.sh:33`。
- 质量链路：`research_engine/agents/validator.py:806-810,994-1016,1050-1057`、`research_engine/eval/metrics.py:221`、`research_engine/self_check.py:57-66`、`research_engine/graph.py:578-591`、`research_engine/critic.py:288`。
- 前端：`web/frontend/src/components/SharedReportPage.tsx:60`、`components/ReportView.tsx:79,157`、`App.tsx:300-312`。
- 文档漂移：`README.md:342,473,514`、`docs/decisions/README.md`、`docs/launch-readiness-review.md:18`。
- 约束冲突：`docs/decisions/0001-initial-design.md:34`、`docs/decisions/0009-l3-multiuser-production.md:8`、`docs/requirements/9-web-ui-rewrite.md:420`、`docs/operations/deployment.md:10`。

---

## 9. 变更记录

| 日期 | 变更 | 说明 |
| --- | --- | --- |
| 2026-10-26 | 建稿 | 基于 2026-10-26 全方位评审（引擎/平台安全/前端产品/文档评测四个视角 + 本机实测）；按 CONTRIBUTING 硬约束与 D-01/D-16/D-18/D-19/D-20 组织为 A/B/C 三类行动；未改动任何代码、评测产物或 git 状态 |
