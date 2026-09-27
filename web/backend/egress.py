"""供应商 / 数据流向快照（P1-9）。

run 创建时固化「本次运行会把什么内容发给谁」：模型与端点域、搜索 provider、
embedding、向量库、审核、境外开关与策略版本 —— 让历史任务能解释「当时数据发给了谁」
（需求 10 §3.1 第 16 项 / 上线清单 §4 数据流向登记）。

**不含任何密钥**；端点只记 host，不记完整 URL 与凭据。
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from config import config
from research_engine.runtime_profile import RuntimeProfile


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


def build_egress_snapshot(profile: Optional[RuntimeProfile]) -> dict:
    """构造 run 级数据流向快照（写进 `runs.request["egress"]`，随导出展示）。"""
    model = profile.model if profile is not None else config.llm.smart_model
    langfuse_configured = bool(config.langfuse.public_key and config.langfuse.secret_key) and (
        (config.langfuse.enabled or "").lower() != "false")
    return {
        "policy_version": profile.version if profile is not None else "legacy",
        "profile": profile.name if profile is not None else None,
        "llm": {
            "provider": "dashscope", "model": model,
            "endpoint_host": _host(config.llm.base_url),
        },
        "embedding": {
            "provider": "dashscope", "model": config.rag.embedding_model,
            "endpoint_host": _host(config.llm.base_url),
        },
        "search": {"provider": config.search.provider},
        "vector_store": {
            "provider": "qdrant", "endpoint_host": _host(config.rag.qdrant_url),
        },
        "moderation": {"provider": "local_rules"},
        "egress": {
            "user_content_to_llm": True,
            "rag_chunks_to_llm": True,
            "query_to_search": True,
            "arxiv_enabled": bool(config.search.enable_arxiv),
            "langfuse_configured": langfuse_configured,
        },
        "policy_note": "L3-A/B 默认不发送用户内容至境外服务；恢复须独立评审（需求 10 §3.1 第 16 项）",
    }
