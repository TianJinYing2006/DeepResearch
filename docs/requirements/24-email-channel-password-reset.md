# 需求 24：邮件通道 + 自助找回密码（批次 A3）

> 状态：**自测**（实现完成：SMTP 通道 / 自助找回端点 / 前端四态 / 测试；待合并与部署）。
> 父需求：`docs/requirements/21-product-modules-completeness.md` §5-A3 / §6.2。
> 上游设计：`docs/requirements/20-login-auth-redesign.md` §6（P1-1 邮件通道）、§7.5（reset token 约定）、§7.6（审计约定）。
> 一句话：建立**邮件外发通道**，在其上完成**匿名自助找回密码全流程**（申请 → 收信 → 重置），
> 补齐邀请制阶段账号闭环的最后缺口；限流、审计、防枚举按既有安全口径执行。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 24 |
| 标题 | 邮件通道 + 自助找回密码（批次 A3） |
| 优先级 | P1 |
| 状态 | 自测 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #109 |
| 关联 PR | 待填 |
| 创建 / 更新 | 2026-10-04 |

## 2. 问题背景：现状审计（事实 → 影响）

| # | 现状事实（证据） | 影响 |
|---|---|---|
| F1 | **全仓零邮件发送实现**：无 SMTP/API 客户端代码、无邮件依赖、无模板；`config.py` 无 mail 配置（全仓搜索 smtp/mail 仅文档命中；`requirements.txt` 无邮件库） | 重置 token 只能人工转达；任何通知类需求（新登录提醒）全部被阻塞 |
| F2 | reset token 唯一交付方式 = **管理员 CLI stdout**（`web/backend/admin.py:325-337` 明文打印一次）；无用户自助入口（无 `/api/auth/forgot`） | 用户忘记密码必须联系管理员，账号闭环不成立（需求 21 §6.2） |
| F3 | 重置**消费端已完备**：`POST /api/auth/reset`（`web/backend/main.py:1180-1197`）→ `complete_password_reset` 原子单次消费 + 改密 + 吊销全部会话（`web/backend/store.py:1225-1247`）；token 只存 SHA-256（migrations/0010:18-31） | 本次**只需补「申请 + 投递」半程**，消费链路复用，不重复造 |
| F4 | reset 限流仅 IP 单维度（`main.py:1186` 复用 `LOGIN_LIMITER`），且**被限流时不写审计**（对照登录 `login_rate_limited` main.py:1071） | 邮箱维度刷取无约束；攻击/误用不可见 |
| F5 | 前端**无找回入口**：`AuthForm.tsx` 仅 `'login' \| 'register'` 两态（`web/frontend/src/features/auth/AuthForm.tsx:5`）；全前端搜索「忘记密码/forgot/reset」零命中 | 即使后端支持，用户也无路径进入 |
| F6 | 审计禁令：detail 禁密码/token/cookie/完整邮箱（需求 20 §7.6 行 219） | 申请类审计只能用 `email_hash`，需沿用既有哈希口径 |
| F7 | 无邮件 sandbox：CI 与 `docker-compose.staging.yml` 均无 MailHog；staging 服务清单 = postgres/redis/qdrant/migrate/minio(profile)/api/worker/caddy(profile) | 演示与联调需要本地可捕获的邮件出口 |
| F8 | 可复用设施齐全：限流构造器 `make_limiter`（`web/backend/ratelimit.py:105-117`，Redis 滑动窗口 fail-open）、审计 `_audit`（`main.py:827-845`）、token 原语 `new_token/token_hash`（`web/backend/auth.py:92-101`）、启动清理 `purge_expired_password_resets`（`store.py:1215-1223`） | 本次实现成本集中在邮件通道本身 |

## 3. 目标与非目标

**目标**

1. 邮件通道：可配置 SMTP 外发（任意服务商），未配置时结构化降级（不静默）；
2. 自助找回：`POST /api/auth/forgot`（防枚举）→ 邮件链接 → 既有 `POST /api/auth/reset` 完成；
3. 前端：登录页「忘记密码」入口 + 重置表单（含无效/过期 token 态）；
4. 安全：IP + 邮箱哈希双维度限流（限流写审计）、冷却期、审计全链路（`email_hash`）、邮件正文不含敏感信息；
5. 运维：SPF/DKIM/DMARC 配置清单进发布手册；staging/本地可选 MailHog profile。

**非目标（登记不纳入本次）**

- 新登录/新设备通知邮件（需求 20 §6 P1-1 剩余部分）——通道建成后作为下一个小需求；
- `users.email_verified_at` 门槛（需求 20 行 232）——邀请制下邮箱由邀请人背书，暂不启用验证门槛；
- 邮件 API 服务商适配器（阿里云 DirectMail API / 腾讯云 SES API）——SMTP 已覆盖其全部能力，API 适配按需再加；
- 短信通道、邮件退信处理（bounce）自动化。

