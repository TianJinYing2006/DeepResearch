"""W5 七项指标聚合（eval run.py Phase 2 核心，grill Q2/Q8 拍板落地）。

指标清单：
1. 完成率     —— 自动四条件（done + error None + ≥300 字 + 章节 ≥2）
2. 引用准确率 —— 直读 state.citations（主链路 validate 已产 verified/existence，不再重跑 validator）
3. 信息覆盖度 —— LLM 对查（expected_subquestions vs findings 摘要，smart/judge 档 + 60s 超时）
4. 检索命中率 —— 第 7 项：gold_keywords 关键词优先 + 嵌入回退（unique 落点，matched_by 标签）
5. Token 成本 —— 双轨桶（模型名/职责）+ input/output 精确加权（W3 pricing 表）
6. 平均步数   —— len(reflection_log)（critic 决策轮数）
7. 反思有效性 —— critic_stop 占比 + hard_gate 终态判定 + 早停/晚停交叉信号

设计约束（Q2）：一指标一判定器；自动能算的不上 LLM；所有判据对齐实际代码。
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional

from config import config
from research_engine.llm.client import LLMClient, build_messages

# judge 档位 = smart（Q3），直建实例 role="judge" 独立进职责桶（Q2）；60s 治 LLM 层无超时（Q4）
JUDGE_TIMEOUT_S = 60.0
# 检索命中率语义回退阈值（Q8 拍板 0.75，仅此一处合法落点；与引用/覆盖度口径隔离）
SEMANTIC_SIM_THRESHOLD = 0.75

_MIN_REPORT_CHARS = 300  # Q2：samples 实测 480~1100 字，300 为宽松下限只防空转/空报告
_MIN_HEADINGS = 2  # Q2：真实报告恒 4 个 ## 标题，≥2 只防无结构/崩溃空壳
_LATE_STOP_ROUNDS = 5  # Q2：W4 实测 2~4 轮收敛，5 为固定启发式上界（如实声明）


# ---------- 1. 完成率 ----------

def count_markdown_headings(report: str) -> int:
    """统计 markdown 标题数（# 或 ##）。"""
    if not report:
        return 0
    return len(re.findall(r"(?m)^#{1,2}\s+\S.*$", report))


def compute_completion(state: Dict[str, Any]) -> Dict[str, Any]:
    """完成率四条件（Q2 拍板）：done + error None + ≥300 字 + 章节 ≥2。"""
    ok_status = state.get("status") == "done"
    ok_error = state.get("error") is None
    report = state.get("report") or ""
    ok_len = len(report) >= _MIN_REPORT_CHARS
    headings = count_markdown_headings(report)
    ok_structure = headings >= _MIN_HEADINGS
    passed = all([ok_status, ok_error, ok_len, ok_structure])
    return {
        "complete": passed,
        "status": state.get("status", ""),
        "error": state.get("error"),
        "report_chars": len(report),
        "headings": headings,
        "conditions": {
            "status_done": ok_status,
            "error_none": ok_error,
            "report_len_ge_300": ok_len,
            "headings_ge_2": ok_structure,
        },
    }


# ---------- 2. 引用准确率（直读 state.citations）----------

