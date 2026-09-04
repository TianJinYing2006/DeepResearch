# -*- coding: utf-8 -*-
"""DeepResearch 全局配置。

所有部署相关的可调参数集中在此，便于从 .env 或环境变量覆盖。
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class LLMConfig:
    """三层 LLM 分级配置（借鉴 gpt-researcher 的 FAST/SMART/STRATEGIC）。

    W4（grill Q7）：分档配置——planner_model / critic_model 独立可配，
    默认均回落 STRATEGIC_MODEL（qwen-plus），保 W3 实测基线；
    strategic_model 保留为 planner_model 的兼容别名（router.strategic_* 继续服务 planner）。
    """
    base_url: str = field(default_factory=lambda: _env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    api_key: str = field(default_factory=lambda: _env("DASHSCOPE_API_KEY"))

    # 三层分级：fast（摘要/提取）、smart（分析/写作）、strategic（规划/裁决）
    fast_model: str = field(default_factory=lambda: _env("FAST_MODEL", "qwen-turbo"))
    smart_model: str = field(default_factory=lambda: _env("SMART_MODEL", "qwen-plus"))
    # W4 Q7：分档。优先各自 env，未设回落 STRATEGIC_MODEL（向后兼容单档配置）
    planner_model: str = field(default_factory=lambda: _env("PLANNER_MODEL", _env("STRATEGIC_MODEL", "qwen-plus")))
    critic_model: str = field(default_factory=lambda: _env("CRITIC_MODEL", _env("STRATEGIC_MODEL", "qwen-plus")))
    # 兼容别名（W4 Q7）：strategic_model 语义 = planner_model，env 读 STRATEGIC_MODEL
    strategic_model: str = field(default_factory=lambda: _env("STRATEGIC_MODEL", "qwen-plus"))

    temperature: float = 0.2
    max_tokens: int = 4096

    # W3（grill Q6）：分层定价表（元/1K tokens，取 output 最贵档做保守上界）。
    # ⚠️ 价格有时效，默认值以阿里云官网为准，失效请更新——不要相信任何写死的长期价。
    pricing: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "qwen-turbo": {"input": 0.0003, "output": 0.0006},
        "qwen-plus": {"input": 0.0008, "output": 0.002},
        "qwen-max": {"input": 0.0024, "output": 0.0096},
        "deepseek-r1": {"input": 0.003, "output": 0.012},  # 预留 W4 强推理切换
    })


@dataclass
class SearchConfig:
    """网络搜索配置（可切换 Provider）。"""
    provider: str = field(default_factory=lambda: _env("SEARCH_PROVIDER", "bocha"))
    bocha_api_key: str = field(default_factory=lambda: _env("BOCHA_API_KEY"))
    max_results: int = 8
    # W4 Q3：Semantic Scholar 后处理管道（可选，缺 key 静默跳过；citationCount 只采不决策）
    semantic_scholar_api_key: str = field(default_factory=lambda: _env("SEMANTIC_SCHOLAR_API_KEY"))


@dataclass
class CodeExecConfig:
    """W4 Q2 代码执行沙箱配置（三层纵深：subprocess -I -E -S + AST 白名单 + Audit Hook）。

    Windows 现实：resource 模块 Unix-only，timeout 是唯一可靠 kill；Job Object 走可选开关。
    """
    timeout: int = 15                      # wall-clock 超时（秒），Windows 唯一可靠 kill 手段
    max_output_bytes: int = 128 * 1024     # stdout/stderr 各截断上限，超限标 [TRUNCATED]
    concurrency: int = 2                   # code 同时执行上限（信号量默认 2，可配 4；防 CPU 密集抢占主进程）
    use_job_object: bool = field(default_factory=lambda: _env("CODE_EXEC_USE_JOB", "false").lower() == "true")  # Windows Job Object 可选增强


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
class LangfuseConfig:
    """Langfuse 可观测配置（W3，grill Q4/Q6/Q7 定稿）。

    三态开关（Q4）：enabled=auto 时三件套齐备即自动启用；enabled=false 强制禁用；
    缺任一 key 自动降级（零外发）。禁用态下 observability 不会 import langfuse.openai。
    """
    public_key: str = field(default_factory=lambda: _env("LANGFUSE_PUBLIC_KEY"))
    secret_key: str = field(default_factory=lambda: _env("LANGFUSE_SECRET_KEY"))
    host: str = field(default_factory=lambda: _env("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    enabled: str = field(default_factory=lambda: _env("LANGFUSE_ENABLED", "auto").lower())
    sample_rate: float = field(default_factory=lambda: float(_env("LANGFUSE_SAMPLE_RATE", "1.0")))
    mask_sensitive: bool = field(default_factory=lambda: _env("LANGFUSE_MASK_SENSITIVE", "false").lower() == "true")
    truncate_len: int = 4000  # Q7：常量级配置（策略进代码，不见开关）


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    langfuse: LangfuseConfig = field(default_factory=LangfuseConfig)
    code_exec: CodeExecConfig = field(default_factory=CodeExecConfig)


config = Config()