## 4. 设计策略（问题 → 备选 → 为什么更好 → 代价）

### D1 邮件发送实现：SMTP-first（stdlib `smtplib`）+ `Mailer` 接口

- 问题：全仓零邮件实现（F1），选型不能绑定单一服务商（需求 20 §9：国内送达率与成本是前置约束）。
- 备选：① 直接引入第三方 SDK（sendgrid/mailgun）；② HTTP API 直连某国内厂商；③ stdlib `smtplib` + 配置化 SMTP。
- 为什么更好：**零新依赖**（`smtplib`/`email` 标准库）、任意服务商通吃（阿里云 DirectMail / 腾讯云 SES / Resend 均提供 SMTP 出口）、部署方换商不改代码；`Mailer` 协议预留 `ApiMailer` 实现位。
- 代价：SMTP 连接参数（TLS 模式）需部署方正确配置；送达率依赖服务商与 DNS 配置（SPF/DKIM 属运维，见 §5.6）。

### D2 自助找回流程：防枚举恒 200 + 复用消费端

- 问题：token 交付依赖管理员（F2）；消费链路已完备（F3）。
- 备选：① 申请接口返回「邮箱不存在」错误（易枚举）；② 恒 200（不泄露存在性）；③ OTP 数字码代替链接。
- 为什么更好：**恒 200** 是业界防枚举标准做法；链接形态复用既有 `/api/auth/reset` 与前端 `#reset=<token>` 直达，无新增消费逻辑；OTP 需额外输入步骤与防爆破设计，收益不抵成本。
- 代价：用户输错邮箱时也看到「已发送」提示（文案提示「若邮箱存在」）；需审计 `mail_send_failed` 让运维发现真实投递问题。
- token 语义沿用需求 20 §7.5：30 分钟、单次消费、只存 SHA-256；`created_by='system'`（F3 表结构已支持）。

### D3 防滥用：IP + 邮箱哈希双维度限流 + 冷却期

- 问题：现状仅 IP 单维度且限流无审计（F4）。
- 备选：① 仅提高 IP 限流；② IP + 邮箱双维度（登录已用此模式 main.py:1069-1070）；③ 图形验证码。
- 为什么更好：②与登录同构、零新设施（F8），同时约束「单 IP 广撒网」与「单邮箱被反复轰炸」；冷却期（同邮箱 60s 内不重复发）避免邮件轰炸与成本浪费。
- 代价：Redis 抖动时 fail-open（既有口径）；验证码留待公开注册阶段。

### D4 前端入口：`AuthForm` 扩展 forgot / reset 两态

- 问题：前端无入口（F5）。
- 备选：① 独立路由页；② `AuthForm` 内新增两态 + URL 参数驱动。
- 为什么更好：②不引入路由改造（现有单页壳），链接 `/#reset=<token>` 打开即重置；登录/注册/找回在同一组件内切换，样式复用。
- 代价：URL 带 token 出现在浏览器历史（`#` 片段不进服务端日志；重置成功后前端 `history.replaceState` 清掉）。

### D5 范围控制：先通道与找回，通知后置

- 问题：需求 20 P1-1 打包了「邮件通道 + 新登录通知 + 自助找回」。
- 备选：① 一次全做；② 本次只做通道 + 找回，通知登记后续。
- 为什么更好：②与需求 21 §5-A3 的范围一致；通知依赖「发送策略/频率控制/退订」设计，独立小需求更干净；通道先上线才能验证送达率。
- 代价：新设备提醒晚一个需求周期；需求 20 文档 P1-1 行需标注拆分。

### D6 测试：Mailer 假体捕获 token 全链路 + MailHog 可选

- 问题：邮件不可在 CI 真发（无 sandbox，F7）。
- 备选：① CI 起 MailHog 容器；② 单测注入 `FakeMailer` 捕获邮件正文提取 token，走完整 HTTP 链路；③ E2E 覆盖真实邮件。
- 为什么更好：②零基础设施、断言可到「邮件正文含重置链接」级别；E2E 只覆盖 UI 态（提交后成功文案、无效 token 报错）；MailHog 作为 staging/本地 `local-mail` profile 供人工联调。
- 代价：真实 SMTP 连通性靠部署后 smoke（发布手册 checklist）。

## 5. 详细设计

