"""需求 36 Phase 2：图 interrupt 行为（离线单测，零 LLM）。

- 关闭态零漂移：HITL 关闭时节点集合与历史一致，全链路节点序列不变（TBD-0 门槛）；
- 澄清回路：interrupt 载荷 / 回答并入 user_scope / ≤2 轮 / 到期自动继续；
- 计划审批：批准 / 修改回路（revise_plan → 再次停等）/ 轮次封顶；
- run()/CLI 路径对 interrupt 显式失败（HITL 仅队列模式支持）。

真 PG「挂起-进程退出-回答-另一 worker 恢复」演练见 `test_hitl_resume_drill_pg.py`。
"""
from __future__ import annotations

from config import config
from research_engine.graph import DeepResearchGraph
from research_engine.state import SubQuestion
from research_engine.streaming import STOP_COMPLETED, STOP_SUSPENDED

ORIGINAL_NODES = {"plan", "research", "critic", "revise", "write", "validate", "repair",
                  "render"}
FINAL_SEQUENCE = ["plan", "research", "critic", "write", "validate", "render"]


def _stop_critic(state):
    state.critic_signal = "stop"
    state.sufficient = True
    state.critic_stop_reason = "stub"
    return "stop"


def _stub_pipeline(g: DeepResearchGraph) -> DeepResearchGraph:
    """把全链路 Agent 换成零 LLM 桩（本文件只验证拓扑/交互协议，不验证研究内容）。"""
    g.planner.plan = lambda topic, ui, state: [
        SubQuestion(id="q1", question="问题一", rationale="r")]
    g.planner.revise_plan = lambda topic, subs, feedback, state=None: [
        SubQuestion(id="q9", question=f"修订后：{feedback}", rationale="r2")]
    g.planner.last_revise_fallback = False
    g.planner.drain_planner_events = lambda: []
    g.planner.drain_degradations = lambda: []
    g.researcher.search_once = lambda q, s: ([], {})
    g.researcher.drain_degradations = lambda: []
    g.critic.decide = _stop_critic
    g.critic.drain_events = lambda: []
    g.context.compress = lambda findings, topic, state: []
    g.writer.write = lambda topic, subs, working, state: "# 报告\n结论 [来源: 1]"
    g.writer.drain_degradations = lambda: []
    g.validator.validate = lambda report, working, state, evidence=None: []
    g.validator.last_validation_stats = {}
    g.validator.drain_degradations = lambda: []
    g.clarifier.decide = lambda *a, **k: {
        "needed": False, "questions": [], "reason": "clear", "failed": False}
    return g


def _nodes(steps):
    return [s.node for s in steps if s.node]


# ---------------------------------------------------------------- 关闭态零漂移

def test_disabled_topology_has_no_hitl_nodes():
    g = DeepResearchGraph()
    drawn = g.graph.get_graph()
    names = {n for n in drawn.nodes if not n.startswith("__")}
    assert names == ORIGINAL_NODES
    start_edges = {(e.source, e.target) for e in drawn.edges if e.source == "__start__"}
    assert start_edges == {("__start__", "plan")}


def test_disabled_path_node_sequence_unchanged():
    """关闭态全链路节点序列 = 历史逐项（条件边直连，无额外节点执行）。"""
    g = _stub_pipeline(DeepResearchGraph())
    steps = list(g.iter_run("主题", thread_id="hitl-graph-closed"))
    assert _nodes(steps) == FINAL_SEQUENCE
    assert steps[-1].terminal and steps[-1].stop_reason == STOP_COMPLETED


# ---------------------------------------------------------------- 澄清回路

