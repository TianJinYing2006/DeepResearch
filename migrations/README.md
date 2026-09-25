# 数据库迁移（L3）

P1 只建立**执行机制**：业务表（`runs` / `run_events` / `users` 等）由 P2 的第一批迁移落库，
本目录当前没有 `*.sql` 属于预期状态（执行器会正常退出并输出 `applied=0`）。

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

执行器实现见 `tools/migrate.sh`；CI 的 `infra` job 会验证「首次执行 + 二次幂等（applied=0）」。
