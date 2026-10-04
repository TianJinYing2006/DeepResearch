# 需求 26：报告只读分享（批次 A5）

> 状态：**自测**（实现完成：后端端点/前端分享面板与只读页/CLI/E2E；待合并与部署）。
> 父需求：`docs/requirements/21-product-modules-completeness.md` §4-C/§4-J、§5-A5、§6.3、§8 风险表。
> 一句话：为完成的研究报告生成**不可猜测的只读分享链接**（可过期、可撤销、全程审计），
> 外部评审者无需账号即可查看；不暴露内部 run_id 与对象存储直链，延续 BFF 口径。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 26 |
| 标题 | 报告只读分享（批次 A5） |
| 优先级 | P1 |
| 状态 | 自测 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #116 |
| 关联 PR | 待填 |
| 创建 / 更新 | 2026-10-04 |

## 2. 问题背景：现状审计（事实 → 影响）

| # | 现状事实（证据） | 影响 |
|---|---|---|
| F1 | **全仓零分享实现**：无 share 表/端点/审计 action；前端无公开只读页；`docs/web-frontend-audit.md:136` 登记「报告不可分享/书签」 | 报告只能导出文件转发；接收方需账号或拿到整份文件 |
| F2 | 导出端点为登录态私有接口：`GET /api/research/{run_id}/report?format=md\|json`（`main.py:2215-2217`），`_authorize_run` 归属校验（非本人 404，`main.py:751-762`）；BFF 口径不暴露对象存储直链（`objectstore.py:5-6`，无 presign） | 分享需新增**免登录窄路径**，且不能复用导出端点的宽权限 |
| F3 | 可复用 token 原语成熟：`new_token()`（256-bit）/`token_hash()`（`auth.py:92-101`）；一次性/过期/撤销/兄弟互斥模式（invites `store.py:1024-1078`、password_reset `store.py:1181-1258`）；`#reset=` 直达 + `replaceState` 前端先例（`AccountPanel.tsx:84,152-156`） | 实现成本集中在数据模型 + 窄路由 + 安全头 + 管理面 |
| F4 | 输出审核闸 `_enforce_output_policy`（flagged/under_review/blocked → 403）**仅导出端点调用**（`main.py:2226`） | 分享读取路径必须同样过闸（被标记的报告不可分享/不可查看） |
| F5 | 报告产物双形态 `report_md`/`export_json`（0002/0012），读取链支持内存→PG→S3（`main.py:2183-2212`）；`ReportView` 可独立渲染（`ReportView.tsx:51-105`） | 分享页可直接复用渲染器与产物读取链 |
| F6 | 无路由（SPA 状态切换）；URL 参数先例 `?invite=`/`?login`/`#reset=`（`AccountPanel.tsx:29-84`） | 分享页用路径分流（`/s/<token>`）最小改造；token 放路径优于查询串（日志/Referrer 泄露面更小） |
| F7 | 审计/限流设施齐备：`_audit`（`main.py:844-862`）、`make_limiter`（`ratelimit.py:105-117`）、request-id 串联 | 分享全生命周期可审计、可按 IP 限流防枚举 |
| F8 | 既有登记：需求 21 §6.3「随机 token + 过期 + 撤销 + 审计；默认关闭」、§8 风险表；FAQ 声称「可导出 Markdown / PDF」（`faq.md:49`）与实现不符（无 PDF） | 方案需含功能开关默认值；顺带修正 FAQ 文案 |

## 3. 目标与非目标

**目标**

1. 分享创建/撤销（仅 run 所有者）；默认过期、可选时长、上限受控；
2. 免登录只读页（`/s/<token>`）：报告正文 + 引用清单；失效统一文案；
3. 安全：token 只存 hash、统一 404（防枚举）、`no-store`/`noindex`/`no-referrer` 响应头、IP 限流、输出审核闸复用；
4. 可管理：报告卡分享面板（创建/复制/撤销/最近访问）+ 管理端 CLI（列出/撤销）；
5. 审计：`share_created` / `share_revoked` / `share_viewed`（含 ip_hash、request_id）。

