"""需求 23 检索 v2 测试：RRF 身份融合 / 缓存随修订号失效 / 证据组装 / rerank fail-open。

全部零外部服务：FakeStore 直接返回 payload 形态语料，embedding 用桩函数。
"""
from __future__ import annotations

import research_engine.rag.retriever as R
from config import config
from research_engine.rag.retriever import HybridRetriever, _assemble, _rrf_merge
from research_engine.rag.scope import RagScope, use_rag_scope


class _FakeStore:
    def __init__(self, *, vector_hits=None, payloads=None):
        self.vector_hits = vector_hits or []
        self.payloads = payloads or []
        self.scroll_calls = 0
        self.unavailable_reason = None
        self.last_error = None

    def search(self, vector, top_k=5, scope=None):
        return self.vector_hits[:top_k]

    def scroll_all(self, limit=10000, *, scope=None):
        self.scroll_calls += 1
        return list(self.payloads)


def _payload(chunk_id, text, doc, *, user_id=None):
    payload = {"text": text, "source": doc, "doc_id": doc, "chunk_id": chunk_id,
               "generation": 1, "locator": {"page": 1}}
    if user_id:
        payload["user_id"] = user_id
    return payload


def _hit(payload, score):
    return {"score": score, "payload": payload}


def _retriever(store) -> HybridRetriever:
    retriever = HybridRetriever()
    retriever.store = store
    retriever.embed_query = lambda q: [0.1, 0.2]
    return retriever


def test_rrf_merges_by_identity_and_both_route_hits_rank_first(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "k")
    # 语料设计：widget 仅出现在 1/3 文档（正 IDF，BM25 才会给出正分）；
    # gadget 仅出现在 c4（BM25 独有命中，验证融合取并集）。
    store = _FakeStore(
        vector_hits=[
            _hit(_payload("c1", "widget alpha beta", "a.md"), 0.9),
            _hit(_payload("c2", "unrelated content", "b.md"), 0.8),
        ],
        payloads=[
            _payload("c1", "widget alpha beta", "a.md"),
            _payload("c2", "unrelated content", "b.md"),
            _payload("c4", "gadget", "d.md"),
        ],
    )
    resp = _retriever(store).retrieve("widget gadget", top_k=3)
    items = resp.items
    assert [item["chunk_id"] for item in items] == ["c1", "c4", "c2"]
    assert items[0]["source"] == "rrf"  # 两路命中者 RRF 分最高
    assert items[0]["ranks"] == {"vector": 1, "bm25": 2}
    assert items[1]["source"] == "bm25"  # BM25 独有命中进入并集
    assert items[2]["source"] == "vector"


def test_same_text_different_docs_are_not_deduped(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "k")
    same = "这是一段完全相同但来自两个不同文档的文本内容用于验证同文多出处不会被文本去重吞掉"
    store = _FakeStore(vector_hits=[
        _hit(_payload("c1", same, "a.md"), 0.9),
        _hit(_payload("c2", same, "b.md"), 0.8),
    ])
    resp = _retriever(store).retrieve("content", top_k=2)
    assert {item["chunk_id"] for item in resp.items} == {"c1", "c2"}


def test_same_doc_overlap_is_deduped(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "k")
    long_text = "起始段落。" + "详细内容说明" * 30
    short_text = long_text[: int(len(long_text) * 0.7)]
    store = _FakeStore(vector_hits=[
        _hit(_payload("c1", long_text, "a.md"), 0.9),
        _hit(_payload("c2", short_text, "a.md"), 0.8),
    ])
    resp = _retriever(store).retrieve("content", top_k=2)
    assert [item["chunk_id"] for item in resp.items] == ["c1"]


def test_max_per_doc_cap(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "k")
    monkeypatch.setattr(config.rag, "max_per_doc", 2)
    store = _FakeStore(vector_hits=[
        _hit(_payload("c1", "第一段完全不同的内容甲" * 3, "a.md"), 0.9),
        _hit(_payload("c2", "第二段完全不同的内容乙" * 3, "a.md"), 0.8),
        _hit(_payload("c3", "第三段完全不同的内容丙" * 3, "a.md"), 0.7),
    ])
    resp = _retriever(store).retrieve("content", top_k=3)
    assert len(resp.items) == 2


def test_bm25_cache_invalidates_on_revision_bump(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "k")
    store = _FakeStore(payloads=[_payload("c1", "widget alpha", "a.md")])
    retriever = _retriever(store)
    with use_rag_scope(user_id="u1", revision=1):
        retriever.retrieve("widget", top_k=1)
        retriever.retrieve("widget", top_k=1)
    assert store.scroll_calls == 1  # 同修订号缓存命中
    with use_rag_scope(user_id="u1", revision=2):
        retriever.retrieve("widget", top_k=1)
    assert store.scroll_calls == 2  # 写路径递增 revision ⇒ 缓存失效重载


