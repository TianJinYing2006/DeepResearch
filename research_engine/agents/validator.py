"""Validator Agent：引用校验（存在性 + 论断忠实度）+ 多源印证 + 置信度分级。

主卖点（防幻觉）的核心实现：
1. 引用存在性校验：报告中的每个引用是否真实存在于检索结果
2. 论断忠实度校验（W2 R2.3）：论断是否忠实于其所引用的 finding 内容
3. 多源交叉印证：关键论断需多个独立来源支持
4. 置信度分级：输出每条论断的置信度
5. 自指复核（W2 R2.4 Q5=A）：verdict 顺带输出 is_meta，兜底启发式漏标

W2 grill 落地（Q2/Q3/Q5/Q6）：
- _build_index 返回 {编号: (source, source_type)}，编号供 citation.finding_id 锚定（Q2=A）
- verified = 本地存在性 AND LLM 忠实度；存在性 False 短路不进 LLM（Q3=A）
- LLM 输入全量 findings（废 [:20] 截断，编号 >20 也可判忠实度）（Q3=A）
- LLM 输出按 finding_id 对齐构造 Citation，不再按序 zip（Q3=A，修错位隐患）
- verdict 增 is_meta 字段（R2.4 第二层复核兜底）
- citation 保留 existence 字段，供 CLI/DoD 双口径统计（Q6=A）

引用协议：Writer 用 [来源: N] 编号引用（N 是研究发现列表中的编号），
Validator 将编号映射回真实来源再做校验（ADR-0002/0005 不变）。
"""
from __future__ import annotations

import contextvars
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from config import config
from research_engine.budget import CallAdmissionDenied, admit_call
from research_engine.evidence import build_evidence_index, resolve_evidence_text
from research_engine.failure_reasons import FailureReason  # W8 Arm 1
from research_engine.llm.client import LLMClient
from research_engine.runtime_profile import effective_llm_model
from research_engine.state import Citation, DegradationEntry, DegradationSink, ResearchFinding
from research_engine.usage import UsageSinkError

# W7 Arm3：validator 修复关闭时（TBD-8 基线对照）回退到 v1.1 提示与行为
VALIDATOR_SYSTEM_LEGACY = """你是研究事实核查员。你的任务是校验报告中的论断与引用。

请以 JSON 格式输出校验结果：
{{
  "citations": [
    {{
      "finding_id": "引用编号（对应输入中的编号，非数字来源传空字符串）",
      "claim": "论断原文",
      "faithful": true/false,
      "supported": true/false,
      "confidence": 0.0-1.0,
      "is_meta": true/false,
      "note": "说明"
    }}
  ]
}}

判定规则：
- faithful: 论断是否被所引 finding 的内容真实支持（忠实于来源：不夸大、不曲解、不张冠李戴）
- supported: 论断是否被至少 {min_sources} 个独立来源支持（多源印证）
- is_meta: 该论断/来源是否在描述 DeepResearch 系统自身（自指/元描述，如"本系统""本Agent""Planner→Researcher→Writer→Validator"四节点编排等）——是则 true
- confidence: 综合置信度 0-1
- note: 说明；faithful=false 时必须给出具体原因
"""

VALIDATOR_SYSTEM = """你是研究事实核查员。你的任务是校验报告中的论断与引用。

请以 JSON 格式输出校验结果：
{{
  "citations": [
    {{
      "finding_id": "引用编号（对应输入中的编号，非数字来源传空字符串）",
      "claim": "论断原文",
      "claim_echo": "逐字回显输入中该条目的 claim 原文（word-by-word，不可改写或缩写）",
      "faithful": true/false,
      "supported": true/false,
      "supporting_evidence_ids": ["支持该论断的证据编号（必须来自研究发现清单，禁止编造；无则空列表）"],
      "contradicting_evidence_ids": ["与该论断冲突或反对的证据编号（只记录；无则空列表）"],
      "confidence": 0.0-1.0,
      "is_meta": true/false,
      "note": "说明"
    }}
  ]
}}

判定规则：
- faithful: 论断是否能**直接由该 finding 内容推断**（不夸大、不曲解、不张冠李戴；包含明确数值/日期/名称的算术推断视为忠实）
- supported: 论断是否被至少 {min_sources} 个**独立来源**支持（同一文档的多个分块不算独立来源）；若为 true，必须在 supporting_evidence_ids 中列出这些支持证据的编号（编号必须真实存在于清单中，系统会做代码复核）
- contradicting_evidence_ids: 清单中若存在与该论断冲突/反对的证据，列出其编号（只记录，不影响 supported 判定）
- is_meta: 该论断/来源是否在描述 DeepResearch 系统自身（自指/元描述，如"本系统""本Agent""Planner→Researcher→Writer→Validator"四节点编排等）——是则 true
- confidence: 综合置信度 0-1
- note: 说明；faithful=false 时必须给出具体原因

重要：输出中的 `claim_echo` 必须逐字回显输入里对应条目的 claim 原文（word-by-word），用于本地核对；不要改写或缩写。
"""


def build_validator_system(cfg=None) -> str:
    """Validator system 提示词的**唯一产生点**（W8 Arm 6）。

    ⚠️ 这是全项目**最需要被指纹盯住的一处**：`validator_fixes_enabled` 开关
    会在两套判据（含 `claim_echo` 逐字回显要求）之间切换，而 validator 的裁决
    **直接就是** `citation_accuracy` 这个主指标本身（评测不重跑裁判，直读主链路产物）。
    W7 已实测：仅换裁判即可产生 +7.53pp 的差。⇒ 提示词选版必须进 provenance。
    """
    c = cfg if cfg is not None else config
    tpl = VALIDATOR_SYSTEM if c.experiment.validator_fixes_enabled else VALIDATOR_SYSTEM_LEGACY
    return tpl.format(min_sources=config_min_sources(c))


def validator_model_name(cfg=None) -> str:
    """主链路 validator 用的模型名（W8 Arm 6：eval 侧 `citation_judge_model` 的唯一来源）。

    不直接写 `config.llm.validator_model` 的原因：eval 报告里叫「裁判模型」，
    生产里叫「validator 模型」，若两处各写一遍，改配置时容易只改一处 ——
    而这两个名字指的是**同一次 LLM 调用**。
    """
    if cfg is not None:
        return cfg.llm.validator_model
    return effective_llm_model("validator")


