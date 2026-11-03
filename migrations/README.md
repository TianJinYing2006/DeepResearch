# 数据库迁移（L3）

- `0001_runs_and_events.sql`（P2-A）：任务持久化第一批 —— `runs`（9 态状态机 + 创建幂等 + 租约/预算/超时字段）与 `run_events`（单调 `sequence` + 级联删除）；
- `0002_run_artifacts.sql`（P2-B）：终局产物表（报告正文 / 导出载荷，一 run 一 kind 一行，覆盖更新）；
- `0015_moderation_lifecycle.sql`（P0-1）：审核生命周期取值补齐（+`cleared`/`under_review`）与 `moderation_records.kind` 扩展（申诉决策 / `output_decision` 不可变决定）；
- `0016_quota_reservations.sql`（P0-2）：预留式月度预算（准入 hold `reserved_cny` / 终局结算 `actual`+`released`）；
- `0017_rag_active_unique.sql`（P0-11）：同一用户同一 `doc_id` 活跃摄取唯一（并发上传去重；`ON CONFLICT` 目标；`deleted` 后可重传）；
- `checks/*.sql`：结构自检（关键列 / 约束 / 级联 / upsert），**不由执行器自动跑**，由 CI `infra` job 与本地验证显式执行。

## 规则

1. 命名 `NNNN_slug.sql`（如 `0001_runs_and_events.sql`），四位递增、字典序执行；
2. **只前向**：不写回滚脚本；出错用新迁移修正（与「产物冻结/历史不回填」的项目纪律一致）；
3. 每个文件由执行器包在单事务里，**文件内不要写 `BEGIN` / `COMMIT`**；
4. 已应用版本记录在 `schema_migrations(version, applied_at, sha256)`；执行器每次部署会校验
   已应用文件的 sha256，不一致即**中止部署**（fail-closed，旧版本继续在线）；
5. 迁移必须能在空库上一次跑通，也必须能对已有库重复执行时全部 `skip`；
6. **已应用迁移文件不得修改**（哪怕只改注释）：线上 `migrate.sh` 会 hash 校验失败并中止部署，
   而「全新库」的 CI `infra` job 测不出这类问题。新增迁移时同步登记 `migrations/APPLIED_SHA256.txt`，
   `tests/test_migrations_immutable.py` 会在 PR CI 强制校验（审计 #378 的部署事故事后防线）。

> 2026-11-03 事故记录（#378）：整改批次曾修改已应用的 `0021_report_shares.sql` 注释，导致
> staging 部署在迁移步骤 fail-closed（「已应用迁移文件内容与记录不一致」）。处置 = 恢复该文件
> 原内容（sha256 与线上记录重新一致）+ 新增本清单与守卫测试；schema 变更一律走新迁移。

## 执行

```bash
# 本地/staging：由 compose 的 migrate 服务自动执行（docker compose ... up 会先等它成功）
docker compose -f docker-compose.staging.yml run --rm migrate
```

执行器实现见 `tools/migrate.sh`；CI 的 `infra` job 会验证「首次执行 + 二次幂等（applied=0）」，
并用 `migrations/checks/*.sql` 断言关键结构与约束（psql `-v ON_ERROR_STOP=1`，断言失败即 job 红）。
