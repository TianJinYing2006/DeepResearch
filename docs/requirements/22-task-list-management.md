# 需求 22：任务列表增强（搜索 / 重命名 / 归档置顶 / 失败重试）

> 状态：**已合**（PR #104 squash 合并至 dev：`b9c7330`；迁移 0018 已在 staging 实测幂等通过，API/Store/前端/E2E 全绿）。
> 父需求：`docs/requirements/21-product-modules-completeness.md` §4-B / §5-A1。
> 飞书镜像：待同步。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 22 |
| 标题 | 任务列表增强（搜索 / 重命名 / 归档置顶 / 失败重试） |
| 优先级 | P1 |
| 状态 | 已合 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #103 |
| 关联 PR | #104（squash `b9c7330`） |
| 创建 / 更新 | 2026-10-03 |

## 2. 问题背景

需求 21 差距表 B 层：任务能跑但**不能管理**。现状历史列表只有「状态筛选 + 分页 + 报告预览」，
任务一多就无法按主题找回；失败任务只能在前端用「用同样参数重试」重新提交（不复用历史上下文）；
没有任何重命名/归档手段。市面对标（Morphic 历史、Kimi「项目」）都把任务列表当核心页面运营。

## 3. 需求分析

**目标**：把 `GET /api/runs` 从「只读分页」升级为可检索、可整理的任务中心；所有写操作可审计、可回滚（软归档）。

**量化定义**：

- 关键词搜索在 1 万条 run 量级下 P95 < 300ms（`pg_trgm` GIN 索引）；
- 重命名/归档/置顶/重试 4 类操作 100% 落 `audit_logs`；
- 失败重试成功率与「手动重新提交」一致（同 request 快照），且 `retry_of` 建立血缘；
- E2E 新增覆盖 ≥ 6 条（搜索、空结果、重命名、归档、置顶排序、失败重试）。

## 4. 当前设计（现状与痛点）

### 4.1 后端

- 路由：仅 `GET /api/runs`（`web/backend/main.py:1354`）：`limit` 默认 20、1~100（:1357），`offset>=0`（:1358），
  `status` 逗号分隔过滤（:1359、:1370）；响应 `{runs, limit, offset}`（:1373）。
- 单条字段 `_run_brief`（`main.py:645-658`）：run_id / topic / status / stop_reason / created_at / started_at /
  finished_at / token_used / cost_estimate_cny / has_report / moderation_status。
- **没有** 重命名 / 删除 / 归档 / 重试接口；`/api/runs` 无关键词与归档过滤。
- `RunStore.list_runs`（`web/backend/store.py:401`）：`user_id/statuses/limit/offset`，
  `ORDER BY r.created_at DESC, r.run_id DESC`（:419-421）。
- `update_status` 白名单不含 `topic`（`store.py:112-117`）——**全仓无 topic 更新路径**。
- `retry_of` 列已存在（`migrations/0001_runs_and_events.sql:12-43`）但在 `_UPDATABLE_FIELDS`（`store.py:114`）中闲置，
  无任何写入方；前端「重试」是 `App.tsx:281-282 → launch(lastRequest)` 的**新 run**，无血缘。

### 4.2 数据模型

`runs` 表（`migrations/0001:12-43`）：run_id(PK) / user_id / tenant_id / status(CHECK 9 态) / research_status /
stop_reason / current_node / **topic NOT NULL** / request jsonb / token_used / cost_estimate_cny /
budget_limit_cny / budget_used_cny / attempt / **retry_of** / idempotency_key / worker_id / worker_status /
lease_expires_at / timeout_at / hard_deadline_at / cancel_requested_at / created_at / queued_at / started_at / finished_at；
历史索引 `(user_id, created_at DESC)`（:55）。后续迁移：用户外键 `ON DELETE SET NULL`（0003）、
`moderation_status`（0004/0015）、`request_hash`（0008）。

### 4.3 前端

`web/frontend/src/features/history/HistoryPanel.tsx`：状态下拉（:106）、每页 10（:22）、
「加载更多」offset 分页（:39、:170）、报告预览模态（:82）；testid：
`history-panel` / `history-status-filter` / `history-retry`（翻页重试）/ `flagged-badge` /
`history-load-more` / `history-preview`。**无搜索框、无行内操作**；API 调用是组件内直接 `fetch`（api.ts 无封装）。

### 4.4 痛点

1. 主题多起来只能靠滚动；`topic` 是唯一可读标识却不可改（导出文件名依赖 topic，需求 17）；
2. 失败的 run 留在列表里无法隐藏，也无「原样重跑」；
3. 进行中任务在启动面板、已完成在历史面板，两个入口心智分裂。

## 5. 优化方案

