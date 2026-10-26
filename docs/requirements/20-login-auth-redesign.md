# 需求 20：登录鉴权体系重构（调研与设计）

> 状态：**实施中**（调研 + 设计已冻结；**P0 已全部落地，P1-2/P1-3 后端已落地**，P1-4/P1-3b/P2 待排期——见 §4.2 / §6 状态列）。
> 来源：2026-10-02 产品讨论（"重新设计登录鉴权原则"，要求贴合现代互联网企业）。
> 定位：账号与会话安全体系的现代化治理；**不改变**既有 BFF 架构与邀请制准入。
>
> **登记更正（2026-10-18）**：本文原标「调研稿、评审后再冻结」，但 **P0-1/P0-2/P0-3 已在代码落地**
> （Cookie `__Host-` 前缀、NIST 口令策略、前端会话与安全页），属文档落后于实现。本次已按代码事实更正
> 状态与 §4.2/§6，并把剩余项细化为可执行清单。实施进度以 `docs/project-status.md` 为唯一看板（D-01）。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 20 |
| 标题 | 登录鉴权体系重构（调研与设计） |
| 优先级 | P1（P0 项内测期即可低成本落地；Passkey/MFA 视排期） |
| 状态 | 实施中（P0 已落地；P1-2/P1-3 后端已落地；其余 P1/P2 待排期） |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #87 |
| 关联 PR | 本 PR |
| 创建 / 更新 | 2026-10-02 |

## 2. 目标与设计原则

- **目标**：把账号与会话安全基线对齐 2025–2026 行业标准（RFC 10017 / NIST SP 800-63B-4 / FIDO Passkey），按"不改架构、渐进增强"落地。
- **原则**：
  1. 沿用 **BFF 形态**（同源 FastAPI + HttpOnly Cookie 会话），不引入浏览器端令牌存储（localStorage JWT 已被 RFC 10017 明确不建议）；
  2. 安全能力**分层可开关**、默认保守；每项改造可独立灰度与回滚；
  3. 体验优先：新增验证方式必须"可选增强"，不打断现有邮箱密码流程；
  4. 涉及用户可见行为（密码策略、登录页布局）的变更先小范围内测验证。

## 3. 行业调研（2025–2026）

### 3.1 总体趋势

1. **浏览器端从 "JWT + localStorage" 转向 BFF + HttpOnly Cookie**：IETF 已发布正式 BCP——RFC 10017《OAuth 2.0 for Browser-Based Applications》（2026-08）；三种架构中 **BFF 为首选**：令牌不落浏览器、会话用 Cookie、JS 无法触达令牌。
2. **Passkey（WebAuthn）成为主流登录方式**：FIDO 2026 报告全球在用 passkey 达 **50 亿**；Passkey Index 2025-10（Amazon/Google/Microsoft/PayPal/TikTok 等 9 家）显示：93% 账号可注册、36% 已注册、**26% 的登录已用 passkey**；**登录成功率 93% vs 其他方式 63%，登录耗时 8.5s vs 31.2s，登录相关工单减少 81%**。Google（8 亿账号）/Amazon（4.65 亿客户）/微软已把 passkey 设为默认选项。
3. **密码规则反转（NIST SP 800-63B-4，2025-07 正式版）**：**禁止定期强改密码、禁止组合复杂度规则**；单因素密码最短 15 字符（多因素场景可 8），上限至少 64；必须比对泄露库；允许粘贴（配合密码管理器）；禁止密保问题。
4. **会话从"一发了之"变成可管理资产**：设备/会话列表、单条撤销、"踢出其他所有设备"、新设备通知（GitHub Sessions 页、Google 账号设备管理均为标准形态）。
5. **刷新令牌标配轮换 + 重用检测**：Auth0/Okta 模式——refresh token 一次性使用，检测到旧 token 重放即**吊销整个 token family**；弱网配 30–60s rotation grace period。
6. **MFA / step-up 常态化**：PCI DSS v4.0 自 2025-03-31 起强制 CDE 全量 MFA；消费级产品普遍采用"风险触发式挑战"（新设备/异地才验），而非全员强制。
7. **风控前置分层**：传统验证码 → 行为式验证码 → **设备指纹 + 风控引擎评分**（国内云厂商标准路线）。

### 3.2 会话架构选型（三种模式）