def test_rerank_success_reorders_and_fail_open_keeps_rrf(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "k")
    monkeypatch.setattr(config.rag, "use_rerank", True)
    store = _FakeStore(vector_hits=[
        _hit(_payload("c1", "widget 第一候选", "a.md"), 0.9),
        _hit(_payload("c2", "widget 第二候选", "b.md"), 0.8),
    ])
    monkeypatch.setattr(R, "_rerank_call", lambda query, docs: [(1, 0.99), (0, 0.01)])
    resp = _retriever(store).retrieve("widget", top_k=2)
    assert [item["chunk_id"] for item in resp.items] == ["c2", "c1"]
    assert resp.items[0]["rerank_score"] == 0.99

    monkeypatch.setattr(R, "_rerank_call", lambda query, docs: None)  # 失败 ⇒ fail-open
    resp = _retriever(store).retrieve("widget", top_k=2)
    assert [item["chunk_id"] for item in resp.items] == ["c1", "c2"]
    assert resp.failure_reason is None and not resp.backend_failures


def test_legacy_payload_identity_is_stable_across_routes(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "k")
    legacy = {"text": "widget 无 chunk_id 的历史点", "source": "old.md"}  # 无 chunk_id/doc_id
    # 两个填充文档保证 widget 为稀有词（正 IDF，BM25 给出正分）
    filler = [{"text": "无关内容 alpha", "source": "f1.md"},
              {"text": "无关内容 beta", "source": "f2.md"}]
    store = _FakeStore(vector_hits=[_hit(legacy, 0.9)],
                       payloads=[dict(legacy)] + filler)
    resp = _retriever(store).retrieve("widget", top_k=1)
    assert len(resp.items) == 1
    assert resp.items[0]["chunk_id"].startswith("legacy:old.md:")
    assert resp.items[0]["source"] == "rrf"  # 两路按同一身份合并


def test_assemble_respects_token_budget_after_first_item():
    def entry(chunk_id, text, doc="a.md"):
        return {"chunk_id": chunk_id, "text": text, "doc": doc, "doc_id": doc,
                "source": "rrf", "score": 1.0}

    small = "字" * 10          # ≈10 token
    big = "字" * 200           # ≈200 token
    picked = _assemble(
        [entry("c1", small), entry("c2", big, "b.md"), entry("c3", small, "c.md")],
        top_k=3, max_per_doc=2, max_tokens=50,
    )
    assert [item["chunk_id"] for item in picked] == ["c1", "c3"]


def test_rrf_merge_is_deterministic_and_identity_based():
    vector = [{"chunk_id": "v1", "text": "甲", "doc": "a"}, {"chunk_id": "v2", "text": "乙", "doc": "b"}]
    bm25 = [{"chunk_id": "b1", "text": "丙", "doc": "c"}, {"chunk_id": "v1", "text": "甲", "doc": "a"}]
    merged = _rrf_merge(vector, bm25, k=60)
    assert merged[0]["chunk_id"] == "v1"
    assert merged[0]["ranks"] == {"vector": 1, "bm25": 2}
    assert [item["chunk_id"] for item in merged] == ["v1", "b1", "v2"]


def test_assemble_per_doc_cap_uses_doc_id():
    """审计 P2#6：同名文件不共享「同文档上限」（按 doc_id 计）。"""
    same_name = [
        {"chunk_id": "a1", "doc": "同名.md", "doc_id": "u1:aaa", "text": "甲" * 60},
        {"chunk_id": "b1", "doc": "同名.md", "doc_id": "u1:bbb", "text": "乙" * 60},
    ]
    picked = _assemble(same_name, top_k=5, max_per_doc=1, max_tokens=10000)
    assert [entry["chunk_id"] for entry in picked] == ["a1", "b1"]

    same_doc = [
        {"chunk_id": "a1", "doc": "同名.md", "doc_id": "u1:aaa", "text": "甲" * 60},
        {"chunk_id": "a2", "doc": "同名.md", "doc_id": "u1:aaa", "text": "丙" * 60},
    ]
    assert len(_assemble(same_doc, top_k=5, max_per_doc=1, max_tokens=10000)) == 1


def test_bm25_cache_tracks_store_revision_without_scope_revision():
    """回归：runner/worker 的 set_scope 不带 revision 时，缓存键必须跟随库内修订号。

    否则长驻进程缓存「摄取前的空语料」永不失效 → 运行期检索恒零命中（staging 实测）。"""

    class _Store:
        def __init__(self):
            self.revision = 1
            self.payloads = [{"text": "a", "user_id": "u1"}]

        def get_rag_revision(self):
            return self.revision

        def scroll_all(self, scope=None):
            return self.payloads

    retriever = HybridRetriever()
    retriever.store = _Store()
    scope = RagScope(user_id="u1")

    metas, _ = retriever._load_all(scope)
    assert len(metas) == 1
    retriever.store.payloads.append({"text": "b", "user_id": "u1"})
    metas, _ = retriever._load_all(scope)  # 修订号未变 → 仍命中缓存
    assert len(metas) == 1
    retriever.store.revision = 2  # 写路径递增 → 缓存失效
    metas, _ = retriever._load_all(scope)
    assert len(metas) == 2
