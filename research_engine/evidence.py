"""证据身份与分层（审计 F07/F08）。

graph._write 之后的三层契约：

- **原文层** ``state.findings``：append-only、只增不改；每条带稳定 ``evidence_id``
  （内容寻址：sha256(source + "\\n" + content) 前 16 位）与 ``content_hash``、``retrieved_at``；
- **工作摘要层** ``state.working_findings``：``ContextManager.compress`` 产物，
  Writer/Validator/Render 的编号协议（Finding N）基准；摘要 finding 的
  ``metadata["origin_evidence_ids"]`` 记录它压缩自哪些原文证据；
- **报告引用层** ``state.citations``：Writer 用 ``[来源: N]`` 引用工作层编号。

Validator 校验忠实度时按 origin 链回到**原文层**（而非只读摘要）；预算不足时
显式标记截断/缺失并计入 stats（不静默截断后继续做事实裁决）。
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from research_engine.state import ResearchFinding

#: 单条工作证据喂给 Validator 的原文上限（超出做头尾保留 + 显式标记）
EVIDENCE_TEXT_MAX_CHARS = 4000
#: 头尾保留时的显式截断标记（进 LLM 输入，模型可见）
EVIDENCE_TRUNCATION_MARK = "\n…[证据截断]…\n"


def compute_content_hash(content: str) -> str:
    """正文 sha256（十六进制全量）——原文完整性/版本复核用。"""
    return hashlib.sha256((content or "").encode()).hexdigest()


def compute_evidence_id(source: str, content: str) -> str:
    """稳定证据 ID（内容寻址）：``ev_`` + sha256(source + "\\n" + content) 前 16 位。"""
    payload = f"{source or ''}\n{content or ''}".encode()
    return "ev_" + hashlib.sha256(payload).hexdigest()[:16]


def ensure_evidence_identity(findings: List[ResearchFinding]) -> int:
    """补齐缺失的证据身份（就地写入；返回补齐条数）。

    只写空字段 ⇒ 重复调用幂等；相同 ``(source, content)`` 恒定得到同一 ID，
    跨进程/回放/导出均可复核。
    """
    filled = 0
    for f in findings:
        changed = False
        if not getattr(f, "evidence_id", ""):
            f.evidence_id = compute_evidence_id(f.source, f.content)
            changed = True
        if not getattr(f, "content_hash", ""):
            f.content_hash = compute_content_hash(f.content)
            changed = True
        if not getattr(f, "retrieved_at", 0.0):
            f.retrieved_at = time.time()
            changed = True
        if changed:
            filled += 1
    return filled


def dedupe_new_findings(
    new_findings: List[ResearchFinding],
    existing_findings: List[ResearchFinding],
) -> Tuple[List[ResearchFinding], Dict[str, int]]:
    """F10（审计）：按**证据身份** ``(evidence_id, sq_id)`` 去重新发现。

    旧行为：graph 层无条件追加 new_findings，同一片段被反复检索即反复累积
    （发现条数虚增、上下文被重复材料挤占）。按 URL 去重又会误删同文档不同
    chunk —— 内容寻址的 ``evidence_id`` 恰好区分「同一片段」与「同文档不同片段」。

    规则：

    - 同一 ``(evidence_id, sq_id)`` 只保留首个（**跨跳**与**跳内**皆然）；
    - **跨子问题保留**：同一片段对不同子问题有归属意义（``sq_id`` 不同 ⇒ 不判重）；
    - 无 ``evidence_id``（旧数据/异常路径）**保守保留**，不误删。

    返回 ``(kept, stats)``；stats = ``{considered, kept, dropped_duplicates}``，
    供 graph 记录本跳新证据率（Critic 停止/换查询的参考信号）。
    """
    seen = {(f.evidence_id, f.sq_id) for f in existing_findings if f.evidence_id}
    kept: List[ResearchFinding] = []
    dropped = 0
    for f in new_findings:
        key = (getattr(f, "evidence_id", ""), getattr(f, "sq_id", ""))
        if key[0]:
            if key in seen:
                dropped += 1
                continue
            seen.add(key)  # 跳内同步更新（同一批重复同样只留首个）
        kept.append(f)
    return kept, {
        "considered": len(new_findings),
        "kept": len(kept),
        "dropped_duplicates": dropped,
    }


def build_evidence_index(findings: List[ResearchFinding]) -> Dict[str, ResearchFinding]:
    """``evidence_id → 原文 finding`` 索引；空 ID 现场补齐，保证可查。"""
    index: Dict[str, ResearchFinding] = {}
    for f in findings:
        eid = getattr(f, "evidence_id", "")
        if not eid:
            eid = compute_evidence_id(f.source, f.content)
            f.evidence_id = eid
        index.setdefault(eid, f)
    return index


def resolve_evidence_text(
    working: ResearchFinding,
    evidence_index: Dict[str, ResearchFinding],
    max_chars: Optional[int] = None,
) -> Tuple[str, str]:
    """取工作证据对应的原文文本；返回 ``(text, status)``。

    status ∈ ``"raw"``（原文直出）/ ``"truncated"``（超预算头尾保留 + 标记）/
    ``"missing"``（声明的原文证据**全部**缺失）/ ``"partial"``（声明的原文证据**部分**
    缺失）。R04（审计）：origin 链必须**逐项**解析——``missing`` / ``partial`` 均不得
    静默：调用方（Validator）拒绝把工作摘要当原始事实依据，对应引用判 UNKNOWN。
    无 origin 链（未压缩路径）时以工作 finding 自身正文为原文。
    """
    if max_chars is None:
        max_chars = EVIDENCE_TEXT_MAX_CHARS
    origin_ids = list((getattr(working, "metadata", None) or {}).get("origin_evidence_ids") or [])
    if not origin_ids:
        text = working.content or ""
        status = "raw"
    else:
        parts = [evidence_index[eid].content for eid in origin_ids if eid in evidence_index]
        missing_count = sum(1 for eid in origin_ids if eid not in evidence_index)
        if not parts:
            # 声明的 origin 一个都解不出来：回落摘要仅供展示，语义上不可作为核验依据
            return working.content or "", "missing"
        text = "\n---\n".join(parts)
        status = "partial" if missing_count else "raw"
    if len(text) > max_chars:
        head = max(1, int(max_chars * 0.75))
        tail = max(0, max_chars - head)
        text = text[:head] + EVIDENCE_TRUNCATION_MARK + (text[-tail:] if tail else "")
        if status == "raw":
            status = "truncated"
    return text, status


def serialize_evidence_index(findings: List[ResearchFinding]) -> List[Dict[str, Any]]:
    """轻量证据索引（任务结果/导出用）：不含正文，含 hash/定位/时间——可复核证据链。"""
    rows: List[Dict[str, Any]] = []
    for f in findings:
        meta = f.metadata or {}
        rows.append({
            "evidence_id": getattr(f, "evidence_id", "") or compute_evidence_id(f.source, f.content),
            "content_hash": getattr(f, "content_hash", "") or compute_content_hash(f.content),
            "source": f.source,
            "source_type": f.source_type,
            "sq_id": getattr(f, "sq_id", ""),
            "confidence": f.confidence,
            "retrieved_at": getattr(f, "retrieved_at", 0.0),
            "locator": meta.get("locator") or {},
        })
    return rows
