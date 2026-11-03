# 需求-42-审计整改批次（2026-11 漏洞与 bug 全仓审计）

> 本文件是本批次唯一总文档：审计范围与结论、Issue/PR 映射、DoD、不修项与理由、验证口径。
> 单个缺陷的细节留在对应 Issue 与 PR；本文件不重复代码级证据，只做索引与口径。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 42 |
| 标题 | 2026-11 漏洞与 bug 全仓审计整改 |
| 优先级 | P0（安全/数据）+ P1（正确性）+ P2（工程健壮性） |
| 状态 | **已合（2026-11-03）**：24 子项 / 32 个 PR 按 §7 顺序 squash 合入 `dev`；合并后 dev CI 由 push 触发（以 Actions 为准） |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #344（批次总跟踪）；子项 #320–#343 |
| 关联 PR | #345–#375（逐个见 §4） |
| 创建 / 更新 | 2026-11-02 / 2026-11-03 |

## 2. 问题背景

2026-11-02 对 `dev @ 21f5f96` 做了一次六面并行的只读审计（认证/会话、非认证 API、数据层与迁移、研究引擎与沙箱、前端、基础设施/CI/依赖），方法为：机械枚举（路由×守卫矩阵、SQL 拼接点全量扫描、依赖审计、迁移↔checks 清单对比）+ 定向复现（FakeStore/TestClient 行为探针、沙箱逃逸 PoC、DOCX 炸弹、上传孤儿、异常原文泄露）+ 文档口径对照（README/需求/ADR/figures）。

审计结论：**无 Critical；3 High；16 Medium；约 30 Low**。核心攻击面（SQL 注入、IDOR、XSS、分享 token、ops 鉴权、RAG 租户隔离）复核为干净；风险集中在备份链路、认证加固、上传资源限额、纵深防御层与文档声称不一致、以及若干并发/生命周期缺口。

整改方式：24 个子 Issue、31 个分支/PR（凡「≤6 文件 / ≤300 行」目标需要拆分的按 `<issue>a/<issue>b` 拆分），全部从 `dev` 拉出、保持单主题、附 pr-review 自审评论与三目标证据、CI 6 项绿；另用 `integration/audit-remediation` 集成分支合并全部 PR 做全量验证（见 §5）。完整审计报告留在 2026-11-02 会话（不入库，符合审计产物纪律）。

## 3. 范围与边界

**修**：审计中已给出代码级证据、且修复不需要产品/合规决策的项（High + Medium 全部、Low 中可安全收敛者）。
**不修（本批次）**：需产品/运营/合规决策或会改变既有对外口径的项，逐条见 §6。
**不做**：全仓重构、冻结产物（`research_engine/eval/results/`）变更、对外数字口径变更、任何绕过 CI 的改动。

## 4. Issue / PR 映射与状态

> 全部 PR base=`dev`、CI 6 项绿、均带 pr-review 自审评论（Blocking 为空或有复评闭环）。迁移编号预留并全部落地：#322=`0028`、#330=`0029`、#338=`0030`、#339=`0031`。