### 5.1 数据模型（迁移 0018，编号顺延）

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
ALTER TABLE runs ADD COLUMN pinned_at   timestamptz;
ALTER TABLE runs ADD COLUMN archived_at timestamptz;
CREATE INDEX runs_topic_trgm_idx ON runs USING gin (topic gin_trgm_ops);
```

> 备注：不加 `deleted_at` —— 删除语义由「归档 + 保留期清扫」承担，避免与 90 天清理/审计口径冲突。

### 5.2 API 变更

| 方法与路径 | 变更 | 语义与约束 |
|---|---|---|
| `GET /api/runs` | 增 `q`、`archived`（默认 false）、排序固定为「置顶优先」 | `q` 对 topic 做 `ILIKE %q%`（转义 `%_`），长度 ≤ 100；`archived=true` 只看归档 |
| `PATCH /api/runs/{run_id}` | 新增，body `{topic}` | 仅本人；strip 后 1~200 字；写 `audit_logs`（`run_renamed`，含新旧值） |
| `POST /api/runs/{run_id}/pin` / `unpin` | 新增 | 幂等；`pinned_at` 置/清；审计 `run_pinned` / `run_unpinned` |
| `POST /api/runs/{run_id}/archive` / `unarchive` | 新增 | 幂等；审计 `run_archived` / `run_unarchived`；活跃状态（QUEUED/RUNNING）禁止归档（409） |
| `POST /api/runs/{run_id}/retry` | 新增 | 仅终态且 `status ∈ {FAILED, LOST, TIMED_OUT}`；复制原 `request` 快照创建新 run，`retry_of=原 run_id`，新幂等键；审计 `run_retried` |

- 列表排序：`ORDER BY pinned_at DESC NULLS LAST, created_at DESC, run_id DESC`；
- 所有路径先按 `user_id` 归属校验（越权返回 404 与现存口径一致），再执行；
- `retry` 复用 runner 既有提交路径（`runner.py:200` 创建逻辑），配额/预算照常走预留事务，不做豁免。

### 5.3 RunStore 方法

| 方法 | 说明 |
|---|---|
| `list_runs(user_id, statuses, q, archived, limit, offset)` | 增 q/archived/置顶排序 |
| `update_topic(user_id, run_id, topic) -> bool` | 归属+影响行数校验 |
| `set_pinned(user_id, run_id, pinned)` / `set_archived(...)` | 幂等 |
| `get_run_for_retry(user_id, run_id)` | 返回 request/terminal 校验 |

### 5.4 前端（HistoryPanel）

- 顶部：搜索框（debounce 400ms）+「显示归档」开关；空结果态；
- 行内操作：重命名（内联编辑）、置顶、归档、重试（仅失败态显示）、预览（保留）；
- 新增 testid：`history-search`、`history-archived-toggle`、`history-rename`、`history-pin`、
  `history-archive`、`history-run-retry`、`history-empty`；
- 「进行中任务」保持在启动面板，历史列表对活跃 run 显示状态徽章并提供「取消」（复用现有 cancel），不做入口合并（避免大改）。

## 6. 设计策略

1. **软归档优先**：不提供用户侧硬删除；数据留存量按需求 10 保留期政策（90 天）清扫，审计与用量账本不受影响；
2. **重试 = 新 run + 血缘**：不改原 run、不做「原地重置」，计费与配额口径无需特判；`retry_of` 终于有了写入方；
3. **搜索选型**：`pg_trgm` 单扩展 + GIN，不引全文检索引擎；1 万条量级下足够，超量再评估 `tsvector`/外部索引；
4. **重命名即改展示名**：导出文件名（需求 17）随 topic 变化属于预期行为，审计留痕；
5. **不做**标签/分组/批量操作（登记为后续增量；需要时另立需求）。

## 7. 验收标准（DoD）

- [x] 迁移 0018 幂等可重跑，含 schema 自检（列/索引/扩展）——2026-10-03 staging 实测：migrate 两次（apply=1 → 0），`pinned_at`/`archived_at`/`runs_topic_trgm_idx`/`pg_trgm` 全部就位
- [x] 5 个新接口 + 列表扩参，全部有归属校验与审计——`tests/test_runs_management_api.py` 5 用例通过（含越权 404 / CSRF / 幂等重试）
- [x] 搜索：`q` 转义正确（`%`/`_`/空串），归档过滤与置顶排序正确——store 用例（真实 PG，CI `infra` job 执行）+ FakeStore API 用例
- [x] 重试：终态校验、血缘写入、配额预留生效；非终态返回 409
- [x] 前端新 testid E2E 全绿（搜索/重命名/归档/重试/空态 3 条新用例；桌面套件 40/40）；`tsc`/`build`/`ruff` 全绿
- [x] 回填需求 21 §10 变更记录

## 8. 影响范围与风险

| 风险 | 对策 |
|---|---|
| `pg_trgm` 扩展可用性 | `postgres:16-alpine` 自带 contrib；迁移内 `IF NOT EXISTS`，失败即中止发布 |
| ILIKE 性能退化 | GIN 索引 + `q` 长度上限；后续量级评估 |
| 重命名影响导出名 | 预期行为，审计留痕；如需「下载名与当前 topic 解耦」另立需求 |
| 归档与保留期冲突 | 归档仅影响列表可见性；保留期清扫按 `finished_at` 照旧 |
| 重试风暴 | 沿用提交限流与每用户并发/日配额（`DR_SUBMIT_RATE_PER_MINUTE` 等） |

## 9. 测试策略

- 单测：`list_runs` 各参数组合、`update_topic` 归属、pin/archive 幂等、retry 终态校验；
- 集成（真实 PG，沿用 CI `infra` job 模式）：trgm 查询计划、迁移重跑；
- E2E：搜索→重命名→置顶→归档→失败重试（路由拦截，与现有 `e2e/` 口径一致）；
- 越权用例：他人 run_id 全部返回 404。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-03 | 新建 | 需求 21 批次 A1 立项 | 初稿：现状核实（接口/表/前端）+ 迁移 0018 + 5 接口 + DoD | 本文档 |
| 2026-10-03 | 实施 | 需求 22 落地 | 迁移 0018（staging 幂等实测）；`list_runs` 扩参（q/archived/置顶排序）+ `update_topic`/`set_pinned`/`set_archived` + `retry_of` 入库；5 新路由（PATCH/pin/unpin/archive/unarchive/retry，含 `run_active`/`run_not_retryable` 错误码）；HistoryPanel 搜索防抖/归档开关/行内操作；测试：新 API 5/5、E2E 40/40、ruff/build 全绿 | 待合 |
