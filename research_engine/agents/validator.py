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

import re
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from config import config
from research_engine.llm.client import LLMClient
from research_engine.state import Citation, ResearchFinding

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
      "confidence": 0.0-1.0,
      "is_meta": true/false,
      "note": "说明"
    }}
  ]
}}

判定规则：
- faithful: 论断是否能**直接由该 finding 内容推断**（不夸大、不曲解、不张冠李戴；包含明确数值/日期/名称的算术推断视为忠实）
- supported: 论断是否被至少 {min_sources} 个独立来源支持（多源印证）
- is_meta: 该论断/来源是否在描述 DeepResearch 系统自身（自指/元描述，如"本系统""本Agent""Planner→Researcher→Writer→Validator"四节点编排等）——是则 true
- confidence: 综合置信度 0-1
- note: 说明；faithful=false 时必须给出具体原因

重要：输出中的 `claim_echo` 必须逐字回显输入里对应条目的 claim 原文（word-by-word），用于本地核对；不要改写或缩写。
"""


class CitationVerdictItem(BaseModel):
    """单条引用的 LLM 校验 verdict（Q3=A 按 finding_id 对齐，Q5=A 增 is_meta 复核）。"""

    finding_id: str = Field(description="引用编号；非数字来源可传空字符串")
    claim: str = Field(description="论断原文")
    claim_echo: str = Field(default="", description="W7 回显字段：必须逐字回显原 claim（word-by-word），本地核对用")
    faithful: bool = Field(description="论断是否忠实于该 finding 内容")
    supported: bool = Field(default=False, description="是否通过多源印证")
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

    def _build_index(self, findings: List[ResearchFinding]) -> Dict[str, Tuple[str, str]]:
        """建立编号 -> (真实来源, 来源类型) 映射（Q2=A：带出 source_type，编号供 finding_id 锚定）。"""
        return {str(i): (f.source, f.source_type) for i, f in enumerate(findings, 1)}

    @staticmethod
    def _build_findings_text(
        findings: List[ResearchFinding], to_check: List[Dict[str, Any]]
    ) -> tuple[str, bool]:
        """W7 Arm5 A′：只喂被引用且存在性通过的 findings，保留原编号。

        返回 (text, trimmed)。
        三条硬约束：
        1. 阶段 1 存在性校验仍基于全量 index（本函数不改变 index）。
        2. 编号保留原编号（用 enumerate(findings, 1) 的原始 i 过滤，不对子集重排）。
        3. 安全阀：to_check 非空但 used_ids 与 findings 编号无交集 → 降级全量并 warn。
        """
        import warnings

        # W7 Arm5：喂料裁剪可通过 VALIDATOR_TRIM_ENABLED 关闭（TBD-8 基线对照）
        if not config.experiment.validator_trim_enabled:
            lines = [
                f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {f.content[:500]}"
                for i, f in enumerate(findings, 1)
            ]
            return "\n".join(lines), False

        # Bug-5 修复：URL 格式引用的 finding_id 为空，通过 source 字段反查 finding 编号
        # 设计-3 修复：同 source 多 findings（RAG 同 URL 多分块）时收集所有编号
        source_to_ids: Dict[str, List[str]] = {}
        for i, f in enumerate(findings, 1):
            source_to_ids.setdefault(f.source, []).append(str(i))
        used_ids = set()
        for r in to_check:
            fid = r.get("finding_id")
            if fid:
                used_ids.add(str(fid))
            elif r.get("source"):
                # URL 格式引用：通过 source 字段反查所有对应编号
                for mapped in source_to_ids.get(r["source"], []):
                    used_ids.add(mapped)
        trimmed = bool(used_ids)

        if trimmed:
            lines = [
                f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {f.content[:500]}"
                for i, f in enumerate(findings, 1)
                if str(i) in used_ids
            ]
            if lines:
                return "\n".join(lines), True
            # 安全阀：used_ids 非空但与 findings 编号无交集（理论上不发生）→ 降级全量
            warnings.warn(
                "Validator feed trim: used_ids 与 findings 编号无交集，降级为全量喂料",
                stacklevel=2,
            )

        # 未触发裁剪或触发安全阀：回退全量
        lines = [
            f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {f.content[:500]}"
            for i, f in enumerate(findings, 1)
        ]
        return "\n".join(lines), False

    _CLAIM_SEP = "。！？；\n"          # 句子边界字符（W2.1）
    _CLAIM_MAX = 200                  # 引用前最多取 200 字符的窗口（W2.1）

    def _claim_text(self, report: str, end: int, start: int = 0) -> str:
        """取引用前的一段文本做 claim（W2.1 待办 + W7 F4：保留上下文/主语兜底）。

        - start 用于隔离多个引用：只取上一个引用结束位置到当前引用之间的文本；
        - 优先对齐最近句子边界（。！？；\n），避免从单词中间硬截断；
        - 若最近句子边界切出的片段以"其/该/此/这"等代词开头，
          前补前一句完整内容，使论断自包含（RAGAS "self-contained" 精神，W7 F4）；
        - 退化为 200 字符窗口；再退化 80 字符（保原行为兜底）；
        - 清理行内 markdown 残留（** ` # 行首 - | 等），纯展示层，不影响校验。
        """
        window_start = max(start, end - self._CLAIM_MAX)
        window = report[window_start:end]
        rel = max(window.rfind(c) for c in self._CLAIM_SEP)
        # claim 在 report 中的真实起始位置，用于 F4 主语兜底时精确截取前一句
        claim_start_in_report = window_start + rel + 1 if rel >= 0 else window_start
        claim = window[rel + 1:] if rel >= 0 else window
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
        return claim

    def _extract_citations(self, report: str) -> List[dict]:
        """从报告中提取 [来源: N] 形式的引用（W7 F3：过滤非论断句）。

        支持多编号引用：[来源: 5, 72, 77] 会被拆分为 3 条独立引用，
        每条单独校验（见 ADR-0005）。仅当引用内容全部为数字 token 时
        才拆分；含非数字内容时视为单一来源字符串原样保留，保持对
        [来源: <真实URL>] 协议的兼容。
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
        for m in re.finditer(pattern, report):
            # 取论断（引用前的一段文本，W2.1 按句子边界 + 清理 markdown）
            # W7 F3：start=last_end 隔离多个引用，避免后一个 claim 混入前一个引用标记
            claim = self._claim_text(report, m.start(), last_end)
            last_end = m.end()
            refs = self._split_ref(m.group(1))
            raw_citation_count += len(refs)
            if filter_enabled and not self._is_assertive(claim):
                filtered_citation_count += len(refs)
                continue  # W7 F3: non-assertive fragments stay out of validation
            for ref in refs:
                citations.append({"claim": claim, "source": ref})
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

    def validate(self, report: str, findings: List[ResearchFinding], state: Any = None) -> List[Citation]:
        """校验报告引用。

        双段式（Q3=A）：
        1. 本地存在性（无 LLM）：编号映射到真实来源 + 来源存在性判定，存在性 False 短路；
        2. LLM 忠实度：仅存在性 True 的引用进 LLM，判定"论断是否忠实于被引 finding 内容"；
           verified = 存在性 AND 忠实度，输出按 finding_id 对齐。
        """
        extracted = self._extract_citations(report)
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
            })
            return []

        # ---- 阶段 2：LLM 忠实度判定（仅存在性 True 的引用；Q3=A 短路）----
        to_check = [r for r in local_results if r["existence"]]
        # W7 Arm5 A′：只喂被引用且存在性通过的 findings，保留原编号；存在性校验仍用全量 index
        findings_text, _ = self._build_findings_text(findings, to_check)
        # W7 F1：claim 不再截断；F2：LLM 输出需 claim_echo 回显原文
        fixes_enabled = config.experiment.validator_fixes_enabled
        if fixes_enabled:
            claim_field = "claim"
        else:
            # 基线对照：恢复 v1.1 的 100 字符截断（W7 F1 修复关闭）
            claim_field = "claim_truncated"
            for r in to_check:
                r["claim_truncated"] = r["claim"][:100]
        citations_json = "\n".join(
            f"- finding_id: {r['finding_id']} | claim: {r[claim_field]} | source: {r['source']}"
            for r in to_check
        )
        system_prompt = VALIDATOR_SYSTEM if fixes_enabled else VALIDATOR_SYSTEM_LEGACY
        user = (
            f"研究发现：\n{findings_text}\n\n"
            f"待校验引用（仅列存在性已通过的）：\n{citations_json}\n\n"
            "请输出校验结果。"
        )
        if fixes_enabled:
            user += "对每条引用，`claim_echo` 字段必须逐字回显上面的 claim 原文。"

        verdicts: Dict[str, List[Dict[str, Any]]] = {}
        unused_verdicts: List[Dict[str, Any]] = []
        llm_failed = False
        if to_check:  # 全部存在性失败时零 LLM 调用（Q3 短路完整落地）
            try:
                # 与 critic.py 同款：Pydantic schema + 纠错重试，防漏 key 静默默认
                # W5（Q2）：role="validator" 进职责桶（直建实例不传 state 的漏计由类级差值补全）
                client = LLMClient(model=config.llm.validator_model, role="validator")
                data = client.chat_json(
                    [
                        {"role": "system", "content": system_prompt.format(min_sources=config_min_sources())},
                        {"role": "user", "content": user},
                    ],
                    state=state,
                    schema=CitationVerdict,
                )
                # Q3=A：按 finding_id 对齐，不再按序 zip
                # Bug-7 修复：同编号多论断共享同一 verdict → 改用 list 存储，按 claim 匹配
                for item in data.get("citations", []):
                    fid = item.get("finding_id", "")
                    if fid and fid in {r["finding_id"] for r in to_check}:
                        verdicts.setdefault(fid, []).append(item)
                    else:
                        unused_verdicts.append(item)
            except Exception:  # noqa: BLE001
                # 降级：仅返回存在性校验结果（保 W1 行为）
                llm_failed = True

        result: List[Citation] = []
        for r in local_results:
            if not r["existence"]:
                # 短路：存在性 False 不进 LLM，直接判未通过（Q3=A）
                result.append(Citation(
                    claim=r["claim"], source=r["source"],
                    verified=False, supported=False,
                    finding_id=r["finding_id"], source_type=r["source_type"],
                    confidence=0.0, note=r["note"], existence=False,
                    verified_relaxed=False,
                ))
                continue

            verdict = None
            candidates = verdicts.get(r["finding_id"], [])
            if candidates:
                # Bug-7 修复：按 claim 文本精确匹配，避免同编号论断共享 verdict
                for v in candidates:
                    if v.get("claim", "").strip() == r["claim"].strip():
                        verdict = v
                        break
                if verdict is None:
                    verdict = candidates[0]
            if verdict is None:
                # 空 finding_id（URL 协议）或 LLM 未按 id 返回：用未消费 verdict 按 claim 兜底匹配
                for j, item in enumerate(unused_verdicts):
                    if item.get("claim", "").strip() == r["claim"].strip():
                        verdict = item
                        unused_verdicts.pop(j)
                        break

            unreliable = False
            if llm_failed:
                # 整体降级：忠实度未知，按存在性通过（保 W1 行为）
                verified, faithful, supported_flag, confidence, note = True, True, False, 0.5, "LLM 校验失败，降级为存在性判定"
            elif verdict is not None:
                fixes_enabled = config.experiment.validator_fixes_enabled
                # W7 F2：claim_echo 回显对齐；不一致标 unreliable 并降级通过
                echo = verdict.get("claim_echo", "") or verdict.get("claim", "")
                if fixes_enabled and echo and self._claim_similarity(echo, r["claim"]) < 0.6:
                    unreliable = True
                    faithful = False
                    supported_flag = bool(verdict.get("supported", False))
                    confidence = float(verdict.get("confidence", 0.5))
                    note = f"claim_echo 错位（相似度低）：{verdict.get('note', '') or ' verdict 与原文 claim 不匹配'}".strip()
                else:
                    faithful = bool(verdict.get("faithful", True))
                    supported_flag = bool(verdict.get("supported", False))
                    confidence = float(verdict.get("confidence", 0.5))
                    note = verdict.get("note", "") or ("" if faithful else "faithful=false 但未附原因")
                verified = faithful
                if unreliable:
                    verified = True  # 降级为保守通过，但 note 留痕
            else:
                # 单条缺失：来源存在但未获 LLM 反馈 → 保守通过 + 提示（贴近 W1 行为）
                verified, faithful, supported_flag, confidence, note = True, True, False, 0.5, "忠实度未获 LLM 反馈（按存在性通过）"

            # W7 TBD-5：宽松口径 = 存在性 AND (忠实 OR 多源印证 OR 保守通过)
            verified_relaxed = True and (faithful or supported_flag or verified)

            result.append(Citation(
                claim=r["claim"], source=r["source"],
                verified=verified,
                supported=supported_flag,
                finding_id=r["finding_id"], source_type=r["source_type"],
                confidence=confidence, note=note, existence=True,
                verified_relaxed=verified_relaxed,
                is_meta=bool(verdict.get("is_meta", False)) if verdict is not None else False,
            ))
        self.last_validation_stats.update({
            "validated_citation_count": len(result),
            "existence_pass_count": sum(1 for c in result if c.existence),
        })
        return result

    @staticmethod
    def _claim_similarity(a: str, b: str) -> float:
        """W7 F2：基于 difflib 的字符级相似度，用于 claim_echo 回显核对。"""
        if not a or not b:
            return 0.0
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a.strip(), b.strip()).ratio()


def config_min_sources() -> int:
    return config.research.min_sources_for_crosscheck