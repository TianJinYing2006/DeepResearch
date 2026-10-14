"""需求 34 批次收口：真实冒烟 + 确定性断言（E1~E5）。

用法（本地；需 .env 的 DASHSCOPE / BOCHA key 与已迁移的本地 PostgreSQL）：

    python tools/smoke_evidence_chain.py --topic "..." [--profile quick] [--dsn ...] [--out ...]

行为：

1. 在本地 PG 建一条 smoke run 行（FK 锚点）→ 绑定 ``FetchContext``（PgSnapshotStore +
   run 级预算）→ ``create_graph`` → 真实研究（默认 quick 档）；
2. 运行后执行 DoD「确定性断言」：
   - 每条**存在**的引用要么 ``quote_status ∈ {located, not_found}``，要么其 finding 有
     显式 ``snapshot_status`` —— 零条「双无」（unknown_source_id 类另计：已被 E5 阻断）；
   - located 者按 L0~L2 **离线复算**：``snapshot[char_start:char_end] == quote`` 且可定位；
   - ``delivery_self_check`` 三项存在；``evidence_grading`` counts 存在；
   - 抓取预算三项均有值且未越上限；
3. 摘要写 ``--out``（默认 ``local-artifacts/smoke-e1e5.json``，gitignored）。

退出码：0 断言全过 / 1 断言失败 / 2 运行失败或前置缺失。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _resolve_dsn(explicit: str) -> str:
    dsn = (explicit or os.getenv("DR_SMOKE_DATABASE_URL")
           or os.getenv("DR_TEST_DATABASE_URL") or "").strip()
    if not dsn:
        print("[smoke] 缺少 DSN：--dsn / DR_SMOKE_DATABASE_URL / DR_TEST_DATABASE_URL")
        sys.exit(2)
    return dsn


def main() -> int:
    parser = argparse.ArgumentParser(description="需求 34 证据链冒烟（E1~E5）")
    parser.add_argument("--topic", default="RISC-V 架构在 2024 年的发展现状")
    parser.add_argument("--instructions", default="请简明作答，控制在 600 字内。")
    parser.add_argument("--profile", default="quick")
    parser.add_argument("--model", default="",
                        help="覆盖档位主模型与 fast 档（如 qwen-plus；本机 MaaS 端点缺 qwen-turbo 时的绕行，见需求 33 §9.1）")
    parser.add_argument("--dsn", default="")
    parser.add_argument("--out", default=str(REPO_ROOT / "local-artifacts" / "smoke-e1e5.json"))
    args = parser.parse_args()

    from dataclasses import replace as dataclass_replace

    from config import config
    from research_engine.graph import create_graph
    from research_engine.quote_locate import locate_quote
    from research_engine.runtime_profile import set_profile
    from research_engine.snapshots import FetchContext, use_fetch_context
    from web.backend.profiles import resolve_profile
    from web.backend.snapshots import PgSnapshotStore
    from web.backend.store import RunStore

    dsn = _resolve_dsn(args.dsn)
    profile = resolve_profile(args.profile)
    if profile is None:
        print(f"[smoke] 未知档位：{args.profile}")
        return 2
    if args.model:
        profile = dataclass_replace(profile, model=args.model)
        # fast 档不在档位覆盖集内（runtime_profile._PROFILE_MODEL_ROLES），显式同步
        config.llm.fast_model = args.model

    # 需求 36：冒烟工具是单次运行、无「挂起-回答-恢复」通道 —— 与 CLI 同口径
    # 强制关闭图上 HITL（告警不静默；run() 的 interrupt 防御仍保留为兜底）
    for hitl_field in ("clarify_enabled", "plan_approval_enabled"):
        if getattr(config.hitl, hitl_field):
            setattr(config.hitl, hitl_field, False)
            print(f"[smoke] ⚠️ {hitl_field} 需要队列模式：冒烟工具已强制关闭"
                  f"（本次运行不等待人类输入）", file=sys.stderr)

    run_store = RunStore(dsn)
    snap_store = PgSnapshotStore(run_store)
    run_id = f"smoke-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    with run_store._connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO runs (run_id, topic) VALUES (%s, %s)", (run_id, args.topic))

    set_profile(profile)
    ctx = FetchContext.create(store=snap_store, run_id=run_id)
    print(f"[smoke] run_id={run_id} profile={args.profile}")
    print(f"[smoke] topic={args.topic}")

    with use_fetch_context(ctx):
        graph = create_graph()  # 须在绑定之后（Researcher 构建时捕获）
        result = graph.run(args.topic, args.instructions)

    budget = ctx.budget.snapshot()
    citations = list(result.citations or [])
    findings = list(result.working_findings or result.findings or [])
    trace_id = getattr(graph, "trace_id", None)

    def _finding_for(citation):
        if citation.finding_id and str(citation.finding_id).isdigit():
            index = int(citation.finding_id) - 1
            if 0 <= index < len(findings):
                return findings[index]
        if citation.source:
            return next((f for f in findings if f.source == citation.source), None)
        return None

    def _snapshot_status(citation) -> str:
        finding = _finding_for(citation)
        if finding is None:
            return ""
        return str((getattr(finding, "metadata", None) or {}).get("snapshot_status") or "")

    # ---- 确定性断言 ----
    # 快照链断言只覆盖 web 引用（arxiv/rag/code 有独立原文层，不在快照链内）
    unmarked = [c for c in citations
                if c.existence and c.source_type == "web"
                and c.quote_status not in ("located", "not_found")
                and not _snapshot_status(c)]
    unmarked_detail = [{"finding_id": c.finding_id, "source": c.source,
                        "source_type": c.source_type} for c in unmarked]
    blocked = [c for c in citations if not c.existence]
    located = [c for c in citations if c.quote_status == "located"]
    offset_violations = []
    for citation in located:
        snapshot_text = snap_store.load_snapshot(citation.snapshot_id)
        if (not snapshot_text
                or snapshot_text[citation.char_start:citation.char_end] != citation.quote
                or locate_quote(snapshot_text, citation.quote) is None):
            offset_violations.append(citation.finding_id or citation.source)

    self_check = dict(result.delivery_self_check or {})
    grading = dict(result.evidence_grading or {})
    checks_ok = len(self_check.get("checks") or []) == 3
    grading_ok = bool((grading.get("counts") or {}).get("total", None) is not None)
    budget_ok = (budget["fetches"] <= budget["max_fetches"]
                 and budget["bytes"] <= budget["max_bytes"]
                 and budget["seconds"] <= budget["max_seconds"])

    with run_store._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM run_snapshots WHERE run_id = %s", (run_id,))
        run_snapshot_count = cur.fetchone()["n"]
        cur.execute(
            "SELECT count(DISTINCT sha256) AS n FROM run_snapshots WHERE run_id = %s", (run_id,))
        distinct_snapshot_count = cur.fetchone()["n"]

    summary = {
        "run_id": run_id,
        "trace_id": trace_id,
        "topic": args.topic,
        "profile": args.profile,
        "run_status": result.run_status,
        "citation_count": len(citations),
        "quote_located": len(located),
        "quote_not_found": sum(1 for c in citations if c.quote_status == "not_found"),
        "blocked_unknown_source": len(blocked),
        "unmarked_citations": len(unmarked),
        "unmarked_detail": unmarked_detail,
        "offset_violations": offset_violations,
        "snapshot_rows": run_snapshot_count,
        "snapshot_distinct": distinct_snapshot_count,
        "fetch_budget": budget,
        "budget_ok": budget_ok,
        "self_check_counts": self_check.get("counts"),
        "self_check_ok": checks_ok,
        "grading_counts": grading.get("counts"),
        "grading_ok": grading_ok,
        "findings_count": len(findings),
        "error": dict(result.error or {}),
        "degradation_tail": [
            (entry.to_dict() if hasattr(entry, "to_dict") else dict(entry))
            for entry in (result.degradation_log or [])[-8:]
        ],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[smoke] run_status={result.run_status} citations={len(citations)} "
          f"located={len(located)} not_found={summary['quote_not_found']} "
          f"blocked={len(blocked)} unmarked={len(unmarked)}")
    print(f"[smoke] snapshots rows={run_snapshot_count} distinct={distinct_snapshot_count} "
          f"fetch_budget={budget['fetches']}/{budget['max_fetches']} "
          f"bytes={budget['bytes']}/{budget['max_bytes']} "
          f"seconds={budget['seconds']}/{budget['max_seconds']}")
    print(f"[smoke] self_check_ok={checks_ok} grading_ok={grading_ok} "
          f"budget_ok={budget_ok} offset_violations={len(offset_violations)}")
    print(f"[smoke] summary -> {out_path}")
    run_store.close()  # 收口连接池（避免退出时 pool 线程警告）

    if result.run_status == "failed":
        print("[smoke] RUN FAILED")
        return 2
    if unmarked or offset_violations or not checks_ok or not grading_ok or not budget_ok:
        print("[smoke] ASSERTIONS FAILED")
        return 1
    print("[smoke] ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