def validator_batch_size() -> int:
    """单次 LLM 忠实度判定的最大引用条数（≤0 = 关闭分批，回退单次调用）；需求 19 / #76。"""
    try:
        return int(os.getenv("DR_VALIDATE_BATCH_SIZE", "16"))
    except ValueError:
        return 16


def validator_concurrency() -> int:
    """分批并行度（clamp 1..4）。

    依据（2026-09-30 调研）：sharding 研究（arXiv 2608.06422）显示把判定拆成子批
    不降低反而提升一致性（κ 0.86 @ 32/批 vs 0.73 @ 1/批），并发度主要受供应商限流约束。
    """
    try:
        value = int(os.getenv("DR_VALIDATE_CONCURRENCY", "3"))
    except ValueError:
        value = 3
    return max(1, min(4, value))


def validator_shard_context() -> bool:
    """A4（#154）：多批场景下每批只喂**本批引用涉及**的 finding 行。

    需求 19 的分批只切了「待判定列表」、没切「上下文」：每批都把整段 findings_text
    拼进 prompt ⇒ 总输入 ≈ 批数 × findings_text（q_002 实测 7 批 ≈ 111k，远超
    30k 校验地板 ⇒ 112 条引用全部因预算被拒）。开启后总输入降到「约 1 份上下文 +
    引用行」（q_002 实测 32.7k @ batch=32）。

    单批场景（``DR_VALIDATE_BATCH_SIZE<=0`` 或引用数 ≤ 批大小）本开关**不产生任何
    差异**——拼装结果与关闭时逐字一致。多批场景会改变喂料口径 ⇒ 按**新 Arm** 对待，
    可用 ``DR_VALIDATE_SHARD_CONTEXT=0`` 回到旧行为做对照。
    """
    return os.getenv("DR_VALIDATE_SHARD_CONTEXT", "1").strip().lower() not in (
        "0", "false", "no", "off")


class CitationVerdictItem(BaseModel):
    """单条引用的 LLM 校验 verdict（Q3=A 按 finding_id 对齐，Q5=A 增 is_meta 复核）。"""

    finding_id: str = Field(description="引用编号；非数字来源可传空字符串")
    claim: str = Field(description="论断原文")
    claim_echo: str = Field(default="", description="W7 回显字段：必须逐字回显原 claim（word-by-word），本地核对用")
    faithful: bool = Field(description="论断是否忠实于该 finding 内容")
    supported: bool = Field(default=False, description="是否通过多源印证")
    # F12（审计）：多源印证必须给出可复核的支持证据编号（代码验证存在性 + 独立来源数）
    supporting_evidence_ids: List[str] = Field(
        default_factory=list,
        description="F12：支持该论断的证据编号（须来自研究发现清单；代码复核独立来源数）",
    )
    contradicting_evidence_ids: List[str] = Field(
        default_factory=list,
        description="F12：反对/冲突证据编号（只记录，不参与 supported 判定）",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="校验置信度 0-1")
    is_meta: bool = Field(default=False, description="是否自指/元描述（描述本系统自身）")
    note: str = Field(default="", description="说明；faithful=false 时必须写原因")


class CitationVerdict(BaseModel):
    """校验结果 schema（防模型漏 key 被静默默认值误判）。"""

    citations: List[CitationVerdictItem] = Field(default_factory=list)


