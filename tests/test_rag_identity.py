"""审计 P1#1：人物身份核验（查询实体抽取 + 证据过滤 + Researcher 接线）。"""
from __future__ import annotations

from types import SimpleNamespace

from research_engine.rag.identity import extract_entities, hit_matches_entities


def test_extract_entities_person_queries():
    assert extract_entities("田金应是谁") == ["田金应"]
    assert "田金应" in extract_entities("田金应的个人背景和职业经历是什么？")
    assert "田金应" in extract_entities("田金应现在多大")
    assert "张三" in extract_entities("「张三」的简历")


def test_extract_entities_ignores_generic_queries():
    assert extract_entities("现在多少岁") == []
    assert extract_entities("我是谁") == []
    assert extract_entities("这个项目的技术栈") == []
    assert extract_entities("LLM 推理延迟如何优化") == []


def test_hit_identity_filter_blocks_other_people():
    assert hit_matches_entities("田金应 的个人简历：邮箱 a@b.c", ["田金应"]) is True
    assert hit_matches_entities("田金洲是北京中医药大学副院长", ["田金应"]) is False
    assert hit_matches_entities("任何文本", []) is True


def test_researcher_drops_foreign_entity_hits():
    """集成：问「田金应是谁」时，其他人物的命中不得进入 findings（无答案路径生效）。"""
    from research_engine.agents.researcher import Researcher
    from research_engine.rag.scope import RagScope
    from research_engine.state import DegradationSink

    class _Retriever:
        def retrieve(self, query, top_k=5, scope=None):
            return SimpleNamespace(items=[
                {"text": "田金应 的个人简历：邮箱 tian@example.com", "doc": "resume.pdf"},
                {"text": "田金洲是北京中医药大学东直门医院副院长", "doc": "other.pdf"},
            ], faults=lambda: [])

    researcher = Researcher.__new__(Researcher)
    researcher.degradations = DegradationSink()
    researcher._rag_scope = RagScope()
    researcher.retriever = _Retriever()

    findings = researcher._search_rag("田金应是谁")

    assert len(findings) == 1
    assert "田金应" in findings[0].content
    assert findings[0].source == "rag:resume.pdf"


def test_researcher_preserves_evidence_identity():
    """审计 P2#6：证据身份保留 —— 标题路径进正文、元数据进 metadata、置信度分档。"""
    from research_engine.agents.researcher import Researcher
    from research_engine.rag.scope import RagScope
    from research_engine.state import DegradationSink

    class _Retriever:
        def retrieve(self, query, top_k=5, scope=None):
            return SimpleNamespace(items=[
                {"text": "田金应 的简历正文", "doc": "resume.pdf", "doc_id": "u1:aaa",
                 "chunk_id": "u1:aaa:g1:0", "title_path": ["个人信息"], "locator": {"page": 1}},
                {"text": "田金应 的工作经历", "doc": "resume.pdf", "doc_id": "u1:aaa",
                 "chunk_id": "u1:aaa:g1:1", "title_path": [], "locator": {"page": 2}},
            ], faults=lambda: [])

    researcher = Researcher.__new__(Researcher)
    researcher.degradations = DegradationSink()
    researcher._rag_scope = RagScope()
    researcher.retriever = _Retriever()

    findings = researcher._search_rag("田金应是谁")

    assert len(findings) == 2
    first, second = findings
    assert first.content.startswith("个人信息")
    assert first.metadata["doc_id"] == "u1:aaa"
    assert first.metadata["chunk_id"] == "u1:aaa:g1:0"
    assert first.metadata["locator"] == {"page": 1}
    assert first.confidence == 0.85
    assert second.content == "田金应 的工作经历"  # 无标题路径不前缀
    assert second.confidence == 0.75


def test_researcher_keeps_all_hits_for_generic_query():
    from research_engine.agents.researcher import Researcher
    from research_engine.rag.scope import RagScope
    from research_engine.state import DegradationSink

    class _Retriever:
        def retrieve(self, query, top_k=5, scope=None):
            return SimpleNamespace(items=[
                {"text": "混合检索的技术实现", "doc": "a.pdf"},
                {"text": "重排模型对比", "doc": "b.pdf"},
            ], faults=lambda: [])

    researcher = Researcher.__new__(Researcher)
    researcher.degradations = DegradationSink()
    researcher._rag_scope = RagScope()
    researcher.retriever = _Retriever()

    assert len(researcher._search_rag("混合检索怎么实现")) == 2
