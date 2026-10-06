# -*- coding: utf-8 -*-
"""审计 F12/F14 回归：多源印证代码复核 + 研究规格与计划身份贯穿。

F12：
- ``supported=true`` 必须给出真实存在、达到 min_sources 个独立来源（不同 source）
  的证据编号；同文档多分块不算独立；缺编号/编号越界 ⇒ 降级 False（应用侧可复核）；
- ``contradicting_evidence_ids`` 只记录不影响判定；validator_stats 三口径可见。

F14：
- plan 固化 ``research_spec``（user_instructions + 验收条件）与 ``plan_version=1``；
- replan 按显式 ``id_mapping`` 重分配旧证据（延续→改写 sq_id；未映射→转未归类 ""），
  版本 +1；降级沿用旧计划则不重映射、不递增版本；
- writer / critic / replan 提示词消费同一份规格。

全部离线：monkeypatch LLMClient / get_router，零 API key。
"""
from __future__ import annotations

from research_engine.agents.planner import (
    Planner,
    build_research_spec,
)
from research_engine.agents.validator import Validator
from research_engine.state import ResearchFinding, ResearchState, SubQuestion


def _f(content: str, source: str, sq_id: str = "q1") -> ResearchFinding:
    return ResearchFinding(content=content, source=source, source_type="web",
                           sq_id=sq_id, confidence=0.6)


def _sub(id_: str, q: str = "Q") -> SubQuestion:
    return SubQuestion(id=id_, question=q, rationale="r")


def _run_validator(monkeypatch, verdicts, report, findings):
    def _chat_json(self, messages, state=None, schema=None, **kw):
        return {"citations": verdicts}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)
    v = Validator()
    return v.validate(report, findings), v


# ---------------------------------------------------------------- F12：多源印证代码复核

def test_multi_source_verified_with_independent_ids(monkeypatch):
    findings = [_f("甲", "https://a.com/1"), _f("乙", "https://b.com/2")]
    report = "论断甲成立 [来源: 1, 2]"
    verdicts = [
        {"finding_id": "1", "claim": "论断甲成立", "claim_echo": "论断甲成立",
         "faithful": True, "supported": True,
         "supporting_evidence_ids": ["1", "2"], "confidence": 0.9},
        {"finding_id": "2", "claim": "论断甲成立", "claim_echo": "论断甲成立",
         "faithful": True, "supported": True,
         "supporting_evidence_ids": ["1", "2"], "confidence": 0.9},
    ]
    cits, v = _run_validator(monkeypatch, verdicts, report, findings)
    assert all(c.supported for c in cits)
    assert all(c.independent_source_count == 2 for c in cits)
    assert all(c.supporting_evidence_ids == ["1", "2"] for c in cits)
    assert v.last_validation_stats["multi_source_verified_count"] == 2
    assert v.last_validation_stats["multi_source_downgraded_count"] == 0


def test_multi_source_downgraded_same_doc_chunks(monkeypatch):
    """同一文档的多个分块不是独立来源 ⇒ supported 降级（note 可复核）。"""
    findings = [_f("分块一", "rag:doc.md"), _f("分块二", "rag:doc.md")]
    report = "论断甲成立 [来源: 1]"
    verdicts = [{"finding_id": "1", "claim": "论断甲成立", "claim_echo": "论断甲成立",
                 "faithful": True, "supported": True,
                 "supporting_evidence_ids": ["1", "2"], "confidence": 0.9}]
    cits, v = _run_validator(monkeypatch, verdicts, report, findings)
    c = cits[0]
    assert c.supported is False
    assert c.independent_source_count == 1
    assert "代码复核" in c.note
    assert v.last_validation_stats["multi_source_downgraded_count"] == 1


def test_multi_source_downgraded_without_ids(monkeypatch):
    """声称 supported 但未给证据编号 ⇒ 降级；faithful=False 时宽松口径也落空。"""
    findings = [_f("甲", "https://a.com/1"), _f("乙", "https://b.com/2")]
    report = "论断甲成立 [来源: 1]"
    verdicts = [{"finding_id": "1", "claim": "论断甲成立", "claim_echo": "论断甲成立",
                 "faithful": False, "supported": True, "confidence": 0.9}]
    cits, _ = _run_validator(monkeypatch, verdicts, report, findings)
    c = cits[0]
    assert c.supported is False
    assert c.verified_relaxed is False
    assert "代码复核" in c.note


def test_contradicting_ids_recorded_but_not_judged(monkeypatch):
    findings = [_f("甲", "https://a.com/1"), _f("乙反例", "https://b.com/2")]
    report = "论断甲成立 [来源: 1]"
    verdicts = [{"finding_id": "1", "claim": "论断甲成立", "claim_echo": "论断甲成立",
                 "faithful": True, "supported": False,
                 "contradicting_evidence_ids": ["2", "999"], "confidence": 0.8}]
    cits, _ = _run_validator(monkeypatch, verdicts, report, findings)
    assert cits[0].contradicting_evidence_ids == ["2"]  # 越界编号被过滤
    assert cits[0].supported is False  # 只记录，不改变判定


def test_legacy_mode_trusts_bool(monkeypatch):
    """validator_fixes 关闭（基线对照）⇒ 不做代码复核，保持 v1.1 语义。"""
    import research_engine.agents.validator as vmod

    monkeypatch.setattr(vmod.config.experiment, "validator_fixes_enabled", False)
    findings = [_f("甲", "https://a.com/1")]
    report = "论断甲成立 [来源: 1]"
    verdicts = [{"finding_id": "1", "claim": "论断甲成立", "faithful": False,
                 "supported": True, "confidence": 0.9}]
    cits, _ = _run_validator(monkeypatch, verdicts, report, findings)
    assert cits[0].supported is True


