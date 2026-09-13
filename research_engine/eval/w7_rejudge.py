"""W7 引用准确率「独立复判」实验：分离「裁判效应」与「arm 效应」，并把引用指标拆成多口径。

背景（2026-09-12 复查发现的硬伤）：
    metrics.py:5 的 citation_accuracy 直读 state.citations，即主链路 validator
    自己的裁决（不重跑独立裁判）；而 validator.py:355 用 config.llm.validator_model，
    arm6 把它换成了 qwen-turbo。=> 「被测对象兼任裁判」，且裁判随 arm 换人。

本脚本冻结检索与生成产物（raw/q_*.raw.json 里的 state.citations / state.findings），
只重跑「忠实度判定」这一步，用 2x2 矩阵把效应拆开：

                    裁判=qwen-plus      裁判=qwen-turbo
    arm0 引用集          A                   B
    arm6 引用集          C                   D

    - 裁判效应（同引用集、只换裁判）：B - A
    - arm 效应（同裁判下比较）      ：C - A

口径拆分（2026-09-12 升级，回应「passed/total 混了两个因素」）：
    生产 metrics.py 同时维护 existence_rate = existence/total 与
    fidelity_rate = verified/existence，而主指标 citation_accuracy 实测是
    「逐题 verified/total」= 端到端通过率。本脚本据此输出四个独立口径：

    1. existence_rate       = existence / total          （来源存在率；本地检查，与裁判无关）
    2. rejudge_fidelity     = passed / existence         （复判忠实度，对齐生产 fidelity_rate）
    3. end_to_end_pass_rate = passed / total             （端到端通过率，对齐生产 citation_accuracy）
    4. passed_refs_per_report / total_refs_per_report    （绝对产出量，绕开比率分母效应）

fallback 语义（2026-09-12 升级）：
    原先「模型未返回该条 verdict」被静默 `passed += 1`（对齐生产降级行为）。独立评测中这
    会高估准确率。现在：
      - passed 为**严格口径**：未获 verdict 一律不计通过；
      - 另出 passed_by_fallback / no_verdict_rate / llm_failed_rate；
      - 闸门模式下 no_verdict_rate > 0 或 llm_failed_rate > 0 即标记该格不可比。

两种模式（2026-09-12 升级）：
    --mode diagnostic（默认）：idx 序号对齐，不要求 claim_echo。快，适合 2x2 模型效应分析。
    --mode formal            ：严格复用生产 validator 判据（VALIDATOR_SYSTEM 全文 +
                               claim_echo 逐字回显 + 按 claim 对齐），用于 release/W8 闸门。
    两者结论若不一致，必须并存两套结果，不得混用。

与生产判据的剩余差异（diagnostic 模式）：去掉 claim_echo 逐字回显（29 条引用会
    把单次输出推到上千 token、单题 >110s），改为 idx 对齐。判定规则（faithful /
    supported / is_meta）与 VALIDATOR_SYSTEM 逐字一致。

用法：
    # 2x2 诊断
    python -m research_engine.eval.w7_rejudge --judges qwen-plus,qwen-turbo --concurrency 4
    # 断点续跑（失败题补齐）
    python -m research_engine.eval.w7_rejudge --resume-from <prev.json> --concurrency 3
    # 闸门模式（生产判据全等）
    python -m research_engine.eval.w7_rejudge --mode formal --judges qwen-plus --concurrency 2
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel, Field  # noqa: E402

from config import config  # noqa: E402
from research_engine.agents.validator import (  # noqa: E402
    VALIDATOR_SYSTEM,
    CitationVerdict,
    config_min_sources,
)
from research_engine.llm.client import LLMClient  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 本次实验 Block 0 的两个关键 arm（同一区块、同一 20 题、同一代码修订）
DEFAULT_ARMS: Dict[str, str] = {
    "arm0_baseline": "run_20260911_194156",
    "arm6_validator_turbo": "run_20260911_232515",
}

# 诊断模式的 system prompt：判定规则与 validator.VALIDATOR_SYSTEM 逐字一致，
# 只把「逐字回显 claim_echo」替换为「按序号 idx 对齐」。
JUDGE_SYSTEM_DIAGNOSTIC = """你是研究事实核查员。你的任务是校验报告中的论断与引用。

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
- is_meta: 该论断/来源是否在描述 DeepResearch 系统自身（自指/元描述，如"本系统""本Agent""Planner→Researcher→Writer→Validator"四节点编排等）——是则 true
- confidence: 综合置信度 0-1
- note: 说明；faithful=false 时必须给出具体原因