| 模式 | 形态 | 代表 | 结论 |
|---|---|---|---|
| **BFF + 服务端会话 Cookie** | 后端管令牌/会话，浏览器只有 HttpOnly Cookie | 绝大多数 Web 第一方产品（GitHub、Google Web） | **RFC 10017 首选**；撤销/管理能力最强；**本项目即此形态** |
| Token-mediating backend | 前端持短期 access token 直连资源服务 | 移动 App、部分 SPA | 适合 App/多端；浏览器端不推荐 |
| 浏览器内 OAuth Client | access token 存内存 | 少数 SPA | 仅 Authorization Code + PKCE；禁用 localStorage |

**RFC 10017 的 Cookie 基线（BFF）**：`Secure` + `HttpOnly` 必须；`SameSite` 建议 Strict（登录跳转场景 Lax 可接受）；`Path=/`；**不设 Domain**；建议 **`__Host-` 前缀**（防子域注入）。

### 3.3 会话生命周期设计（现代企业基线）

- **登录即换 session ID**（防会话固定）：成功后作废旧会话、签发全新会话（本项目为新建会话，天然满足）；
- **双超时**：绝对超时（如 30 天）+ 空闲超时（敏感系统更短）；
- **权限变更 / 改密后吊销全部会话**；登出必须服务端作废（不能只删 cookie）；
- **会话可管理**：设备列表（UA/IP/最近活跃）+ 单条撤销 + 全部下线（GitHub/Google 标准 UI）；
- **SSO 场景配 OIDC Back-Channel Logout**；
- 撤销即时传播：集中会话存储 + 每请求校验（本项目直查 PG，天然即时）。

### 3.4 凭据与登录方式（按现代优先级）

1. **Passkey/WebAuthn**：抗钓鱼、免密码、跨设备同步；策略上"可选注册 + 登录页优先展示"（Google/Amazon 实证：默认化比宣传教育有效）；
2. **魔法链接 / 邮箱 OTP**：低摩擦，适合邀请制与低频 SaaS；要求单次使用 + 短 TTL + 服务端风险信号；
3. **密码**：Argon2id 存储（本项目已达标）；策略按 NIST（长度优先、泄露库比对、不强制轮换）+ 强度计（zxcvbn 类）；
4. **MFA**：TOTP（通用）→ WebAuthn（推荐）→ 恢复码；**风险触发式 step-up**（新设备/异地/敏感操作）；
5. **国内特色**：短信验证码（多维限流 + 图形/行为验证码防刷）、运营商"一键登录"（转化提升、成本约 -30%）、**扫码登录**（PC 端事实标准：uuid + 轮询/长轮询状态机——待扫描 → 已扫描待确认 → 已确认；二维码 5 分钟失效、手机端已登录态做担保）；
6. **社交/企业登录**：OIDC（Google/GitHub/微信开放平台）；企业侧 SAML/OIDC SSO + SCIM（离职即时吊销）。

### 3.5 防护与风控（分层）

- L1 限流：IP / 账号 / 设备多维度、渐进延迟、指数退避（避免硬锁定 DoS）；
- L2 人机验证：无感 → 行为式验证码 → 强验证码（按风险升级）；
- L3 风险引擎：设备指纹、新设备/异地/代理特征 → allow / step-up / deny；
- 审计：成功/失败登录、IP、UA、地点、方式；国内合规要求网络日志留存 ≥ 6 个月（本项目 audit_logs 保留 180 天，可评估上调）。

### 3.6 自建 vs 托管（选型速览，2026 价格点）

| 方案 | 定位 | 备注 |
|---|---|---|
| Clerk | React 场景 DX 最佳 | 纯托管、B2B 组织/SSO 打包；**无自托管** |
| Auth0 / Okta | 企业级 CIAM 最全 | 合规认证最广；贵（10k MAU ≈ $240/月） |
| **Logto** | 开源 + 可自托管 | OIDC 标准、passkey 原生；10k MAU ≈ $16/月（自托管免费）——**与自托管栈最匹配的候选** |
| Supabase Auth | 已用 Supabase 时顺带 | 与 RLS 集成 |
| Keycloak / Ory / FusionAuth | 完全自托管 | Keycloak 重且升级会登出；Ory 模块化 API-first |
| 国内（Authing / 腾讯云 / 易盾） | 短信、一键登录、实名、风控生态 | 若面向国内公众用户时再评估 |

## 4. 现状分析

### 4.1 已实现（对照现代基线）