**非目标（登记后续）**

- 密码保护、批注/评论、编辑权限、工作区级策略（邀请制单租户阶段无必要）；
- 分享页导出/下载（只读浏览，防二次扩散；登记后续评估）；
- 短链服务/自定义域名；分享打开后的注册转化埋点（批次 B 分析）。

## 4. 调研结论与设计策略（问题 → 备选 → 为什么更好 → 代价）

### D1 链接形态：路径式 `GET /s/<token>`（SPA 分流）+ 免登录 API

- 调研事实：bearer capability 的泄露面 = 日志/Referrer/预览机器人；**token 放路径优于查询串**（查询串被日志默认记录、更长更易复制）；分享页应自包含（无第三方资源）。
- 决策：前端检测 `location.pathname` 以 `/s/` 开头 → 直接渲染只读分享视图（不经登录门）；数据来自新免登录窄端点 `GET /api/share/<token>`；响应头 `Cache-Control: private, no-store`、`X-Robots-Tag: noindex, nofollow, noarchive`、`Referrer-Policy: no-referrer`。
- 为什么更好：不暴露 run_id（token 即全部凭证）；窄端点只读单报告，不复用宽权限导出接口；SPA 现有渲染器直接复用。
- 代价：FastAPI 静态托管需保证 `/s/*` 回落 `index.html`（现有 dist 托管已含 SPA 回落，实施时验证）。

### D2 数据模型：`report_shares`（token 只存 hash；一 run 一条活跃链接）

- 决策：迁移 0021；列：`share_id`（展示/审计用）、`token_hash`（PK）、`run_id`（FK CASCADE）、`created_by`、`created_at`、`expires_at NOT NULL`、`revoked_at`、`last_accessed_at`、`access_count`；创建时先撤销该 run 既有活跃链接（兄弟互斥，同 password_reset 模式）；「重新生成」= 旧撤销 + 新 token。
- 为什么更好：hash 存储防库泄露直接利用；单活跃链接满足内测规模（避免链接矩阵管理负担）；FK CASCADE 保证删除 run/注销账号即失效。
- 代价：一 run 不能同时给多接收者不同过期时间（登记后续）。

### D3 安全细节（调研清单落地）

- **统一 404**：不存在 / 已撤销 / 已过期 / 无报告 / 审核未通过 —— 一律 404 + 同一文案（不泄露状态差异）；
- **限流**：新 limiter（`DR_SHARE_RATE_PER_MINUTE` 默认 30/min，按 IP）防 token 枚举；
- **审计**：`share_viewed` 记 `share_id` + `ip_hash`（不记 token、不记完整 IP）；创建/撤销记 actor；
- **审核闸**：创建时与每次查看时都过 `_enforce_output_policy`（被 flagged 的报告不可分享、已分享的查看也 404）；
- **响应头**：`no-store` / `noindex, nofollow, noarchive` / `no-referrer`；分享页不加载任何第三方脚本/字体。

### D4 管理面：报告卡分享面板 + 管理端 CLI

- 所有者端点：`POST /api/runs/{run_id}/share`（创建，body：`expires_days` ∈ {0,1,7,30}，默认 7；0=永不过期需显式提交）、`GET /api/runs/{run_id}/share`（当前链接状态 + 最近访问）、`DELETE /api/runs/{run_id}/share`（撤销）；
- CLI：`admin.py share-list [--active]` / `share-revoke --share-id`（与 feedback-list 同模式）；
- 前端：报告卡「分享」按钮 → Modal（创建/复制链接/过期选择/撤销确认/最近访问时间）；历史报告预览也可创建（复用同一端点）。

### D5 默认值与开关：功能开关默认关，过期默认 7 天，支持显式「永不过期」

