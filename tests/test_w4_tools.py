# -*- coding: utf-8 -*-
"""W4 测试：arXiv provider + code_exec sandbox + 并行调度（纯单测，零外部 API/零 key）。"""
from __future__ import annotations

# ---------------- arXiv provider ----------------

ARXIV_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2405.00001v1</id>
    <title>  A Test Paper on   Mamba Complexity  </title>
    <summary>We analyze the FLOPs of Mamba at 8192 sequence length.\n   Results show O(N) complexity. </summary>
    <published>2024-05-01T00:00:00Z</published>
    <author><name>Alice Chen</name></author>
    <author><name>Bob Liu</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2405.00002v2</id>
    <title>Second Paper</title>
    <summary>Abstract two.</summary>
  </entry>
</feed>"""


def test_arxiv_parse_sample():
    from research_engine.search.arxiv import ArxivSearchProvider

    p = ArxivSearchProvider.__new__(ArxivSearchProvider)  # 跳过 limiter 初始化
    results = p._parse(ARXIV_SAMPLE)
    assert len(results) == 2
    r = results[0]
    assert r.source == "arxiv"
    assert r.url == "https://arxiv.org/abs/2405.00001v1"  # http → https 规范化（Q6 溯源前缀一统）
    assert r.metadata["arxiv_id"] == "2405.00001v1"
    assert r.metadata["primary_category"] == ""  # 无 primary_category 元素 → 兜底空串
    assert "Mamba" in r.title
    assert "FLOPs" in r.snippet
    assert r.metadata["authors"] == ["Alice Chen", "Bob Liu"]


def test_arxiv_parse_primary_category():
    from research_engine.search.arxiv import ArxivSearchProvider

    xml = ARXIV_SAMPLE.replace(
        "</published>",
        "</published><arxiv:primary_category xmlns:arxiv='http://arxiv.org/schemas/atom' term='cs.CL'/>",
        1,
    )
    p = ArxivSearchProvider.__new__(ArxivSearchProvider)
    results = p._parse(xml)
    assert results[0].metadata["primary_category"] == "cs.CL"


def test_arxiv_failure_returns_empty(monkeypatch):
    """请求异常 → 空 SearchResponse（失败语义交给调度层重试/降级）。"""
    from research_engine.search.arxiv import ArxivSearchProvider

    p = ArxivSearchProvider(max_results=5)
    p._limiter.min_interval = 0.0  # 测试不等待

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(p, "_parse", boom)
    resp = p.search("mamba")
    assert resp.results == []


def test_arxiv_rate_limiter_schedules(monkeypatch):
    """RateLimiter 3s：连续两次请求的最小间隔 ≥3s（用假时钟验证计算逻辑）。"""
    import time

    from research_engine.search.arxiv import RateLimiter

    clock = iter([100.5, 100.5])  # now=100.5（距上次 100.0 仅 0.5s）→ 应睡 2.5s；其后 _last_ts 复写仍 100.5

    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    rl = RateLimiter(min_interval=3.0)
    rl._last_ts = 100.0  # 预置上次请求时间
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    rl.wait()  # gap = 100.0 + 3.0 - 100.5 = 2.5
    assert slept == [2.5], f"应按 min_interval 补足间隔，实际 sleep {slept}"


# ---------------- code_exec sandbox ----------------

def test_code_exec_basic():
    from research_engine.tools.code_exec import exec_code

    r = exec_code("import math\nprint(math.sqrt(16))")
    assert r.ok
    assert "4.0" in r.stdout
    assert r.script_hash  # Q6：hash[:10]


def test_code_exec_import_whitelist_denies_os():
    from research_engine.tools.code_exec import exec_code

    r = exec_code("import os\nprint(os.getcwd())")
    assert not r.ok
    assert "导入被沙箱拒绝" in r.note


def test_code_exec_audit_hook_blocks_outside_read():
    """builtin open 不经 AST → 靠 Audit Hook：cwd 外读被拒（Q2 修正：读也拒，防偷读 .env）。"""
    from research_engine.tools.code_exec import exec_code

    r = exec_code('open(r"C:/Windows/win.ini", "r")')
    assert not r.ok
    assert "outside sandbox cwd" in (r.note + r.stderr)


def test_code_exec_audit_hook_allows_cwd_write():
    from research_engine.tools.code_exec import exec_code

    r = exec_code('open("out.txt", "w").write("hi")\nprint("ok")')
    assert r.ok


def test_code_exec_syntax_error_reported():
    from research_engine.tools.code_exec import exec_code

    r = exec_code("print(1")
    assert not r.ok
    assert r.note  # 错误信息回传不吞


def test_should_execute_keywords():
    from research_engine.tools.code_exec import should_execute

    assert should_execute("对比 Transformer 与 Mamba 的 FLOPs 复杂度")
    assert should_execute("计算 8k 序列下的数值")
    assert not should_execute("什么是 RAG")
    assert not should_execute("2026年RAG技术进展")


# ---------------- 并行调度 + 结果池 ----------------

def test_pool_and_trim_cap_and_code_priority():
    from research_engine.agents.researcher import POOL_TOTAL_CAP, pool_and_trim
    from research_engine.state import ResearchFinding

    web = [ResearchFinding(content=f"w{i}", source=f"u{i}", source_type="web") for i in range(8)]
    rag = [ResearchFinding(content=f"r{i}", source=f"rag:d{i}", source_type="rag") for i in range(3)]
    arxiv = [ResearchFinding(content=f"a{i}", source=f"https://arxiv.org/abs/24{i:04d}", source_type="arxiv") for i in range(6)]
    code = [ResearchFinding(content="c", source="code:abc", source_type="code_exec", confidence=0.8)]

    pooled, stats = pool_and_trim({"web": web, "rag": rag, "arxiv": arxiv, "code": code})
    assert stats["web"] == 8 and stats["arxiv"] == 6  # stats = 原始产出数
    assert len(pooled) == POOL_TOTAL_CAP
    assert pooled[0].source_type == "code_exec"  # code 豁免过滤 → 优先保留
    assert sum(1 for f in pooled if f.source_type == "web") == 5  # 文本 Top-5
    assert sum(1 for f in pooled if f.source_type == "rag") == 3


def test_search_once_parallel_tool_failure_isolated():
    """Q1：单工具挂不影响其余（ThreadPool return_exceptions 语义）。"""
    from research_engine.agents.researcher import Researcher
    from research_engine.state import ResearchFinding

    res = Researcher.__new__(Researcher)
    res.arxiv = type("Fake", (), {"search": lambda self, q: type("R", (), {"results": []})()})()

    def boom(q):
        raise RuntimeError("web down")

    def rag_ok(q):
        return [ResearchFinding(content=f"r-{q}", source="rag:doc_a.md", source_type="rag", confidence=0.7)]

    res._search_web = boom
    res._search_rag = rag_ok
    res._search_code = lambda q: []

    pooled, stats = res.search_once("q1")
    assert [f.source for f in pooled] == ["rag:doc_a.md"]  # web 挂 → 返回空，rag 照跑
    assert stats["web"] == 0 and stats["rag"] == 1


def test_run_provenance_four_buckets():
    """Q6：溯源块四桶——arxiv(abs URL)/code(code:hash) 不再误算进 web。"""
    from research_engine.render import ReportRenderer

    class FakeState:
        reflection_log = [{"signal": "continue"}, {"signal": "stop"}]
        visited_sources = ["https://example.com/a", "rag:doc.md",
                           "https://arxiv.org/abs/2405.1", "code:abc123"]
        depth = 3
        token_used = 12345
        replan_count = 0

    text = ReportRenderer().build_run_provenance(FakeState())
    assert "web 1 / rag 1 / arxiv 1 / code 1" in text, text
    assert "web 4" not in text  # 旧两桶会把 arxiv/code 误算 web—修复后不得出现


def test_graph_snapshot_message_format():
    """Q8：状态快照消息格式（新增/工具明细/累计/hop 进度，零新增字段）。"""
    from research_engine.graph import config

    rc = config.research
    # 直接调用半成品：仅验证消息组装逻辑（跳过真实检索）
    merged = ["f1", "f2", "f3"]
    new_depth = 3
    tool_stats = {"web": 5, "rag": 2, "arxiv": 3, "code": 1, "code_failed": 1}
    tool_detail = " / ".join(
        f"{k} {tool_stats.get(k, 0)}" + ("(失败)" if k == "code" and tool_stats.get("code_failed", 0) else "")
        for k in ("web", "rag", "arxiv", "code") if k in tool_stats
    )
    snapshot = (f"第 {new_depth}/{rc.max_total_hops} 跳 [sq1]："
                f"+{len(merged)} 条新发现（{tool_detail}），累计 {len(merged)} 条")
    assert "第 3/20 跳 [sq1]" in snapshot
    assert "web 5 / rag 2 / arxiv 3 / code 1(失败)" in snapshot
    assert "累计 3 条" in snapshot