| 能力 | 现状 | 位置 |
|---|---|---|
| BFF 架构（同源 + Cookie 会话） | ✅ 与 RFC 10017 一致 | `web/backend/main.py` |
| 服务端会话（DB 只存 SHA-256 摘要） | ✅ sessions 表含 ip/UA/last_seen/expires | `web/backend/store.py:1034` |
| 会话管理 API（列表 / 单条撤销 / 撤销其他全部） | ✅ 后端已就绪 | `main.py:1101/1117/1139` |
| 登出服务端作废 | ✅ | `main.py:1061` |
| 密码哈希 | ✅ Argon2id（`argon2-cffi` 默认参数） | `web/backend/auth.py` |
| CSRF 防护 | ✅ 双提交 Cookie + X-CSRF-Token | `auth.py` / `main.py:703` |
| 登录限流（IP + 账号双维度） | ✅ | `main.py:243 / 1039` |
| 登录/会话审计 | ✅ login_success / login_failed / logout / session_revoked / password_changed / account_deletion，含 email_hash | `main.py:1035-1230`，`admin.py audit-list` |
| 审计留存 | ✅ audit_logs 180 天轮转 | `web/backend/retention.py:77` |
| 密码重置 | ⚠️ 管理员签发令牌（admin CLI），非自助 | `admin.py:304` / `main.py:1155` |
| 账号注销 | ✅（含密码确认 + 数据清理） | `main.py:1200` |
| staging 启动硬校验 | ✅ AUTH_REQUIRED / COOKIE_SECURE / CORS / LLM key | `main.py:209` |

### 4.2 差距清单

> 说明（2026-10-18）：下列为**立项时**的差距，已落地项就地标注 ✅ + 代码位置。

**P0（内测期低成本、高收益）——已全部落地**
- ✅ Cookie `__Host-` 前缀：`auth.py:29-35 host_prefix_cookie_name()`，`DR_COOKIE_SECURE=true` 时返回 `__Host-dr_session` / `__Host-dr_csrf`（staging 即此）。
- ✅ 密码策略对齐 NIST：`MIN_PASSWORD_LENGTH=12`（`auth.py:26`）+ `BLOCKED_PASSWORDS` 弱口令表 + `check_password_strength()`（精确弱口令 / 重复字符 / 服务名 / 邮箱本地部分；`auth.py:38-70`）；注册与改密双入口校验（`main.py:883 _reject_weak_password` → `:1147` / `:1320`）。
- ✅ 会话管理页：前端已接入既有 API（`SecurityPanel.tsx` 调用 `/api/auth/sessions` 列表 / 单条撤销 / 撤销其他；端点 `main.py:1256` / `:1272` / `:1294`）。
- ✅ 空闲超时收紧（P0-4）：`DR_SESSION_IDLE_SECONDS` 机制已在（`main.py`）；**staging 默认已收紧为 43200（12h）**（`docker-compose.staging.yml`），显式 `0` 可回到「仅绝对超时」。
- ✅ 新登录 / 新设备通知（P1-1b）：登录成功且 `(UA, IP 网段)` 为该用户既有活跃会话之外的新设备 ⇒ 发提醒邮件（`auth.device_key` / `main._notify_new_login`）；SMTP 未配置时**显式降级**（审计 `login_notify_skipped`）。

**P1（产品化前）**
- ✅ 自助邮箱找回（SMTP + `/api/auth/forgot` + 重置链路）：已随需求 24 落地。
- ✅ **Passkey（WebAuthn）作为可选登录 / 第二因子**：`passkeys.py`（py_webauthn 选项生成 / 签名校验）/ 迁移 0027（`webauthn_credentials` + `webauthn_challenges`）/ `/api/auth/webauthn/*`（注册、登录、列表、删除）；`sign_count` 单调校验；删除需密码确认（密码为备用方式）；前端登录按钮与安全面板管理已接入。
- ✅ **TOTP MFA + 恢复码（后端）**：`totp.py`（RFC 6238，stdlib）/ `secretbox.py`（AES-256-GCM）/ 迁移 0026（`mfa_factors` + `recovery_codes` + `sessions.mfa_verified`）/ 登录第二步（半程会话 + `/api/auth/mfa/*`）；前端管理界面归 P1-4。
- ⏳ 风险触发式 step-up（仅新设备 / 敏感操作才挑战；当前是「启用后每次登录都要第二步」的强制口径）。
- ⏳ 前端安全设置页整合（改密 / 会话 / MFA / 注销）—— **会话与改密部分已落地**（P0-3 / 需求 24），MFA 管理部分待 P1。

