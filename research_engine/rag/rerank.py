"""云端 rerank（需求 23 §5.2/§6.6）：DashScope gte-rerank，fail-open。

设计口径（与文档 §6.6 一致）：

- **默认关闭**（`DR_RAG_RERANK=false`），由 §8 评测矩阵决定是否默认开；
- **fail-open**：未配 key / 调用失败 / 超时一律返回 ``None``，调用方回落 RRF 排序
  —— 重排是质量增强，失败绝不丢结果、也不进降级日志；
- 只传文档文本，不传内容以外的元数据；延迟 / 费用由评测与运维指标记录（§8.3）。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


def rerank(query: str, documents: Sequence[str]) -> Optional[List[Tuple[int, float]]]:
    """调用云端重排。

    Returns:
        按相关性降序的 ``[(原始下标, 相关性分数), ...]``；失败 / 未配置返回 ``None``
        （调用方按 fail-open 回落融合顺序）；空输入返回 ``[]``。
    """
    if not documents:
        return []
    from config import config

    if not config.llm.api_key:
        return None
    try:
        import httpx

        response = httpx.post(
            config.rag.rerank_url,
            headers={
                "Authorization": f"Bearer {config.llm.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.rag.rerank_model,
                "input": {"query": query, "documents": list(documents)},
                "parameters": {"return_documents": False, "top_n": len(documents)},
            },
            timeout=config.rag.rerank_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        results = (body.get("output") or {}).get("results") or []
        ranked = sorted(
            ((int(item.get("index", 0)), float(item.get("relevance_score", 0.0)))
             for item in results),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return ranked or None
    except Exception:  # noqa: BLE001 —— 重排失败一律 fail-open
        return None
