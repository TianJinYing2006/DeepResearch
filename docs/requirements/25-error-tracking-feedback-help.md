# 需求 25：错误追踪 + 反馈入口 + 帮助/合规页（批次 A4）

> 状态：**自测**（实现完成：错误追踪 / 反馈 / 帮助 / 注册同意；待合并与部署）。
> 父需求：`docs/requirements/21-product-modules-completeness.md` §4-H/§4-I、§5-A4。
> 一句话：补齐内测上线前的**可观测闭环**（前端白屏/未捕获异常零感知 → 错误聚合）、
> **反馈闭环**（用户反馈无入口 → 站内表单 + 台账）、**合规闭环**（注册同意无留档 → 勾选 + 版本记录），
> 并把帮助入口与法律文本接入产品页面。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 25 |
| 标题 | 错误追踪 + 反馈入口 + 帮助/合规页（批次 A4） |
| 优先级 | P1 |
| 状态 | 自测 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #114 |
| 关联 PR | 待填 |
| 创建 / 更新 | 2026-10-04 |

## 2. 问题背景：现状审计（事实 → 影响）

| # | 现状事实（证据） | 影响 |
|---|---|---|
| F1 | **错误追踪全仓零实现**：无 SDK/DSN/上报端点；`docs/launch-readiness-review.md:45,69` 已登记「前端白屏/未捕获异常零感知」；`production-readiness.md §4 数据流向表`无错误上报项 | 线上问题靠用户口头反馈；无堆栈、无版本关联、无聚合 |
| F2 | 前端仅 React `ErrorBoundary` 兜底（`web/frontend/src/components/ErrorBoundary.tsx:18-20` → `console.error`），**无** `window.onerror` / `unhandledrejection`；无全局错误出口 | 渲染错误之外的所有异常（事件回调、Promise、资源加载）完全丢失 |
| F3 | 后端有结构化错误契约（`web/backend/errors.py`）、审计、进程内指标、可选 OTel/Langfuse（默认关）；**无异常聚合**；`production-readiness.md:145`「错误日志带 run_id/user_id」未完成 | 500 只能靠告警阈值（5xx 率）发现，缺上下文 |
| F4 | 无产品反馈存储/端点/入口；现有 `moderation_appeals`（0014）是**内容审核申诉**（与 run 审核状态强绑定，`main.py:1696-1701`），语义不同 | 用户反馈只有口头/邮件；无法统计、跟进、闭环 |
| F5 | 无 FAQ/帮助中心；页脚仅 LandingPage 有协议链接（`LandingPage.tsx:268-278`）；工作台无页脚 | 常见问题反复人工回答；帮助信息不可发现 |
| F6 | 注册同意是被动文案「注册即表示同意」（`AuthForm.tsx:428-437`），**无勾选动作、无版本/时间留档**；`docs/requirements/21:143` 登记缺口 | PIPL 同意举证链缺失；协议更新后无法证明用户同意的是哪版 |
| F7 | 法律文本为草案模板（`docs/legal/privacy-policy.md` 54 行 / `terms-of-service.md` 44 行），联系方式待补充；隐私政策未提错误诊断数据 | 上线合规前置未完成；新增错误追踪必须同步数据流向登记 |
| F8 | 可复用设施齐全：`Modal`（焦点陷阱/portal）、`ReportView`（markdown 渲染，法律弹窗已用）、`_audit()`、限流器、`config.py` dataclass 模式、`/api/options` 下发模式、`alerts.py` 告警状态机、FakeStore/Playwright 路由拦截模式 | 实现成本集中在数据与页面本身 |

## 3. 目标与非目标

**目标**

1. 错误追踪：Sentry 协议前后端接入（DSN 环境变量，默认关）；PII 最小化；**数据不出境**为默认部署形态；
2. 反馈：站内反馈表单（登录用户）+ `user_feedback` 台账 + 管理 CLI 查询 + 成功/失败反馈；
3. 帮助：FAQ 文档 + 帮助弹窗（工作台与落地页可达）；
4. 合规：注册同意勾选 + `user_consents` 版本留档；隐私政策/数据流向同步更新；
5. 运维：自托管错误服务进 compose（可选 profile）；发布手册补错误追踪核对与注入演练。