**P2（开放注册 / 多端时）**
- ⏳ OIDC 社交登录（Google/GitHub）；企业 SSO（SAML/OIDC + SCIM）。
- ⏳ 设备指纹与登录风险评分；扫码登录（如出现 PC/移动双端诉求）。
- ⏳ 托管 IdP 评估（Logto 自托管为首选候选；Clerk/Auth0 为纯托管备选）。

## 5. 目标态设计（原则级）

```
凭据层：邮箱密码（NIST 策略）+ Passkey（新增首选）+ [P2] OIDC
        └ MFA：TOTP / WebAuthn，风险触发 step-up
会话层：BFF + __Host- 双 Cookie（HttpOnly 会话 + 非 HttpOnly CSRF）
        └ 双超时（绝对 7d + 空闲 12h）· 登录轮换 · 撤销即时生效
        └ 会话管理页（设备/IP/最近活跃 · 单条撤销 · 全部下线）
风控层：登录限流（已有）→ 新设备通知 → [P2] 设备指纹 / 风险评分
审计层：audit_logs（已有）+ 用户可见的登录活动页
```

## 6. 实施清单（细化；2026-10-18 按代码事实登记）

> 图例：✅ 已落地 / ⚠️ 部分 / ⏳ 待开工。每项给「验收 / DoD」以便独立开 PR；实施进度以 `docs/project-status.md` 为准。

| 项 | 内容 | 规模 | 依赖 | 状态 | 验收 / DoD |
|---|---|---|---|---|---|
| P0-1 | Cookie `__Host-` 前缀（会话 + CSRF） | 小 | — | ✅ | `DR_COOKIE_SECURE=true` 时两 Cookie 均带 `__Host-`；本地 http 退回裸名；存量会话一次性失效（低峰公告） |
| P0-2 | 密码策略对齐 NIST | 小 | — | ✅ | 长度 ≥12；精确弱口令 / 重复字符 / 服务名 / 邮箱名拒绝；注册与改密同口径；单测覆盖 |
| P0-3 | 前端「会话与安全」页 | 中 | — | ✅ | 列表展示 UA / IP / 最近活跃 + 当前标记；单条撤销（终止他人需重输密码）；退出其他设备 |
| P0-4 | 空闲超时默认收紧（内测 12–24h） | 小 | — | ✅ | 机制已有 + **staging 默认 43200（12h）**；显式 `0` 保留；e2e 走 uvicorn 直连（不受 compose 默认影响） |
| P1-1a | 邮件通道 + 自助找回 | 中 | — | ✅ | 已随需求 24 落地（SMTP + `/api/auth/forgot`）；未配 SMTP 时 503 `mail_unavailable` + CLI 兜底 |
| P1-1b | **新登录 / 新设备通知** | 中 | P1-1a ✅ | ✅ | 登录成功且 (UA, IP 网段) 为新设备 ⇒ 发提醒邮件；同设备不重复；SMTP 未配置**显式降级**（`login_notify_skipped`）；审计 `login_notified` / `login_notify_failed`；`tests/test_login_notify.py` |
| P1-2 | Passkey（WebAuthn）注册 / 登录 / 管理 | 中 | 库选型：`webauthn`（py_webauthn）✅ | ✅ | 可选增强、不打断密码流程；凭据 CRUD + `sign_count` 单调校验（非递增按克隆拒绝）；删除需密码确认（最后一把仍可删，密码为备用方式）；迁移 0027 落地 §7.8 字段；`tests/test_passkeys.py` 7 例 + `test_run_store.py` 真 PG 1 例 |
| P1-3 | TOTP MFA 因子 + 恢复码 + 登录第二步（**后端**） | 中 | 新增依赖 `cryptography`（AES-256-GCM） | ✅ | TOTP secret **可逆加密**（密钥 `DR_MFA_SECRET_KEY`，未配置 fail-closed）；恢复码只存摘要 + 单次；半程会话阻断受保护接口；`tests/test_mfa.py` 8 例 |
| P1-3b | 风险触发式 step-up（仅新设备 / 敏感操作挑战，MFA 可选化） | 小 | P1-3 | ⏳ | 当前口径为「启用即每次登录第二步」；本项改为按风险触发（新设备/异地） |
| P1-4 | 前端安全设置页整合（MFA 管理） | 小 | P1-2 / P1-3 | ⏳ | MFA 因子列表 / 增删 / 恢复码；沿用 P0-3 的 `SecurityPanel` 版式 |
| P2-1 | OIDC 社交登录 / 企业 SSO（SAML + SCIM） | 大 | 产品开放策略 | ⏳ | — |
| P2-2 | 设备指纹 / 风险评分 / 扫码登录 | 大 | 产品开放策略 | ⏳ | — |
| P2-3 | 托管 IdP 评估（Logto 自托管首选） | 中 | 开放注册决策 | ⏳ | — |

