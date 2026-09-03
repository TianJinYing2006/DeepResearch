# -*- coding: utf-8 -*-
"""DeepResearch 全局配置。

所有部署相关的可调参数集中在此，便于从 .env 或环境变量覆盖。
"""
import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class LLMConfig:
    """三层 LLM 分级配置（借鉴 gpt-researcher 的 FAST/SMART/STRATEGIC）。

    初始阶段全部用便宜的 qwen-turbo/plus，后续可单独升级 strategic 到 qwen-max。
    """
    base_url: str = field(default_factory=lambda: _env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    api_key: str = field(default_factory=lambda: _env("DASHSCOPE_API_KEY"))

    # 三层分级：fast（摘要/提取）、smart（分析/写作）、strategic（规划/裁决）
    fast_model: str = field(default_factory=lambda: _env("FAST_MODEL", "qwen-turbo"))
    smart_model: str = field(default_factory=lambda: _env("SMART_MODEL", "qwen-plus"))
    strategic_model: str = field(default_factory=lambda: _env("STRATEGIC_MODEL", "qwen-plus"))

    temperature: float = 0.2
    max_tokens: int = 4096


@dataclass
class SearchConfig:
    """网络搜索配置（可切换 Provider）。"""
    provider: str = field(default_factory=lambda: _env("SEARCH_PROVIDER", "bocha"))
    bocha_api_key: str = field(default_factory=lambda: _env("BOCHA_API_KEY"))
    max_results: int = 8


@dataclass
class RAGConfig:
    """RAG 配置。"""
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL", "http://127.0.0.1:6333"))
    collection: str = "deepresearch_docs"
    embedding_model: str = "text-embedding-v3"
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 5
    use_rerank: bool = False  # rerank 先评测再决定去留


@dataclass
class ResearchConfig:
    """研究流程配置（成本/质量旋钮，借鉴 dzhng 的 breadth/depth）。"""
    max_depth: int = 5          # 多跳检索最大深度（防死循环）；W1 改为全局 max_total_hops 后作为单子问题语义上限保留
    breadth: int = 3            # 每轮生成的搜索查询数
    max_subquestions: int = 4   # Planner 最多分解的子问题数
    max_concurrent: int = 3     # 并行检索数（W1 不启用，留 W2 与 Send 并行）
    min_sources_for_crosscheck: int = 2  # 多源印证所需最少独立来源数

    # ---- W1 新增：全局预算 / 硬闸（呼应 grill Q4/Q5/Q6）----
    max_total_hops: int = 20    # 全局总跳数上限；与旧 max_depth×max_subquestions=5×4 精确等价（Q4=A）
    per_subq_hop_cap: int = 5   # 每子问题跳数上限 = max_total_hops/max_subquestions，防 starvation（Q5=A）
    max_replan: int = 1         # revise 触发 Planner.replan 的最大次数，硬上限防空转（Q2-B 兜底）
    token_budget: int = 200_000 # LLM token 总预算，作为硬闸停止条件之一（Q6-B）；正常等价预算下不先于 hop 触发


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)


config = Config()
