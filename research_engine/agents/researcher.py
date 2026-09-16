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

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

from research_engine.failure_reasons import (  # W8 Arm 1 / Arm 4
    FailureReason,
    classify_exception,
    is_fault_reason,
)
from research_engine.rag.retriever import HybridRetriever
from research_engine.search.arxiv import ArxivSearchProvider
from research_engine.search.bocha import BochaSearchProvider
from research_engine.state import DegradationEntry, DegradationSink, ResearchFinding
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
        # W8 Arm 1：降级记录缓冲区（共享实现，见 state.DegradationSink）
        self.degradations = DegradationSink()

    def _record_degradation(
        self,
        component: str,
        reason: str,
        detail: str = "",
        fallback_action: str = "empty_list",
        node: str = "researcher",
    ) -> None:
        """记录一次降级（线程安全）。

        `reason` 必须来自 `failure_reasons` 枚举；Arm 4 落地后应改为直接传
        `resp.failure_reason`（单向派生），不再走 `classify_tool_exception`。
        """
        self.degradations._record_degradation(
            component=component,
            reason=reason,
            detail=detail,
            fallback_action=fallback_action,
            node=node,
        )

    def drain_degradations(self) -> List[DegradationEntry]:
        """取走并清空缓冲区（graph 节点调用，把条目交给 reducer 入 state）。"""
        return self.degradations.drain_degradations()

    # ---- 各工具单源检索（mutually independent，可并行）----

    def _search_web(self, query: str) -> List[ResearchFinding]:
        """网络搜索。失败 → 空列表（Q1 并行 + Q4 读型 retries=1 在调度层）。"""
        try:
            resp = self.search.search(query, max_results=8)
        except Exception as e:  # noqa: BLE001
            # Arm 4 后 provider 已结构化返回失败原因；能抛到这里的属**未预期**内部错误，
            # 按非工具类归类（llm_error/token_limit/recursion_limit/internal），
            # 不再凭异常文本猜工具层 5 值。
            self._record_degradation("web_search", classify_exception(e), detail=str(e))
            return []
        # 单向派生：reason 直接取 resp.failure_reason，禁止在此手写第二个字面量
        if is_fault_reason(resp.failure_reason):
            self._record_degradation("web_search", resp.failure_reason, detail=resp.failure_detail)
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

    def _search_rag(self, query: str) -> List[ResearchFinding]:
        """RAG 知识库检索（去重契约：source=rag:<filename>，Q8 教训防饿死）。

        W8 Arm 4（决策 D-02）：retriever 返回 :class:`RetrieveResponse` ——
        ``[]`` 不再同时表达「没命中」与「向量库不可用」。故障条目**单向派生**自
        ``resp.faults()``；**零命中（``empty_result``）不进降级日志**（D-03）。
        """
        try:
            resp = self.retriever.retrieve(query, top_k=5)
        except Exception as e:  # noqa: BLE001
            # 同上：能抛到这里的属未预期内部错误，按非工具类归类
            self._record_degradation("rag_search", classify_exception(e), detail=str(e))
            return []
        for bf in resp.faults():
            component = "rag_search" if bf.backend == "all" else f"rag_search:{bf.backend}"
            self._record_degradation(component, bf.reason, detail=bf.detail)
        findings = []
        for h in resp.items:
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

    def _search_arxiv(self, query: str) -> List[ResearchFinding]:
        """arXiv 学术检索（Q3：provider 已 relevance 排序 + 3s 限流）。"""
        resp = self.arxiv.search(query)  # provider 内部失败返回带 failure_reason 的空响应
        # 单向派生：reason 直接取 resp.failure_reason。零命中（empty_result）不上抛降级（D-03）。
        if is_fault_reason(resp.failure_reason):
            self._record_degradation(
                "arxiv_search", resp.failure_reason, detail=getattr(resp, "failure_detail", "")
            )
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
        """代码执行（Q1 P2 触发 + Q6 源协议 code:{hash}；失败 note 进 finding 不吞）。

        W8 Arm 2（§5.2）：脚本**不含** query 文本；query 只经
        `exec_code(script, query=query)` 进 `script_hash`（保证 50 条 code 证据
        各自唯一，见 §5.2 预检第 3 条）与 `metadata`，**从不进入被执行的代码**。
        """
        script = _default_code_script()
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
                    "query": query,  # W8 Arm 2：查询与产出的关联走元数据，不走脚本源码
                    "structured_match": None,  # Q6：validator 后处理填充（不参与判定）
                },
            )
            return [finding]
        # 失败也进 findings（note 不吞 → W2 附录/可信度可见）
        # W8 Arm 1：代码执行失败同样是降级，留痕（component=code_exec）
        self._record_degradation(
            "code_exec",
            _classify_code_exec_failure(out.note or ""),
            detail=out.note or "",
            fallback_action="failure_finding",
        )
        return [
            ResearchFinding(
                content=f"[执行失败] {out.note}",
                source=f"code:{out.script_hash}",
                source_type="code_exec",
                confidence=0.2,
                metadata={"exit_code": out.metadata.get("exit_code"), "note": out.note,
                          "query": query,  # W8 Arm 2：失败路径同样用元数据关联查询
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


def _classify_code_exec_failure(note: str) -> str:
    """把 code_exec 的失败 note 归到 §5.1.2 枚举里的工具类值（不新增枚举值）。

    原先只判「含『解析』→ parse_error，否则 provider_error」，实测偏粗：
    `exec_code` 的三类真实失败（脚本语法错、超时、运行时非零退出）会被压成同一个值。
    实测依据（2026-09-16 基线三轮）：144/144 条 code_exec 产出全是失败形态，
    note 形如 `SyntaxError: invalid syntax` ⇒ 应落 `parse_error`（**脚本**解析失败），
    原映射会误标 `provider_error`（该值语义是「provider 返回错误」，与脚本自身无关）。
    """
    n = note or ""
    low = n.lower()
    if "超时" in n or "timeout" in low or "timed out" in low:
        return FailureReason.TIMEOUT.value
    if "SyntaxError" in n or "语法" in n or "解析" in n:
        return FailureReason.PARSE_ERROR.value
    return FailureReason.PROVIDER_ERROR.value


def _default_code_script() -> str:
    """返回确定性计算脚本模板（当前为固定模板，未来由 LLM 生成增强）。

    模板覆盖：序列长度 × 常数 → 数值；打印 key=value（Q6 structured_match 呈现层数据源）。

    W8 Arm 2（§5.2.1）：**本函数不再接收 query，脚本源码里不含任何查询文本**。
    原实现是 `f"print('query={query!r}')"` —— 把用户可控文本插进**将被执行的代码**里。
    查询与产出的关联改由 `metadata["query"]` 承载（见 `_search_code`），
    执行证据仍靠 `exec_code(script, query=query)` 进 `script_hash` 区分（§5.2 预检第 3 条）。
    """
    return (
        "import math\n"
        "n = 8192\n"
        "flops_per_token = 6 * n * 2\n"
        "total_flops = n * flops_per_token\n"
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