> **建议顺序**：P0-4（收尾 P0，半天）→ P1-1b（新登录通知，1~2 天）→ P1-3 TOTP MFA（依赖比 Passkey 少、覆盖更广）→ P1-2 Passkey → P2。

## 7. 字段设计策略（数据模型逐字段）

> 总则：**机密字段绝不出库、不出日志、不出响应**；PII 最小收集 + 日志伪名化；所有令牌只存单向摘要；
> 新增字段一律「可空 + 默认值」保证向后兼容；时间统一 `timestamptz`（UTC）；破坏性改名走一次性公告。
> 现有 schema 见 `migrations/0003`（users/sessions/invites）、`0007`（audit_logs）、`0010`（会话治理/reset token）、`0005`（注销台账）。

### 7.1 敏感级定义

| 级别 | 含义 | 处理策略 |
|---|---|---|
| 🔴 机密 | 泄露即可冒用身份 | 单向摘要或加密存储；禁入日志 / 响应 / 导出 |
| 🟠 PII | 个人信息 | 最小收集；日志伪名化（`email_hash`）；注销删除；仅本人可见 |
| 🟡 内部 | 运维 / 审计需要 | 仅服务端与管理 CLI 可见 |
| ⚪ 公开 | 无风险 | 可展示 |

### 7.2 `users`

| 字段 | 类型 / 约束 | 级别 | 设计策略 |
|---|---|---|---|
| `user_id` | text PK | 🟡 | 不透明 ID（不承载邮箱等语义）；对外出现在审计/会话；**用户删除后审计仍保留该 ID 线索**（审计表无外键） |
| `email` | text NOT NULL，`UNIQUE(lower(email))` | 🟠 | 登录标识 + 通知通道；接口/日志一律用 `email_hash`（sha256 前缀）伪名化；大小写不敏感唯一；注销即随行删除；P1 加 `email_verified_at` 前不依赖邮箱找回 |
| `password_hash` | text NOT NULL | 🔴 | Argon2id（参数内嵌于哈希串，可平滑升级）；登录失败统一文案防账号枚举；任何接口 / 日志 / 导出不返回 |
| `status` | text CHECK `active`/`banned` | 🟡 | 封禁须同时吊销该用户全部会话（P1 落地为显式动作 + 审计 `admin_*`） |
| `created_at` / `updated_at` | timestamptz | 🟡 | 只读审计用途 |
| `last_login_at` | timestamptz 可空 | 🟡 | 登录活动页展示；写入失败不得阻断登录 |

### 7.3 `sessions`

| 字段 | 类型 / 约束 | 级别 | 设计策略 |
|---|---|---|---|
| `token_hash` | text PK（SHA-256） | 🔴 | 原始 token 只存在于 `__Host-dr_session` HttpOnly Cookie（创建响应一次）；库内只存摘要；**P0 改 `__Host-` 前缀会令存量会话一次性失效**（安排低峰公告） |
| `session_id` | text UNIQUE（`gen_random_uuid`） | 🟡 | 对外会话标识（撤销 API 路径参数）；**不是凭证**、不授予任何权限 |
| `user_id` | FK → `users` ON DELETE CASCADE | 🟡 | 注销即级联删除会话 |
| `created_at` / `expires_at` | timestamptz | 🟡 | 绝对超时 7 天（`DR_SESSION_TTL=604800`）；过期由清理任务删除；**P0 新增空闲超时判定**（`last_seen_at` + `DR_SESSION_IDLE_SECONDS`，内测收紧到 12–24h） |
| `last_seen_at` | timestamptz NOT NULL | 🟡 | 节流更新（避免逐请求写放大）；会话列表「最近活跃」展示 |
| `ip` | text 可空 | 🟠 | 会话列表 + 风险信号；随会话到期删除；不参与授权判定 |
| `user_agent` | text 可空 | 🟠 | 会话列表展示（设备/浏览器）；**入库前截断**（建议 ≤512 字符，防存储放大）；不参与授权判定 |

### 7.4 `invites`（邀请码）