# ---------------------------------------------------------------- F14：研究规格与计划身份

def test_plan_writes_spec_and_version():
    from research_engine.graph import DeepResearchGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)

    class _P:
        def plan(self, topic, user_instructions="", state=None):
            return [_sub("q1")]

        def drain_planner_events(self):
            return []

        def drain_degradations(self):
            return []

    graph.planner = _P()
    state = ResearchState(topic="t", user_instructions="用中文；排除娱乐八卦")
    delta = graph._plan(state)
    assert delta["plan_version"] == 1
    spec = delta["research_spec"]
    assert spec["user_instructions"] == "用中文；排除娱乐八卦"
    assert any("信息不足" in c for c in spec["acceptance_criteria"])


def test_replan_remaps_old_evidence_explicitly():
    from research_engine.graph import DeepResearchGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)

    class _P:
        last_replan_fallback = False
        last_replan_mapping = {"q1": "q2"}

        def replan(self, topic, subs, findings, reason, state=None):
            return [_sub("q2", "新问题"), _sub("q3", "另一问题")]

        def drain_planner_events(self):
            return []

        def drain_degradations(self):
            return []

    graph.planner = _P()
    f1 = _f("甲", "https://a.com/1", sq_id="q1")
    f2 = _f("乙", "https://b.com/2", sq_id="q9")  # 幽灵旧 ID（未映射）
    f3 = _f("丙", "https://c.com/3", sq_id="")
    state = ResearchState(topic="t", subquestions=[_sub("q1")], findings=[f1, f2, f3],
                          needs_replan=True, replan_count=0, plan_version=1)
    delta = graph._revise(state)

    by_src = {f.source: f.sq_id for f in delta["findings"]}
    assert by_src["https://a.com/1"] == "q2"   # 显式映射延续
    assert by_src["https://b.com/2"] == ""     # 未映射 → 转未归类（不错配新章节）
    assert by_src["https://c.com/3"] == ""     # 原未归类保持
    assert delta["plan_version"] == 2
    assert "重映射 1 条、转未归类 1 条" in delta["progress"][0]["msg"]
    # 节点纯函数契约：原 state 对象不得被就地修改
    assert f1.sq_id == "q1" and f2.sq_id == "q9"


def test_replan_fallback_keeps_plan_identity():
    from research_engine.graph import DeepResearchGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)

    class _P:
        last_replan_fallback = True
        last_replan_mapping = {}

        def replan(self, topic, subs, findings, reason, state=None):
            return list(subs)  # 降级：沿用旧计划

        def drain_planner_events(self):
            return []

        def drain_degradations(self):
            return []

    graph.planner = _P()
    f1 = _f("甲", "https://a.com/1", sq_id="q1")
    state = ResearchState(topic="t", subquestions=[_sub("q1")], findings=[f1],
                          needs_replan=True, replan_count=0, plan_version=1)
    delta = graph._revise(state)
    assert "findings" not in delta, "降级不得重分配旧证据"
    assert "plan_version" not in delta, "计划未变 ⇒ 版本不递增"
    assert "降级" in delta["progress"][0]["msg"]


def test_writer_prompt_includes_spec():
    from research_engine.agents.writer import Writer

    captured: dict = {}

    class _Router:
        def smart_chat(self, system, user, state=None):
            captured["user"] = user
            return "# 报告"

    class _Ctx:
        def format_for_writer(self, findings, subquestions=None):
            return "Finding 1: 内容"

    w = Writer.__new__(Writer)
    w.router = _Router()
    w.context = _Ctx()
    state = ResearchState(topic="t", user_instructions="用中文；排除娱乐",
                          research_spec=build_research_spec("用中文；排除娱乐"))
    w.write("t", [_sub("q1")], [_f("甲", "https://a.com/1")], state)
    assert "用户附加要求（必须遵守）：用中文；排除娱乐" in captured["user"]
    assert "验收条件：" in captured["user"]


def test_critic_prompt_includes_spec(monkeypatch):
    captured: dict = {}

    def _chat_json(self, messages, state=None, schema=None, **kw):
        captured["user"] = next(m["content"] for m in messages if m["role"] == "user")
        return {"sufficient": True, "needs_replan": False, "knowledge_gap": "",
                "next_queries": [], "cited_evidence_ids": []}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)

    from research_engine.critic import Critic

    state = ResearchState(topic="t", subquestions=[_sub("q1")],
                          research_spec=build_research_spec("用中文；排除娱乐"))
    Critic().decide(state)
    assert "用户附加要求（必须遵守）：用中文；排除娱乐" in captured["user"]


def test_replan_prompt_includes_spec_and_requests_mapping(monkeypatch):
    captured: dict = {}

    class _Router:
        def strategic_json(self, system, user, state=None):
            captured["system"] = system
            captured["user"] = user
            return {"subquestions": [{"id": "q1", "question": "新问题", "rationale": "r"}],
                    "id_mapping": {"q1": "q1", "q9": "q1"}}  # q9 非法键应被过滤

    monkeypatch.setattr("research_engine.agents.planner.get_router", lambda: _Router())
    p = Planner.__new__(Planner)
    p._planner_events = []
    p.last_replan_mapping = {}
    p.last_replan_fallback = False
    state = ResearchState(topic="t", user_instructions="用中文",
                          research_spec=build_research_spec("用中文"))
    new_subs = p.replan("t", [_sub("q1"), _sub("q2")], [], "跑偏", state)

    assert "用户附加要求（必须遵守）：用中文" in captured["user"]
    assert "id_mapping" in captured["system"]
    assert [s.id for s in new_subs] == ["q1"]
    assert p.last_replan_mapping == {"q1": "q1"}  # 旧 ID 键 + 新 ID 值双校验
    assert p.last_replan_fallback is False