def compute_citation(citations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """基于主链路校验产物统计（Q2/Q8：不再重跑 validator——那是又一次 LLM 对查）。

    口径对齐 W2：verified = 存在且忠实；existence 单独统计；by_source_type 拆分。
    """
    total = len(citations)
    verified = sum(1 for c in citations if c.get("verified"))
    existence = sum(1 for c in citations if c.get("existence"))
    by_type: Dict[str, Dict[str, int]] = {}
    failed_notes: Dict[str, int] = {}
    for c in citations:
        t = c.get("source_type") or "unknown"
        bucket = by_type.setdefault(t, {"total": 0, "verified": 0, "existence": 0})
        bucket["total"] += 1
        bucket["verified"] += 1 if c.get("verified") else 0
        bucket["existence"] += 1 if c.get("existence") else 0
        if not c.get("verified"):
            key = (c.get("note") or "未说明")[:40]
            failed_notes[key] = failed_notes.get(key, 0) + 1
    return {
        "total_citations": total,
        "verified": verified,
        "existence_rate": round(existence / total, 4) if total else 0,
        "fidelity_rate": round(verified / existence, 4) if existence else 0,  # W2 忠实度口径
        "by_source_type": by_type,
        "failed_note_distribution": failed_notes,
    }


# ---------- 3. 信息覆盖度（LLM 对查）----------

_COVERAGE_SYSTEM = (
    "你是评测裁判。给定一组该研究期望回答的子问题，以及系统实际检索到的研究发现，"
    "请逐个子问题判断：研究发现是否足以回答该子问题。"
    '只输出 JSON：{"results": [{"subquestion": str, "covered": bool, "reason": str}]}。'
)


def compute_coverage(
    expected_subquestions: List[str],
    findings: List[Dict[str, Any]],
    judge: Optional[LLMClient] = None,
) -> Dict[str, Any]:
    """覆盖度 LLM 对查（Q2：1 prompt/条，逐子问题判覆盖；smart 档语义判定，零阈值）。"""
    if not expected_subquestions:
        return {"covered_count": 0, "total": 0, "coverage": 0.0, "per_sub": [], "judge_failed": False}
    if not findings:
        return {
            "covered_count": 0, "total": len(expected_subquestions), "coverage": 0.0,
            "per_sub": [{"subquestion": s, "covered": False, "reason": "无任何研究发现"} for s in expected_subquestions],
            "judge_failed": False,
        }
    judge = judge or _make_judge()
    find_summary = "\n".join(
        f"- {f.get('content', '')[:300]}" for f in findings[:40]
    )
    subs_text = "\n".join(f"- {s}" for s in expected_subquestions)
    user = f"期望回答的子问题：\n{subs_text}\n\n系统实际检索到的研究发现：\n{find_summary}\n\n请逐个子问题给出判定。"
    try:
        data = judge.chat_json(build_messages(_COVERAGE_SYSTEM, user), timeout=JUDGE_TIMEOUT_S)
        per_sub = []
        covered = 0
        for item in data.get("results", []):
            ok = bool(item.get("covered"))
            covered += 1 if ok else 0
            per_sub.append({"subquestion": item.get("subquestion", ""), "covered": ok, "reason": item.get("reason", "")})
        return {
            "covered_count": covered,
            "total": len(expected_subquestions),
            "coverage": round(covered / len(expected_subquestions), 4),
            "per_sub": per_sub,
            "judge_failed": False,
        }
    except Exception:  # noqa: BLE001
        # 局部失败（Q4 Level 2）：记录缺失项，由 run.py 标 partial + missing_metrics
        return {
            "covered_count": 0, "total": len(expected_subquestions), "coverage": 0.0,
            "per_sub": [], "judge_failed": True, "error": "coverage judge 超时/失败",
        }


# ---------- 4. 检索命中率（第 7 项：关键词优先 + 嵌入回退）----------

def _embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """调百炼 embedding（text-embedding-v3），分批 ≤10（ingest.py:83 同款上限），失败返回 None。"""
    try:
        from openai import OpenAI

        client = OpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)
        out: List[List[float]] = []
        for i in range(0, len(texts), 10):  # 百炼单次 batch 上限 10，超了报 400
            resp = client.embeddings.create(
                model=config.rag.embedding_model,
                input=texts[i : i + 10],
            )
            out.extend(d.embedding for d in resp.data)
        return out
    except Exception:  # noqa: BLE001
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def compute_retrieval_hit(
    gold_keywords: List[str],
    findings: List[Dict[str, Any]],
    embed_fn: Callable[[List[str]], Optional[List[List[float]]]] = _embed_texts,
) -> Dict[str, Any]:
    """检索命中率（Q8）：关键词优先（精确哨兵）→ 嵌入回退（语义命中 >0.75）。

    matched_by 标签单独报告（keyword / semantic / miss），不混口径。
    """
    hit_text = " ".join((f.get("content") or "") for f in findings)
    per_kw = []
    semantic_kws: List[str] = []
    for kw in gold_keywords:
        if kw and kw in hit_text:
            per_kw.append({"keyword": kw, "matched": True, "matched_by": "keyword"})
        else:
            per_kw.append({"keyword": kw, "matched": False, "matched_by": "miss"})
            semantic_kws.append(kw)

    # 嵌入回退：仅对关键词未命中的条目做语义判定（Q1 语义回退的合法落点，仅此处）
    if semantic_kws and findings and embed_fn:
        pool_texts = [(f.get("content") or "")[:500] for f in findings][:20]  # 池限量防 batch 超限
        texts = [kw for kw in gold_keywords] + pool_texts
        vec_all = embed_fn(texts)  # embed_fn 内部已分批 ≤10
        if vec_all is not None and len(vec_all) == len(texts):
            kw_vecs = vec_all[: len(gold_keywords)]
            pool_vecs = vec_all[len(gold_keywords):]
            for i, kw in enumerate(gold_keywords):
                if per_kw[i]["matched_by"] == "miss":
                    best = max((_cosine(kw_vecs[i], pv) for pv in pool_vecs), default=0.0)
                    if best >= SEMANTIC_SIM_THRESHOLD:
                        per_kw[i]["matched"] = True
                        per_kw[i]["matched_by"] = "semantic"
                        per_kw[i]["semantic_sim"] = round(best, 4)

    hit = sum(1 for k in per_kw if k["matched"])
    return {
        "total_keywords": len(gold_keywords),
        "hit": hit,
        "retrieval_hit_rate": round(hit / len(per_kw), 4) if per_kw else 0.0,
        "matched_by": {
            "keyword": sum(1 for k in per_kw if k["matched_by"] == "keyword"),
            "semantic": sum(1 for k in per_kw if k["matched_by"] == "semantic"),
            "miss": sum(1 for k in per_kw if k["matched_by"] == "miss"),
        },
        "per_keyword": per_kw,
    }


