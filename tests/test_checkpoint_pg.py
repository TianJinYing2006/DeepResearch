"""需求 35 R3：PG checkpoint 契约（真 PG；CI infra lane；本地无 DSN 自动跳过）。

覆盖：PostgresSaver 建表/落盘/跨「进程」恢复（部分执行 → stream(None) 续跑不重放已完成节点）。
"""
from __future__ import annotations

import operator
import os
import uuid
from typing import Annotated, TypedDict

import pytest

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_checkpointer_setup_roundtrip_and_resume():
    from langgraph.graph import END, START, StateGraph

    from web.backend.checkpoint import get_pg_checkpointer

    saver = get_pg_checkpointer(DSN)
    assert saver is not None

    class _S(TypedDict, total=False):
        steps: Annotated[list, operator.add]  # 归约通道：最终值可证明「未重放已完成节点」

    def n1(state):  # noqa: ARG001
        return {"steps": ["n1"]}

    def n2(state):  # noqa: ARG001
        return {"steps": ["n2"]}

    def n3(state):  # noqa: ARG001
        return {"steps": ["n3"]}

    builder = StateGraph(_S)
    builder.add_node("n1", n1)
    builder.add_node("n2", n2)
    builder.add_node("n3", n3)
    builder.add_edge(START, "n1")
    builder.add_edge("n1", "n2")
    builder.add_edge("n2", "n3")
    builder.add_edge("n3", END)
    app = builder.compile(checkpointer=saver)

    thread_id = f"cp-contract-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": thread_id}}
    try:
        # 部分执行：首帧=初始态，第二帧=n1 完成（checkpoint 已在节点边界落盘）
        stream = app.stream({"steps": []}, cfg, stream_mode="values")
        frames = []
        frames.append(next(stream))          # 初始态
        frames.append(next(stream))          # n1 完成
        stream.close()                        # 模拟进程被杀（不再消费）
        assert frames[-1]["steps"] == ["n1"]

        # 新「进程」恢复：stream(None) 从 checkpoint 续跑
        resumed = [chunk for chunk in app.stream(None, cfg, stream_mode="values")]
        assert resumed, "恢复流不得为空"
        # 首帧 = 恢复点 checkpoint 状态（graph.iter_run 的 skip_leading 依据）
        assert resumed[0]["steps"] == ["n1"]
        assert resumed[-1]["steps"] == ["n1", "n2", "n3"]  # 无重复、全完成
    finally:
        try:
            saver.delete_thread(thread_id)
        except Exception:  # noqa: BLE001 —— 清理失败不影响断言
            pass


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_sum_llm_tokens_ledger_reconciliation():
    """账本汇总契约（RunStore.sum_llm_tokens）：kind=llm、含 attempt、max(total, in+out)。"""
    from web.backend.store import RunStore

    store = RunStore(DSN)
    run_id = f"ledger-sum-{uuid.uuid4().hex[:8]}"
    try:
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO runs (run_id, topic) VALUES (%s, %s)", (run_id, "t"))
            cur.execute(
                "INSERT INTO usage_ledger (run_id, attempt, kind, provider, model, role, "
                "input_tokens, output_tokens, total_tokens) VALUES "
                "(%s, 1, 'llm', 'dashscope', 'qwen-plus', 'planner', 10, 20, 30), "
                "(%s, 1, 'llm', 'dashscope', 'qwen-plus', 'writer', 5, 5, 0), "
                "(%s, 2, 'llm', 'dashscope', 'qwen-plus', 'validator', 1, 2, 3), "
                "(%s, 1, 'search', 'bocha', '', 'web', 0, 0, 7)",
                (run_id, run_id, run_id, run_id),
            )
        assert store.sum_llm_tokens(run_id) == 30 + 10 + 3  # search 不计；total=0 时用 in+out
    finally:
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM usage_ledger WHERE run_id = %s", (run_id,))
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
        store.close()