| 字段 | 级别 | 设计策略 |
|---|---|---|
| `code_hash` (PK) | 🔴 | SHA-256 摘要；原始邀请码只在创建响应出现一次；库泄露不可用 |
| `created_by` / `created_at` | 🟡 | 可空 = 管理员 CLI 创建；审计串联 |
| `expires_at` | 🟡 | 可空（永不过期仅限内部测试）；过期为终态 |
| `used_by` / `used_at` | 🟡 | **一次性原子消费**（唯一约束 + 条件更新，防并发复用）；`used_by` 随用户删除置空但保留使用痕迹 |
| `revoked_at` | 🟡 | 撤销即失效（终态，与 used 互斥） |

### 7.5 `password_reset_tokens`

| 字段 | 级别 | 设计策略 |
|---|---|---|
| `token_hash` (PK) | 🔴 | 只存 SHA-256 摘要；**明文 token 仅出现于站内/邮件一次**，日志严禁记录 |
| `user_id` | 🟡 | FK ON DELETE CASCADE |
| `created_by` | 🟡 | 当前=管理员；P1 自助找回后=系统（保留字段语义） |
| `created_at` / `expires_at` | 🟡 | 有效期 30 分钟（短窗口） |
| `consumed_at` | 🟡 | 单次原子消费；**消费成功即吊销该用户全部会话**（已在 `main.py:1155` 实现） |

### 7.6 `audit_logs`（append-only）

| 字段 | 级别 | 设计策略 |
|---|---|---|
| `id` (bigserial) | 🟡 | 只追加；无更新/删除 API；保留期清理按时间轮转 |
| `at` / `action` | 🟡 | 事件最小集（认证成败/注销/改密/CSRF/授权/管理动作）；`action` 为稳定枚举字符串 |
| `actor_user_id` | 🟡 | **无外键**——用户删除后审计仍保留身份线索；仅内部 ID，无 PII |
| `target_type` / `target_id` | 🟡 | 指向被操作对象（如 session_id/user_id），不含敏感值 |
| `ip` / `user_agent` | 🟠 | 安全审计所需；保留 **180 天**（`retention.py:77`；若合规要求 ≥6 个月可上调） |
| `request_id` | 🟡 | 与访问日志/响应头 `X-Request-Id` 串联，支持事件回溯 |
| `detail` (jsonb) | 🟡 | 只放结构化最小信息；**硬性禁令**：密码、token、cookie、完整邮箱（用 `email_hash`） |

### 7.7 Cookie 与传输字段

| Cookie（P0 改名后） | 属性 | 设计策略 |
|---|---|---|
| `__Host-dr_session` | HttpOnly + Secure + SameSite=Lax + Path=/ + 无 Domain + `__Host-` | 值 = 不透明随机 token（`token_urlsafe(32)`）；TTL = 绝对超时；登出/撤销/改密时服务端作废；前端 JS 不可读 |
| `__Host-dr_csrf` | Secure + SameSite=Lax + Path=/ + 无 Domain + `__Host-`（**非 HttpOnly**） | 双提交凭证：请求头 `X-CSRF-Token` 比对；随会话轮换；失败审计 `csrf_failed`；不承载任何身份信息 |

### 7.8 需求 20 规划新增字段（尚不存在，先定策略）

| 字段 / 表 | 级别 | 设计策略 |
|---|---|---|
| `users.email_verified_at` | 🟠 | 邮件通道（P1）落地后启用；未验证可登录，但禁用自助找回与通知外发 |
| `mfa_factors`（factor_id, user_id, type[totp/webauthn], secret_encrypted, name, last_used_at） | 🔴 | TOTP secret 与密码哈希**分开保管**，必须**可逆加密**（应用独立密钥/信封加密），非哈希；恢复码另存 |
| `webauthn_credentials`（credential_id PK, user_id, public_key(COSE), sign_count, transports, aaguid, backup_state） | 🟡（公钥无风险） | credential_id 全局唯一防重放；`sign_count` 单调递增检测克隆；**删最后一把凭证必须走防锁死流程**（要求备用方式） |
| `recovery_codes`（code_hash, used_at） | 🔴 | SHA-256 摘要 + 单次使用；生成时一次性全量展示 |
| 新设备判定（device_hash = f(UA, IP 网段)） | 🟠 | 只做「新设备提示/step-up」信号，不做跨站追踪；通知内容不含敏感信息；保留期同 audit |

### 7.9 字段级横切规则