# ---------- 5. Token 成本（双轨 + 精确加权）----------

def compute_cost(
    model_io_stats: Dict[str, Dict[str, int]],
    model_stats: Dict[str, int],
    role_stats: Dict[str, int],
    single_token: Optional[int] = None,
) -> Dict[str, Any]:
    """成本 = Σ (input×input价 + output×output价)（W3 pricing 表精确加权，无近似比例）。

    双模式：
    - 全局（Phase1 收尾类级桶快照）：model_stats/role_stats/io 齐备，双轨报告；
    - 单条兜底（并发下类级桶不可归因）：传 single_token（state.token_used），
      仅给 token 计数，per_model/per_role 为空（如实声明"并发下单条成本不归因"）。
    """
    pricing = config.llm.pricing
    per_model: Dict[str, Dict[str, Any]] = {}
    total_tokens = 0
    total_cost = 0.0
    for model, tokens in (model_stats or {}).items():
        io = (model_io_stats or {}).get(model, {})
        inp = io.get("input", 0)
        out = io.get("output", 0)
        price = pricing.get(model, {})
        cost = (inp / 1000 * price.get("input", 0)) + (out / 1000 * price.get("output", 0))
        total_tokens += tokens
        total_cost += cost
        per_model[model] = {"tokens": tokens, "input": inp, "output": out, "cost": round(cost, 6)}
    # 单条兜底：全局桶为空但给了 single_token → 只报计数（按 smart 均价粗估，属参考值）
    if not per_model and single_token:
        total_tokens = single_token
        price = pricing.get(config.llm.smart_model, {})
        total_cost = single_token / 1000 * (price.get("input", 0) + price.get("output", 0)) / 2
    return {
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "per_model": per_model,
        "per_role": dict(role_stats or {}),
        "single_token_only": bool(not per_model and single_token),
    }


