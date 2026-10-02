"""术语歧义 / 偏题检测（需求 14 / #73）。

背景：搜索引擎会对缩写做拼写纠错 / 联想（内测实测 JEV→EVA），检索来源整体偏题，
但流程正常完成、引用校验照常「严格通过」。本模块提供**确定性（零 LLM）**的术语命中
检测，供渲染层产出机制化警示（不再依赖模型自发提示）。

口径（保守，只在确凿时告警）：
- 语料 = findings 内容 + visited_sources（检索实际拿到的材料）；无语料不判定；
- 术语 = ASCII 词（≥2 字符、去停用词）+ 主题内 CJK 二元组（去重、保序）；
- **强信号**：主题中的全大写缩写（如 JEV）在语料 **0 命中**；
- **弱信号**：整体命中率 < 阈值（``DR_TERM_HIT_MIN``，默认 0.35）；
- 只影响展示文本：不改 ``run_status``、不写 ``degradation_log``（不污染故障归因统计）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List

_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

#: 英文停用词（最小集；避免把 the/of 这类词计入术语命中率）
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "about", "over",
    "are", "was", "were", "will", "would", "can", "could", "should", "does", "did",
    "not", "but", "its", "their", "they", "you", "your", "our", "how", "what", "why",
    "when", "where", "which", "who", "whom", "has", "have", "had", "been", "being",
    "impact", "effect", "analysis", "research", "study", "report",
}


def drift_threshold() -> float:
    """命中率阈值（默认 0.35）；环境变量可调，用于基线标定。"""
    try:
        return float(os.getenv("DR_TERM_HIT_MIN", "0.35"))
    except ValueError:
        return 0.35


@dataclass
class TermDriftReport:
    """术语偏离检测结果（纯数据，便于单测与展示层消费）。"""

    terms: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    missing_acronyms: List[str] = field(default_factory=list)
    ratio: float = 1.0
    suspicious: bool = False
    reason: str = ""


def extract_terms(topic: str) -> List[str]:
    """提取主题术语：ASCII 词（去停用词）+ CJK 二元组（去重、保序）。"""
    terms: List[str] = []
    seen: set = set()

    def push(term: str) -> None:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            terms.append(term)

    for match in _ASCII_WORD.finditer(topic or ""):
        word = match.group(0)
        if word.lower() in _STOPWORDS:
            continue
        push(word)
    for run in _CJK_RUN.findall(topic or ""):
        for i in range(len(run) - 1):
            push(run[i:i + 2])
    return terms


def analyze_term_drift(topic: str, corpus: Iterable[str]) -> TermDriftReport:
    """检测主题术语在语料中的命中情况，返回结构化结果。"""
    joined = "\n".join(str(item or "") for item in corpus).lower()
    report = TermDriftReport()
    if not joined.strip():
        report.reason = "no_corpus"
        return report

    terms = extract_terms(topic)
    report.terms = terms
    if not terms:
        report.reason = "no_terms"
        return report

    report.missing = [t for t in terms if t.lower() not in joined]
    report.ratio = round((len(terms) - len(report.missing)) / len(terms), 4)

    acronyms = [t for t in terms
                if t.isascii() and len(t) >= 2 and t.isupper()
                and any(ch.isalpha() for ch in t)]
    report.missing_acronyms = [t for t in acronyms if t.lower() not in joined]

    if report.missing_acronyms:
        report.suspicious = True
        report.reason = "acronym_missing"
    elif report.ratio < drift_threshold():
        report.suspicious = True
        report.reason = "low_hit_ratio"
    return report