重要：输出必须为每条输入条目给出一条结果，`idx` 原样回填输入序号，不要遗漏或多出。
"""


class RejudgeItem(BaseModel):
    """单条引用的独立复判 verdict（诊断模式：按 idx 对齐）。"""

    idx: int = Field(description="输入条目序号，原样回填")
    faithful: bool = Field(description="论断是否能直接由该 finding 内容推断")
    supported: bool = Field(default=False, description="是否通过多源印证")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="校验置信度 0-1")
    is_meta: bool = Field(default=False, description="是否自指/元描述")
    note: str = Field(default="", description="说明；faithful=false 时必须写原因")


class RejudgeVerdict(BaseModel):
    citations: List[RejudgeItem] = Field(default_factory=list)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.strip(), b.strip()).ratio()


def _build_findings_text(findings: List[Dict[str, Any]], cited_ids: set) -> str:
    """仅喂被引用的 findings，保留原始编号（与 validator._build_findings_text 裁剪分支一致）。"""
    lines = [
        f"- [{i}] 来源: {f.get('source','')} (类型: {f.get('source_type','')}) {str(f.get('content',''))[:500]}"
        for i, f in enumerate(findings, 1)
        if str(i) in cited_ids
    ]
    return "\n".join(lines)


def _blank_result(n_total: int, n_exist: int, error: str) -> Dict[str, Any]:
    return {"total": n_total, "existence": n_exist, "passed": None, "passed_fallback": None,
            "no_verdict": n_exist, "llm_failed": True, "retry_rounds": 0, "error": error}


def _ask(
    judge_model: str,
    findings_text: str,
    items: List[Tuple[int, Dict[str, Any]]],
    mode: str,
    timeout: float,
) -> Dict[int, Dict[str, Any]]:
    """向裁判发起一次调用，返回 {key: verdict}。key 为引用在 to_check 中的下标。"""
    if mode == "formal":
        citations_json = "\n".join(
            f"- finding_id: {c.get('finding_id','')} | claim: {c.get('claim','')} "
            f"| source: {c.get('source','')}"
            for _, c in items
        )
        system = VALIDATOR_SYSTEM.format(min_sources=config_min_sources())
        user = (f"研究发现：\n{findings_text}\n\n"
                f"待校验引用（仅列存在性已通过的）：\n{citations_json}\n\n"
                "请输出校验结果。对每条引用，`claim_echo` 字段必须逐字回显上面的 claim 原文。")
        schema: Any = CitationVerdict
    else:
        citations_json = "\n".join(
            f"- idx: {k} | finding_id: {c.get('finding_id','')} | claim: {c.get('claim','')} "
            f"| source: {c.get('source','')}"
            for k, c in items
        )
        system = JUDGE_SYSTEM_DIAGNOSTIC.format(
            min_sources=config.research.min_sources_for_crosscheck)
        user = (f"研究发现：\n{findings_text}\n\n"
                f"待校验引用（仅列存在性已通过的）：\n{citations_json}\n\n"
                "请输出校验结果，每条输入的 idx 都要有对应结果。")
        schema = RejudgeVerdict

    client = LLMClient(model=judge_model, role="rejudge")
    data = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0, schema=schema, timeout=timeout,
    )

    out: Dict[int, Dict[str, Any]] = {}
    if mode == "formal":
        by_fid: Dict[str, List[Dict[str, Any]]] = {}
        for it in data.get("citations", []):
            by_fid.setdefault(str(it.get("finding_id", "")), []).append(it)
        for k, c in items:
            fid, claim = str(c.get("finding_id") or ""), str(c.get("claim", ""))
            for v in by_fid.get(fid, []):
                if str(v.get("claim", "")).strip() == claim.strip():
                    out[k] = v
                    break
            if k in out:
                continue
            best, score = None, 0.0
            for v in by_fid.get(fid, []):
                s = _similarity(str(v.get("claim_echo") or v.get("claim", "")), claim)
                if s > score:
                    best, score = v, s
            if best is not None and score >= 0.6:
                out[k] = best
    else:
        by_idx: Dict[int, Dict[str, Any]] = {}
        for it in data.get("citations", []):
            try:
                by_idx[int(it.get("idx"))] = it
            except (TypeError, ValueError):
                continue
        for k, _ in items:
            if k in by_idx:
                out[k] = by_idx[k]
    return out


def _judge_question(
    judge_model: str,
    findings: List[Dict[str, Any]],
    citations: List[Dict[str, Any]],
    timeout: float = 600.0,
    mode: str = "diagnostic",
    max_retry: int = 2,
) -> Dict[str, Any]:
    """对单题的冻结引用集重跑忠实度判定。

    缺失裁决重问（2026-09-12 升级）：首轮若裁判漏回某条，只把**漏掉的条目**再问一次，
    最多 max_retry 轮。目的是把 no_verdict 压到 0——否则「未获裁决」无论算通过还是算
    不通过都会让 arm 比较产生数 pp 的偏置（实测 arm6×plus 曾漏 59 条）。

    返回：
      total           冻结引用总数（含 existence=False）
      existence       本地存在性通过数
      passed          严格口径通过数（= existence ∧ 复判 faithful；未获 verdict 不计通过）
      passed_fallback 生产降级口径通过数（未获 verdict 记为通过）
      no_verdict      经重问后仍未获裁判反馈的条数
      llm_failed      整题调用是否失败
      retry_rounds    实际重问轮数
    """
    source_to_ids: Dict[str, List[str]] = {}
    for i, f in enumerate(findings, 1):
        source_to_ids.setdefault(f.get("source", ""), []).append(str(i))

    to_check = [c for c in citations if c.get("existence")]
    if not to_check:
        return {"total": len(citations), "existence": 0, "passed": 0, "passed_fallback": 0,
                "no_verdict": 0, "llm_failed": False, "retry_rounds": 0, "error": None}

    cited_ids: set = set()
    for c in to_check:
        fid = str(c.get("finding_id") or "")
        if fid:
            cited_ids.add(fid)
        elif c.get("source"):
            cited_ids.update(source_to_ids.get(c["source"], []))
    findings_text = _build_findings_text(findings, cited_ids)

    verdict_of: Dict[int, Dict[str, Any]] = {}
    pending = [(k, c) for k, c in enumerate(to_check)]
    rounds = 0
    try:
        while pending and rounds <= max_retry:
            got = _ask(judge_model, findings_text, pending, mode, timeout)
            if not got:
                if rounds == 0:
                    return _blank_result(len(citations), len(to_check), "裁判未返回任何可对齐的裁决")
                break
            before = len(pending)
            verdict_of.update(got)
            pending = [(k, c) for k, c in pending if k not in got]
            rounds += 1
            if len(pending) == before:  # 无进展，避免死循环
                break
    except Exception as exc:  # noqa: BLE001
        if not verdict_of:
            return _blank_result(len(citations), len(to_check), str(exc)[:300])
        # 部分成功：保留已获裁决，剩余计入 no_verdict

    passed = passed_fallback = no_verdict = 0
    for k in range(len(to_check)):
        v = verdict_of.get(k)
        if v is None:
            no_verdict += 1
            passed_fallback += 1          # 生产降级口径：未获反馈 → 保守通过
            continue                        # 严格口径：不计通过
        ok = bool(v.get("faithful"))
        passed += 1 if ok else 0
        passed_fallback += 1 if ok else 0

    return {"total": len(citations), "existence": len(to_check), "passed": passed,
            "passed_fallback": passed_fallback, "no_verdict": no_verdict,
            "llm_failed": False, "retry_rounds": rounds, "error": None}


def load_frozen(arms: Dict[str, str]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    frozen: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for arm, run_name in arms.items():
        run_dir = RESULTS_DIR / run_name
        qs = {}
        for f in sorted(glob.glob(str(run_dir / "raw" / "q_*.raw.json"))):
            q_id = Path(f).name.split(".")[0]
            state = json.loads(Path(f).read_text(encoding="utf-8")).get("state") or {}
            qs[q_id] = {"findings": state.get("findings", []),
                        "citations": state.get("citations", [])}
        frozen[arm] = qs
    return frozen


def summarize_cell(rows: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """把一个格子的逐题结果汇总成多口径指标（失败题从分子分母中剔除）。

    口径语义（2026-09-12 升级）：
      - 新格式行带 passed_fallback：passed=严格口径，passed_fallback=生产降级口径。
      - 旧格式行（升级前）的 passed 实为**生产降级口径**（当时把 no_verdict 计为通过），
        因此严格口径 = passed - no_verdict，降级口径 = passed。此处精确换算，不猜。
    """
    ok = [v for v in rows.values() if not v.get("llm_failed") and v.get("passed") is not None]
    n_q = len(ok)
    total = sum(v.get("total", 0) for v in ok)
    exist = sum(v.get("existence", 0) for v in ok)
    nov = sum(v.get("no_verdict", 0) for v in ok)
    if ok and "passed_fallback" in ok[0]:
        passed = sum(v.get("passed", 0) for v in ok)
        passed_fb = sum(v.get("passed_fallback", 0) for v in ok)
    else:  # 旧格式：passed 本就是降级口径
        passed_fb = sum(v.get("passed", 0) for v in ok)
        passed = passed_fb - nov
    return {
        "valid_q": n_q, "failed_q": len(rows) - n_q,
        "total_refs": total, "existence": exist,
        "passed": passed, "passed_fallback": passed_fb, "no_verdict": nov,
        "existence_rate": exist / total if total else 0.0,
        "rejudge_fidelity": passed / exist if exist else 0.0,
        "end_to_end_pass_rate": passed / total if total else 0.0,
        "end_to_end_pass_rate_fallback": passed_fb / total if total else 0.0,
        "passed_refs_per_report": passed / n_q if n_q else 0.0,
        "total_refs_per_report": total / n_q if n_q else 0.0,
        "no_verdict_rate": nov / exist if exist else 0.0,
        "llm_failed_rate": (len(rows) - n_q) / len(rows) if rows else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="W7 引用准确率独立复判（裁判效应 vs arm 效应）")
    parser.add_argument("--judges", type=str, default="qwen-plus,qwen-turbo",
                        help="固定裁判模型列表，逗号分隔")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0=全部，冒烟用）")
    parser.add_argument("--mode", choices=["diagnostic", "formal"], default="diagnostic",
                        help="diagnostic=idx 对齐（快）；formal=生产判据全等（claim_echo）")
    parser.add_argument("--judge-timeout", type=float, default=600.0,
                        help="单次复判调用超时秒数（默认 600）")
    parser.add_argument("--resume-from", type=Path, default=None,
                        help="从上一次复判结果 JSON 续跑：仅重跑缺失/失败的格子")
    parser.add_argument("--only", type=str, default="",
                        help="只跑指定格子，格式 'arm=judge;arm=judge'")
    parser.add_argument("--max-retry", type=int, default=2,
                        help="缺失裁决重问轮数（默认 2；把 no_verdict 压到 0 才可比较）")
    parser.add_argument("--out", type=Path, default=None, help="结果 JSON 输出路径")
    parser.add_argument("--arms", type=str, default="", help="覆盖默认 arm，格式 name=run_dir_name,...")
    args = parser.parse_args()

    judges = [j.strip() for j in args.judges.split(",") if j.strip()]
    arms = dict(DEFAULT_ARMS)
    if args.arms:
        arms = {}
        for pair in args.arms.split(","):
            k, v = pair.split("=", 1)
            arms[k.strip()] = v.strip()

    out_path = args.out or (RESULTS_DIR / f"w7_rejudge_{time.strftime('%Y%m%d_%H%M%S')}.json")

    frozen: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for arm, run_name in arms.items():
        run_dir = RESULTS_DIR / run_name
        qs = {}
        for f in sorted(glob.glob(str(run_dir / "raw" / "q_*.raw.json"))):
            if args.limit and len(qs) >= args.limit:
                break
            q_id = Path(f).name.split(".")[0]
            state = json.loads(Path(f).read_text(encoding="utf-8")).get("state") or {}
            qs[q_id] = {"findings": state.get("findings", []),
                        "citations": state.get("citations", [])}
        frozen[arm] = qs
        print(f"[预载] {arm:24} run={run_name}  题数={len(qs)}  "
              f"引用总数={sum(len(v['citations']) for v in qs.values())}")

    print("\n=== 原始口径（各 arm 自己链路的 validator 裁决；裁判随 arm 变，跨 arm 不可比）===")
    original: Dict[str, Dict[str, float]] = {}
    for arm, qs in frozen.items():
        total = sum(len(v["citations"]) for v in qs.values())
        exist = sum(1 for v in qs.values() for c in v["citations"] if c.get("existence"))
        ver = sum(1 for v in qs.values() for c in v["citations"] if c.get("verified"))
        original[arm] = {"total": total, "existence": exist, "verified": ver,
                         "existence_rate": exist / total if total else 0.0,
                         "fidelity_rate": ver / exist if exist else 0.0,
                         "end_to_end": ver / total if total else 0.0}
        print(f"  {arm:24} 引用 {total:5d}  存在率 {original[arm]['existence_rate']*100:6.2f}%  "
              f"忠实度 {original[arm]['fidelity_rate']*100:6.2f}%  "
              f"端到端 {original[arm]['end_to_end']*100:6.2f}%")

    grid: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    reused = 0
    if args.resume_from and args.resume_from.exists():
        prev = json.loads(args.resume_from.read_text(encoding="utf-8"))
        for cell_key, rows in prev.get("per_question", {}).items():
            a, j = cell_key.split("|", 1)
            for q_id, v in rows.items():
                # 缺裁决（no_verdict>0）视为数据不完整 → 一并重跑，否则 arm 比较有偏
                if v.get("llm_failed") or v.get("passed") is None or v.get("no_verdict"):
                    continue
                grid.setdefault((a, j), {})[q_id] = v
                reused += 1
        print(f"[续跑] 从 {args.resume_from.name} 复用 {reused} 条有效题目结果")

    only = set()
    for pair in [p for p in args.only.split(";") if p.strip()]:
        if "=" in pair:
            a, j = pair.split("=", 1)
            only.add((a.strip(), j.strip()))

    tasks = []
    for arm, qs in frozen.items():
        for q_id, payload in qs.items():
            for judge in judges:
                if only and (arm, judge) not in only:
                    continue
                if q_id in grid.get((arm, judge), {}):
                    continue
                tasks.append((arm, q_id, judge, payload))

    print(f"\n=== 复判：待跑 {len(tasks)} 次调用（模式={args.mode}，并发 {args.concurrency}，"
          f"单次超时 {args.judge_timeout:.0f}s）===")
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {
            pool.submit(_judge_question, judge, p["findings"], p["citations"],
                        args.judge_timeout, args.mode, args.max_retry): (arm, q_id, judge)
            for arm, q_id, judge, p in tasks
        }
        for fut in as_completed(futs):
            arm, q_id, judge = futs[fut]
            try:
                grid.setdefault((arm, judge), {})[q_id] = fut.result()
            except Exception as exc:  # noqa: BLE001
                grid.setdefault((arm, judge), {})[q_id] = _blank_result(0, 0, str(exc)[:300])
            done += 1
            if done % 10 == 0 or done == len(tasks):
                print(f"  进度 {done}/{len(tasks)}  已耗时 {time.time()-t0:.0f}s")

    cell = {(a, j): summarize_cell(grid.get((a, j), {})) for a in arms for j in judges}

    # ---- 多口径表 ----
    print("\n=== 多口径复判结果 ===")
    print(f"{'arm|judge':38}{'n':>4}{'存在率':>10}{'复判忠实度':>12}{'端到端':>10}"
          f"{'通过条/篇':>11}{'引用条/篇':>11}{'no_verdict':>12}")
    for a in arms:
        for j in judges:
            c = cell[(a, j)]
            flag = "*" if (c["failed_q"] or c["no_verdict"]) else " "
            print(f"{a+'|'+j:38}{c['valid_q']:>4}{c['existence_rate']*100:>9.2f}%"
                  f"{c['rejudge_fidelity']*100:>11.2f}%{c['end_to_end_pass_rate']*100:>9.2f}%"
                  f"{c['passed_refs_per_report']:>11.1f}{c['total_refs_per_report']:>11.1f}"
                  f"{c['no_verdict']:>5}{flag}")
    print("  * = 该格存在失败题或未获反馈的条目，见 JSON 明细；"
          "严格口径下 no_verdict 不计通过，fallback 口径见 JSON")
    print("  注：existence_rate 依赖各 arm 自己的 findings 集合，是产物属性而非裁判属性。")

    # ---- 效应分解 ----
    base_arm, treat_arm, judge_ref = "arm0_baseline", "arm6_validator_turbo", judges[0]
    if base_arm in arms and treat_arm in arms and len(judges) >= 2:
        A = cell[(base_arm, judge_ref)]["end_to_end_pass_rate"]
        B = cell[(base_arm, judges[1])]["end_to_end_pass_rate"]
        C = cell[(treat_arm, judge_ref)]["end_to_end_pass_rate"]
        D = cell[(treat_arm, judges[1])]["end_to_end_pass_rate"]
        print("\n=== 效应分解（端到端口径）===")
        print(f"  A = {base_arm} × {judge_ref:12}: {A*100:6.2f}%   （基准格）")
        print(f"  B = {base_arm} × {judges[1]:12}: {B*100:6.2f}%")
        print(f"  C = {treat_arm} × {judge_ref:12}: {C*100:6.2f}%")
        print(f"  D = {treat_arm} × {judges[1]:12}: {D*100:6.2f}%")
        print(f"  ① 纯裁判效应（同引用集、只换裁判）  B - A = {(B-A)*100:+6.2f} pp")
        print(f"  ② 同裁判下的 arm 效应              C - A = {(C-A)*100:+6.2f} pp")
        print(f"  ③ 端到端 vs 忠实度：同裁判下 arm6 的忠实度Δ = "
              f"{(cell[(treat_arm, judge_ref)]['rejudge_fidelity']-cell[(base_arm, judge_ref)]['rejudge_fidelity'])*100:+6.2f} pp")
        print(f"  ④ 存在率Δ（同裁判无关）            = "
              f"{(cell[(treat_arm, judge_ref)]['existence_rate']-cell[(base_arm, judge_ref)]['existence_rate'])*100:+6.2f} pp")
        print(f"  ⑤ 绝对通过条数/篇：{cell[(base_arm, judge_ref)]['passed_refs_per_report']:.1f} → "
              f"{cell[(treat_arm, judge_ref)]['passed_refs_per_report']:.1f}"
              f"（{(cell[(treat_arm, judge_ref)]['passed_refs_per_report']/cell[(base_arm, judge_ref)]['passed_refs_per_report']-1)*100:+.1f}%）")
        print("\n  判读提示：若 ① 与 ② 同量级且 ③④⑤ 未改善，则「arm 增益」主要是测量伪影与分母收缩。")

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": args.mode, "judges": judges, "arms": arms,
        "original": original,
        "cells": {f"{a}|{j}": v for (a, j), v in cell.items()},
        "per_question": {f"{a}|{j}": v for (a, j), v in grid.items()},
        "elapsed_s": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果已落盘：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