| Issue | 级别 | 主题 | 分支 | PR | 状态 |
|---|---|---|---|---|---|
| #320 | High | 账号注销链路完整性（撤销分享 + 数据覆盖） | `fix/320-deletion-completeness` | #374 | 已交付 |
| #321 | Medium | TOTP 绑定缺少密码 step-up | `fix/321-mfa-bind-stepup` | #348 | 已交付 |
| #322 | Medium | TOTP 码可重放 | `fix/322a-mfa-replay-migration` + `fix/322-totp-replay` | #350 + #354 | 已交付（合并序 350→363→354） |
| #323 | Medium | 重认证无限流/失败无审计 + 会话卫生 | `fix/323-session-reauth-hardening` | #363 | 已交付 |
| #324 | Medium | 客户端错误信息回显异常原文 | `fix/324-client-error-redaction` + `fix/324b-rag-error-redaction` | #347 + #352 | 已交付 |
| #325 | Medium | 上传加固（孤儿/OOXML 上限/解析墙钟） | `fix/325-upload-hardening` | #357 | 已交付 |
| #326 | High | 备份/部署链路明文密钥外泄 | `fix/326-backup-secret-exposure` | #368 | 已交付（合并后需运维处置，见 §6） |
| #327 | High | 代码沙箱网络隔离失效与已知绕过 | `fix/327-codeexec-sandbox` | #359 | 已交付（含独立复评 Approve） |
| #328 | High | 前端登出未清理运行状态（跨账号残留） | `fix/328-frontend-logout-reset` | #351 | 已交付 |
| #329 | Medium | 迁移 0018–0021 缺 checks + 无一致性守卫 | `fix/329-migration-check-parity` | #349 | 已交付 |
| #330 | Medium | 分享并发可产生多条活跃链接 | `fix/330-share-active-unique` | #358 | 已交付 |
| #331 | Medium | SSRF 加固（MCP 重定向/DNS + CGNAT） | `fix/331-ssrf-hardening` | #372 | 已交付 |
| #332 | Medium | web 快照正文未净化进事实校验 | `fix/332-snapshot-sanitize` | #371 | 已交付 |
| #333 | Medium | 系统提示泄漏标记漂移 | `fix/333-prompt-leak-markers` | #373 | 已交付 |
| #334 | Medium | CSP↔Sentry 配置耦合 + 静态缓存 | `fix/334-csp-sentry-cache` | #346 | 已交付 |
| #335 | Medium | 租约 fence（ingestion/checkpoint/deletion outbox） | `fix/335-lease-fences` | #355 | 已交付 |
| #336 | Medium | 前端可访问性与敏感输入 | `fix/336-frontend-a11y-inputs` | #353 | 已交付 |
| #337 | Low | API 低危收敛 | `fix/337a-api-lows-shell` + `fix/337b-api-lows-limits` | #364 + #367 | 已交付 |
| #338 | Low | 数据层低危收敛 | `fix/338a-idempotency` + `fix/338b-data-lows` | #361 + #365 | 已交付 |
| #339 | Low | 运维脚本健壮性 | `fix/339-ops-scripts` | #370 | 已交付 |
| #340 | Low | 引擎低危收敛 | `fix/340a-engine-correctness` + `fix/340b-engine-fetch-mcp` | #375 + #369 | 已交付 |
| #341 | Low | 前端低危收敛 | `fix/341-frontend-lows` + `fix/341b-frontend-session-sync` | #356 + #360 | 已交付 |
| #342 | Low | 前端 devDependencies 漏洞治理 | `fix/342-frontend-deps` | #362 | 已交付（生产依赖 0 漏洞；dev-only 待单独议题） |
| #343 | Low | 改密失效 reset token + 恢复码强度 | `fix/343-auth-lows` | #366 | 已交付 |
| —（需求 42 总文档） | — | 批次总文档 | `docs/42-audit-remediation-doc` | #345 | 已交付 |
| —（收口回填） | — | 状态/数字收口（stacked 在 #345 之上） | `docs/42-batch-closeout` | 本 PR | 待合 |

**拆分说明**：13 个 Issue 因「≤6 文件 / ≤300 行」拆成 2 个 PR（见上表）；#349 因 4 个结构断言文件为机械 SQL 样板且一致性守卫必须与 4 个文件同时落地（拆开会红），在 PR 内登记体量说明后单 PR 交付。

**已登记、不在本批次重复处理**（口径见 `README.md` §3.2 / `docs/requirements/27-agent-audit-remediation.md` / `docs/project-status.md`）：
代码沙箱的 `-E` 环境变量继承、`os.chdir` cwd 绕过与 subinterpreter 绕过（#327 已修，README §3.2 更新为「已修 + 剩余限制」）；
R06–R09 引擎评测项（需求 27 挂账）；`/metrics` Prometheus mount 的应用层鉴权（P0-5，由 Caddy 403 + 本机绑定兜底）；U6/U10/U26 前端审计（#336/#341 覆盖其中未修分支）。

## 5. 验收标准（DoD）

- [x] 24 个 Issue 全部有对应 PR（见 §4），无漏项（#320 为分派补漏，已由 #374 补齐）。
- [x] 每个 PR：base=`dev`、单主题、≤6 文件（超限项有拆分/体量说明）、PR 描述 6 项齐全、自审评论已发。
- [x] 每个 PR CI 6 项全绿（lint-and-test 3.11/3.12/3.13 + infra + frontend + e2e）。
- [x] 中/高风险 PR 有三目标证据（旧行为未回归 / 新行为行为级断言 / mutation 能抓住）。
- [x] 集成分支 `integration/audit-remediation` 依序合并全部 31 个 PR 后：`ruff` 全过；全量 pytest 仅剩 5 个已知 `.deps` 漂移 OTel 环境失败（1475 收集 / 1387 通过 / 84 跳过）；6 个仓库守卫全 PASS；前端 `tsc --noEmit` + `vitest`（67 passed / 11 files）+ `vite build` 全过；`docker compose config` 通过；5 处跨 PR 冲突由 Lead 解析（集成记录见本 PR 描述）。
- [x] **合并态 GitHub Actions 真实 CI**：经 draft PR #377（`integration/audit-remediation` → `dev`，仅验证不合并）跑完整 6 项，最终 `run 41563062131` **全绿**（e2e 4m21s / infra 2m39s / lint 矩阵 1m35s~2m0s / frontend 25s）。
  预演期间抓到并修复 1 个**只在合并态出现**的不兼容（单 PR CI 各自全绿的盲区）：#329 的 `checks/0021` 造了同 run 两条活跃分享，而 #330 的迁移 `0029` 新增「一 run 一活跃链接」唯一部分索引 `report_shares_run_active_uk` ⇒ infra 的「迁移结构断言」步骤失败；**修复 = #349 补 commit `f333a18`**（辅助行显式 `revoked_at=now()`，不弱化断言；单 PR 与合并态双兼容），合并顺序不受影响。本地 31 个 checks 在合并态 schema 下已复跑 **0 失败**。