def test_clarify_interrupt_resume_and_scope(monkeypatch):
    monkeypatch.setattr(config.hitl, "clarify_enabled", True)
    g = _stub_pipeline(DeepResearchGraph())

    def decide(topic, ui, clarification, state=None):
        if not (clarification or {}).get("answers"):
            return {"needed": True, "questions": ["研究范围？"], "reason": "ambiguous",
                    "failed": False}
        return {"needed": False, "questions": [], "reason": "clear", "failed": False}

    g.clarifier.decide = decide

    steps1 = list(g.iter_run("主题", thread_id="hitl-clarify-1"))
    assert _nodes(steps1) == ["clarify"]
    assert steps1[-1].stop_reason == STOP_SUSPENDED
    assert steps1[-1].interrupt == {
        "kind": "clarification", "round": 1,
        "content": {"questions": ["研究范围？"], "topic": "主题"}}

    steps2 = list(g.iter_run("主题", thread_id="hitl-clarify-1", resume=True,
                             resume_value={"kind": "clarification", "action": "reply",
                                           "text": "范围=近三个月", "auto": False}))
    nodes2 = _nodes(steps2)
    # 恢复后：await_clarify 完成 → clarify 复判（已消歧）→ 进入主链路
    assert nodes2 == ["await_clarify", "clarify", *FINAL_SEQUENCE]
    assert steps2[-1].stop_reason == STOP_COMPLETED
    final = steps2[-1].state
    assert final.user_scope == "范围=近三个月"
    assert final.research_spec.get("user_scope") == "范围=近三个月"
    assert final.clarification["answers"][0]["text"] == "范围=近三个月"


def test_clarify_round_cap(monkeypatch):
    monkeypatch.setattr(config.hitl, "clarify_enabled", True)
    monkeypatch.setattr(config.hitl, "max_clarify_rounds", 2)
    g = _stub_pipeline(DeepResearchGraph())
    g.clarifier.decide = lambda *a, **k: {
        "needed": True, "questions": ["还有歧义？"], "reason": "x", "failed": False}

    s1 = list(g.iter_run("主题", thread_id="hitl-clarify-cap"))
    assert s1[-1].stop_reason == STOP_SUSPENDED and s1[-1].interrupt["round"] == 1

    s2 = list(g.iter_run("主题", thread_id="hitl-clarify-cap", resume=True,
                         resume_value={"action": "reply", "text": "答1", "auto": False}))
    assert s2[-1].stop_reason == STOP_SUSPENDED and s2[-1].interrupt["round"] == 2

    s3 = list(g.iter_run("主题", thread_id="hitl-clarify-cap", resume=True,
                         resume_value={"action": "reply", "text": "答2", "auto": False}))
    nodes3 = _nodes(s3)
    assert nodes3 == ["await_clarify", *FINAL_SEQUENCE]  # 达上限：不再回 clarify 追问
    assert s3[-1].state.clarify_round == 2


def test_clarify_expiry_auto_continue(monkeypatch):
    monkeypatch.setattr(config.hitl, "clarify_enabled", True)
    g = _stub_pipeline(DeepResearchGraph())
    g.clarifier.decide = lambda *a, **k: {
        "needed": True, "questions": ["q？"], "reason": "x", "failed": False}

    list(g.iter_run("主题", thread_id="hitl-clarify-auto"))
    s2 = list(g.iter_run("主题", thread_id="hitl-clarify-auto", resume=True,
                         resume_value={"kind": "clarification", "action": "reply",
                                       "text": "", "auto": True,
                                       "reason": "clarification_expired"}))
    nodes2 = _nodes(s2)
    assert nodes2 == ["await_clarify", *FINAL_SEQUENCE]  # 到期：不再追问，直接继续
    final = s2[-1].state
    assert final.user_scope == ""
    assert "user_scope" not in final.research_spec  # 空 scope 不进规格（关闭态同字节）


def test_clarify_llm_failure_skips_with_governance_event(monkeypatch):
    monkeypatch.setattr(config.hitl, "clarify_enabled", True)
    g = _stub_pipeline(DeepResearchGraph())
    g.clarifier.decide = lambda *a, **k: {
        "needed": False, "questions": [], "reason": "TimeoutError", "failed": True}

    steps = list(g.iter_run("主题", thread_id="hitl-clarify-fail"))
    assert _nodes(steps) == ["clarify", *FINAL_SEQUENCE]
    assert steps[-1].stop_reason == STOP_COMPLETED
    events = steps[-1].state.planner_events
    assert any(e.get("event") == "clarify_skipped" for e in events)
    assert steps[-1].state.degradation_log == []  # 跳过不是故障


