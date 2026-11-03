# 需求-42-审计整改批次（2026-11 漏洞与 bug 全仓审计）

> 本文件是本批次唯一总文档：审计范围与结论、Issue/PR 映射、DoD、不修项与理由、验证口径。
> 单个缺陷的细节留在对应 Issue 与 PR；本文件不重复代码级证据，只做索引与口径。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 42 |
| 标题 | 2026-11 漏洞与 bug 全仓审计整改 |
| 优先级 | P0（安全/数据）+ P1（正确性）+ P2（工程健壮性） |
| 状态 | 进行中 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #344（批次总跟踪）；子项 #320–#343 |
| 关联 PR | 逐个回填（见 §4 表） |
| 创建 / 更新 | 2026-11-02 / 2026-11-02 |

## 2. 问题背景

2026-11-02 对 `dev @ 21f5f96` 做了一次六面并行的只读审计（认证/会话、非认证 API、数据层与迁移、研究引擎与沙箱、前端、基础设施/CI/依赖），方法为：机械枚举（路由×守卫矩阵、SQL 拼接点全量扫描、依赖审计、迁移↔checks 清单对比）+ 定向复现（FakeStore/TestClient 行为探针、沙箱逃逸 PoC、DOCX 炸弹、上传孤儿、异常原文泄露）+ 文档口径对照（README/需求/ADR/figures）。

审计结论：**无 Critical；3 High；16 Medium；约 30 Low**。核心攻击面（SQL 注入、IDOR、XSS、分享 token、ops 鉴权、RAG 租户隔离）复核为干净；风险集中在备份链路、认证加固、上传资源限额、纵深防御层与文档声称不一致、以及若干并发/生命周期缺口。

完整审计报告留在 2026-11-02 会话（不入库，符合审计产物纪律）；本文件是整改侧的权威索引。

## 3. 范围与边界

**修**：审计中已给出代码级证据、且修复不需要产品/合规决策的项（High + Medium 全部、Low 中可安全收敛者）。
**不修（本批次）**：需产品/运营/合规决策或会改变既有对外口径的项，逐条见 §6。
**不做**：全仓重构、冻结产物（`research_engine/eval/results/`）变更、对外数字口径变更、任何绕过 CI 的改动。

## 4. Issue / PR 映射与状态

> 分支命名 `fix/<issue>-<slug>`，base=`dev`，一需求一分支一 PR；每个 PR 附带 pr-review 自审评论与三目标证据。
> 迁移编号预留：#322=`migrations/0028`、#330=`0029`、#338=`0030`、#339=`0031`。

| Issue | 级别 | 主题 | 分支 | PR | 状态 |
|---|---|---|---|---|---|
| #320 | High | 账号注销链路完整性（撤销分享 + 数据覆盖） | `fix/320-deletion-completeness` | 待开 | 进行中 |
| #321 | Medium | TOTP 绑定缺少密码 step-up | `fix/321-mfa-bind-stepup` | 待开 | 进行中 |
| #322 | Medium | TOTP 码可重放 | `fix/322-totp-replay` | 待开 | 进行中 |
| #323 | Medium | 重认证无限流/失败无审计 + 会话卫生 | `fix/323-session-reauth-hardening` | 待开 | 进行中 |
| #324 | Medium | 客户端错误信息回显异常原文 | `fix/324-client-error-redaction` | 待开 | 进行中 |
| #325 | Medium | 上传加固（孤儿/OOXML 上限/解析墙钟） | `fix/325-upload-hardening` | 待开 | 进行中 |
| #326 | High | 备份/部署链路明文密钥外泄 | `fix/326-backup-secret-exposure` | 待开 | 进行中 |
| #327 | High | 代码沙箱网络隔离失效与已知绕过 | `fix/327-codeexec-sandbox` | 待开 | 进行中 |
| #328 | High | 前端登出未清理运行状态（跨账号残留） | `fix/328-frontend-logout-reset` | 待开 | 进行中 |
| #329 | Medium | 迁移 0018–0021 缺 checks + 无一致性守卫 | `fix/329-migration-check-parity` | 待开 | 进行中 |
| #330 | Medium | 分享并发可产生多条活跃链接 | `fix/330-share-active-unique` | 待开 | 进行中 |
| #331 | Medium | SSRF 加固（MCP 重定向/DNS + CGNAT） | `fix/331-ssrf-hardening` | 待开 | 进行中 |
| #332 | Medium | web 快照正文未净化进事实校验 | `fix/332-snapshot-sanitize` | 待开 | 进行中 |
| #333 | Medium | 系统提示泄漏标记漂移 | `fix/333-prompt-leak-markers` | 待开 | 进行中 |
| #334 | Medium | CSP↔Sentry 配置耦合 + 静态缓存 | `fix/334-csp-sentry-cache` | 待开 | 进行中 |
| #335 | Medium | 租约 fence（ingestion/checkpoint/deletion outbox） | `fix/335-lease-fences` | 待开 | 进行中 |
| #336 | Medium | 前端可访问性与敏感输入 | `fix/336-frontend-a11y-inputs` | 待开 | 进行中 |
| #337 | Low | API 低危收敛 | `fix/337-api-lows` | 待开 | 进行中 |
| #338 | Low | 数据层低危收敛 | `fix/338-data-lows` | 待开 | 进行中 |
| #339 | Low | 运维脚本健壮性 | `fix/339-ops-scripts` | 待开 | 进行中 |
| #340 | Low | 引擎低危收敛 | `fix/340a` / `fix/340b` | 待开 | 进行中 |
| #341 | Low | 前端低危收敛 | `fix/341-frontend-lows` | 待开 | 进行中 |
| #342 | Low | 前端 devDependencies 漏洞治理 | `fix/342-frontend-deps` | 待开 | 进行中 |
| #343 | Low | 改密失效 reset token + 恢复码强度 | `fix/343-auth-lows` | 待开 | 进行中 |

