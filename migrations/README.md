# 数据库迁移（L3）

- `0001_runs_and_events.sql`（P2-A）：任务持久化第一批 —— `runs`（9 态状态机 + 创建幂等 + 租约/预算/超时字段）与 `run_events`（单调 `sequence` + 级联删除）；
- `0002_run_artifacts.sql`（P2-B）：终局产物表（报告正文 / 导出载荷，一 run 一 kind 一行，覆盖更新）；
- `checks/*.sql`：结构自检（关键列 / 约束 / 级联 / upsert），**不由执行器自动跑**，由 CI `infra` job 与本地验证显式执行。

## 规则

1. 命名 `NNNN_slug.sql`（如 `0001_runs_and_events.sql`），四位递增、字典序执行；
2. **只前向**：不写回滚脚本；出错用新迁移修正（与「产物冻结/历史不回填」的项目纪律一致）；
3. 每个文件由执行器包在单事务里，**文件内不要写 `BEGIN` / `COMMIT`**；
4. 已应用版本记录在 `schema_migrations(version, applied_at)`；
5. 迁移必须能在空库上一次跑通，也必须能对已有库重复执行时全部 `skip`。

## 执行

```bash
# 本地/staging：由 compose 的 migrate 服务自动执行（docker compose ... up 会先等它成功）
docker compose -f docker-compose.staging.yml run --rm migrate
```

执行器实现见 `tools/migrate.sh`；CI 的 `infra` job 会验证「首次执行 + 二次幂等（applied=0）」，
并用 `migrations/checks/*.sql` 断言关键结构与约束（psql `-v ON_ERROR_STOP=1`，断言失败即 job 红）。