**非目标（登记不纳入本次）**

- 会话回放（Session Replay）、性能追踪（Web Vitals）、产品分析埋点——登记后续；
- 反馈的公开看板/投票/路线图（邀请制阶段无必要）；
- 匿名反馈提交（邀请制+防滥用优先；登记后续）；
- 工单 SLA / 自动分流（反馈量级未到；先人工 `feedback-list`）。

## 4. 调研结论与设计策略（问题 → 备选 → 为什么更好 → 代价）

### D1 错误追踪形态：Sentry SDK + DSN 可切换，默认自托管（数据不出境）

- 调研事实：Sentry Cloud 服务器在海外，存在**数据出境合规风险**（本仓 `production-readiness.md §3.10` 要求数据出境评估、默认不外发用户内容）；Sentry 自托管官方最低 16GB 内存（40+ 容器），对本项目过重；**GlitchTip**（MIT）完全兼容 Sentry SDK（只换 DSN），512MB 内存 + PG/Redis（我们已有）即可跑；阿里云 ARMS RUM 也兼容 Sentry SDK（DSN 指向 SLS），数据在国内但为托管 SaaS、按 PV 计费。
- 决策：代码只依赖 **Sentry 协议**（`sentry-sdk` / `@sentry/browser`），DSN 走环境变量（默认空 = 全关）；部署形态可选：
  - **默认（推荐）**：staging/生产自托管 GlitchTip（compose 一键，数据不出境）；
  - 备选：阿里云 ARMS RUM（换 DSN 即可）；Sentry Cloud 仅经数据出境评审后启用。
- 代价：GlitchTip 无会话回放/分布式追踪（本需求不需要）；自托管需要一个小实例（~512MB）。

### D2 PII 策略：默认不采内容，只采技术上下文

- 调研事实：错误追踪若开启 `send_default_pii` 会把请求头/Cookie/用户信息带出去；反馈组件最佳实践要求「自动捕获页面/环境，不问用户重复信息」。
- 决策：前后端 `send_default_pii=false`；`before_send` 钩子清洗（去掉 Cookie/Authorization/query 中的 token）；用户仅带内部 `user_id`（不带邮箱）；release=git SHA、environment=staging/production；采样率与开关可配。错误事件里的业务上下文只加 `request_id` / `run_id`（与既有 request-id 中间件串联，顺带完成 F3 的未勾选项）。
- 代价：排障时看不到用户输入（需要时靠用户主动反馈补充）。

### D3 前端捕获：SDK 自动接 + ErrorBoundary 显式上报

- 决策：`@sentry/browser` 在 `main.tsx` 按 `/api/options` 下发的 DSN 初始化（无 DSN 不初始化，零开销）；SDK 自带 `window.onerror`/`unhandledrejection`；`ErrorBoundary.componentDidCatch` 增加 `captureException`；登录/退出时 `setUser(id)` / `setUser(null)`。
- 为什么更好：DSN 运行时下发 → 同一构建产物可跑任何环境（与 `/api/options` 既有模式一致），不需要构建时注入。
- 代价：DSN 出现在前端属正常（Sentry DSN 本就是公开的写入凭据）。

### D4 反馈入口：账号条「反馈」+ 页脚链接 → 弹窗表单（3 字段内）

- 调研事实：站内组件响应率是邮件渠道的 5~10 倍；最佳实践 = 持久但低干扰的入口 + 1~3 字段（分类/正文/可选联系方式）+ 自动捕获上下文 + 成功文案；「每个字段约砍半完成率」。
- 决策：`user_feedback` 表 + `POST /api/feedback`（登录用户，限流 + 审计）；表单仅：分类（bug/建议/其他）+ 正文（10~2000 字）+ 可选联系方式；自动附 `page`（前端传入枚举）+ `request_id`；工作台账号条与落地页页脚各放入口，共用 Modal 表单。
- 为什么更好：与我们邀请制内测的规模匹配（不引入第三方组件/看板）；登录态天然可跟进。
- 代价：匿名用户（落地页访客）不能提交（登记后续）。