- **命名**：令牌字段一律 `*_hash` 后缀（单向存储的事实声明）；时间字段一律 `timestamptz`；
- **摘要算法**：高熵令牌统一 SHA-256（无需加盐/慢哈希）；用户密码唯一使用 Argon2id；
- **索引**：`token_hash` 主键；`user_id` / `expires_at` 二级索引保证撤销与清理 O(log n)（已具备）；
- **演进**：新增可空/默认值；迁移只前向（`tools/migrate.sh`）；破坏性改动（cookie 前缀）单独排期 + 公告；
- **删除联动**：`users → sessions / password_reset_tokens` CASCADE；`runs.user_id` SET NULL（历史保留）；`audit_logs` 无外键长期保留；注销 PII 清除由 `deletion.py` + outbox（Qdrant 向量清理）闭环。

## 8. 数据安全设计（账号与用户数据）

> 与 §7 字段策略配套：§7 定义「字段怎么存」，本节定义「数据怎么保护」。

### 8.1 威胁模型（务实版）

- 要防：数据库 / 备份被拷走后凭证可直接利用；日志 / 导出泄露凭证；内部误操作；传输窃听；
- 不假设能防：拥有 root / 宿主机权限的攻击者（需独立 KMS/HSM 与盘级加密，超出内测阶段成本）。

### 8.2 分层措施

| 层 | 现状 | 本需求动作 / 后续 |
|---|---|---|
| 传输 | 公网 HTTPS（Caddy，内部 CA 自签）；容器内 PG/Redis 走 Docker 私有网络；COS 走 HTTPS | 域名 + 备案后切公网受信证书（runbook §7.1）；零代码改动 |
| 凭证存储 | 密码 Argon2id；会话/邀请/重置 token 仅存 SHA-256 摘要——库泄露不直接可用 | 维持；P1 TOTP secret 落地时必须**可逆加密**（§7.8） |
| 静态数据 | PG 数据卷位于系统盘（Lighthouse 无盘级加密）；**备份此前为明文** | **备份加密**：`BACKUP_ENCRYPT=1` + 口令环境变量注入（AES-256-CBC + PBKDF2 200k；runbook §2.3） |
| 密钥管理 | `.env` 权限 600；`DR_OPS_TOKEN` 随机 32B | 登记轮换策略：ops token / COS 密钥 / 备份口令；备份口令**异地托管**（同机存放 = 没加密） |
| 日志与审计 | audit 脱敏（email_hash、无 token/密码）；180 天轮转 | 维持；「登录活动」展示可复用审计（P1） |
| 保留与删除 | sessions 过期清理；audit 180 天；注销 CASCADE + outbox 清向量 | 维持；PIPL「最少必要」口径复核 |
| 对象存储 | COS 生命周期 90 天已生效 | 桶策略最小权限复核（列清单后确认） |

### 8.3 明确不做（现阶段）

- 自研 KMS/HSM、宿主机盘级加密、全库字段级加密（收益/成本不成比例）；
- 备份口令与备份文件同机存放（加密意义归零）；
- 自造加密原语——一律使用 openssl/AEAD 标准件。

## 9. 风险与取舍

- **Passkey 兼容性**：老设备/浏览器不支持时必须有密码回退；企业环境（部分客户端）需评估；
- **邮件通道**：国内送达率与成本是"通知/找回"前置约束，选型先于开发；
- **NIST 15 字符**：对内测体验有摩擦，采用"12 + 强度计 + 泄露库"折中并留升级路径；
- **明确不做**：JWT/localStorage 方案；短信验证码（成本/合规/被刷风险，非目标场景）；自研风控引擎（过度设计）。
- **兼容性**：Cookie 改名（`__Host-` 前缀）会让存量会话失效一次——安排在低峰期，作为一次性登录成本公告。

## 10. 参考来源

