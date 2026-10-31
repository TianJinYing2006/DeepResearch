# Changelog

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 风格，版本号遵循语义化版本。
对外数字（测试数、提交数等）以 [`docs/figures.md`](docs/figures.md) 为唯一来源，本文件不重复维护易漂移的数字。

## [Unreleased]

### Changed

- 注册改为**开放注册**（邮箱 + 密码 + 同意条款），不再需要邀请码；仍受注册/登录限流、单用户每日配额与月度预算约束。
- **quick 档模型改为 `qwen-flash`（Issue #310）**：百炼 `qwen-turbo` 持续 429 `limit_requests` 且 quick 档把四个核心角色钉在 turbo ⇒ quick 不可用；`PROFILE_VERSION` v1→v2，补 qwen-flash 成本单价；standard/deep 不变。

### Removed

- 邀请制/邀请码：后端邀请码校验与 `/api/options.invite_only`、管理 CLI `create-invite` / `list-invites` / `revoke-invite`、前端邀请码字段与 `?invite=` 邀请弹窗、相关 E2E 全部删除。

### Fixed

- **模型限流错误归因与失败进度显示（Issue #308）**：provider 429/5xx/网络异常不再被记为 `internal`，统一为 `llm_error`（非工具层；错误卡可重试、归因 `llm`、文案可行动）；失败/取消/超时的运行进度不再显示「当前阶段 完成 100%」，改为「运行失败 / 已停止 / 已到时限停止」；补齐 `llm_error` / `internal` / `token_limit` / `recursion_limit` 的错误规格。
- **LLM 有界重试（Issue #310）**：任务时限存在时 SDK 重试被关闭（`max_retries=0`）；新增客户端层有界重试，仅对 provider 429/5xx/网络错误生效（默认 2 次、指数退避+抖动、受剩余时限约束，非 provider 错误/流式不重试），避免一次瞬时限流打穿整轮 run。

### Security

- 开放注册后的成本/滥用风险登记：后续评估邮箱验证、CAPTCHA 或邀请式灰度回退开关。

## [0.2.0] - 2026-10-26

自 `v0.1.0`（2026-09-07，W1–W6 里程碑）以来的研究引擎、L3 多用户与产品化底座收口版本。

### Added

- **L3 多用户与账户体系（需求 20）**：邀请制注册、`__Host-` Cookie、NIST 口令策略、空闲超时、会话/设备管理、新设备登录提醒、TOTP MFA + 恢复码 + 登录 step-up、Passkey（WebAuthn）注册/登录/管理、邮件自助找回密码、管理 CLI。
- **研究质量与可恢复性（需求 34/35）**：网页快照落盘、逐字引文 + offset、`find_in_source`、证据链分级处置与红线锁定、交付自检、evidence manifest、来源身份分组、PG checkpoint 跨进程恢复、报告流式（REPORT_DELTA/RESET）、档位耗时标定。
- **人机协作与工具面（需求 36/37）**：澄清/计划审批 HITL 回路、报告后追问、MCP 消费端全链路、对外 MCP Server（四工具 + Bearer 鉴权）。
- **导出与分享（需求 26/38）**：统一 `ExportDocument`、md/json/docx 同步导出、PDF 异步导出作业、历史报告导出、只读分享链接（7 天有效 / 可撤销）。
- **知识库（需求 23）**：三层数据/版本化重建、RRF 融合、管理 API 与评测脚手架。
- **产品体验**：品牌更名「爱考据 iKaoju」、研究档案主题工作台与报告版式、证据侧栏、登录/落地页、任务列表搜索/重命名/置顶/归档/重试、移动端适配、ICP 备案展示。
- **可观测与运营**：OTel 原生埋点（traces/metrics）、Prometheus 指标、告警外送（webhook）、错误追踪/反馈/合规页、容量压测与容量模型、申诉/复核状态机、审核 provider 抽象。
- **工程与治理**：CI 6 项检查（lint 矩阵 ×3 + infra + frontend + e2e）、公开仓脱敏守卫、ADR 体系（17 篇）、`docs/figures.md` 单一数字源、CONTRIBUTING v2.2。

