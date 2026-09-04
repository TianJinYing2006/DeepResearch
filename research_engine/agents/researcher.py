# -*- coding: utf-8 -*-
"""Researcher Agent：单跳检索（并行全工具 + 结果池择优，W4 Q1/Q5 定案）。

W4 重构（grill Q1/Q5/Q6/Q8）：
- 并行调度：ThreadPoolExecutor 并行 web/rag/arxiv/(code 命中时)，单工具挂不影响其余（return_exceptions 语义）
- 结果池择优（Q1=E）：文本结果按 provider 已排序相关性取 Top-5/工具；code 结果整条保留（豁免相似度过滤）
  但计入总量 Top-10 封顶（防 Monte Carlo 多结果失控）；总量封顶 10 条
- 体积截断（Q5）：web 300 / arxiv abstract 1000 / code stdout 头 8KB+尾 4KB；rag 源 chunk 已控(800)
- 择优的诚实简化：不重复打分——providers（Bocha/arXiv/RAG）自身已做 relevance 排序，
  取工具内 Top-N 即"择优"（Q1 择优目的是防总量爆炸而非重新排序）
- code 触发（Q1 P2）：关键词启发式 should_execute()；失败产物也进 findings（note 不吞，Q2/Q4）
- 返回 (findings, tool_stats)：tool_stats 供 graph 层组装"状态快照"消息（Q8）
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

from research_engine.rag.retriever import HybridRetriever
from research_engine.search.arxiv import ArxivSearchProvider
from research_engine.search.bocha import BochaSearchProvider
from research_engine.state import ResearchFinding
from research_engine.tools.code_exec import exec_code, should_execute

# R2.4 Q5=A 第一层：自指/元描述关键词启发式初标（漏标由 validator verdict 兜底复核）
META_KEYWORDS = (
    "deepresearch", "本系统", "本 agent", "本agent", "本工具",
    "planner→researcher→writer→validator", "四节点编排",
    "该agent", "该 agent", "本 架构",
)

# ---- Q5 体积截断数值（入池层 pool_and_trim）----
WEB_SNIPPET_MAX = 300
ARXIV_ABSTRACT_MAX = 1000
CODE_HEAD_MAX = 8 * 1024
CODE_TAIL_MAX = 4 * 1024
POOL_TEXT_TOP_K = 5        # 文本类工具各自 Top-5（provider 已 relevance 排序）
POOL_TOTAL_CAP = 10        # 总量封顶 10（code 计入，Q5）
_TRUNC = "…[截断]"


def _truncate_head_tail(text: str, head: int, tail: int, mark: str = _TRUNC) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + mark + text[-tail:]


def _truncate_head(text: str, limit: int, mark: str = _TRUNC) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + mark


def _is_meta_content(content: str) -> bool:
    """判定 finding 内容是否为自指/元描述（描述 DeepResearch 系统自身）。"""
    c = content.lower()
    return any(k in c for k in META_KEYWORDS)


class Researcher:
    """单跳检索器（并行全工具）。"""

    def __init__(self):
        self.search: BochaSearchProvider = BochaSearchProvider()
        self.arxiv: ArxivSearchProvider = ArxivSearchProvider()
        self.retriever = HybridRetriever()

    # ---- 各工具单源检索（mutually independent，可并行）----

    def _search_web(self, query: str) -> List[ResearchFinding]:
        """网络搜索。失败 → 空列表（Q1 并行 + Q4 读型 retries=1 在调度层）。"""
        try:
            resp = self.search.search(query, max_results=8)
            findings = []
            for r in resp.results:
                content = f"{r.title}\n{r.snippet}"
                findings.append(
                    ResearchFinding(
                        content=_truncate_head(content, WEB_SNIPPET_MAX),
                        source=r.url,
                        source_type="web",
                        confidence=0.6,
                        is_meta=_is_meta_content(content),
                        metadata=r.metadata or {},
                    )
                )
            return findings
        except Exception:  # noqa: BLE001
            return []

    def _search_rag(self, query: str) -> List[ResearchFinding]:
        """RAG 知识库检索（去重契约：source=rag:<filename>，Q8 教训防饿死）。"""
        try:
            hits = self.retriever.retrieve(query, top_k=5)
            findings = []
            for h in hits:
                doc = h.get("doc") or h.get("source") or "unknown"
                text = h["text"]
                findings.append(
                    ResearchFinding(
                        content=text,  # 源 chunk_size=800 已控，Q5 不再截
                        source=f"rag:{doc}",
                        source_type="rag",
                        confidence=0.7,
                        is_meta=_is_meta_content(text),
                    )
                )
            return findings
        except Exception:  # noqa: BLE001
            return []

    def _search_arxiv(self, query: str) -> List[ResearchFinding]:
        """arXiv 学术检索（Q3：provider 已 relevance 排序 + 3s 限流）。"""
        resp = self.arxiv.search(query)  # provider 内部失败返回空
        findings = []
        for r in resp.results:
            findings.append(
                ResearchFinding(
                    content=_truncate_head(r.snippet, ARXIV_ABSTRACT_MAX),
                    source=r.url,  # abs URL 作 source（Q6 去重契约）
                    source_type="arxiv",
                    confidence=0.65,
                    is_meta=_is_meta_content(r.snippet),
                    metadata=r.metadata or {},
                )
            )
        return findings

    def _search_code(self, query: str) -> List[ResearchFinding]:
        """代码执行（Q1 P2 触发 + Q6 源协议 code:{hash}；失败 note 进 finding 不吞）。"""
        # 计算型查询 → 当前落地：让 sandbox 跑"打印查询所需数值"的确定性兜底不可行时，
        # 由上层（未来 LLM 生成脚本）注入；本期沙箱能力先行，脚本生成待 W4 增强。
        # 这里生成一个针对查询的基础计算脚本（数值/对比类查询的确定性正视），
        # 输出带 key=value，为 Q6 structured_match 呈现层留数据。
        script = _default_code_script(query)
        out = exec_code(script, query=query)
        if out.ok:
            finding = ResearchFinding(
                content=f"[code 执行结果]\n{out.stdout}",
                source=f"code:{out.script_hash}",  # Q6 单 hash（脚本+参数+查询）
                source_type="code_exec",
                confidence=0.8,
                metadata={
                    "exit_code": 0,
                    "elapsed": round(out.elapsed, 3),
                    "structured_match": None,  # Q6：validator 后处理填充（不参与判定）
                },
            )
            return [finding]
        # 失败也进 findings（note 不吞 → W2 附录/可信度可见）
        return [
            ResearchFinding(
                content=f"[执行失败] {out.note}",
                source=f"code:{out.script_hash}",
                source_type="code_exec",
                confidence=0.2,
                metadata={"exit_code": out.metadata.get("exit_code"), "note": out.note,
                          "elapsed": round(out.elapsed, 3)},
            )
        ]

    # ---- 主入口：并行调度（Q1=E）----

    def search_once(self, query: str, state: Any = None) -> Tuple[List[ResearchFinding], Dict[str, int]]:
        """对单个查询做一跳并行检索；visited_sources 去重由 graph 层完成（Q8 语义不变）。

        返回 (pooled_findings, tool_stats)——tool_stats 供"状态快照"消息（Q8）。
        """
        code_enabled = should_execute(query)
        targets: Dict[str, Any] = {"web": self._search_web, "rag": self._search_rag, "arxiv": self._search_arxiv}
        if code_enabled:
            targets["code"] = self._search_code

        all_findings: Dict[str, List[ResearchFinding]] = {k: [] for k in targets}
        with ThreadPoolExecutor(max_workers=len(targets)) as ex:
            futs = {ex.submit(fn, query): key for key, fn in targets.items()}
            for fut, key in futs.items():
                try:
                    all_findings[key] = fut.result() or []
                except Exception:  # noqa: BLE001 — 单工具异常不绊倒整跳（Q1 return_exceptions 语义）
                    all_findings[key] = []

        pooled, stats = pool_and_trim(all_findings)
        # Q8 状态快照：code 失败计数（失败 finding confidence<0.5）供消息 "code 1(失败)"
        stats["code_failed"] = sum(
            1 for f in all_findings.get("code", []) if f.confidence < 0.5
        )
        return pooled, stats


# ---- Q1 P2 兜底脚本：数值/对比类查询的确定性计算（无 LLM，纯 stdlib）----

_SIMPLE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:k|万|亿|×|\*|x)?", re.IGNORECASE)


def _default_code_script(query: str) -> str:
    """为计算型查询生成一个基础计算脚本（当前为确定性模板，未来由 LLM 生成增强）。

    模板覆盖：序列长度 × 常数 → 数值；打印 key=value（Q6 structured_match 呈现层数据源）。
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*[kK万]", query)
    return (
        "import math\n"
        "n = 8192\n"
        "flops_per_token = 6 * n * 2  # 保守系数\n"
        "total_flops = n * flops_per_token\n"
        f"print('query={query!r}')\n"
        "print('sequence_length=' + str(n))\n"
        "print('approx_flops=' + '{:.3e}'.format(total_flops))\n"
    )


# ---- Q5 结果池择优（纯函数，可单测）----

def pool_and_trim(all_findings: Dict[str, List[ResearchFinding]]) -> Tuple[List[ResearchFinding], Dict[str, int]]:
    """合并各工具 finding：截断（已在上游做）+ 择优（文本 Top-5/工具，code 豁免过滤但计入总量 10）。

    返回 (pooled, tool_stats)；pooled 保持顺序稳定（code 优先 → 文本按工具序）。
    """
    stats: Dict[str, int] = {k: len(v) for k, v in all_findings.items()}
    code_findings = all_findings.get("code", [])
    text_findings: List[ResearchFinding] = []
    for key in ("web", "rag", "arxiv"):
        text_findings.extend(all_findings.get(key, [])[:POOL_TEXT_TOP_K])  # provider 已 relevance 排序

    pooled = code_findings + text_findings  # code 优先（豁免过滤的语义）
    if len(pooled) > POOL_TOTAL_CAP:
        pooled = pooled[:POOL_TOTAL_CAP]
    return pooled, stats