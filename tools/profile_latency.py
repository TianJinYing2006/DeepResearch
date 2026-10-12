"""档位耗时标定（需求 35 R5 / TBD-7 拍板口径，2026-10-12）。

从任务库统计各档位（quick / standard / deep）的墙钟耗时 p50 / p90，供
`docs/operations/capacity-model.md` §6 回填与「是否值得做并行实验」决策。

用法::

    python tools/profile_latency.py --dsn "$DR_DATABASE_URL" [--since-days 30]
    # 服务器（PG 未公网暴露）可执行等价 SQL（见 capacity-model.md §6 记录）：
    #   ssh <host> "docker exec -i <pg> psql -U deepresearch -d deepresearch" < 本文件头 SQL

口径（TBD-7，不得曲解）：
- 样本 = 终局且 `started_at` / `finished_at` 齐全的 run，按 `request.profile.name` 分组；
- 输出 p50 / p90 / avg / max（秒）+ 样本数；**样本 < 20 的档位如实标注「样本不足」**；
- quick 档补测**不能替代** standard / deep 数据；墙钟不代表质量，并行决策须走固定题集对照实验。
"""
from __future__ import annotations

import argparse
import os
import sys

STATS_SQL = """
SELECT COALESCE(request -> 'profile' ->> 'name', request ->> 'profile', '(legacy)') AS profile,
       count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at)))::numeric, 1) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (
           ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at)))::numeric, 1) AS p90_s,
       round(avg(EXTRACT(EPOCH FROM (finished_at - started_at)))::numeric, 1) AS avg_s,
       round(max(EXTRACT(EPOCH FROM (finished_at - started_at)))::numeric, 1) AS max_s
  FROM runs
 WHERE finished_at IS NOT NULL AND started_at IS NOT NULL
   AND (%s::int IS NULL OR created_at >= now() - make_interval(days => %s::int))
 GROUP BY 1
 ORDER BY 1
"""

MIN_SAMPLES = 20  # TBD-7：样本不足必须如实标注，不作为决策依据


def _rows(dsn: str, since_days: int | None) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(STATS_SQL, (since_days, since_days))
        return cur.fetchall()


def render_markdown(rows: list[dict], since_days: int | None) -> str:
    window = f"最近 {since_days} 天" if since_days else "全量"
    lines = [f"| 档位 | 样本 n（{window}） | p50 (s) | p90 (s) | avg (s) | max (s) | 备注 |",
             "|---|---|---|---|---|---|---|"]
    for row in rows:
        note = "样本充足" if row["n"] >= MIN_SAMPLES else f"样本不足（<{MIN_SAMPLES}，如实标注）"
        lines.append(
            f"| {row['profile']} | {row['n']} | {row['p50_s']} | {row['p90_s']} "
            f"| {row['avg_s']} | {row['max_s']} | {note} |")
    if not rows:
        lines.append("| （无终局样本） | 0 | - | - | - | - | 无数据，不得推断 |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="档位耗时标定（需求 35 R5）")
    parser.add_argument("--dsn", default=os.getenv("DR_DATABASE_URL", ""),
                        help="PostgreSQL DSN（默认读 DR_DATABASE_URL）")
    parser.add_argument("--since-days", type=int, default=None,
                        help="仅统计最近 N 天创建的 run（默认全量）")
    args = parser.parse_args()
    if not args.dsn:
        print("缺少 DSN：--dsn 或 DR_DATABASE_URL", file=sys.stderr)
        return 2
    rows = _rows(args.dsn, args.since_days)
    print(render_markdown(rows, args.since_days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