**已登记、不在本批次重复处理**（口径见 `README.md` §3.2 / `docs/requirements/27-agent-audit-remediation.md` / `docs/project-status.md`）：
代码沙箱的 `-E` 环境变量继承与 `os.chdir` cwd 绕过（README §3.2，本批次 #327 一并修复并把 README 限制段更新为已修/剩余）；
R06–R09 引擎评测项（需求 27 挂账）；`/metrics` Prometheus mount 的应用层鉴权（P0-5 #293，由 Caddy 403 + 本机绑定兜底）；U6/U10/U26 前端审计（#336/#341 覆盖其中未修分支）。

## 5. 验收标准（DoD）

- [ ] 24 个 Issue 均关闭（`Closes #<issue>`）或有「不修」结论与理由（§6）。
- [ ] 每个 PR：base=`dev`、单主题、≤300 行/≤6 文件（超出有拆分说明）、PR 描述 6 项齐全、自审评论已发。
- [ ] 每个 PR CI 6 项全绿（lint-and-test 3.11/3.12/3.13 + infra + frontend + e2e）。
- [ ] 中/高风险 PR 有三目标证据：旧行为未回归 / 新行为行为级断言 / mutation 能抓住。
- [ ] 集成分支 `integration/audit-remediation` 依序合并全部 PR 后：`ruff` + 全量 pytest（零依赖 lane 仅剩 5 个已知 OTel 环境失败）+ 6 个仓库守卫 + 前端三连 全绿。
- [ ] 不修项在 §6 登记理由；文档口径漂移（README §3.2、runbook 备份章节、production-readiness 解析限额、需求 26 注销撤销分享）与代码同步。
- [ ] 对外数字（README/figures 的测试数、E2E 数、需求/ADR 篇数）在全部合并后回填。

## 6. 不修项与理由（登记）

| 项 | 理由 | 处置 |
|---|---|---|
| 用户枚举：注册 409 / Passkey `authenticate/begin` 的 `allowCredentials` 数量（审计 L3） | 统一响应会改变注册与 Passkey 登录的既有 UX；彻底消除需邮箱验证/账号恢复流程，属产品决策 | 记入本表；如需做，另立需求 |
| 账号维度限流可被用来定向锁死受害者（审计 L7） | 需明确「失败才计数/成功后清零/退避策略/是否加验证码」的运营口径，改变限流语义 | 记入本表；另立需求评估 |
| eval 口径（R06/R08、引用准确率分母） | 已登记挂账（需求 27），涉及评测口径变更需 bump `SCORER_VERSION` 与断点登记 | 保持需求 27 挂账 |
| 前端 devDeps 中无法安全升级的 major 项（若有） | 生产依赖 0 漏洞；major 升级需单独回归窗口 | #342 PR 内登记剩余项 |

## 7. 影响范围与风险

- **认证/会话**（#321/#322/#323/#343）：改变 MFA 绑定与 step-up 行为、重认证限流、登出 CSRF、Cookie 删除属性；需同步前端与 E2E；回滚 = revert 对应 PR（无数据迁移的项）或保留新列（#322 的 `last_used_step` 可空，旧代码忽略）。
- **迁移**（#322/#330/#338/#339）：只前向、幂等；旧代码可忽略新列/新索引；回滚不写 down 脚本（项目铁律）。
- **上传/备份/运维**（#325/#326/#339）：改变拒绝阈值与同步范围；需在 staging 先验证一次上传与备份 cron。
- **沙箱**（#327）：收紧执行面，可能让既有「本可运行」的沙箱脚本被拒（预期）；`CODE_EXEC_EVIDENCE_ENABLED` 仍默认关。
- **前端**（#328/#336/#341/#342）：登出/换账号行为、登录门焦点、密码输入交互变化；E2E 覆盖。

## 8. 测试策略

- 零依赖 lane（本地）：`py -3.13` + `PYTHONPATH=.deps`，清空 `DR_*`/`DR_TEST_*` 后全量 pytest；已知 5 个 OTel 用例为 `.deps` 漂移环境失败，以 CI 三版本矩阵为准。
- 集成 lane：真实 PG/Redis 由 CI `infra` job 覆盖（迁移幂等 + checks 结构断言 + 仓储/Worker/备份恢复演练）；本地 Docker 不可用时在 PR 内标注「未验证，CI 覆盖」。
- 前端：`tsc --noEmit` + `vitest run` + `vite build`；E2E 由 CI `e2e` job 跑（先 build dist）。
- 守卫：`check_results_whitelist` / `check_frontend_boundary` / `check_public_ip` / `check_doc_consistency` / `check_tailwind_tokens` / `check_ui_patterns` 全过。

## 9. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-11-02 | 文档 | 2026-11-02 全仓审计（0 Critical/3 High/16 Medium/~30 Low） | 建立批次总文档与 Issue/PR 索引、DoD、不修项 | 本 PR |