### 5.1 配置（`config.py` 新增 `MailConfig`；`.env.example` 同步）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DR_SMTP_HOST` / `DR_SMTP_PORT` | 空 / 465 | 空 = 未配置（`/api/auth/forgot` 返回 `mail_unavailable` 503，提示联系管理员） |
| `DR_SMTP_USER` / `DR_SMTP_PASSWORD` | 空 | SMTP 认证（服务商授权码） |
| `DR_SMTP_FROM` | 空 | 发件人（需与服务商域名一致，配合 SPF/DKIM） |
| `DR_SMTP_TLS` | `ssl` | `ssl`（465）/ `starttls`（587）/ `none`（本地 MailHog 1025） |
| `DR_MAIL_BASE_URL` | `http://localhost:5173` | 邮件链接基址（前端地址） |
| `DR_RESET_COOLDOWN_SECONDS` | 60 | 同邮箱重发冷却 |

### 5.2 模块 `web/backend/mailer.py`

- `Mailer` 协议：`send(to, subject, text, html=None) -> None`；`SMTPMailer`（stdlib）实现；`FakeMailer`（tests）。
- 工厂 `get_mailer()`：未配置 host/from → 返回 `None`（端点结构化降级）；发送异常上抛由端点捕获审计。
- 启动日志打印「邮件通道：已配置/未配置」（不含凭据）。

### 5.3 端点 `POST /api/auth/forgot`（`main.py`）

- 请求 `{email}`；流程：
  1. IP 限流（`LOGIN_LIMITER`，键 `forgot:ip`）+ 邮箱哈希限流（`LOGIN_ACCOUNT_LIMITER` 模式，键 `forgot:acct`）；被限流 → 429 + 审计 `forgot_rate_limited`（IP 与 email_hash）；
  2. 查用户（存在且 active）：冷却检查（最近未消费 token `created_at` 60s 内 → 跳过发送但恒 200）；创建 token（`created_by='system'`）；发信（失败 → 审计 `mail_send_failed`，仍恒 200）；审计 `forgot_requested`（email_hash）；
  3. 用户不存在：不做任何动作，恒 200（时序尽量对齐：同样执行限流检查）。
- 响应恒 `{"ok": true}`（防枚举）；未配置邮件通道 → `mail_unavailable` 503（运维事实，非存在性泄露）。
- 复用 `store.create_password_reset`（先删旧未消费 token，兄弟互斥）；审计 detail 只放 `email_hash`（sha256(email.lower()) 前 16 位）。

### 5.4 邮件模板

- 纯文本 + 简 HTML；内容：重置链接 `{DR_MAIL_BASE_URL}/#reset={token}`、有效期 30 分钟、非本人忽略说明、不展示用户名/邮箱。
- 主题：「重置你的 DeepResearch 密码」。

### 5.5 前端（`AuthForm.tsx`）——市面调研与采用设计

**调研结论**（UX Patterns Guide 2026 / uxpatterns.dev / RunSignup 改版案例 / WCAG 2.2 SC 3.3.8 可访问认证口径）：

1. **请求态**：中性文案防枚举；给「返回登录」路径；提示检查垃圾邮件；重发以**服务端冷却**为准（不是纯前端计数）；
2. **失效/已用链接**：必须给「重新申请」活路（「链接已过期。重新申请」优于「Invalid token」死胡同）；
3. **新密码态**：规则**从开始就常显**（不能藏 tooltip）；逐条**实时反馈灰→绿**（避免红/绿对比对色觉障碍不友好）；确认一致性提示；显示/隐藏切换；允许粘贴与密码管理器（`autocomplete="new-password"`）；成功后**不自动登录**，回登录页；
4. **布局**：居中卡片与登录页一致（RunSignup 案例：一致性优先）；每步都有「返回登录」。

**采用设计**（`Mode = 'login' | 'register' | 'forgot' | 'reset'`；`#reset=<token>` 进入 reset，成功后 `history.replaceState` 清 hash）：

| 态 | 内容 |
|---|---|
| forgot 请求 | 标题「找回密码」；说明「输入注册邮箱，我们会发送重置链接」；邮箱 + 主按钮「发送重置链接」；「返回登录」 |
| forgot 已发送 | 「如果该邮箱已注册，我们已发送重置链接」+「链接 30 分钟内有效，没收到请检查垃圾邮件」；重发按钮（60s 冷却倒计时，与服务端冷却对齐）；「返回登录」 |
| reset 设置 | 标题「设置新密码」；**常显规则**：至少 12 位 / 不含常见口令 / 不含邮箱账号名 / 不含服务名；新密码 + 确认密码逐条实时灰→绿 + 一致提示；显示/隐藏；主按钮「重置密码」 |
| reset 失效 | 「链接无效或已过期」+ 主按钮「重新申请」（切 forgot 请求态）；「返回登录」 |
| reset 成功 | 「密码已重置，所有设备已退出登录，请用新密码重新登录」+「返回登录」（不自动登录） |