### Security

- 公开仓真实 IP 脱敏 + `tools/check_public_ip.py` 守卫；部署主机/密钥迁移 GitHub Secrets，仓库不再保存真实 host。
- 应用层全局安全响应头：CSP、HSTS（staging/production）、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`。
- MFA step-up 5 次/分/用户、MFA 管理 10 次/分、Passkey 管理 10 次/分（Redis 滑动窗口，进程内兜底）；命中 429 + 审计。
- Passkey 注册要求当前密码重确认（防会话劫持后持久化绑定）。
- 反代信任：compose 默认可信 CIDR + Caddy 覆盖 `X-Forwarded-For`；应用按真实客户端 IP 限流/审计。
- 备份/恢复凭据不再进入 `pg_dump`/`psql`/`pg_restore` argv；`backup-cron.sh` 改用 0600 临时 env-file；生产强制 `BACKUP_REQUIRE_ENCRYPTION=1`。
- 公网 `GET /metrics` 统一由 Caddy 返回 403，指标仅供本机/内网抓取。

### Changed

- **治理**：CONTRIBUTING v2.2 明确 L3-A/B 边界、5 道产品化门禁、PR ≤300 行 / ≤6 文件、发布批次（2~4 周）、三档门禁、公开仓脱敏与 AI 规则；ADR-0017 边界重划、ADR-0009 转正、ADR 索引修正。
- **CI**：PR 校验覆盖所有 base；apt/pango/CJK 字体/Playwright 浏览器缓存；关键步骤超时；e2e 从 10~18 分钟回到 ~5 分钟。
- **部署**：staging 自动部署（CI 绿后）、腾讯云镜像源、部署分段耗时/超时、Caddy SSE 反代、Caddy 配置变更时重建容器再 reload。
- **测试**：`tests/conftest.py` 在零依赖 lane 下忽略 `.env` 来源的 `DR_TEST_*`，本地与 CI 同口径；集成 lane 需 `DR_TEST_INTEGRATION=1` 显式 opt-in。

### Fixed

- 修复本机 `.env` 配置不可达 PG/Redis 时 80 个假失败（掩盖真回归）。
- 修复 CI apt/字体/Playwright 偶发 10~18 分钟等待。
- 修复部署 `caddy reload` 静默不生效（单文件 bind mount 旧 inode + 失败只 WARN）。
- 修复安全响应头缺失、MFA/Passkey 接口无限流、Passkey 注册无重认证、备份 DSN argv 泄漏、公网 `/metrics` 暴露。
- 修复 staging 构建 apt/pip 镜像超时、部署脚本无阶段超时。

### Known limitations

- 当前处于 **L3-A/B 邀请制内测（≤10 用户）**，5 道产品化门禁未全部通过（ADR-0017）。
- 服务器默认口令轮换、公网 IP 更换/安全组（P0-6）仍需运维窗口。
- **应用版本回滚演练未执行**：SSH forced command 只允许默认 `deploy.sh`，无法远程传 `--rollback`；需服务器侧受控入口（Issue #299，runbook §9 如实登记）。
- 前端 Sentry 若启用，需通过 `DR_CSP_CONNECT_SRC` 追加其 origin，否则上报会被 CSP 拦截。
- 行级安全（RLS，P0-7）尚未评估/实施。

### Upgrade notes

- 发布前先做备份并验证可读（`tools/backup.sh` + `tools/backup-verify.sh`）；迁移 0001–0027 只前向，数据回退依赖备份恢复。
- 应用回滚使用镜像 tag `rollback-<sha>`（`deploy.sh --rollback`）；数据库不回滚。
- 运行环境：Python 3.11–3.13、Node 22、PostgreSQL 16、Redis、Qdrant、MinIO/COS。

## [0.1.0] - 2026-09-07

W1–W6 里程碑版本（初始研究引擎、Web 工作台与基础部署）。

[0.2.0]: https://github.com/TianJinYing2006/DeepResearch/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/TianJinYing2006/DeepResearch/releases/tag/v0.1.0