- [x] 不修项在 §6 登记理由；文档口径漂移（README §3.2、runbook 备份章节、production-readiness 解析限额、需求 26 注销撤销分享）与代码同步。
- [x] 对外数字（README/figures 的测试数、E2E 数、需求/ADR/迁移数）已在本收口 PR 回填。
- [x] 合并：32 个 PR 已按 §4/§7 顺序 squash 合入 `dev`（2026-11-03；合并前 dev head `2119d0b`，本收口 PR 为最后一个）。合并期间因 dev 前进而冲突的分支（#354/#363/#364/#366/#374/#376 等）均由 Lead 先 merge dev → 人工解冲突 → 分支 CI 6/6 绿 → 再 squash 合并。

## 6. 不修项与理由（登记）

| 项 | 理由 | 处置 |
|---|---|---|
| 用户枚举：注册 409 / Passkey `authenticate/begin` 的 `allowCredentials` 数量（审计 L3） | 统一响应会改变注册与 Passkey 登录的既有 UX；彻底消除需邮箱验证/账号恢复流程，属产品决策 | 记入本表；如需做，另立需求 |
| 账号维度限流可被用来定向锁死受害者（审计 L7） | 需明确「失败才计数/成功后清零/退避策略/是否加验证码」的运营口径，改变限流语义 | 记入本表；另立需求评估 |
| `snapshots.save_snapshot` 未透传 RunOwnership（审计 L5，与 #335 同源） | 需把 RunOwnership 透传过引擎 FetchContext 边界，跨 PR 重叠且影响面大 | #338b（#365）PR 描述已登记理由与影响；作为 #335 follow-up 或另立需求 |
| 前端 devDependencies 13 项漏洞（#342 后） | 生产依赖实测 0 漏洞（`npm audit --omit=dev`），`fixAvailable=false` 居多（vite/tailwind 需主版本升级，需单独回归窗口）；CI 已加 `npm audit --omit=dev` 门禁 | #342 PR 内登记；另立升级议题 |
| SSE 每用户默认上限 2 条（#337b） | 多标签页同时看同一任务流可能 429（EventSource 不自动重连）；属产品口径 | #367 PR 登记；待产品决定是否调大或前端提示 |
| 代码沙箱剩余限制（#327 后） | 无 OS 级隔离（纯 Python 白名单）、Windows Job Object 默认关、目录/路径元数据可枚举、ctypes 进程创建对抗未端到端验证 | README §3.2 如实登记 |
| **#326 合并后运维处置（必须执行）** | 本 PR 只阻止新增：COS 里可能已有旧明文 dump / 含 `.env` 的源码快照 | 按 `docs/operations/release-runbook.md` §2.3/§10 盘点清理；如已外泄轮换 `.env` 密钥 |
| eval 口径（R06/R08、引用准确率分母） | 已登记挂账（需求 27），涉及评测口径变更需 bump `SCORER_VERSION` 与断点登记 | 保持需求 27 挂账 |

## 7. 影响范围、集成冲突与合并顺序

- **认证/会话**（#321/#322/#323/#343）：改变 MFA 绑定与 step-up 行为、重认证限流、登出 CSRF、Cookie 删除属性；前端与 E2E 已同步；回滚 = revert 对应 PR（#322 的 `last_used_step` 可空，旧代码忽略）。
- **迁移**（#322/#330/#338/#339 → 0028–0031）：只前向、幂等；旧代码可忽略新列/新索引；回滚不写 down 脚本（项目铁律）。
- **上传/备份/运维**（#325/#326/#339）：改变拒绝阈值与同步范围；合并后需在 staging 先验证一次上传与备份 cron（#326 另见 §6 运维处置）。
- **沙箱**（#327）：收紧执行面，可能让既有「本可运行」的沙箱脚本被拒（预期）；`CODE_EXEC_EVIDENCE_ENABLED` 仍默认关。
- **前端**（#328/#336/#341/#342）：登出/换账号行为、登录门焦点、密码输入交互变化；E2E 覆盖。