- `DR_SHARE_ENABLED` 默认 `false`（需求 21 §8「默认关闭」，部署时灰度开启；未开启时端点统一 404/按钮隐藏）；
- 过期选项：**1 / 7 / 30 天（默认 7）**；`expires_days=0` = 永不过期 —— 仅限**显式选择**，前端二次确认并明示风险（「永久有效，请谨慎；可随时撤销」），审计 `share_created` detail 标注 `expires_days=0`；
- 数据模型 `expires_at` 可空（NULL = 永不过期）；管理面 `share-list` 对永久链接显式标注 `permanent`，便于定期盘点。

### D6 分享页 UI：只读、自包含、失效可解释

- 页头：报告标题 + 「由 DeepResearch 生成 · 只读分享」+ 过期时间（本地化显示）；
- 正文：`ReportView` 渲染 `report_md`；引用清单区块；无任何操作按钮（不导出、不登录跳转）；
- 失效：统一文案「链接无效或已过期」+ 无其他信息；加载失败区分网络错误（可重试）。

## 5. 详细设计

### 5.1 迁移 0021（`report_shares`）

```sql
share_id text PRIMARY KEY, token_hash text UNIQUE NOT NULL, run_id text NOT NULL REFERENCES runs ON DELETE CASCADE,
created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
expires_at timestamptz,  -- NULL = 永不过期（显式选择；管理面标注 permanent）
revoked_at timestamptz, last_accessed_at timestamptz,
access_count int NOT NULL DEFAULT 0
-- 索引：(run_id) WHERE revoked_at IS NULL（活跃链接查询）、(expires_at)（清理，NULL 不参与）
```

### 5.2 后端

- `store.py`：`create_report_share`（事务：撤销旧活跃 → 插入）、`get_active_share(run_id)`、`resolve_share(token_hash)`（含 run 存在性/报告可用性判定）、`revoke_report_share(run_id)`、`touch_share_access(token_hash)`（访问计数 + 时间）、`purge_expired_shares`（存储卫生，保留撤销/过期 30 天供审计）、`list_report_shares`；
- `main.py`：三个所有者端点（登录 + CSRF + 归属校验 `_authorize_run`）+ 免登录 `GET /api/share/{token}`（限流 → resolve → 审核闸 → 返回 `{topic, markdown, generated_at, expires_at}`，响应头按 D3）；
- 错误码：`share_disabled`（功能关闭 404 或 403？——用 404 不泄露功能存在；实施时选 404 + 文案「未找到」）、`share_not_found`（统一 404）；
- `deletion.py`：账号注销连带删除其创建的分享（随 runs CASCADE 已覆盖 run；created_by 线索保留在审计）。

### 5.3 前端

- `ShareModal`（报告卡入口；testid `share-*`）：创建（过期下拉）→ 显示链接 + 复制按钮 + 「最近访问」；撤销（确认后）；未开启功能时按钮隐藏（`/api/options` 下发 `share_enabled`）；
- `SharedReportPage`（`/s/<token>` 分流）：加载态/成功/失效三态；`ReportView` 复用；自包含（无外部资源）；
- `AccountPanel`/`App` 分流改造：`pathname.startsWith('/s/')` 时**优先**于登录门渲染分享页（外部评审者可能未登录）。

### 5.4 运维

- `.env.example`：`DR_SHARE_ENABLED` / `DR_SHARE_MAX_EXPIRY_DAYS` / `DR_SHARE_RATE_PER_MINUTE` 注释组；
- FAQ 修正：删除「PDF」表述（当前导出 = Markdown / JSON）；分享段落更新为「支持只读分享（可过期/可撤销）」；
- 发布手册：分享开关核对 + 一次「创建 → 匿名访问 → 撤销 → 404」smoke。

## 6. 测试策略