# ---------------------------------------------------------------- 计划审批

def test_plan_approval_approve(monkeypatch):
    monkeypatch.setattr(config.hitl, "plan_approval_enabled", True)
    g = _stub_pipeline(DeepResearchGraph())

    s1 = list(g.iter_run("主题", thread_id="hitl-approve"))
    assert _nodes(s1) == ["plan"]
    assert s1[-1].stop_reason == STOP_SUSPENDED
    payload = s1[-1].interrupt
    assert payload["kind"] == "plan_approval" and payload["round"] == 1
    assert payload["content"]["subquestions"][0]["id"] == "q1"
    assert payload["content"]["plan_version"] == 1

    s2 = list(g.iter_run("主题", thread_id="hitl-approve", resume=True,
                         resume_value={"kind": "plan_approval", "action": "approve",
                                       "text": "", "auto": False}))
    assert _nodes(s2) == ["await_plan", *FINAL_SEQUENCE[1:]]  # plan 不重放
    assert s2[-1].stop_reason == STOP_COMPLETED
    assert s2[-1].state.approval_round == 1


def test_plan_approval_revise_then_approve(monkeypatch):
    monkeypatch.setattr(config.hitl, "plan_approval_enabled", True)
    g = _stub_pipeline(DeepResearchGraph())
    list(g.iter_run("主题", thread_id="hitl-revise"))

    s2 = list(g.iter_run("主题", thread_id="hitl-revise", resume=True,
                         resume_value={"kind": "plan_approval", "action": "revise",
                                       "text": "收敛范围", "auto": False}))
    assert _nodes(s2) == ["await_plan", "revise_plan"]
    assert s2[-1].stop_reason == STOP_SUSPENDED
    assert s2[-1].interrupt["round"] == 2
    assert s2[-1].interrupt["content"]["subquestions"][0]["id"] == "q9"

    s3 = list(g.iter_run("主题", thread_id="hitl-revise", resume=True,
                         resume_value={"kind": "plan_approval", "action": "approve",
                                       "text": "", "auto": False}))
    final = s3[-1].state
    assert final.subquestions[0].id == "q9"
    assert final.plan_version == 2  # 成功修订递增版本
    assert final.approval_round == 2


def test_plan_approval_revise_cap_forces_continue(monkeypatch):
    monkeypatch.setattr(config.hitl, "plan_approval_enabled", True)
    monkeypatch.setattr(config.hitl, "max_approval_rounds", 1)
    g = _stub_pipeline(DeepResearchGraph())
    list(g.iter_run("主题", thread_id="hitl-revise-cap"))

    # 第 1 轮就提出修改，但轮次上限=1 ⇒ 强制继续（不再给修订回路）
    s2 = list(g.iter_run("主题", thread_id="hitl-revise-cap", resume=True,
                         resume_value={"kind": "plan_approval", "action": "revise",
                                       "text": "改", "auto": False}))
    nodes2 = _nodes(s2)
    assert "revise_plan" not in nodes2
    assert nodes2 == ["await_plan", *FINAL_SEQUENCE[1:]]
    assert s2[-1].stop_reason == STOP_COMPLETED


# ---------------------------------------------------------------- run()/CLI 防御

def test_run_path_rejects_interrupt(monkeypatch):
    """run()/CLI 无 resume 通道：出现挂起必须显式失败（绝不把挂起当终局）。"""
    monkeypatch.setattr(config.hitl, "clarify_enabled", True)
    g = _stub_pipeline(DeepResearchGraph())
    g.clarifier.decide = lambda *a, **k: {
        "needed": True, "questions": ["q？"], "reason": "x", "failed": False}

    out = g.run("主题", thread_id="hitl-run-guard")
    assert out.run_status == "failed"
    assert "HITL" in str((out.error or {}).get("message", ""))
