"""评测侧独立引用裁判（审计 F15）：常规评测中持续执行，与主链路 validator 解耦。

**为什么必须独立**：`compute_citation` 直读主链路 validator 的 verdict —— 修改
validator 判据会同时改变「被测系统」与「评分尺子」（W7 用 2x2 矩阵实测纯裁判效应
可达数 pp）。W7 的 :mod:`research_engine.eval.w7_rejudge` 是**冻结证据的离线复判
实验**；本模块把同一严格口径搬进常规评测（Phase 2），每道题持续执行。

**口径（与 W7 严格口径一致）**：

- existence（本地判定）与 fidelity（裁判判定）分离；
- 未获裁决（no_verdict）**不计通过**（严格口径），另报 ``no_verdict_rate``；
- 裁判调用失败 ⇒ ``llm_failed=True``（该题独立口径显式失败，不静默、不冒充通过）。
  **A1（2026-10-07）**：超时/网络异常先重试（共 2 次尝试、默认 120s）；仍失败才显式失败
  —— 10·07 基线实测单次 50 条引用的裁判请求在 60s 上限下 30% 超时；
- 证据按 F08 三层口径组装：编号基准 = 工作摘要层（与 Writer/Validator 同一份），
  正文按 ``origin_evidence_ids`` 回**原文层**（缺 origin 链时用自身正文）。

与主链路的关系：``citation``（主链路 verdict）保留为对照口径；
``citation_judge``（本模块）是**可比、可复算**的独立口径，二者差值
（``end_to_end_delta_vs_main_link``）即「裁判效应」的持续可见量。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from research_engine.agents.validator import config_min_sources
from research_engine.evidence import build_evidence_index, resolve_evidence_text
from research_engine.llm.client import LLMClient
from research_engine.state import ResearchFinding

#: 独立裁判单次调用默认超时（A1，2026-10-07）。证据正文 + 约 50 条引用的 JSON 输出
#: 在模型高负载时会超过 60s（10·07 基线实测 18/60 条超时）；配合 exception 重试，
#: 超时不再直接判 ``llm_failed``。
DEFAULT_JUDGE_TIMEOUT_S = 120.0

#: 独立裁判 system 提示词（唯一产生点；idx 对齐，不做 claim_echo 逐字回显——
#: 那是主链路 validator 的修复开关，独立裁判保持稳定判据）。
JUDGE_SYSTEM = """你是研究事实核查员（独立裁判）。你的任务是校验报告中的论断与引用。

