"""W8 Arm 4：工具失败原因结构化（决策 **D-02** `RetrieveResponse` / **D-03** 零命中不算故障）。

零 LLM、零网络、零 Qdrant —— 全部用假对象 + monkey patch，**可进 CI**。
覆盖拍板要求的五个场景：未配置 / 空结果 / provider 异常 / 部分 backend 失败 / 正常命中。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from research_engine.failure_reasons import (
    NON_FAULT_REASONS,
    TOOL_REASONS,
    FailureReason,
    is_fault_reason,
)

# ---------------------------------------------------------------- 测试替身


class _FakeStore:
    """VectorStore 替身：可模拟「可用 / 不可用 / 查询抛异常」。"""

    def __init__(self, hits=None, payloads=None, unavailable=None, err=None):
        self._hits = hits or []
        self._payloads = payloads or []
        self._unavailable = unavailable
        self._err = err

    def search(self, vector, top_k=5, scope=None):
        if self._unavailable:
            return []  # 复刻真实行为：不可用时静默返回空
        if self._err:
            raise RuntimeError(self._err)
        return self._hits

    def scroll_all(self, limit=10000, scope=None):
        return self._payloads

    @property
    def unavailable_reason(self):
        return self._unavailable

    @property
    def last_error(self):
        return self._err


def _make_retriever(monkeypatch, store, texts=(), sources=(), embed_ok=True, api_key="k"):
    """构造一个不碰真实 Qdrant / embedding 的 HybridRetriever。"""
    import research_engine.rag.retriever as R
    from research_engine.rag.retriever import HybridRetriever

    r = HybridRetriever.__new__(HybridRetriever)
    r.store = store
    r._client = None
    from rank_bm25 import BM25Okapi

    from research_engine.rag.tokenizer import tokenize

    bm25 = BM25Okapi([tokenize(t) for t in texts]) if texts else None
    # P5：BM25 语料改为「按检索作用域缓存」；匿名作用域键 = (None, None)
    r._cache = {(None, None): (list(texts), list(sources), bm25)}
    # 与真实环境解耦：不依赖 .env 里有没有 DASHSCOPE_API_KEY
    monkeypatch.setattr(R, "config", SimpleNamespace(
        llm=SimpleNamespace(api_key=api_key),
        rag=SimpleNamespace(top_k=5),
    ))
    if embed_ok:
        r.embed_query = lambda q: [0.1, 0.2]
    else:
        def _boom(q):
            raise RuntimeError("embedding 500")

        r.embed_query = _boom
    return r


def _fake_post(*, status=200, payload=None, raise_exc=None):
    """给 bocha 用的 requests.post 替身。"""

    def _post(url, headers=None, json=None, timeout=None):
        if raise_exc is not None:
            raise raise_exc
        resp = requests.Response()
        resp.status_code = status
        if status >= 400:
            resp._content = b"err"
            resp.url = url

            def _raise_for_status():
                raise requests.exceptions.HTTPError(f"HTTP {status}", response=resp)

        else:
            def _raise_for_status():
                return None
        resp.raise_for_status = _raise_for_status
        if payload is None:
            resp.json = lambda: (_ for _ in ()).throw(ValueError("Expecting value: line 1 column 1"))
        else:
            resp.json = lambda: payload
        return resp

    return _post


# ---------------------------------------------------------------- D-03：判定规则一处定义


def test_empty_result_is_not_a_fault():
    """决策 D-03：零命中是**结果**不是故障 ⇒ 不上抛 run 级降级。

    算术理由：``resolve_run_status()`` 是「degradation_log 非空 + 有报告 ⇒ degraded」，
    若零命中也进日志，几乎每轮 run 都会是 degraded，run_status 就不再是健康度信号。
    """
    assert FailureReason.EMPTY_RESULT.value in NON_FAULT_REASONS
    assert is_fault_reason(FailureReason.EMPTY_RESULT.value) is False
    assert is_fault_reason(None) is False
    for r in (FailureReason.NOT_CONFIGURED, FailureReason.TIMEOUT,
              FailureReason.PROVIDER_ERROR, FailureReason.PARSE_ERROR):
        assert is_fault_reason(r.value) is True, f"{r} 是故障，必须上抛"


# ---------------------------------------------------------------- 场景 1：正常命中


def test_retrieve_normal_hit(monkeypatch):
    """正常命中：无 failure_reason、无 backend 失败、无降级条目。"""
    store = _FakeStore(
        hits=[{"id": 1, "score": 0.9, "payload": {"text": "chunk A", "source": "doc_a.md"}}],
        payloads=[{"text": "chunk A", "source": "doc_a.md"}],
    )
    r = _make_retriever(monkeypatch, store, texts=["chunk A"], sources=["doc_a.md"])
    resp = r.retrieve("q")

    assert resp.items, "正常场景必须有命中项"
    assert resp.failure_reason is None
    assert resp.ok is True
    assert resp.backend_failures == []
    assert resp.faults() == [], "正常命中不写 degradation_log"


# ---------------------------------------------------------------- 场景 2：零命中（是结果不是故障）


def test_retrieve_zero_hit_is_empty_result(monkeypatch):
    """两路都正常执行、确实没命中 ⇒ `empty_result`，**不进**降级日志（D-03）。"""
    store = _FakeStore(hits=[], payloads=[])  # 可用，只是没数据
    r = _make_retriever(monkeypatch, store)
    resp = r.retrieve("q")

    assert resp.items == []
    assert resp.failure_reason == FailureReason.EMPTY_RESULT.value
    assert resp.has_fault is False
    assert resp.faults() == [], "零命中不得写 degradation_log"


# ---------------------------------------------------------------- 场景 3：RAG 未配置


def test_retrieve_not_configured(monkeypatch):
    """没配 embedding key + 向量库不可用 ⇒ `not_configured`（处置动作是换配置）。"""
    store = _FakeStore(unavailable=FailureReason.PROVIDER_ERROR.value, err="connection refused")
    r = _make_retriever(monkeypatch, store, api_key="")  # 无 DASHSCOPE_API_KEY
    resp = r.retrieve("q")

    assert resp.items == []
    assert resp.failure_reason == FailureReason.NOT_CONFIGURED.value, \
        "聚合时 not_configured 优先级最高（最具处置价值）"
    assert resp.has_fault is True
    faults = resp.faults()
    assert len(faults) == 1, "整体失败只记一条，不重复记账"
    assert faults[0].backend == "all"
    # 失败事实不丢：两路 backend 的原因都在 backend_failures 里留着
    assert {bf.backend for bf in resp.backend_failures} == {"vector", "bm25"}


# ---------------------------------------------------------------- 场景 4：provider 异常


def test_retrieve_provider_error(monkeypatch):
    """embedding 调用失败 + 向量库不可用 ⇒ provider_error，且原始摘要要留着排障。"""
    store = _FakeStore(unavailable=FailureReason.PROVIDER_ERROR.value, err="connection refused")
    r = _make_retriever(monkeypatch, store, embed_ok=False)
    resp = r.retrieve("q")

    assert resp.items == []
    assert resp.failure_reason == FailureReason.PROVIDER_ERROR.value
    assert "embedding 500" in resp.failure_detail or "connection refused" in resp.failure_detail, \
        "异常摘要必须保留，否则无从排障"


# ---------------------------------------------------------------- 场景 5：部分 backend 失败但有结果


def test_retrieve_partial_backend_failure_keeps_failures(monkeypatch):
    """向量路挂了、BM25 有结果 ⇒ **不得**因为「有结果」就把失败事实吞掉。"""
    # ⚠️ BM25 语料必须 ≥3 篇且查询词只命中其中一篇：rank_bm25 的 IDF 在
    # 「df == N」时为负 ⇒ 单篇语料会打不出正分（不是代码问题，是算法性质）。
    store = _FakeStore(payloads=[{"text": "chunk A", "source": "doc_a.md"},
                                 {"text": "other B", "source": "doc_b.md"},
                                 {"text": "third C", "source": "doc_c.md"}])
    r = _make_retriever(monkeypatch, store,
                        texts=["chunk A", "other B", "third C"],
                        sources=["doc_a.md", "doc_b.md", "doc_c.md"], embed_ok=False)
    resp = r.retrieve("chunk")

    assert resp.items, "BM25 那一路仍应有结果"
    assert resp.failure_reason is None, "整体没失败"
    assert [bf.backend for bf in resp.backend_failures] == ["vector"]
    faults = resp.faults()
    assert len(faults) == 1 and faults[0].backend == "vector", \
        "部分失败必须逐 backend 上抛，否则失败事实被吞"


def test_researcher_derives_partial_failure_with_component(monkeypatch):
    """researcher 单向派生：部分失败 ⇒ component 带 backend 名（`rag_search:vector`）。"""
    from research_engine.agents.researcher import Researcher
    from research_engine.state import DegradationSink

    store = _FakeStore(payloads=[{"text": "chunk A", "source": "doc_a.md"},
                                 {"text": "other B", "source": "doc_b.md"},
                                 {"text": "third C", "source": "doc_c.md"}])
    r = Researcher.__new__(Researcher)
    r.degradations = DegradationSink()
    r.retriever = _make_retriever(monkeypatch, store,
                                  texts=["chunk A", "other B", "third C"],
                                  sources=["doc_a.md", "doc_b.md", "doc_c.md"], embed_ok=False)

    findings = r._search_rag("chunk")
    assert findings, "BM25 有结果就应该产出 finding"
    got = r.drain_degradations()
    assert len(got) == 1
    assert got[0].component == "rag_search:vector", f"component 应带 backend 名，实际 {got[0].component}"
    assert got[0].reason == FailureReason.PROVIDER_ERROR.value


def test_researcher_zero_hit_writes_no_degradation(monkeypatch):
    """D-03 端到端：零命中不写降级条目 ⇒ run_status 仍为 success。"""
    from research_engine.agents.researcher import Researcher
    from research_engine.state import DegradationSink

    store = _FakeStore(hits=[], payloads=[])
    r = Researcher.__new__(Researcher)
    r.degradations = DegradationSink()
    r.retriever = _make_retriever(monkeypatch, store)

    assert r._search_rag("q") == []
    assert r.drain_degradations() == [], "零命中不得写 degradation_log（否则 run_status 恒为 degraded）"


# ---------------------------------------------------------------- bocha：五类失败不再靠抛异常


def test_bocha_not_configured_does_not_raise(monkeypatch):
    """未配置 API key：改造前是 `raise RuntimeError`（消费方只能猜），现在结构化返回。"""
    from research_engine.search.bocha import BochaSearchProvider

    p = BochaSearchProvider.__new__(BochaSearchProvider)
    p.api_key = ""
    resp = p.search("q")
    assert resp.failure_reason == FailureReason.NOT_CONFIGURED.value
    assert resp.failure_reason in TOOL_REASONS
    assert resp.results == []


@pytest.mark.parametrize("exc,expected", [
    (requests.exceptions.Timeout("t/o"), FailureReason.TIMEOUT.value),
    (requests.exceptions.ConnectionError("refused"), FailureReason.PROVIDER_ERROR.value),
])
def test_bocha_transport_failures(monkeypatch, exc, expected):
    """传输层按异常类型精确归类，不猜异常文本。"""
    import research_engine.search.bocha as B
    from research_engine.search.bocha import BochaSearchProvider

    p = BochaSearchProvider.__new__(BochaSearchProvider)
    p.api_key = "k"
    monkeypatch.setattr(B.requests, "post", _fake_post(raise_exc=exc))
    resp = p.search("q")
    assert resp.failure_reason == expected


@pytest.mark.parametrize("status,expected", [
    (401, FailureReason.NOT_CONFIGURED.value),
    (403, FailureReason.NOT_CONFIGURED.value),
    (500, FailureReason.PROVIDER_ERROR.value),
    (429, FailureReason.PROVIDER_ERROR.value),
])
def test_bocha_http_status(monkeypatch, status, expected):
    """401/403 ⇒ 换配置（not_configured）；5xx/429 ⇒ provider_error。"""
    import research_engine.search.bocha as B
    from research_engine.search.bocha import BochaSearchProvider

    p = BochaSearchProvider.__new__(BochaSearchProvider)
    p.api_key = "k"
    monkeypatch.setattr(B.requests, "post", _fake_post(status=status))
    resp = p.search("q")
    assert resp.failure_reason == expected


def test_bocha_parse_error(monkeypatch):
    """拿到 200 但解不出 JSON（网关拦截页常见）⇒ parse_error。"""
    import research_engine.search.bocha as B
    from research_engine.search.bocha import BochaSearchProvider

    p = BochaSearchProvider.__new__(BochaSearchProvider)
    p.api_key = "k"
    monkeypatch.setattr(B.requests, "post", _fake_post(status=200, payload=None))
    resp = p.search("q")
    assert resp.failure_reason == FailureReason.PARSE_ERROR.value


def test_bocha_empty_result(monkeypatch):
    """正常响应但零结果 ⇒ empty_result（结果，不是故障）。"""
    import research_engine.search.bocha as B
    from research_engine.search.bocha import BochaSearchProvider

    p = BochaSearchProvider.__new__(BochaSearchProvider)
    p.api_key = "k"
    monkeypatch.setattr(B.requests, "post", _fake_post(status=200, payload={"data": {"webPages": {"value": []}}}))
    resp = p.search("q")
    assert resp.failure_reason == FailureReason.EMPTY_RESULT.value
    assert is_fault_reason(resp.failure_reason) is False


# ---------------------------------------------------------------- arxiv：解析失败与零命中分离


def test_arxiv_parse_error(monkeypatch):
    """200 但不是合法 Atom ⇒ parse_error。"""
    import research_engine.search.arxiv as A
    from research_engine.search.arxiv import ArxivSearchProvider

    p = ArxivSearchProvider()

    class _Resp:
        status_code = 200
        text = "<feed><entry>oops"  # 未闭合 ⇒ 真畸形，ET 必抛 ParseError

        def raise_for_status(self):
            return None

    monkeypatch.setattr(A.requests, "get", lambda *a, **k: _Resp())
    resp = p.search("q")
    assert resp.failure_reason == FailureReason.PARSE_ERROR.value


def test_arxiv_empty_result(monkeypatch):
    """合法 Atom 但零条目 ⇒ empty_result（与解析失败分开）。"""
    import research_engine.search.arxiv as A
    from research_engine.search.arxiv import ArxivSearchProvider

    p = ArxivSearchProvider()

    class _Resp:
        status_code = 200
        text = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def raise_for_status(self):
            return None

    monkeypatch.setattr(A.requests, "get", lambda *a, **k: _Resp())
    resp = p.search("q")
    assert resp.failure_reason == FailureReason.EMPTY_RESULT.value
    assert is_fault_reason(resp.failure_reason) is False


# ---------------------------------------------------------------- 单向派生契约


def test_tool_reasons_have_no_unexpected_producers():
    """Arm 4 后：临时替身 `classify_tool_exception` 必须已删除（工具层是唯一产生点）。"""
    import research_engine.failure_reasons as FR

    assert not hasattr(FR, "classify_tool_exception"), \
        "Arm 4 已落地：工具类 5 值必须由 provider 真填充，不得再靠异常文本猜"
