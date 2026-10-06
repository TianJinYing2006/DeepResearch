"""审计 P1#3：压缩归因 —— 按 (来源, 子问题) 分组，不跨子问题合并材料。"""
from __future__ import annotations

import research_engine.context.manager as manager_module
from research_engine.context.manager import ContextManager
from research_engine.state import ResearchFinding


def test_compress_keeps_sq_id_attribution(monkeypatch):
    """同一文件下 q1/q2 的材料必须分别压缩并各自保留 sq_id（此前会合并归入首个）。"""
    calls: list[str] = []

    class _Router:
        def fast_chat(self, system, user, state=None):
            calls.append(user)
            return f"[摘要] {user[:24]}"

    monkeypatch.setattr(manager_module, "get_router", lambda: _Router())

    findings = [
        ResearchFinding(content=f"q1 材料 {i}", source="rag:resume.pdf",
                        source_type="rag", confidence=0.7, sq_id="q1")
        for i in range(16)
    ] + [
        ResearchFinding(content=f"q2 材料 {i}", source="rag:resume.pdf",
                        source_type="rag", confidence=0.7, sq_id="q2")
        for i in range(15)
    ]

    compressed = ContextManager(max_findings=30).compress(findings, "测试主题")

    assert len(compressed) == 2, "两个子问题应各自成组压缩（不得合并为一条）"
    assert {f.sq_id for f in compressed} == {"q1", "q2"}
    assert all(f.source == "rag:resume.pdf" for f in compressed)
    assert len(calls) == 2, "每组一次压缩调用"


def test_compress_same_source_same_sq_still_merges(monkeypatch):
    """同来源且同子问题：仍合并为一条摘要（保持原有压缩收益）。"""
    class _Router:
        def fast_chat(self, system, user, state=None):
            return "[摘要]"

    monkeypatch.setattr(manager_module, "get_router", lambda: _Router())
    findings = [
        ResearchFinding(content=f"材料 {i}", source="rag:doc.md",
                        source_type="rag", confidence=0.6, sq_id="q1")
        for i in range(31)
    ]

    compressed = ContextManager(max_findings=30).compress(findings, "主题")

    assert len(compressed) == 1 and compressed[0].sq_id == "q1"