# ---------- 6. 平均步数 ----------

def compute_steps(state: Dict[str, Any]) -> Dict[str, Any]:
    """平均步数 = len(reflection_log)（critic 决策轮数，graph.py:193 每轮追加）。"""
    reflection_log = state.get("reflection_log") or []
    return {"steps": len(reflection_log), "depth": state.get("depth", 0)}


# ---------- 7. 反思有效性 ----------

def compute_reflection(state: Dict[str, Any], coverage: float) -> Dict[str, Any]:
    """结构性口径 + 交叉信号（Q2/TBD-8 拍板，不标期望跳数）。

    - hard_stop：终态任一硬闸触顶（depth / token_used / replan_count）
    - critic_stop：未触顶且 critic_signal == "stop"
    - 早停候选：critic_stop ∧ 覆盖度 < 90%；晚停候选：critic_stop ∧ 轮数 > 5
    """
    rc = config.research
    depth = state.get("depth", 0)
    token_used = state.get("token_used", 0)
    replan_count = state.get("replan_count", 0)
    signal = state.get("critic_signal", "")
    hard_reasons: List[str] = []
    if depth >= rc.max_total_hops:
        hard_reasons.append("max_total_hops")
    if token_used >= rc.token_budget:
        hard_reasons.append("token_budget")
    if replan_count >= rc.max_replan:
        hard_reasons.append("max_replan")
    is_hard = bool(hard_reasons)
    is_critic = (not is_hard) and signal == "stop"
    reflection_log = state.get("reflection_log") or []
    rounds = len(reflection_log)
    early = is_critic and coverage < 0.90
    late = is_critic and rounds > _LATE_STOP_ROUNDS
    return {
        "stop_type": "hard_stop" if is_hard else ("critic_stop" if is_critic else "other"),
        "hard_reasons": hard_reasons,
        "critic_signal": signal,
        "rounds": rounds,
        "early_stop_candidate": early,
        "late_stop_candidate": late,
        "coverage_at_stop": round(coverage, 4),
    }


# ---------- 汇总 ----------

def _make_judge() -> LLMClient:
    """judge 直建实例（Q3：smart 档，role=judge 独立职责桶；不污染 router 主链路桶）。"""
    return LLMClient(model=config.llm.smart_model, role="judge")


def compute_all(
    state: Dict[str, Any],
    dataset_row: Dict[str, Any],
    judge: Optional[LLMClient] = None,
    embed_fn: Callable[[List[str]], Optional[List[List[float]]]] = _embed_texts,
) -> Dict[str, Any]:
    """聚合七项指标（run.py Phase 2 单条入口）。

    state: Phase1 raw 里的 state 快照；dataset_row: 标注行（含 expected_subquestions / gold_keywords）。
    """
    court = judge or _make_judge()
    findings = state.get("findings") or []
    citations = state.get("citations") or []

    coverage_res = compute_coverage(dataset_row.get("expected_subquestions") or [], findings, court)
    coverage = coverage_res.get("coverage", 0.0)

    return {
        "completion": compute_completion(state),
        "citation": compute_citation(citations),
        "coverage": coverage_res,
        "retrieval_hit": compute_retrieval_hit(dataset_row.get("gold_keywords") or [], findings, embed_fn),
        "cost": compute_cost(
            state.get("model_io_stats") or {},
            state.get("model_stats") or {},
            state.get("role_stats") or {},
            single_token=state.get("_token_used_single"),  # 单条兜底（并发不可归因场景）
        ),
        "steps": compute_steps(state),
        "reflection": compute_reflection(state, coverage),
        "raw_complete": state.get("status") == "done",
    }