### D5 帮助中心：`docs/help/faq.md` + `/api/help/faq` + 帮助弹窗

- 决策：FAQ 用 markdown 单文件（与 `docs/legal` 同模式），端点只读返回；前端复用 `Modal` + `ReportView` 渲染；落地页页脚与工作台页脚可达。
- 为什么更好：无路由改造、内容与代码分离（运营可改）；与法律文档渲染链路一致。
- 代价：无搜索/分类（FAQ 规模小，目录锚点即可）。

### D6 注册同意：勾选框 + `user_consents` 版本留档

- 调研事实：PIPL 场景需要「知情同意」可举证；协议会更新，需记录同意的是哪一版。
- 决策：注册表单增加必选勾选框「我已阅读并同意《用户协议》与《隐私政策》」（未勾选禁用提交）；新增 `user_consents` 表（`user_id / doc_type(terms|privacy) / version / agreed_at / ip_hash`）；`version = sha256(文档内容)[:12]`，注册成功事务内写入两行；存量用户不回填（登录不拦，登记说明）。
- 代价：管理员 CLI 创建的用户无勾选记录（CLI 场景记 `source=admin` 可辨识；本次不阻塞）。

### D7 合规同步：隐私政策 + 数据流向登记

- 决策：`privacy-policy.md` 增「错误诊断数据」小节（采集什么/存哪/不出境/保留期）；`production-readiness.md §4` 数据流向表加「错误追踪（自托管 GlitchTip，境内）」行；发布手册 §2.4d 增错误服务核对 + 注入演练（前端抛错、后端 500 各一次确认可达）。

## 5. 详细设计

### 5.1 配置（`config.py` 新增 `ObservabilityConfig`；`.env.example` 同步）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DR_SENTRY_DSN` | 空 | 后端 DSN（空 = 关闭；GlitchTip/ARMS/Sentry 均可） |
| `DR_SENTRY_DSN_FRONTEND` | 空 | 前端 DSN（经 `/api/options` 下发；空 = 前端不初始化） |
| `DR_SENTRY_ENVIRONMENT` | `staging` | 环境标签 |
| `DR_SENTRY_TRACES_SAMPLE_RATE` | `0` | 性能追踪采样（本需求不做追踪，保持 0） |
| `DR_SENTRY_RELEASE` | 空 | 发版标识（部署时注入 git SHA；空则不设） |

### 5.2 后端接入（`web/backend/observability.py` 新模块）

- `init_error_tracking()`：DSN 空则 no-op；`sentry_sdk.init(integrations=[FastapiIntegration, ...], send_default_pii=False, before_send=_scrub)`；在 `main.py` 启动与 `worker.py` 入口调用；
- `_scrub(event)`：删 `request.cookies` / `request.headers` 敏感键 / query 中 `token|reset`；`tags` 补 `request_id`（从中间件状态）；
- 依赖：`sentry-sdk[fastapi]` 进 `requirements.txt` + lock（唯一新依赖）。

### 5.3 前端接入

- 依赖 `@sentry/browser`；`main.tsx` 启动时若 `/api/options.sentry_dsn` 非空则 `Sentry.init({dsn, environment, release, sendDefaultPii:false, tracesSampleRate:0})`；
- `ErrorBoundary.componentDidCatch` → `Sentry.captureException(error, {contexts})`；
- 登录/注销同步 `Sentry.setUser({id})` / `setUser(null)`；
- `/api/options` 增字段 `sentry_dsn` / `sentry_environment` / `release`。

### 5.4 反馈（迁移 0020 + 端点 + CLI + 前端）

