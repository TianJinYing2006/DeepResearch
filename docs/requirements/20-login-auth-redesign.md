# 需求 20：登录鉴权体系重构（调研与设计）

> 状态：**调研稿**（行业调研 + 现状差距 + 目标态原则 + 分阶段改造清单；评审后再冻结为实施需求）。
> 来源：2026-10-02 产品讨论（"重新设计登录鉴权原则"，要求贴合现代互联网企业）。
> 定位：账号与会话安全体系的现代化治理；**不改变**既有 BFF 架构与邀请制准入。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 20 |
| 标题 | 登录鉴权体系重构（调研与设计） |
| 优先级 | P1（P0 项内测期即可低成本落地；Passkey/MFA 视排期） |
| 状态 | 调研稿 |
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

**P0（内测期低成本、高收益）**
- Cookie 无 `__Host-` 前缀（`dr_session` / `dr_csrf` 均可加前缀，符合 `Secure + Path=/ + 无 Domain` 前提）；
- 密码策略弱于 NIST：`MIN_PASSWORD_LENGTH=10`、无泄露库比对 / 强度计；建议 15（或 12 + 强度计 + 常见泄露口令库，二选一评估）；
- 会话管理页：**后端 API 已就绪，前端未接入**（`web/frontend/src` 无 `auth/sessions` 调用）；
- 空闲超时默认关闭（`DR_SESSION_IDLE_SECONDS=0`，仅 7 天绝对超时）——至少给内测环境配一个 12–24h 空闲值；
- 新登录/新设备通知：无（依赖邮件通道，需先建通道）。

**P1（产品化前）**
- 自助邮箱找回（魔法链接 / OTP）——当前依赖管理员签发令牌；
- Passkey（WebAuthn）作为可选登录 / 第二因子；
- TOTP MFA + 恢复码；风险触发式 step-up（新设备挑战）；
- 前端安全设置页（改密、会话管理、MFA 管理、注销）整合。

**P2（开放注册 / 多端时）**
- OIDC 社交登录（Google/GitHub）；企业 SSO（SAML/OIDC + SCIM）；
- 设备指纹与登录风险评分；扫码登录（如出现 PC/移动双端诉求）；
- 托管 IdP 评估（Logto 自托管为首选候选；Clerk/Auth0 为纯托管备选）。

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

## 6. 分阶段实施（草案，评审后拆 issue）

| 阶段 | 项 | 规模 | 依赖 |
|---|---|---|---|
| P0-1 | Cookie `__Host-` 前缀（含 CSRF cookie） | 小 | 无（staging 已全程 HTTPS 内部 CA） |
| P0-2 | 密码策略对齐 NIST + 泄露库比对 | 小 | 引入口令黑名单（zxcvbn / HIBP k-anonymity 任选） |
| P0-3 | 前端「会话与安全」页接入既有会话 API | 中 | 无 |
| P0-4 | 空闲超时默认值收紧（内测 12–24h） | 小 | 无（评估对 e2e/体验影响） |
| P1-1 | 邮件通道 + 新登录通知 + 自助找回 | 中 | 邮件服务选型（国内送达） |
| P1-2 | Passkey（注册/登录/管理） | 中 | WebAuthn 库选型（simplewebauthn / py_webauthn） |
| P1-3 | TOTP MFA + 恢复码 + step-up 策略 | 中 | 与 P1-2 可合并排期 |
| P2 | OIDC 登录 / 设备指纹风控 / 扫码登录 / IdP 评估 | 大 | 产品开放策略 |

## 7. 风险与取舍

- **Passkey 兼容性**：老设备/浏览器不支持时必须有密码回退；企业环境（部分客户端）需评估；
- **邮件通道**：国内送达率与成本是"通知/找回"前置约束，选型先于开发；
- **NIST 15 字符**：对内测体验有摩擦，采用"12 + 强度计 + 泄露库"折中并留升级路径；
- **明确不做**：JWT/localStorage 方案；短信验证码（成本/合规/被刷风险，非目标场景）；自研风控引擎（过度设计）。
- **兼容性**：Cookie 改名（`__Host-` 前缀）会让存量会话失效一次——安排在低峰期，作为一次性登录成本公告。

## 8. 参考来源

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

## 9. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-02 | 建稿 | 产品讨论：重新设计登录鉴权原则，要求贴合现代互联网企业 | 行业调研（RFC 10017 / NIST 800-63B-4 / FIDO / 大厂会话管理 / 国内实践 / 选型）+ 现状差距 + 目标态 + P0/P1/P2 清单 | 本 PR |