| 主题 | 来源 |
|---|---|
| 浏览器应用 OAuth 最佳实践（BFF） | RFC 10017 https://www.rfc-editor.org/info/rfc10017 |
| 密码与认证器要求（2025 正式版） | NIST SP 800-63B-4 https://pages.nist.gov/800-63-4/sp800-63b.html |
| Passkey 采用与收益数据 | FIDO Passkey Index 2025 https://fidoalliance.org/passkey-index-2025 · State of Passkeys 2026（PCMag 报道） |
| 刷新令牌轮换 + 重用检测 | Auth0 https://auth0.com/docs/secure/tokens/refresh-tokens/refresh-token-rotation · Okta https://developer.okta.com/docs/guides/refresh-tokens/main/ |
| 会话管理实践（大厂形态） | GitHub https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/viewing-and-managing-your-sessions |
| 扫码登录原理 | 腾讯云开发者社区 https://cloud.tencent.com/developer/article/1948928 |
| 登录防刷分层（验证码/设备指纹/风控） | 腾讯云开发者社区 https://cloud.tencent.com/developer/article/2741111 |
| 自建 vs 托管对比 | Clerk vs Auth0 https://clerk.com/articles/clerk-vs-auth0-which-authentication-platform-fits-your-team · Logto vs Auth0 https://guptadeepak.com/ciam-compass/compare/auth0-vs-logto · Ory vs Keycloak https://www.ory.com/comparisons/ory-vs-keycloak |

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-26 | 实施 | P0-2b：Passkey 注册 step-up | `/api/auth/webauthn/register/begin` 要求当前密码（`invalid_credentials` + `passkey_enroll_auth_failed` 审计）；前端安全面板添加 Passkey 前弹密码；`tests/test_passkeys.py` 新增「错误密码 401 / 缺失密码 422」用例；MFA 前端整合（P1-4）仍未做 | 本 PR |
| 2026-10-24 | 实施 | P1-2：Passkey（WebAuthn） | 新增依赖 `webauthn`（+ lock 外科式：cbor2 / pyasn1 / pyasn1-modules / pyOpenSSL）；`passkeys.py`；迁移 0027（`webauthn_credentials`/`webauthn_challenges`）+ 结构断言；`/api/auth/webauthn`（status/register begin+complete/authenticate begin+complete/delete）；`sign_count` 克隆检测；删除需密码（最后一把可删，密码为备用方式）；前端登录按钮 + 安全面板管理 + base64url 单测；`tests/test_passkeys.py` 7 例 + 真 PG 1 例。**风险触发 step-up（P1-3b）与 MFA 前端整合（P1-4）未做** | 本 PR |
| 2026-10-18 | 实施 | P1-3 后半：TOTP MFA 后端 | 新增依赖 `cryptography`（+ lock 外科式）；`totp.py`/`secretbox.py`；迁移 0026（`mfa_factors`/`recovery_codes`/`sessions.mfa_verified`）+ 结构断言；登录第二步（半程会话）+ `/api/auth/mfa`（status/begin/confirm/disable/verify）；6 条错误码；`tests/test_mfa.py` 8 例。**前端管理界面（P1-4）与风险触发 step-up（P1-3b）未做** | 本 PR |
| 2026-10-18 | 实施 | 收尾 P0 并开工 P1 | **P0-4**（staging 空闲超时默认 43200/12h）+ **P1-1b**（新设备登录提醒：`auth.device_key`/`is_new_device` + `mailer.new_login_email` + `main._notify_new_login`，best-effort 显式降级）；`tests/test_login_notify.py` 6 例 | 本 PR |
| 2026-10-18 | 更正 + 细化 | 文档漂移：P0-1/P0-2/P0-3 已落地但本文仍标「调研稿」 | 状态改「实施中（P0 已落地）」；§4.2 逐项标注 ✅/⚠️/⏳ 与代码位置；§6 由「分阶段草案」细化为带**状态 + 验收/DoD** 的实施清单（P0-4 收紧默认、P1-1b 新登录通知、P1-2/P1-3 MFA/Passkey、P2）；补「建议顺序」 | 本 PR |
| 2026-10-02 | 建稿 | 产品讨论：重新设计登录鉴权原则，要求贴合现代互联网企业 | 行业调研（RFC 10017 / NIST 800-63B-4 / FIDO / 大厂会话管理 / 国内实践 / 选型）+ 现状差距 + 目标态 + P0/P1/P2 清单 | 本 PR |
| 2026-10-02 | 修订 | 评审反馈：补充「每个字段的设计策略」 | 新增 §7 字段设计策略（敏感级定义 + users/sessions/invites/reset/audit 逐字段 + Cookie 字段 + 规划新增字段 + 横切规则）；原 §7~§9 顺延为 §8~§10 | 本 PR |
| 2026-10-02 | 修订 | 实施反馈：补充「数据安全性」设计 | 新增 §8 数据安全设计（威胁模型 / 分层措施 / 明确不做）；原 §8~§10 顺延为 §9~§11；配套 `tools/backup.sh` 备份加密（另 PR） | 本 PR |