- 表 `user_feedback`：`feedback_id text PK`、`user_id text`（无外键，删除用户保留线索）、`category text CHECK (bug|idea|other)`、`message text`、`contact text`（可空）、`page text`、`request_id text`、`status text DEFAULT 'new' CHECK (new|reviewing|closed)`、`created_at`；索引 `(user_id, created_at DESC)`、`(status, created_at DESC)`；
- `POST /api/feedback`：登录 + CSRF + 限流（复用 `SUBMIT_LIMITER` 键 `feedback:{user_id}`）；message 10~2000、category 枚举、contact ≤200；审计 `feedback_submitted`（detail 不含 message 正文，只记 category/page 长度）；返回 `{ok, feedback_id}`；
- 管理 CLI：`admin.py feedback-list [--status] [--limit]`（打印时间/用户/category/摘要前 80 字）；
- 前端 `FeedbackModal`：分类下拉 + 正文 + 可选联系方式 + 提交；成功态文案；错误 role=alert；testid `feedback-*`。

### 5.5 帮助（FAQ 文档 + 端点 + 弹窗）

- `docs/help/faq.md`：账号与邀请 / 知识库上传（支持格式、大小、处理时长、失败处理）/ 研究流程与配额 / 导出与分享 / 隐私与安全（会话管理、注销、数据去向）——每节 3~6 问；
- `GET /api/help/faq` → `{markdown}`（文件缺失时结构化 503）；
- 前端 `HelpModal`（Modal + ReportView），入口：落地页页脚 + 工作台页脚「帮助」；testid `help-*`。

### 5.6 注册同意（迁移 0020 + 注册链路 + 前端）

- 表 `user_consents`：`user_id text`、`doc_type text CHECK (terms|privacy)`、`version text`、`agreed_at timestamptz DEFAULT now()`、`ip_hash text`；主键 `(user_id, doc_type)`；
- 版本：`sha256(文档内容).hexdigest()[:12]`，由 `GET /api/legal/{doc}` 附带返回 `version`（前端展示「当前版本」可选）；
- `POST /api/auth/register`：请求体加 `agree_terms: bool`（必须 true，否则 400 `invalid_request`「请先同意用户协议与隐私政策」）；注册成功后写两条 consent（terms/privacy）；审计 `register_success` detail 加 `consents: [versions]`；
- 前端注册表单：勾选框 + 协议链接（打开既有 LegalModal）；未勾选禁用「注册并登录」。

### 5.7 运维（compose + 发布手册）

- `docker-compose.staging.yml` 增 `glitchtip`（+其 PG 复用现有 postgres？GlitchTip 需要独立库：用现有 postgres 实例建 `glitchtip` 数据库与用户，或独立容器；**设计：复用现有 postgres 实例**，migrate 时创建库；profile `errors`）——实施时按最小改动落；
- 发布手册 §2.4d：错误服务可达性核对（DSN 配置后注入演练：前端 `throw` 按钮/后端 `/api/ops/errors-probe`？——**不做专用探针**，用真实 500 演练）+ 隐私政策数据流向更新核对。

## 6. 测试策略

| 层 | 覆盖 |
|---|---|
| 单测 | `test_error_tracking.py`：DSN 空 no-op；`_scrub` 去除 Cookie/Authorization/token query；事件带 request_id |
| 单测 | `test_feedback_api.py`：成功入库 + 审计（无正文）+ 限流 429 + 参数校验 + 未登录 401 |
| 单测 | `test_consents.py`：注册未勾选 400；勾选后写入两行且 version=文档 hash；`GET /api/legal` 带 version |
| 回归 | auth/session 既有用例（注册请求体新增字段的兼容性：默认 false 会破坏既有测试？——**注册测试需同步加 `agree_terms: true`**，属预期接口演进） |
| E2E | 反馈入口提交成功文案；帮助弹窗渲染 FAQ；注册勾选框交互（未勾选禁用、勾选后可提交）；`/api/options` 无 DSN 时不初始化（无网络请求断言） |
| infra | 迁移 0020 幂等（CI infra job 自动覆盖） |
| 演练（部署后 smoke） | 前端抛错、后端 500 各一次，在错误服务控制台确认可达（发布手册清单） |