| 层 | 覆盖 |
|---|---|
| 单测 `test_share_api.py` | 创建（含默认/上限过期、功能关闭 404）；复制/查看；撤销后 404；过期后 404；统一 404（不存在/撤销/过期同文案同状态）；审核 flagged 不可分享；限流 429；审计三条 action；跨用户创建 404 |
| 真库 `test_run_store.py` | 兄弟互斥（重建旧撤销）、resolve 契约、touch 访问计数、purge |
| E2E | 报告卡创建分享（mock）→ 链接形态断言；`/s/<bad-token>` 显示统一失效页；`/s/<ok>`（mock）渲染报告 + 无操作按钮 + 不触发第三方请求 |
| 安全 smoke（部署后） | `curl` 匿名访问 200 → 撤销 → 404；检查响应头（no-store/noindex/no-referrer） |

## 7. 影响范围与风险

| 风险 | 对策 |
|---|---|
| 链接转发扩散（无法撤回已复制内容） | 只读 + 默认 7 天 + 一键撤销 + 访问审计；文案明示「拿到链接即可查看」 |
| token 出现在日志/Referrer | 路径式 + `no-referrer` + 服务端不打印 token（观测清洗已剔 query；路径日志在发布手册核对） |
| 免登录端点扩大攻击面 | 窄端点（单报告只读）+ IP 限流 + 统一 404 + 审核闸 + 功能默认关 |
| 分享页第三方资源泄露 Referrer | 自包含页面（无外部脚本/字体），实施时核查 |
| 被撤销后仍可见（缓存） | `Cache-Control: no-store` + 无 CDN 缓存；服务端每次实时校验 |

## 8. 验收标准（DoD）

- [x] 迁移 0021 幂等；一 run 单活跃链接（重建互斥）
- [x] 所有者端点：创建（1/7/30 天，默认 7；`0`=永不过期，显式选择 + 前端二次确认）/ 查看状态 / 撤销；未开启功能不可用
- [x] 免登录 `GET /api/share/{token}`：只读返回报告；不存在/撤销/过期/审核未过统一 404；响应头 no-store/noindex/no-referrer；永不过期链接正常解析（`expires_at IS NULL`）
- [x] 安全：token 只存 hash；IP 限流；审计 `share_created`/`share_revoked`/`share_viewed`（ip_hash，不记 token）
- [x] 前端：分享面板（创建/复制/撤销/最近访问/永久二次确认）+ `/s/<token>` 只读页与失效页；功能关闭时入口隐藏
- [x] 管理：CLI `share-list` / `share-revoke`
- [x] 运维：开关/限流配置进 `.env.example`；FAQ 修正（待需求 25 合并后随收尾 PR 落地）
- [ ] 测试：单测 7 全绿 + E2E 2 全绿；真库契约与 CI 全绿；飞书镜像同步；需求 21 §5 A5 勾选 + §10 回填（合并后）

## 9. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-04 | 新建 | 需求 21 批次 A5 立项 | 先调研后方案：现状审计（F1~F8：零分享实现 / 导出私有 / token 原语可复用 / 审核闸 / BFF 口径）+ 分享链接安全调研（bearer capability / 路径 token / no-store+noindex+no-referrer / 默认过期 / 统一 404 / 可管理可审计）+ D1~D6 决策 + 详细设计 + DoD | 本文档 |
| 2026-10-04 | 修订 | 决策确认 | 用户确认：D5 支持**永不过期**（显式选择 + 二次确认 + 管理面 permanent 标注；默认仍 7 天）；D6 纯只读浏览（不导出）；D1 路径式 `/s/<token>`。建 Issue 后实施 | 本文档 |
| 2026-10-04 | 实施 | 需求 26 落地 | 迁移 0021（report_shares）；store 全套（互斥/解析/撤销/访问计数/清理）；端点 POST/GET/DELETE `/api/runs/{run_id}/share` + 免登录 `GET /api/share/{token}`（统一 404/限流/审核闸/安全响应头/审计）+ `/s/{token}` SPA 壳；前端 ShareModal + SharedReportPage + 报告卡入口 + `/api/options.share_enabled`；CLI `share-list`/`share-revoke`；`share_not_found` 错误码；测试：share API 7 + E2E 2 | #116 |