**跨 PR 集成冲突（已由集成分支解析）**：`store.py`（#320/#335/#338b）、`main.py`（#321/#323/#324a/#325/#337/#367）、`tests/test_mfa.py`（#321/#322/#323）、`tests/test_auth_api.py`（#323/#343）、`tests/fakes.py`（#320/#335）、`tests/test_share_api.py`（#330/#337a）、`web/backend/ingestion.py`（#324b/#325/#335）、`web/frontend/src/components/AccountPanel.tsx`（#328/#336/#341/#341b）。

**建议合并顺序（已按此顺序在集成分支验证可干净合并）**：
`#345（总文档）→ #346 → #349 → #350 → #348 → #363 → #354 → #366 → #347 → #352 → #357 → #358 → #361 → #365 → #368 → #370 → #364 → #367 → #369 → #371 → #373 → #372 → #375 → #374 → #355 → #359 → #351 → #353 → #356 → #360 → #362 → 本收口 PR（stacked 在 #345 之上）`。
其中 #350（0028 迁移）必须先于 #363/#354；#355 与 #324b/#325 在 `ingestion.py` 邻接。
合并态预演另发现 1 处非冲突类不兼容（#349 的 checks/0021 × #330 的迁移 0029），已在 #349 分支修复（commit `f333a18`，`Refs #329, #330`）；合并 #349 时自动带上，顺序不变。draft PR #377 仅用于合并态 CI 验证，**请勿合并**。

## 8. 测试策略

- 零依赖 lane（本地）：`py -3.13` + `PYTHONPATH=.deps`，清空 `DR_*`/`DR_TEST_*` 后全量 pytest；已知 5 个 OTel 用例为 `.deps` 漂移环境失败，以 CI 三版本矩阵为准。
- 集成 lane：真实 PG/Redis 由 CI `infra` job 覆盖（迁移幂等 + checks 结构断言 + 仓储/Worker/备份恢复演练）；本地 Docker 不可用时在 PR 内标注「未验证，CI 覆盖」。
- 前端：`tsc --noEmit` + `vitest run` + `vite build`；E2E 由 CI `e2e` job 跑（先 build dist）。
- 守卫：`check_results_whitelist` / `check_frontend_boundary` / `check_public_ip` / `check_doc_consistency` / `check_tailwind_tokens` / `check_ui_patterns` 全过。
- 集成验证（本批次新增口径）：`integration/audit-remediation` 分支合并全部 PR 后跑上述全部检查，作为「合并后 dev 状态」的预演。

## 9. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-11-02 | 文档 | 2026-11-02 全仓审计（0 Critical/3 High/16 Medium/~30 Low） | 建立批次总文档与 Issue/PR 索引、DoD、不修项 | #345 |
| 2026-11-03 | 文档 | 31 个整改 PR 全部开完（CI 绿）+ 集成分支全量验证通过 | 回填 §4 索引（Issue→分支→PR）、DoD 勾选、不修项补全（L5/devDeps/SSE/#326 运维处置）、拆分说明、集成冲突与建议合并顺序 | 本 PR |
| 2026-11-03 | 修复 | 合并态 GitHub Actions 真实 CI 预演（draft PR #377）发现 #329 `checks/0021` 与 #330 迁移 `0029` 的唯一索引不兼容 | #349 补 commit `f333a18`（辅助行 `revoked_at=now()`，单 PR/合并态双兼容，不弱化断言）；合并态 CI `run 41563062131` 6/6 绿；本地 31 checks 复跑 0 失败 | #349 / #377 |
| 2026-11-03 | 合并 | 批次合并收口 | 31 个整改 PR + 本收口 PR 全部 squash 合入 `dev`；6 个分支因基线前进做「merge dev + 解冲突」后 CI 绿再合（#354/#363/#364/#366/#374/#376）；合并后触发 dev 全量 CI 与 staging 部署 | 本 PR |
| 2026-11-03 | 修复 | staging 部署事故（合并后第一时间发现）：合并批次修改了**已应用**迁移 `0021_report_shares.sql` 的注释，撞上 #370 新增的 sha256 校验，deploy 在迁移步骤 fail-closed（旧版本继续在线；同轮第 2/3 次重试为服务器→GitHub 网络断流） | PR #378：恢复 0021 原字节（sha256 与线上记录 `383d45e…` 重新一致）；新增 `migrations/APPLIED_SHA256.txt` + `tests/test_migrations_immutable.py`，把「已应用迁移不可修改」前移到 PR CI；`migrations/README.md` 补铁律与本次事故记录 | #378 |