- 新 testid：`auth-forgot-link` / `auth-forgot-email` / `auth-forgot-submit` / `auth-forgot-sent` / `auth-forgot-resend` / `auth-reset-password` / `auth-reset-confirm` / `auth-reset-submit` / `auth-reset-invalid` / `auth-reset-done`。

### 5.6 运维（`docs/operations/release-runbook.md` 追加清单）

- DNS：SPF（含服务商 include）、DKIM（服务商给出的公钥）、DMARC（`p=none` 起步）；发件域名与 `DR_SMTP_FROM` 一致；
- 上线 smoke：真实邮箱申请 → 收信 → 完成重置 → 全设备会话吊销确认；
- staging：`docker-compose.staging.yml` 增 `mailhog` 服务（`profiles: ["local-mail"]`，1025/8025），供联调。

## 6. 测试策略

| 层 | 覆盖 |
|---|---|
| 单测 `tests/test_mailer.py` | SMTPMailer 参数化（ssl/starttls/none，假 smtplib）；未配置降级；发送异常上抛 |
| 单测 `tests/test_forgot_api.py` | 恒 200（存在/不存在/未配置 503）；FakeMailer 捕获正文提取 token → 走 `/api/auth/reset` 全链路成功；冷却期内不重发；双维度限流 429 + 审计；`mail_send_failed` 审计；email_hash 不出完整邮箱 |
| 既有回归 | `test_session_governance.py`（token 单次消费/过期）保持全绿 |
| 前端 E2E | forgot 入口可见 → 提交成功文案；`#reset=<bad-token>` 打开显示错误态；不影响既有 auth 用例 |
| infra（CI） | 无新增服务（FakeMailer 注入） |

## 7. 影响范围与风险

| 风险 | 对策 |
|---|---|
| 邮件送达率（进垃圾箱） | SPF/DKIM/DMARC 清单 + 上线 smoke；文案避免营销词 |
| 防枚举恒 200 掩盖真实投递失败 | `mail_send_failed` 审计 + 启动日志 + 发布手册 smoke |
| SMTP 凭据泄露 | 只走环境变量；日志/审计禁打印；`.env.example` 留空 |
| 冷却期被绕过（并发申请） | 创建 token 前 `FOR UPDATE` 查重（复用 store 事务语义）+ 限流双维度 |
| 前端 hash token 泄漏 | `#` 不进服务端日志；成功后 `replaceState`；不预填表单 |

## 8. 验收标准（DoD）

- [x] 邮件通道：SMTP 可配置（ssl/starttls/none），未配置结构化 503；启动日志可见状态
- [x] 自助找回：`/api/auth/forgot` 防枚举恒 200；邮件链接 → `/api/auth/reset` 全链路（FakeMailer 单测）成功并吊销全部会话
- [x] 安全：IP + 邮箱哈希双维度限流（429 + 审计）；冷却期；审计 `forgot_requested` / `forgot_rate_limited` / `mail_send_failed`；审计与邮件正文无完整邮箱/token
- [x] 前端：忘记密码入口 + 重置表单（无效/过期 token 态 + 成功回登录）；E2E 覆盖（新增 3 用例）；`npm run build` 绿
- [x] 运维：SPF/DKIM/DMARC 清单进发布手册（§2.4c）；staging `local-mail` profile（MailHog）
- [x] 测试：`test_mailer.py`(7) + `test_forgot_api.py`(7) 全绿；既有 auth/session 回归全绿；真库冷却契约测试进 `test_run_store.py`
- [ ] 文档：需求 20 §6 P1-1 拆分标注（已标注）；飞书镜像同步（合并后执行）

## 9. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-04 | 新建 | 需求 21 批次 A3 立项 | 初稿：现状审计（F1~F8：零邮件实现 / CLI 发 token / 消费端完备 / 单维限流 / 无前端入口）+ D1~D6 决策 + 详细设计 + DoD | 本文档 |
| 2026-10-04 | 修订 | 评审确认与市面调研 | 决策确认：SMTP-first（stdlib）、范围=通道+找回（新登录通知后置）；前端设计按市面调研定稿（防枚举中性文案 / 规则常显+实时灰→绿 / 失效链接可复活 / 成功回登录） | 本文档 |
| 2026-10-04 | 实施 | 需求 24 落地 | `MailConfig`（DR_SMTP_*）+ `web/backend/mailer.py`（SMTP ssl/starttls/none + 模板）+ `POST /api/auth/forgot`（恒 200 防枚举 / 双维限流 / 冷却 / `mail_send_failed` 审计 / 未配置 503）+ `mail_unavailable` 错误码 + 前端 AuthForm 四态（forgot/sent/reset/invalid/done）+ 发布手册 §2.4c + staging `local-mail` profile；测试：mailer 7 / forgot 7 / 真库冷却契约 / E2E 3 | #109 |