## 7. 影响范围与风险

| 风险 | 对策 |
|---|---|
| 错误上报误带 PII | `send_default_pii=false` + `_scrub` 白名单式清洗 + 单测锁定 |
| 新依赖体积（前端 SDK ~30KB gzip） | 按需初始化（无 DSN 不加载逻辑仍会打包——用动态 `import()` 延迟加载，无 DSN 零成本） |
| GlitchTip 资源占用 | 复用现有 PG；限制内存（compose 256~512M）；profile 可选启用 |
| 注册接口变更破坏既有客户端 | 新增字段默认 false + 明确 400 文案；同 PR 更新全部测试与 E2E |
| 法律文本仍为草案 | 本次只做「接入 + 同意留档 + 错误诊断数据补充」，不冒充法律定稿（保持文首草案标注）；联系方式由运营补充 |
| 反馈垃圾/滥用 | 登录 + 限流 + 长度上限 + 审计 |

## 8. 验收标准（DoD）

- [x] 错误追踪：DSN 配置即启用（前后端），默认关；PII 最小化单测通过（后端 `send_default_pii=False` + 清洗；前端 v11 `dataCollection` 关闭用户信息/Cookie/请求体/查询参数）；release/environment 标签就位
- [x] 前端：ErrorBoundary 上报 + SDK 自动接 `window.onerror`/Promise（SDK 初始化即接管）；无 DSN 时动态模块不加载（独立懒 chunk）
- [x] 反馈：`user_feedback` 迁移幂等；`POST /api/feedback` 入库 + 审计（不含正文）+ 限流；CLI `feedback-list`；前端入口（账号条 + 落地页页脚帮助）提交成功/失败可见
- [x] 帮助：`/api/help/faq` + 弹窗渲染；落地页页脚「帮助中心」+ 工作台「帮助」入口可达
- [x] 合规：注册勾选（未勾选不可提交，服务端 422）+ `user_consents` 版本留档；隐私政策增错误诊断小节；数据流向表加行
- [x] 运维：compose `errors` profile（GlitchTip + 独立库初始化）；发布手册 §2.4d；注入演练清单
- [x] 测试：`test_error_tracking.py`(4) + `test_feedback_api.py`(4) + 注册同意留档 + 既有注册用例适配；E2E 新增 3；CI 全绿
- [ ] 文档：需求 21 §5 A4 勾选 + §10 回填；飞书镜像同步（合并后执行）

## 9. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-04 | 新建 | 需求 21 批次 A4 立项 | 先调研后方案：代码现状审计（F1~F8）+ 错误追踪选型调研（Sentry Cloud 出境风险 / GlitchTip 512MB 兼容 / ARMS 兼容 DSN）+ 反馈组件最佳实践 + D1~D7 决策 + 详细设计 + DoD | 本文档 |
| 2026-10-04 | 修订 | 决策确认 | 用户确认：D1 默认自托管 GlitchTip（DSN 可切 ARMS/Sentry）；D4 页脚入口 + 弹窗（登录用户）；D6 独立留档表 `user_consents`。开始实施 | 本文档 |
| 2026-10-04 | 实施 | 需求 25 落地 | 迁移 0020（user_feedback / user_consents）；`ObservabilityConfig` + `observability.py`（Sentry 协议、PII 清洗、request_id tag、失败不阻断）；`POST /api/feedback`、`GET /api/help/faq`、legal 带 version、注册 `agree_terms` + 同意留档；`admin feedback-list`；前端 `@sentry/browser`（v11 `dataCollection`、动态懒加载）+ ErrorBoundary 上报 + 反馈/帮助弹窗 + 注册勾选；`docs/help/faq.md`；compose `errors` profile + 发布手册 §2.4d + 隐私政策/数据流向更新；测试：错误追踪 4 / 反馈 4 / 注册同意 / E2E 3 | #114 |