class Validator:
    """引用校验器。"""

    def __init__(self):
        from research_engine.llm.router import get_router
        self.router = get_router()
        self.last_validation_stats: Dict[str, Any] = {}
        # W8 Arm 1：降级记录缓冲区（由 graph 节点 drain 后入 state）
        self.degradations = DegradationSink()

    def drain_degradations(self) -> List[DegradationEntry]:
        """取走并清空降级记录（graph 节点调用）。"""
        return self.degradations.drain_degradations()

    def _build_index(self, findings: List[ResearchFinding]) -> Dict[str, Tuple[str, str]]:
        """建立编号 -> (真实来源, 来源类型) 映射（Q2=A：带出 source_type，编号供 finding_id 锚定）。"""
        return {str(i): (f.source, f.source_type) for i, f in enumerate(findings, 1)}

    @staticmethod
    def _source_to_ids(findings: List[ResearchFinding]) -> Dict[str, List[str]]:
        """来源 → finding 编号列表（RAG 同 URL 多分块时一个 source 对应多个编号）。"""
        out: Dict[str, List[str]] = {}
        for i, f in enumerate(findings, 1):
            out.setdefault(f.source, []).append(str(i))
        return out

    @staticmethod
    def _used_finding_ids(
        to_check: List[Dict[str, Any]], source_to_ids: Dict[str, List[str]],
    ) -> set:
        """由待校验引用反查涉及的 finding 编号（数字 id 优先，URL 引用按 source 反查）。"""
        used: set = set()
        for r in to_check:
            fid = r.get("finding_id")
            if fid:
                used.add(str(fid))
            elif r.get("source"):
                for mapped in source_to_ids.get(r["source"], []):
                    used.add(mapped)
        return used

    @classmethod
    def _build_findings_lines(
        cls,
        findings: List[ResearchFinding], to_check: List[Dict[str, Any]],
        evidence_index: Optional[Dict[str, ResearchFinding]] = None,
        stats: Optional[Dict[str, int]] = None,
        statuses: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, str], bool]:
        """与 :meth:`_build_findings_text` 同渲染逻辑，但返回 ``{编号: 行文本}``。

        A4（#154）：需求 19 的分批只切了「待判定列表」、没切「上下文」——每批都把
        整段 findings_text 拼进 prompt，总输入 ≈ 批数 × findings_text（q_002 实测
        7 批 ≈ 111k）。返回按编号索引的行之后，调用方可以只挑本批引用涉及的行，
        总输入降到「约 1 份上下文 + 少量引用行」（q_002 实测 32.7k @ batch=32）。

        ``stats`` / ``statuses`` 仍按**全量 to_check** 一次性统计：喂料分片是
        token 优化，不得改变证据健康度的计数口径（同一 finding 被多批引用、
        或按批重复渲染，都不重复计数）。
        """
        import warnings

        index = evidence_index or {}

        def _text_for(f: ResearchFinding, number: int) -> str:
            text, status = resolve_evidence_text(f, index)
            if stats is not None and status in ("truncated", "missing", "partial"):
                stats[status] = stats.get(status, 0) + 1
            if statuses is not None:
                statuses[str(number)] = status
                statuses[f"src:{f.source}"] = status
            return text

        def _render(subset: Optional[set]) -> Dict[str, str]:
            # 过滤先于渲染：只有入选的行才解析证据（与旧实现的 stats 口径一致）
            return {
                str(i): f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {_text_for(f, i)}"
                for i, f in enumerate(findings, 1)
                if subset is None or str(i) in subset
            }

        # W7 Arm5：喂料裁剪可通过 VALIDATOR_TRIM_ENABLED 关闭（TBD-8 基线对照）
        if not config.experiment.validator_trim_enabled:
            return _render(None), False

        # Bug-5 修复：URL 格式引用的 finding_id 为空，通过 source 字段反查 finding 编号
        # 设计-3 修复：同 source 多 findings（RAG 同 URL 多分块）时收集所有编号
        used_ids = cls._used_finding_ids(to_check, cls._source_to_ids(findings))

        if used_ids:
            lines = _render(used_ids)
            if lines:
                return lines, True
            # 安全阀：used_ids 非空但与 findings 编号无交集（理论上不发生）→ 降级全量
            warnings.warn(
                "Validator feed trim: used_ids 与 findings 编号无交集，降级为全量喂料",
                stacklevel=2,
            )

        # 未触发裁剪或触发安全阀：回退全量
        return _render(None), False

    @classmethod
    def _build_findings_text(
        cls,
        findings: List[ResearchFinding], to_check: List[Dict[str, Any]],
        evidence_index: Optional[Dict[str, ResearchFinding]] = None,
        stats: Optional[Dict[str, int]] = None,
        statuses: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, bool]:
        """W7 Arm5 A′：只喂被引用且存在性通过的 findings，保留原编号。

        F07/F08（审计）：不再对证据做 500 字符静默截断 —— 按工作摘要的
        ``metadata["origin_evidence_ids"]`` 回到**原文层**取正文（``evidence_index``），
        超出预算时头尾保留 + 显式 ``[证据截断]`` 标记；原文缺失时回落摘要并计入
        ``stats["missing"]``（调用方写入 validator_stats，不静默）。

        R04（审计）：``statuses`` 收集每个编号 / 来源的 origin 解析状态
        （``raw`` / ``truncated`` / ``partial`` / ``missing``）——调用方据此把
        origin 缺失的引用判 **UNKNOWN**，禁止把工作摘要当原始事实依据。

        返回 (text, trimmed)。
        三条硬约束：
        1. 阶段 1 存在性校验仍基于全量 index（本函数不改变 index）。
        2. 编号保留原编号（用 enumerate(findings, 1) 的原始 i 过滤，不对子集重排）。
        3. 安全阀：to_check 非空但 used_ids 与 findings 编号无交集 → 降级全量并 warn。

        A4（#154）：本函数只是 :meth:`_build_findings_lines` 的拼接包装，
        供单批/回退路径与既有用例使用；多批路径直接用行索引做分片。
        """
        lines, trimmed = cls._build_findings_lines(
            findings, to_check, evidence_index, stats, statuses)
        return "\n".join(lines.values()), trimmed

    _CLAIM_SEP = "。！？；\n"          # 句子边界字符（W2.1）
    _CLAIM_MAX = 200                  # 引用前最多取 200 字符的窗口（W2.1）

    def _claim_text(self, report: str, end: int, start: int = 0) -> Tuple[str, int]:
        """取引用前的一段文本做 claim（W2.1 待办 + W7 F4：保留上下文/主语兜底）。

        - start 用于隔离多个引用：只取上一个引用结束位置到当前引用之间的文本；
        - 优先对齐最近句子边界（。！？；\n），避免从单词中间硬截断；
        - 若最近句子边界切出的片段以"其/该/此/这"等代词开头，
          前补前一句完整内容，使论断自包含（RAGAS "self-contained" 精神，W7 F4）；
        - 退化为 200 字符窗口；再退化 80 字符（保原行为兜底）；
        - 清理行内 markdown 残留（** ` # 行首 - | 等），纯展示层，不影响校验。

        F11（审计 3b）：同时返回 ``raw_start``（claim 句在 report 中的原始起始偏移，
        供返工删除定位；代词兜底时仍指向被校验句本身）。

        R02（审计）：先剥离窗口**收尾**的句末终止符再找内部句子边界 —— 否则
        ``论断。[来源: 1]`` 会切出空 claim 并触发 80 字符兜底，把上一句背景材料
        一起纳入删除区间（返工误删）。
        """
        window_start = max(start, end - self._CLAIM_MAX)
        window = report[window_start:end]
        stripped = window.rstrip(self._CLAIM_SEP)
        if stripped.strip():
            rel = max(stripped.rfind(c) for c in self._CLAIM_SEP)
            if rel >= 0:
                claim_start_in_report = window_start + rel + 1
                claim = stripped[rel + 1:]
            else:
                claim_start_in_report = window_start
                claim = stripped
        else:
            # 窗口全是终止符/空白（退化路径）：保留原兜底行为
            claim_start_in_report = window_start
            claim = ""
        raw_start = claim_start_in_report
        claim = re.sub(r"[*_`]{1,3}", "", claim)                                   # ** 加粗/_斜体_/`代码`
        claim = re.sub(r"^\s*#\s*", "", claim, flags=re.M)                          # 行首 # 标题（Bug-12：不删行内 #）
        claim = re.sub(r"^\s*[-|]\s*", "", claim, flags=re.M)                      # 行首 - 列表 / | 表格碎片
        claim = re.sub(r"\s{2,}", " ", claim).strip().replace("\n", " ")
        # W7 F4：主语兜底（以"其/该/此/这"开头时，前补前一句完整内容，使代词指代明确）
        _PRONOUN_PREFIXES = ("其", "该", "此", "这")
        if config.experiment.validator_fixes_enabled and claim and claim[0] in _PRONOUN_PREFIXES:
            before = report[window_start:claim_start_in_report]
            last_sent = before.strip().rstrip("。！？；").strip()
            if last_sent:
                # Bug-12 同步：清理补回文本中的行内 markdown 残留
                last_sent = re.sub(r"[*_`]{1,3}", "", last_sent)
                claim = f"{last_sent}；{claim}"
        if not claim.strip():  # 边界切分到空（窗口尾恰为句号等）才退回 80 字符硬截断兜底
            fallback_start = max(start, end - 80)
            claim = report[fallback_start:end].strip().replace("\n", " ")
            raw_start = fallback_start
        return claim, raw_start

    def _extract_citations(self, report: str) -> List[dict]:
        """从报告中提取 [来源: N] 形式的引用（W7 F3：过滤非论断句）。

        支持多编号引用：[来源: 5, 72, 77] 会被拆分为 3 条独立引用，
        每条单独校验（见 ADR-0005）。仅当引用内容全部为数字 token 时
        才拆分；含非数字内容时视为单一来源字符串原样保留，保持对
        [来源: <真实URL>] 协议的兼容。

        R02（审计）：连续引用标记（``[来源: 1][来源: 2]``，中间仅空白）视为
        **同一论断句**的引用组 —— 共享同一 ``claim_start`` / ``claim_end``
        区间，返工按句汇总支持关系，不会再拆出两个不同区间导致半句被删 /
        孤立引用残留。
        """
        # A Validator instance can be reused by the evaluator; stats must describe
        # this extraction/validation only, not leak counts from a prior report.
        self.last_validation_stats = {}
        citations = []
        pattern = r"\[来源:\s*([^\]]+)\]"
        last_end = 0
        filter_enabled = config.experiment.validator_assertive_filter_enabled
        raw_citation_count = 0
        filtered_citation_count = 0
        matches = list(re.finditer(pattern, report))
        i = 0
        while i < len(matches):
            # 归并连续标记组：组内相邻标记之间只能是空白（否则属于不同论断）
            run = [matches[i]]
            j = i + 1
            while (j < len(matches)
                   and not report[matches[j - 1].end():matches[j].start()].strip()):
                run.append(matches[j])
                j += 1
            # 取论断（引用前的一段文本，W2.1 按句子边界 + 清理 markdown）
            # W7 F3：start=last_end 隔离多个引用，避免后一个 claim 混入前一个引用标记
            # F11：同时记录 claim 起始偏移与引用标记结束偏移（返工删除定位）
            claim, claim_start = self._claim_text(report, run[0].start(), last_end)
            claim_end = run[-1].end()
            last_end = claim_end
            refs: List[str] = []
            for marker in run:
                refs.extend(self._split_ref(marker.group(1)))
            raw_citation_count += len(refs)
            if filter_enabled and not self._is_assertive(claim):
                filtered_citation_count += len(refs)
                i = j
                continue  # W7 F3: non-assertive fragments stay out of validation
            for ref in refs:
                citations.append({"claim": claim, "source": ref,
                                  "claim_start": claim_start, "claim_end": claim_end})
            i = j
        self.last_validation_stats.update({
            "filter_enabled": filter_enabled,
            "raw_citation_count": raw_citation_count,
            "candidate_citation_count": len(citations),
            "filtered_citation_count": filtered_citation_count,
            "filtered_rate": round(filtered_citation_count / raw_citation_count, 4)
            if raw_citation_count else 0.0,
        })
        return citations

    @staticmethod
    def _is_assertive(claim: str) -> bool:
        """W7 F3：判断 claim 是否为可校验的论断句（而非元话语/过渡句/表格残片）。

        规则（纯启发式，零 LLM）：
        - 长度 >= 8（过滤碎片）
        - 不含 markdown 表格列分隔 `|`
        - 非纯标题/列表项（以 # 开头）
        - 不是典型元话语开头（本节/本章/综上所述/如图/下面/接下来）
        - 设计-2 修复：移除"因此/首先/其次/最后/总之/由此可见"等论断引导词过滤
          ——这些是有效论断的常见开头，不应被静默丢弃
        """
        if "|" in claim:
            return False
        meta_prefixes = (
            "本节", "本章", "综上所述", "如图", "表 ", "下面", "接下来",
        )
        stripped = claim.strip()
        if stripped.startswith("#") or stripped.startswith("-"):
            return False
        for prefix in meta_prefixes:
            if stripped.startswith(prefix):
                return False
        return True

    @staticmethod
    def _split_ref(ref: str) -> List[str]:
        """拆分多编号引用：'5, 72, 77' -> ['5', '72', '77']。

        LLM 实际输出中常见 [来源: 5, 72, 77]、[来源: 5、8]、[来源: 3和7]
        等变体，统一按分隔符拆分。分隔符覆盖中英文逗号、顿号、分号、
        斜杠、空格及"和"字。

        P0 引用协议统一：
        - 先 strip 每个 token 的 '#' 前缀（兼容 LLM 输出的 #1, #2 格式）
        - 只有当拆出的所有 token 都是纯数字时才视为多编号引用
        - 否则整体作为单一来源字符串返回（兼容 URL 协议）
        """
        raw_tokens = [t for t in re.split(r"[#,\u3001\uFF0c\uFF1B\u3001/\u548c\s;]+", ref.strip()) if t]
        tokens = [t.strip().lstrip("#").strip() for t in raw_tokens]
        tokens = [t for t in tokens if t]
        if len(tokens) > 1 and all(re.fullmatch(r"\d+", t) for t in tokens):
            return tokens
        # 单一 token 时，如果 strip # 后是纯数字，也返回数字编号
        if len(tokens) == 1 and re.fullmatch(r"\d+", tokens[0]):
            return tokens
        return [ref.strip()]

    #: F11（审计 3a）：事实句最短长度（字符）——过滤标题/碎片，控制统计噪声
    _FACT_MIN_CHARS = 20
    #: 句子切分分隔符（R03：与引用提取的 claim 边界语义一致）
    _SENT_SEPS = "。！？!?\n"
    #: R03：返工降格保留的未引用事实句上限（审计可见性；统计计数不受此限）
    _UNCITED_SPAN_LIMIT = 100

    @classmethod
    def _sentence_spans(cls, report: str) -> List[Tuple[int, int, bool]]:
        """带偏移的句子切分：返回 ``(start, end, cited)``（R03）。

        - 标题行（``#`` 开头）不产出 span；
        - ``cited``：句内出现 ``[来源: ...]``，或句末**仅隔分隔符/空白**紧邻
          引用标记（``事实句。[来源: 1]`` 属于已引用，不再被误计为无引用）；
        - ``end`` 含收尾标点与紧邻引用标记，可直接用于返工定位。
        """
        text = report or ""
        spans: List[Tuple[int, int, bool]] = []
        n = len(text)
        pos = 0
        while pos < n:
            seg_start = pos
            while pos < n and text[pos] not in cls._SENT_SEPS:
                pos += 1
            seg = text[seg_start:pos]
            end_text = pos
            # 句末紧邻引用标记（中间只允许分隔符/空白）
            j = pos
            while j < n and text[j] in cls._SENT_SEPS:
                j += 1
            k = j
            while True:
                m = re.match(r"[ \t]*\[来源:[^\]]+\]", text[k:])
                if not m:
                    break
                k += m.end()
            cited = ("[来源:" in seg) or (k > j)
            if seg.strip() and not seg.lstrip().startswith("#"):
                spans.append((seg_start, k if k > j else end_text, cited))
            pos = k if k > j else end_text
            while pos < n and text[pos] in cls._SENT_SEPS:
                pos += 1
        return spans

    @staticmethod
    def _fact_coverage_stats(report: str) -> Dict[str, Any]:
        """审计 F11（3a）+ R03：无引用事实句覆盖率与**明细 span**（零 LLM、只看不判）。

        - 事实句：标题行外、可见文本长度 ≥ ``_FACT_MIN_CHARS`` 的句子；
        - 有引用：句内或句末紧邻 ``[来源: ...]``（多编号/URL 协议同计）；
        - ``uncited_fact_spans``：未引用事实句的 ``{claim, start, end}`` 明细
          （供返工做确定性降格标注；有上限，避免 state 体积失控）；
        - 覆盖率口径 = 有引用句 / 事实句 —— 与引用校验口径分开记录。
        """
        spans = Validator._sentence_spans(report)
        total = 0
        cited = 0
        uncited_spans: List[Dict[str, Any]] = []
        for start, end, is_cited in spans:
            raw = (report or "")[start:end]
            text_only = raw.rstrip("。！？!?\n")
            if len(text_only.strip()) < Validator._FACT_MIN_CHARS:
                continue
            total += 1
            if is_cited:
                cited += 1
            elif len(uncited_spans) < Validator._UNCITED_SPAN_LIMIT:
                uncited_spans.append({
                    "claim": text_only.strip(),
                    "start": start,
                    "end": start + len(text_only),
                })
        return {
            "fact_sentence_count": total,
            "uncited_fact_sentence_count": total - cited,
            "citation_coverage": round(cited / total, 4) if total else 1.0,
            "uncited_fact_spans": uncited_spans,
        }

    def validate(self, report: str, findings: List[ResearchFinding], state: Any = None,
                 *, evidence: Optional[List[ResearchFinding]] = None) -> List[Citation]:
        """校验报告引用。

        双段式（Q3=A）：
        1. 本地存在性（无 LLM）：编号映射到真实来源 + 来源存在性判定，存在性 False 短路；
        2. LLM 忠实度：仅存在性 True 的引用进 LLM，判定"论断是否忠实于被引 finding 内容"；
           verified = 存在性 AND 忠实度，输出按 finding_id 对齐。

        F07/F08（审计）：``findings`` 为**工作摘要层**（编号协议基准，与 Writer 同一份）；
        ``evidence`` 为**原文层**（可选）——忠实度判定按 origin 链回原文取正文，
        长文后段事实不再因 500 字符截断被误拒/漏判。
        """
        extracted = self._extract_citations(report)
        # F11（审计 3a）：无引用事实句覆盖率随 stats 落库（不进 LLM、不做门禁）
        self.last_validation_stats.update(self._fact_coverage_stats(report))
        index = self._build_index(findings)
        known = {src for src, _ in index.values()}

        # ---- 阶段 1：本地存在性（短路，不需要 LLM）----
        local_results: List[Dict[str, Any]] = []
        for c in extracted:
            ref = c["source"]
            # 支持 [来源: N] 编号 或 [来源: <真实来源>]（ADR-0002 协议）
            hit = index.get(ref)
            if hit is not None:
                real_source, source_type = hit
                finding_id = ref
            else:
                real_source, source_type = ref, ""
                # 越界编号（如 999）保留数字 finding_id 供附录展示；URL 等非数字来源留空（Q2）
                finding_id = ref if re.fullmatch(r"\d+", ref) else ""
            existence = real_source in known
            local_results.append({
                **c,
                "source": real_source,
                "source_type": source_type,
                "finding_id": finding_id,
                "existence": existence,
                "note": "" if existence else "来源不存在于研究发现（编号越界或来源未命中）",
            })

        if not local_results:
            self.last_validation_stats.update({
                "validated_citation_count": 0,
                "existence_pass_count": 0,
                "evidence_truncated_count": 0,
                "evidence_missing_count": 0,
                "evidence_partial_count": 0,
                "evidence_unknown_citation_count": 0,
            })
            return []

        # ---- 阶段 2：LLM 忠实度判定（仅存在性 True 的引用；Q3=A 短路）----
        to_check = [r for r in local_results if r["existence"]]
        # W7 Arm5 A′：只喂被引用且存在性通过的 findings，保留原编号；存在性校验仍用全量 index
        # F07/F08：证据索引来自原文层；截断/缺失显式计入 evidence_stats（随 stats 落库）
        # R04：evidence_status 按编号/来源记录 origin 解析健康度 —— 缺失/部分缺失的引用
        # 即使 LLM 判 faithful 也不得通过（UNKNOWN，禁止用工作摘要当原始依据）。
        evidence_index = build_evidence_index(evidence or [])
        evidence_stats: Dict[str, int] = {}
        evidence_status: Dict[str, str] = {}
        # A4（#154）：取「按编号索引」的喂料行 —— 多批时按批挑行，避免整段上下文重复 N 次
        source_to_ids = self._source_to_ids(findings)
        findings_lines, _ = self._build_findings_lines(findings, to_check, evidence_index,
                                                       evidence_stats, evidence_status)
        findings_text = "\n".join(findings_lines.values())
        # W7 F1：claim 不再截断；F2：LLM 输出需 claim_echo 回显原文
        fixes_enabled = config.experiment.validator_fixes_enabled
        if fixes_enabled:
            claim_field = "claim"
        else:
            # 基线对照：恢复 v1.1 的 100 字符截断（W7 F1 修复关闭）
            claim_field = "claim_truncated"
            for r in to_check:
                r["claim_truncated"] = r["claim"][:100]
        # W8 Arm 6：唯一产生点（含开关选版 + min_sources 渲染），与 prompt_hash 逐字一致
        system_prompt = build_validator_system()

        def _judge_batch(
            batch: List[Tuple[int, Dict[str, Any]]],
        ) -> Tuple[List[Dict[str, Any]], List[int], str]:
            """单批判定（需求 19 / #76）：同款判据 / schema / 提示词，只把 verdict 范围收窄到本批。

            返回 ``(verdict_items, 失败下标, 错误信息)``；**降级留痕不在此处** ——
            统一由主线程收集后写入，避免多线程并发修改 degradation 缓冲。
            """
            citations_json = "\n".join(
                f"- finding_id: {r['finding_id']} | claim: {r[claim_field]} | source: {r['source']}"
                for _, r in batch
            )
            # A4（#154）：分片喂料 —— 只保留本批引用涉及的 finding 行（单批时与
            # findings_text 逐字一致）。findings_lines / source_to_ids 只读，线程安全。
            if shard_context:
                batch_ids = self._used_finding_ids([r for _, r in batch], source_to_ids)
                # A4 加固（#154）：本批引用反查不到任何编号（finding_id 为空且 source 未映射）
                # 时回退全量喂料——否则该批「研究发现」段为空，等于让模型在无证据下裁决。
                feed_text = (
                    "\n".join(line for number, line in findings_lines.items()
                              if number in batch_ids)
                    if batch_ids else findings_text
                )
            else:
                feed_text = findings_text
            user = (
                f"研究发现：\n{feed_text}\n\n"
                f"待校验引用（仅列存在性已通过的）：\n{citations_json}\n\n"
                "请输出校验结果。"
            )
            if fixes_enabled:
                user += "对每条引用，`claim_echo` 字段必须逐字回显上面的 claim 原文。"
            try:
                # 与 critic.py 同款：Pydantic schema + 纠错重试，防漏 key 静默默认
                # W5（Q2）：role="validator" 进职责桶（直建实例不传 state 的漏计由类级差值补全）
                client = LLMClient(model=validator_model_name(), role="validator")
                data = client.chat_json(
                    [
                        # ⚠️ Arm 6：system_prompt 已由 build_validator_system() **渲染完毕**
                        # （内含 min_sources），这里再 .format() 一次会因为模板里残留的
                        # JSON 花括号而 KeyError —— 而它被下面的 except 兜住 ⇒
                        # 「提示词坏了」会上报成「LLM 调用失败」，是最难查的假象。
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user},
                    ],
                    state=state,
                    schema=CitationVerdict,
                )
                return list(data.get("citations", [])), [], ""
            except UsageSinkError:
                # F06：strict 记账失败必须上抛（不得按批次降级为 existence_only）
                raise
            except Exception as e:  # noqa: BLE001
                return [], [idx for idx, _ in batch], str(e)

        verdicts: Dict[str, List[Dict[str, Any]]] = {}
        unused_verdicts: List[Dict[str, Any]] = []
        failed_row_ids: set = set()
        expected_fids = {r["finding_id"] for r in to_check}
        # A4（#154）：多批场景是否只喂本批引用涉及的 finding 行（单批场景无差异）
        _batch_size = validator_batch_size()
        shard_context = (
            validator_shard_context()
            and _batch_size > 0
            and len(to_check) > _batch_size
        )
        # A4（#154）：未校验必须按「饥饿」/「判据拒绝」分开统计 —— 两者一个是
        # 工程缺陷（预算不足 / 调用失败）、一个是质量信号（LLM 判不忠实），
        # 混在「未校验引用 N%」里会把后续决策带偏（例如误判为需要放宽判据）。
        budget_starved_count = 0
        unverified_starved_count = 0
        verified_rejected_count = 0
        if to_check:  # 全部存在性失败时零 LLM 调用（Q3 短路完整落地）
            # F13（审计）：忠实度阶段预算/时限准入 —— 耗尽则整体跳过（保留存在性结论并留痕）
            try:
                admit_call(state)
            except CallAdmissionDenied as exc:
                self.degradations._record_degradation(
                    component="llm",
                    reason=FailureReason.TOKEN_LIMIT.value,
                    detail=f"validator fidelity phase skipped: {exc}"[:300],
                    fallback_action="existence_only",
                    node="validator",
                )
                # A4（#154）：预算准入拒绝 ⇒ 这些引用根本没进 LLM（饥饿），
                # 与「判了但没通过」严格区分。
                budget_starved_count = len(to_check)
                to_check = []
            if to_check:
                batch_size = _batch_size  # 与上面 shard_context 判定取同一值
                indexed = list(enumerate(to_check))
                batches: List[List[Tuple[int, Dict[str, Any]]]] = (
                    [indexed[i:i + batch_size] for i in range(0, len(indexed), batch_size)]
                    if batch_size > 0 else [indexed]
                )
                if len(batches) > 1:
                    # 需求 19 / #76：分批并行。上下文必须显式注入线程 —— usage sink 与运行档位
                    # 都是 contextvars，不注入会导致 validator 调用漏记账、档位模型回落全局默认。
                    base_ctx = contextvars.copy_context()
                    with ThreadPoolExecutor(
                        max_workers=min(validator_concurrency(), len(batches))
                    ) as pool:
                        futures = [pool.submit(base_ctx.copy().run, _judge_batch, b)
                                   for b in batches]
                        batch_results = [future.result() for future in futures]
                else:
                    batch_results = [_judge_batch(batches[0])]
                for items, failed_idxs, err in batch_results:
                    if failed_idxs:
                        failed_row_ids.update(id(to_check[idx]) for idx in failed_idxs)
                        # W8 Arm 1：忠实度失效必须留痕（existence_only），按批隔离（每失败批一条）
                        self.degradations._record_degradation(
                            component="llm",
                            reason=FailureReason.LLM_ERROR.value,
                            detail=(f"phase=validator batch idx={failed_idxs[0]}..{failed_idxs[-1]}; "
                                    f"error={err}"),
                            fallback_action="existence_only",
                            node="validator",
                        )
                    for item in items:
                        fid = item.get("finding_id", "")
                        if fid and fid in expected_fids:
                            verdicts.setdefault(fid, []).append(item)
                        else:
                            unused_verdicts.append(item)

        # F12（审计）：多源印证的代码复核基础 —— 编号→finding（支持证据必须真实存在），
        # 并统计 claimed / verified / downgraded 三个口径（审计可见性）
        by_number = {str(i): f for i, f in enumerate(findings, 1)}
        min_sources = config_min_sources()
        ms_claimed = ms_verified = ms_downgraded = 0
        fixes_enabled = config.experiment.validator_fixes_enabled

        # R01（审计）：裁决**消费式**对齐 —— 每份 verdict 至多服务一条引用。
        # 旧实现回退 ``candidates[0]``，同一来源的不同论断（数值/日期/否定词变化）
        # 会继承他人裁决被判通过；现在只有逐字一致（规范化后）或
        # 「数值 + 否定签名一致且高相似」的裁决才可复用，且用后即消费。
        consumed_verdict_ids: set = set()
        verdict_missing_for_citation = 0

        def _take_verdict(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if not fixes_enabled:
                # 基线对照（validator_fixes_enabled=False）：保持 v1.1 语义
                candidates = verdicts.get(record["finding_id"], [])
                if candidates:
                    return candidates[0]
                for j, item in enumerate(unused_verdicts):
                    if item.get("claim", "").strip() == record["claim"].strip():
                        return unused_verdicts.pop(j)
                return None
            pool = (verdicts.get(record["finding_id"], [])
                    if record["finding_id"] else unused_verdicts)
            target = self._norm_claim_text(record["claim"])
            # 1) 规范化后逐字一致（claim / claim_echo 任一命中即为「对应裁决」；
            #    回显错位留给后续 claim_echo 核对判 UNKNOWN，而不是当成缺裁决）
            for item in pool:
                if id(item) in consumed_verdict_ids:
                    continue
                claim_text = str(item.get("claim") or "")
                echo_text = str(item.get("claim_echo") or "")
                if (self._norm_claim_text(claim_text) == target
                        or self._norm_claim_text(echo_text) == target):
                    consumed_verdict_ids.add(id(item))
                    return item
            # 2) 高相似兜底：仅当数值签名与否定签名一致（标点/空白差异可容忍，
            #    数值变更（一百→九百）与否定词增删绝不继承裁决）
            best: Optional[Dict[str, Any]] = None
            best_ratio = 0.0
            for item in pool:
                if id(item) in consumed_verdict_ids:
                    continue
                echoed = str(item.get("claim_echo") or item.get("claim") or "")
                if self._numeric_signature(echoed) != self._numeric_signature(record["claim"]):
                    continue
                if self._negation_signature(echoed) != self._negation_signature(record["claim"]):
                    continue
                ratio = self._claim_similarity(echoed, record["claim"])
                if ratio >= 0.85 and ratio > best_ratio:
                    best, best_ratio = item, ratio
            if best is not None:
                consumed_verdict_ids.add(id(best))
            return best

        result: List[Citation] = []
        evidence_unknown_citations = 0
        for r in local_results:
            if not r["existence"]:
                # 短路：存在性 False 不进 LLM，直接判未通过（Q3=A）
                result.append(Citation(
                    claim=r["claim"], source=r["source"],
                    verified=False, supported=False,
                    finding_id=r["finding_id"], source_type=r["source_type"],
                    confidence=0.0, note=r["note"], existence=False,
                    verified_relaxed=False,
                    claim_start=r.get("claim_start", -1), claim_end=r.get("claim_end", -1),
                ))
                continue

            # R04：origin 链缺失/部分缺失 ⇒ 对应引用判 UNKNOWN（不消费裁决、不判通过）
            origin_key = (str(r["finding_id"]) if r["finding_id"]
                          else f"src:{r['source']}")
            origin_status = evidence_status.get(origin_key, "raw")
            origin_unknown = origin_status in ("missing", "partial")

            verdict: Optional[Dict[str, Any]] = None
            verification_failed = False
            if id(r) in failed_row_ids:
                # 审计 P1#2：LLM 失败 ⇒ 校验未完成（不视为通过）；仅保留存在性结论
                verified, faithful, supported_flag, confidence, note = (
                    False, False, False, 0.5, "LLM 校验失败：校验未完成（不视为通过）")
                verification_failed = True
                # A4（#154）：批调用失败（含预算拒绝）⇒ 未校验是「饥饿」不是「判不过」
                unverified_starved_count += 1
            elif origin_unknown:
                verified, faithful, supported_flag, confidence, note = (
                    False, False, False, 0.5,
                    "原文证据缺失（origin 链未逐项解析）：校验未完成（不视为通过）")
                verification_failed = True
                evidence_unknown_citations += 1
            else:
                verdict = _take_verdict(r)
                if verdict is None:
                    # 没有与论断对应的裁决：不得继承同来源其他论断的通过结论（R01）
                    if fixes_enabled:
                        note = "未获对应裁决（不复用同来源其他论断的 verdict）：校验未完成（不视为通过）"
                    else:
                        note = "忠实度未获 LLM 反馈：校验未完成（不视为通过）"
                    verified, faithful, supported_flag, confidence = False, False, False, 0.5
                    verification_failed = True
                    if fixes_enabled:
                        verdict_missing_for_citation += 1
                else:
                    # W7 F2：claim_echo 回显对齐；不一致 ⇒ 校验未完成（P1#2：不再降级判通过）
                    echo = verdict.get("claim_echo", "") or verdict.get("claim", "")
                    if fixes_enabled and echo and self._claim_similarity(echo, r["claim"]) < 0.6:
                        faithful = False
                        supported_flag = bool(verdict.get("supported", False))
                        confidence = float(verdict.get("confidence", 0.5))
                        note = f"claim_echo 错位（相似度低）：{verdict.get('note', '') or ' verdict 与原文 claim 不匹配'}".strip()
                        verification_failed = True
                    else:
                        faithful = bool(verdict.get("faithful", True))
                        supported_flag = bool(verdict.get("supported", False))
                        confidence = float(verdict.get("confidence", 0.5))
                        note = verdict.get("note", "") or ("" if faithful else "faithful=false 但未附原因")
                    verified = faithful
                    if not verified and not verification_failed:
                        # A4（#154）：拿到裁决且判不忠实 ⇒ 质量信号（判据拒绝）。
                        # echo 错位等「校验未完成」态不计入 —— 那属于第三类，不是判据结论。
                        verified_rejected_count += 1

            # F12（审计）：多源印证的**代码复核** —— LLM 声称 supported 时必须给出真实存在、
            # 且达到 min_sources 个独立来源（不同 source；同文档多分块不算）的证据编号；
            # 不满足则降级 supported=False（应用侧自此可复核 supported=true 的达成依据）。
            citation_supporting: List[str] = []
            citation_contradicting: List[str] = []
            independent_count = 0
            if verdict is not None and fixes_enabled:
                citation_supporting = self._norm_evidence_ids(
                    verdict.get("supporting_evidence_ids"), by_number)
                citation_contradicting = self._norm_evidence_ids(
                    verdict.get("contradicting_evidence_ids"), by_number)
                independent_count = len({by_number[sid].source for sid in citation_supporting})
                if supported_flag:
                    ms_claimed += 1
                if supported_flag and independent_count < min_sources:
                    supported_flag = False
                    note = (f"{note.rstrip('；')}；多源印证未过代码复核"
                            f"（独立来源 {independent_count}/{min_sources}）").strip("；")
                    ms_downgraded += 1
                elif supported_flag:
                    ms_verified += 1

            # W7 TBD-5：宽松口径 = 存在性 AND (忠实 OR 多源印证)；校验未完成不进入宽松通过
            verified_relaxed = (True and (faithful or supported_flag)
                                and not verification_failed)

            result.append(Citation(
                claim=r["claim"], source=r["source"],
                verified=verified,
                supported=supported_flag,
                finding_id=r["finding_id"], source_type=r["source_type"],
                confidence=confidence, note=note, existence=True,
                verified_relaxed=verified_relaxed,
                verification_failed=verification_failed,
                is_meta=bool(verdict.get("is_meta", False)) if verdict is not None else False,
                claim_start=r.get("claim_start", -1), claim_end=r.get("claim_end", -1),
                supporting_evidence_ids=citation_supporting,
                contradicting_evidence_ids=citation_contradicting,
                independent_source_count=independent_count,
            ))
        unconsumed_verdicts = 0
        if fixes_enabled:
            all_verdict_items = list(unused_verdicts) + [
                item for items in verdicts.values() for item in items
            ]
            unconsumed_verdicts = sum(
                1 for item in all_verdict_items if id(item) not in consumed_verdict_ids)
        self.last_validation_stats.update({
            "validated_citation_count": len(result),
            "existence_pass_count": sum(1 for c in result if c.existence),
            # F07（审计）：证据回原文的显式健康度（截断头尾保留 / 原文缺失回落摘要）
            "evidence_truncated_count": evidence_stats.get("truncated", 0),
            "evidence_missing_count": evidence_stats.get("missing", 0),
            # R04（审计）：部分 origin 缺失同样不可判通过（独立计数，不静默）
            "evidence_partial_count": evidence_stats.get("partial", 0),
            "evidence_unknown_citation_count": evidence_unknown_citations,
            # R01（审计）：裁决对齐审计 —— 缺裁决引用数 / 未消费裁决数
            "verdict_missing_for_citation_count": verdict_missing_for_citation,
            "verdict_unused_count": unconsumed_verdicts,
            # F12（审计）：多源印证三口径 —— LLM 声称数 / 代码复核通过数 / 复核降级数
            "multi_source_claimed_count": ms_claimed,
            "multi_source_verified_count": ms_verified,
            "multi_source_downgraded_count": ms_downgraded,
            # A4（#154）：未校验归因拆分 —— 饥饿（预算准入拒绝 / 批调用失败）
            # 与判据拒绝（拿到裁决但判不忠实）必须分开，否则「未校验 N%」无法指导决策。
            "unverified_starved_count": budget_starved_count + unverified_starved_count,
            "verified_rejected_count": verified_rejected_count,
        })
        return result

    @staticmethod
    def _claim_similarity(a: str, b: str) -> float:
        """W7 F2：基于 difflib 的字符级相似度，用于 claim_echo 回显核对。"""
        if not a or not b:
            return 0.0
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a.strip(), b.strip()).ratio()

    #: R01：claim 规范化时剥离的标点/空白/标记字符（保留数字与汉字）
    _CLAIM_PUNCT_RE = re.compile(r"[\s。！？；，,.;:：!?、\"'“”‘’()（）\[\]【】*_`#·]+")
    #: R01：数值签名（阿拉伯数字 + 中文数词连写），用于阻断跨数值的裁决复用
    _NUM_SIG_RE = re.compile(r"\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两]+")
    #: R01：否定词签名，用于阻断跨否定语义的裁决复用
    _NEG_SIG_RE = re.compile(r"[不未无没非勿莫禁]")

    @classmethod
    def _norm_claim_text(cls, text: str) -> str:
        """R01：claim 逐字对齐前的规范化（去标点/空白/装饰符，保留数字与汉字）。"""
        return cls._CLAIM_PUNCT_RE.sub("", text or "")

    @classmethod
    def _numeric_signature(cls, text: str) -> tuple:
        """R01：数值签名 —— 数值序列不同（一百→九百 / 2024→2025）即拒绝复用裁决。"""
        return tuple(cls._NUM_SIG_RE.findall(text or ""))

    @classmethod
    def _negation_signature(cls, text: str) -> tuple:
        """R01：否定词签名 —— 否定语义不同（未/无/不 增删）即拒绝复用裁决。"""
        return tuple(sorted(set(cls._NEG_SIG_RE.findall(text or ""))))

    @staticmethod
    def _norm_evidence_ids(raw: Any, by_number: Dict[str, ResearchFinding]) -> List[str]:
        """F12：规范化支持/反对证据编号 —— 去 ``#`` 前缀、保序去重、只保留清单中真实存在的编号。"""
        out: List[str] = []
        for token in raw if isinstance(raw, (list, tuple)) else []:
            sid = str(token or "").strip().lstrip("#").strip()
            if sid in by_number and sid not in out:
                out.append(sid)
        return out


def config_min_sources(cfg=None) -> int:
    """多源印证阈值（唯一产生点；`cfg` 参数供 prompt 指纹复用，默认取全局 config）。"""
    c = cfg if cfg is not None else config
    return c.research.min_sources_for_crosscheck