请以 JSON 格式输出校验结果：
{{
  "citations": [
    {{
      "idx": 输入条目的序号（整数，原样回填）,
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
- is_meta: 该论断/来源是否在描述 DeepResearch 系统自身（自指/元描述）——是则 true
- confidence: 综合置信度 0-1
- note: 说明；faithful=false 时必须给出具体原因

重要：输出必须为每条输入条目给出一条结果，`idx` 原样回填输入序号，不要遗漏或多出。
"""


def build_citation_judge_system(cfg=None) -> str:
    """独立引用裁判 system 提示词的唯一产生点（W8 Arm 6 口径：登记进 prompt_hash 槽位）。"""
    return JUDGE_SYSTEM.format(min_sources=config_min_sources(cfg))


class CitationJudgeItem(BaseModel):
    """单条引用的独立裁判 verdict（按 idx 对齐）。"""

    idx: int = Field(description="输入条目序号，原样回填")
    faithful: bool = Field(description="论断是否能直接由该 finding 内容推断")
    supported: bool = Field(default=False, description="是否通过多源印证")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="校验置信度 0-1")
    is_meta: bool = Field(default=False, description="是否自指/元描述")
    note: str = Field(default="", description="说明；faithful=false 时必须写原因")


class CitationJudgeVerdict(BaseModel):
    citations: List[CitationJudgeItem] = Field(default_factory=list)


def _as_finding(item: Any) -> Optional[ResearchFinding]:
    """raw state 里 findings 是 dict；测试可能传对象——两种都吃。"""
    if isinstance(item, ResearchFinding):
        return item
    try:
        return ResearchFinding.model_validate(item)
    except Exception:  # noqa: BLE001 —— 脏数据不阻断评测，按缺证据处理
        return None


def _build_findings_text(
    working: List[ResearchFinding],
    evidence_index: Dict[str, ResearchFinding],
    to_check: List[Dict[str, Any]],
) -> str:
    """只喂被引用（且存在性通过）的证据；编号保留工作层原编号，正文回原文层。"""
    source_to_ids: Dict[str, List[str]] = {}
    for i, f in enumerate(working, 1):
        source_to_ids.setdefault(f.source, []).append(str(i))
    used_ids = set()
    for c in to_check:
        fid = c.get("finding_id")
        if fid:
            used_ids.add(str(fid))
        elif c.get("source"):
            used_ids.update(source_to_ids.get(c["source"], []))

    lines: List[str] = []
    for i, f in enumerate(working, 1):
        if str(i) not in used_ids:
            continue
        text, _status = resolve_evidence_text(f, evidence_index)
        lines.append(f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {text}")
    if not lines:  # 安全阀：used_ids 与编号无交集（URL 协议等）→ 全量
        for i, f in enumerate(working, 1):
            text, _status = resolve_evidence_text(f, evidence_index)
            lines.append(f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {text}")
    return "\n".join(lines)


def _ask(
    judge: LLMClient,
    findings_text: str,
    items: List[Tuple[int, Dict[str, Any]]],
    timeout: float,
) -> Dict[int, Dict[str, Any]]:
    """一次裁判调用；返回 ``{to_check 下标: verdict}``（按 idx 对齐）。"""
    citations_json = "\n".join(
        f"- idx: {k} | finding_id: {c.get('finding_id', '')} | "
        f"claim: {c.get('claim', '')} | source: {c.get('source', '')}"
        for k, c in items
    )
    user = (
        f"研究发现：\n{findings_text}\n\n"
        f"待校验引用（仅列存在性已通过的）：\n{citations_json}\n\n"
        "请输出校验结果，每条输入的 idx 都要有对应结果。"
    )
    data = judge.chat_json(
        [
            {"role": "system", "content": build_citation_judge_system()},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        schema=CitationJudgeVerdict,
        timeout=timeout,
    )
    by_idx: Dict[int, Dict[str, Any]] = {}
    for item in data.get("citations", []) if isinstance(data, dict) else []:
        try:
            by_idx[int(item.get("idx"))] = item
        except (TypeError, ValueError):
            continue
    return {k: by_idx[k] for k, _ in items if k in by_idx}


def _empty_result(total: int, existence: int, *, llm_failed: bool,
                  error: str = "") -> Dict[str, Any]:
    return {
        "total": total, "existence": existence, "passed": None if llm_failed else 0,
        "no_verdict": existence if llm_failed else 0, "llm_failed": llm_failed,
        "existence_rate": round(existence / total, 4) if total else 0.0,
        "fidelity_rate": None if llm_failed else 0.0,
        "end_to_end_pass_rate": None if llm_failed else 0.0,
        "no_verdict_rate": None if llm_failed else 0.0,
        "independent": True,
        "error": error,
    }


def judge_citations(
    citations: List[Dict[str, Any]],
    working_findings: List[Any],
    evidence_findings: Optional[List[Any]] = None,
    judge: Optional[LLMClient] = None,
    *,
    max_retry: int = 1,
    timeout: float = DEFAULT_JUDGE_TIMEOUT_S,
) -> Dict[str, Any]:
    """对一道题的引用集跑独立裁判；返回严格口径指标（见模块 docstring）。

    - ``working_findings``：编号协议基准（Writer/Validator 同一份；可为 dict/对象）；
    - ``evidence_findings``：原文层（可选；缺省等于 working）；
    - ``judge``：裁判客户端（可注入；缺省 smart 档 role=judge 直建）。
    """
    if judge is None:
        from research_engine.eval.metrics import judge_model_name

        judge = LLMClient(model=judge_model_name(), role="judge")

    total = len(citations)
    to_check = [c for c in citations if c.get("existence")]
    if total == 0:
        return _empty_result(0, 0, llm_failed=False)
    if not to_check:
        return _empty_result(total, 0, llm_failed=False)

    working = [f for f in (_as_finding(item) for item in working_findings) if f is not None]
    raw_items = evidence_findings if evidence_findings is not None else working_findings
    evidence = [f for f in (_as_finding(item) for item in raw_items) if f is not None]
    evidence_index = build_evidence_index(evidence)
    findings_text = _build_findings_text(working, evidence_index, to_check)

    verdict_of: Dict[int, Dict[str, Any]] = {}
    pending = list(enumerate(to_check))
    rounds = 0
    last_error = ""
    while pending and rounds <= max_retry:
        try:
            got = _ask(judge, findings_text, pending, timeout)
        except Exception as exc:  # noqa: BLE001 —— A1：超时/网络先重试，耗尽后显式失败
            last_error = f"{type(exc).__name__}: {exc}"
            rounds += 1
            continue
        if not got:
            break
        before = len(pending)
        verdict_of.update(got)
        pending = [(k, c) for k, c in pending if k not in got]
        rounds += 1
        if len(pending) == before:  # 无进展，避免死循环
            break
    if not verdict_of:  # 独立口径显式失败，不静默
        return _empty_result(total, len(to_check), llm_failed=True,
                             error=(last_error or "裁判未返回任何可对齐的裁决")[:300])

    passed = 0
    no_verdict = 0
    for k in range(len(to_check)):
        verdict = verdict_of.get(k)
        if verdict is None:
            no_verdict += 1
            continue  # 严格口径：未获裁决不计通过
        if bool(verdict.get("faithful")):
            passed += 1

    existence = len(to_check)
    return {
        "total": total,
        "existence": existence,
        "passed": passed,
        "no_verdict": no_verdict,
        "llm_failed": False,
        "existence_rate": round(existence / total, 4) if total else 0.0,
        "fidelity_rate": round(passed / existence, 4) if existence else 0.0,
        "end_to_end_pass_rate": round(passed / total, 4) if total else 0.0,
        "no_verdict_rate": round(no_verdict / existence, 4) if existence else 0.0,
        "independent": True,
        "retry_rounds": rounds,
        "error": "",
    }


def compute_citation_judge(state: Dict[str, Any], judge: Optional[LLMClient] = None,
                           *, timeout: float = DEFAULT_JUDGE_TIMEOUT_S) -> Dict[str, Any]:
    """``compute_all`` 入口：从 raw state 取引用/工作层/原文层并跑独立裁判。"""
    citations = state.get("citations") or []
    working = state.get("working_findings") or state.get("findings") or []
    evidence = state.get("findings") or []
    return judge_citations(citations, working, evidence, judge, timeout=timeout)
