# -*- coding: utf-8 -*-
"""W8 命名三分（Arm 1 遗留项）：三层 `status` 的键名契约。

DoD（需求文档 §7）：「落盘记录中**不存在**语义歧义的裸 `status` 键」。
本文件把这句话拆成可执行断言，并**同时锁住反向约束** ——
历史产物里的裸 `status` 不许回填（它们是证据不是缓存，见 Arm 7 台账纪律），
所以方向是「写侧只出新键 + 读侧 dual-read」。

三层对照（`research_engine/eval/status_keys.py` 是唯一真相源）：

| 层 | 键名 | 语义 | 落盘位置 |
| --- | --- | --- | --- |
| ① | `run_status` | 流程健康度 | `ResearchState`（`state.py`；Arm 1 已落地） |
| ② | `invoke_status` | 单次调用执行结果 | `raw/*.raw.json` **顶层** |
| ③ | `metrics_status` | 指标齐备性 | `eval/*.eval.json` **顶层** |

本文件覆盖的断言族：
- **写侧**：层②③ 的新记录顶层无裸 `status`（单测 + 端到端落盘扫描）
- **读侧**：dual-read 新键优先 / 旧键回落 / 皆无得 None
- **反向**：历史产物仍带裸 `status`（没被回填）且读得出来
- **登记**：层②③ 之外残留的裸 `status` 逐条登记且**登记为真**
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

import research_engine.eval.run as run_mod
from research_engine.eval.provenance import PROVENANCE_DEFAULTS
from research_engine.eval.status_keys import (
    BARE_STATUS_SITES,
    INVOKE_STATUS,
    INVOKE_STATUS_RETURN_VALUES,
    INVOKE_STATUS_STORED_VALUES,
    LEGACY_STATUS,
    METRICS_STATUS,
    METRICS_STATUS_VALUES,
    read_invoke_status,
    read_metrics_status,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_MODULE_DIR = REPO_ROOT / "research_engine" / "eval"
RESULTS = EVAL_MODULE_DIR / "results"
W7_MANIFEST = RESULTS / "w7_experiment_20260911_194151" / "manifest.json"

#: before 基线的三个 run —— §10.4 验收记录靠它们存续，是**最不能被动**的历史产物
BEFORE_BASELINE_RUNS = (
    "run_20260916_001005",
    "run_20260916_011813",
    "run_20260916_022440",
)


# ---------------------------------------------------------------- 测试替身

DATASET = {
    "meta": {"version": "test-naming"},
    "rows": [
        {"id": "q_001", "query": "问题一", "difficulty": "easy", "type": "fact"},
        {"id": "q_002", "query": "问题二", "difficulty": "easy", "type": "fact"},
    ],
}


class _FakeState:
    """最小 `ResearchState` 替身（只带 `_run_one` 会读的字段）。

    `model_dump()` 里**故意留一个裸 `status`** —— 它就是 graph 流转状态，
    即登记表第 1 条要锁住的那个「合法残留」。
    """

    def __init__(self, status: str = "done", token_used: int = 7, error=None):
        self.status = status
        self.token_used = token_used
        self.error = error

    def model_dump(self):
        return {
            "status": self.status,  # ← 层①之外的 graph 流转状态，嵌在 state 命名空间内
            "topic": "t",
            "report": "# report" if self.status == "done" else "",
            "error": self.error,
            "token_used": self.token_used,
            "run_status": "success",
            "degradation_log": [],
        }


class _FakeGraph:
    def __init__(self, behaviour):
        self._behaviour = behaviour

    def run(self, topic, thread_id=None):  # noqa: ARG002
        return self._behaviour(topic)


def _fake_metrics():
    """形状对齐 `metrics.compute_all` 的返回值（够 `_summarize` 聚合即可）。"""
    return {
        "completion": {"complete": True},
        "citation": {"fidelity_rate": 0.8, "relaxed_rate": 0.85, "existence_rate": 0.9},
        "coverage": {"coverage": 0.5, "judge_failed": False},
        "retrieval_hit": {"retrieval_hit_rate": 0.6},
        "steps": {"steps": 3},
        "reflection": {"stop_type": "critic_stop"},
        "insufficient": {"marker_ratio": 0.1},
        "cost": {"total_tokens": 10},
    }


@pytest.fixture
def hermetic(monkeypatch):
    """把 phase1/phase2 的外部依赖掐成零成本，并**挡住对真实产物的写入**。"""
    monkeypatch.setattr(run_mod, "RETRY_SLEEP_S", 0)
    monkeypatch.setattr(run_mod, "run_provenance", lambda: dict(PROVENANCE_DEFAULTS))
    monkeypatch.setattr(run_mod, "create_graph", lambda: _FakeGraph(lambda _t: _FakeState()))
    monkeypatch.setattr(run_mod, "_make_judge", lambda: object())
    monkeypatch.setattr(run_mod, "compute_all", lambda state, row, judge=None: _fake_metrics())
    # ⚠️ phase2 末尾的 generate_report 会写**真实**的 docs/eval-report.md 与 results/history.json
    # ⇒ 必须掐掉，否则「跑一次单测」等于改一次取证产物（Arm 7 台账纪律）
    monkeypatch.setattr("research_engine.eval.report_gen.generate_report", lambda *a, **k: None)
    return monkeypatch


# ---------------------------------------------------------------- 扫描工具


def _status_key_paths(obj, prefix: tuple = ()) -> list:
    """递归收集**所有键名恰为 `status`** 的键路径（用来证明它无处藏身）。"""
    out: list = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = prefix + (k,)
            if k == LEGACY_STATUS:
                out.append(path)
            out.extend(_status_key_paths(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_status_key_paths(v, prefix + (i,)))
    return out


def _scan_written(run_dir: Path) -> dict:
    """{落盘 json 的相对路径: [status 键路径, ...]}"""
    found = {}
    for p in sorted(run_dir.rglob("*.json")):
        found[p.relative_to(run_dir).as_posix()] = _status_key_paths(
            json.loads(p.read_text(encoding="utf-8"))
        )
    return found


def _run_pipeline(tmp_path: Path, hermetic, behaviour=None) -> Path:
    if behaviour is not None:
        hermetic.setattr(run_mod, "create_graph", lambda: _FakeGraph(behaviour))
    run_dir = tmp_path / "run_naming"
    run_mod.phase1(DATASET, run_dir, 2)
    run_mod.phase2(DATASET, run_dir)
    return run_dir


# ================================================================ 1. 写侧：端到端


def test_end_to_end_written_records_have_no_bare_status(tmp_path, hermetic):
    """**最硬的一条**：真跑 phase1+phase2，扫它们**真正写到磁盘**的每一个 json。

    断言三层：
    ① 任何文件都不得出现**顶层**裸 `status`（DoD 原话）；
    ② raw 里唯一允许出现 `status` 的地方是 graph 状态快照内部（键路径以 `state` 开头）；
    ③ eval / summary / phase1_global_stats 任意层级都不许有 `status`。
    """
    scan = _scan_written(_run_pipeline(tmp_path, hermetic))
    assert scan, "没扫到任何落盘 json —— 用例自身失效了"

    for name, paths in scan.items():
        bare_top = [p for p in paths if len(p) == 1]
        assert not bare_top, f"{name} 顶层出现裸 status：{paths}"

    for name, paths in scan.items():
        if Path(name).name.endswith(".raw.json"):
            assert paths, (
                f"{name} 里连 state 快照内的 `state.status` 都没有 —— "
                "登记表第 1 条的事实基础变了，需重新论证"
            )
            for p in paths:
                assert p[0] == "state", f"{name} 的 status 出现在 {p}，不在 state 快照内"
        else:
            assert paths == [], f"{name} 任意层级都不该有 status，实际 {paths}"


# ================================================================ 2. 写侧：层②


def test_run_one_ok_writes_invoke_status_only(tmp_path, hermetic):
    res = run_mod._run_one(
        {"id": "q_001", "query": "x"}, {"version": "v"}, tmp_path,
        _FakeGraph(lambda _t: _FakeState("done")),
    )
    assert res[INVOKE_STATUS] == "ok"
    assert LEGACY_STATUS not in res
    assert res["raw"][INVOKE_STATUS] == "done"
    assert LEGACY_STATUS not in res["raw"]


def test_run_one_incomplete_when_graph_produced_no_report(tmp_path, hermetic):
    """graph 跑通但没产出报告 ⇒ 层② 记 `incomplete`（**不是** 层③ 的 `partial`）。"""
    res = run_mod._run_one(
        {"id": "q_001", "query": "x"}, {"version": "v"}, tmp_path,
        _FakeGraph(lambda _t: _FakeState("writing")),
    )
    assert res[INVOKE_STATUS] == "ok"
    assert res["raw"][INVOKE_STATUS] == "incomplete"
    assert LEGACY_STATUS not in res["raw"]


def test_run_one_failed_writes_invoke_status_only(tmp_path, hermetic):
    def _boom(_topic):
        raise RuntimeError("graph 炸了")

    res = run_mod._run_one(
        {"id": "q_001", "query": "x"}, {"version": "v"}, tmp_path, _FakeGraph(_boom),
    )
    assert res[INVOKE_STATUS] == "failed"
    assert LEGACY_STATUS not in res
    assert res["raw"][INVOKE_STATUS] == "failed"
    assert LEGACY_STATUS not in res["raw"]


def test_phase1_timeout_record_has_no_bare_status(tmp_path, hermetic):
    """超时是**层② 独有的**取值（层③ 只是原样透传）⇒ 它也必须走新键。"""
    hermetic.setattr(run_mod, "TASK_TIMEOUT_S", 0.01)
    hermetic.setattr(
        run_mod, "create_graph",
        lambda: _FakeGraph(lambda _t: (time.sleep(0.3), _FakeState("done"))[1]),
    )
    run_dir = tmp_path / "run_timeout"
    run_mod.phase1(
        {"meta": {"version": "v"}, "rows": [dict(DATASET["rows"][0])]}, run_dir, 1
    )
    raw = json.loads((run_dir / "raw" / "q_001.raw.json").read_text(encoding="utf-8"))
    assert raw[INVOKE_STATUS] == "timeout"
    assert LEGACY_STATUS not in raw


# ================================================================ 3. 写侧：层③


def test_evaluate_one_writes_metrics_status_only(tmp_path, hermetic):
    res = run_mod._evaluate_one({"id": "q_001"}, {"state": {}}, object(), tmp_path)
    assert res[METRICS_STATUS] == "ok"
    assert LEGACY_STATUS not in res
    assert res["missing_metrics"] == []


def test_evaluate_one_partial_when_judge_failed(tmp_path, hermetic):
    hermetic.setattr(
        run_mod, "compute_all",
        lambda state, row, judge=None: {
            **_fake_metrics(), "coverage": {"coverage": None, "judge_failed": True},
        },
    )
    res = run_mod._evaluate_one({"id": "q_001"}, {"state": {}}, object(), tmp_path)
    assert res[METRICS_STATUS] == "partial"
    assert res["missing_metrics"] == ["coverage"]
    assert LEGACY_STATUS not in res


def test_evaluate_one_failed_on_exception(tmp_path, hermetic):
    def _boom(state, row, judge=None):
        raise ValueError("judge 炸了")

    hermetic.setattr(run_mod, "compute_all", _boom)
    res = run_mod._evaluate_one({"id": "q_001"}, {"state": {}}, object(), tmp_path)
    assert res[METRICS_STATUS] == "failed"
    assert LEGACY_STATUS not in res


def test_phase2_passthrough_keeps_metrics_status_only(tmp_path, hermetic):
    """层② `timeout` / `failed` 无 state 可评 ⇒ 原样透传给层③，且同样不写裸键。"""
    run_dir = tmp_path / "run_passthrough"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "raw" / "q_001.raw.json").write_text(
        json.dumps({"q_id": "q_001", INVOKE_STATUS: "timeout", "error": "task timeout",
                    **PROVENANCE_DEFAULTS},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    run_mod.phase2({"meta": {"version": "v"}, "rows": list(DATASET["rows"])}, run_dir)
    res = json.loads((run_dir / "eval" / "q_001.eval.json").read_text(encoding="utf-8"))
    assert res[METRICS_STATUS] == "timeout"
    assert LEGACY_STATUS not in res


def test_phase2_missing_dataset_row_writes_metrics_status_only(tmp_path, hermetic):
    run_dir = tmp_path / "run_missing_row"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "raw" / "q_999.raw.json").write_text(
        json.dumps({"q_id": "q_999", INVOKE_STATUS: "done", "state": {}, **PROVENANCE_DEFAULTS},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    run_mod.phase2({"meta": {"version": "v"}, "rows": list(DATASET["rows"])}, run_dir)
    res = json.loads((run_dir / "eval" / "q_999.eval.json").read_text(encoding="utf-8"))
    assert res[METRICS_STATUS] == "failed"
    assert LEGACY_STATUS not in res


# ================================================================ 4. 读侧：dual-read


def test_read_invoke_status_prefers_new_key():
    assert read_invoke_status({INVOKE_STATUS: "done", LEGACY_STATUS: "failed"}) == "done"


def test_read_invoke_status_falls_back_to_legacy():
    assert read_invoke_status({LEGACY_STATUS: "timeout"}) == "timeout"


def test_read_invoke_status_none_when_both_absent():
    assert read_invoke_status({}) is None
    assert read_invoke_status({"q_id": "q_001"}) is None


def test_read_metrics_status_prefers_new_key():
    assert read_metrics_status({METRICS_STATUS: "ok", LEGACY_STATUS: "partial"}) == "ok"


def test_read_metrics_status_falls_back_to_legacy():
    assert read_metrics_status({LEGACY_STATUS: "partial"}) == "partial"


def test_read_metrics_status_none_when_both_absent():
    assert read_metrics_status({}) is None


def test_summarize_accepts_legacy_metrics_status(tmp_path, hermetic):
    """层③ 计数必须仍能消费**只有旧键**的历史 eval（phase2 断点续跑会直接读它们）。"""
    results = [
        {"q_id": "a", LEGACY_STATUS: "ok", "metrics": _fake_metrics()},
        {"q_id": "b", LEGACY_STATUS: "partial", "metrics": _fake_metrics()},
        {"q_id": "c", LEGACY_STATUS: "failed", "metrics": None},
        {"q_id": "d", METRICS_STATUS: "ok", "metrics": _fake_metrics()},
    ]
    summary = run_mod._summarize(results, {"version": "v"}, tmp_path)
    assert (summary["metrics_ok"], summary["metrics_partial"], summary["metrics_failed"]) == (2, 1, 1)


def test_summarize_prefers_new_key_over_legacy(tmp_path, hermetic):
    """两键并存且**取值冲突**时新键必须赢 —— 防止某天有人把优先级写反。"""
    results = [
        {"q_id": "a", METRICS_STATUS: "ok", LEGACY_STATUS: "failed", "metrics": _fake_metrics()},
    ]
    summary = run_mod._summarize(results, {"version": "v"}, tmp_path)
    assert (summary["metrics_ok"], summary["metrics_failed"]) == (1, 0)


# ================================================================ 5. 反向约束：历史产物不回填


def _sample_legacy(files: list, limit: int) -> list:
    return [p for p in files[:limit]]


def test_legacy_raw_artifacts_are_not_backfilled():
    """历史 raw 仍带裸 `status`（没被回填），且 dual-read 读得出。

    双向锁：① 谁哪天"顺手回填了"会红；② 谁哪天"顺手删了兼容分支"也会红。
    """
    files = sorted(RESULTS.glob("run_*/raw/*.raw.json"))
    assert files, "真仓库里找不到历史 raw —— 用例失去意义"

    legacy = 0
    for p in _sample_legacy(files, 20):
        data = json.loads(p.read_text(encoding="utf-8"))
        if INVOKE_STATUS in data:
            continue
        assert LEGACY_STATUS in data, f"{p.name} 既无新键也无旧键"
        assert read_invoke_status(data) in INVOKE_STATUS_STORED_VALUES
        legacy += 1
    assert legacy > 0, "样本里没有任何带裸 status 的历史 raw —— 兼容分支是否还必要？请重新论证"


def test_legacy_eval_artifacts_are_not_backfilled():
    files = sorted(RESULTS.glob("run_*/eval/*.eval.json"))
    assert files, "真仓库里找不到历史 eval —— 用例失去意义"

    legacy = 0
    for p in _sample_legacy(files, 20):
        data = json.loads(p.read_text(encoding="utf-8"))
        if METRICS_STATUS in data:
            continue
        assert LEGACY_STATUS in data, f"{p.name} 既无新键也无旧键"
        assert read_metrics_status(data) in METRICS_STATUS_VALUES
        legacy += 1
    assert legacy > 0, "样本里没有任何带裸 status 的历史 eval —— 兼容分支是否还必要？请重新论证"


def test_before_baseline_runs_are_intact():
    """before 基线的三个 run 是 §10.4 验收记录的取证源 ⇒ 逐字节未被本 Arm 触碰。"""
    checked = 0
    for run_id in BEFORE_BASELINE_RUNS:
        raw_dir = RESULTS / run_id / "raw"
        if not raw_dir.exists():
            continue
        checked += 1
        for p in sorted(raw_dir.glob("*.raw.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            assert INVOKE_STATUS not in data, f"{p} 被回填了新键 —— 历史产物只读！"
        assert list(
            (RESULTS / run_id / "raw").glob("*.raw.json")
        ), f"{run_id} 的 raw 不见了"
    assert checked > 0, "三个 before 基线 run 一个都没找到 —— 台账纪律的取证基础没了"


# ================================================================ 6. 未收敛裸 status 的登记为真


def test_bare_status_sites_registry_is_true():
    """登记表不是文档而是**可执行事实**：逐条验证落点确实存在。"""
    assert len(BARE_STATUS_SITES) >= 4
    blob = "\n".join(f"{s['site']} {s['semantics']}" for s in BARE_STATUS_SITES)

    # 第 1 条：raw 的 state 快照内确有 `state.status`
    sample = json.loads(
        sorted(RESULTS.glob("run_*/raw/*.raw.json"))[0].read_text(encoding="utf-8")
    )
    assert LEGACY_STATUS in (sample.get("state") or {}), "登记表第 1 条落点不成立"
    assert "state.status" in blob

    # 第 2 条：history.json 每条记录确有裸 `status`
    history = json.loads((RESULTS / "history.json").read_text(encoding="utf-8"))
    assert history, "history.json 为空"
    assert all(LEGACY_STATUS in rec for rec in history), "登记表第 2 条落点不成立"

    # 第 3 条：W7 manifest 的 runs[].status
    assert W7_MANIFEST.exists(), "W7 manifest 不见了 —— 结论文档的取证源，登记表第 3 条失去依据"
    runs = json.loads(W7_MANIFEST.read_text(encoding="utf-8"))["runs"]
    assert runs and all(LEGACY_STATUS in r for r in runs), "登记表第 3 条落点不成立"

    # 第 4 条：Langfuse trace 的 status（外部字段）
    assert "Langfuse" in blob


def test_history_status_is_vacuous():
    """`history.json` 的 `status` 自称「回归判定」，实际写的是 `summary.struct.regression`，
    而**全仓从未有任何代码写入 `summary.struct`** ⇒ 它恒等于默认值 `"PASS"`，是空转字段。

    本用例不是替它背书，而是把「这字段在骗人」固化成可执行事实：
    ① `regression` 全仓只出现在 report_gen 的**读取**处；
    ② history 里该键的取值集合恰为 `{"PASS"}`。
    """
    hits = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "research_engine").rglob("*.py")
        # 登记表所在模块提到 `regression` 属说明性引用，不算落点
        if p.name != "status_keys.py" and "regression" in p.read_text(encoding="utf-8")
    ]
    assert hits == ["research_engine/eval/report_gen.py"], (
        f"`regression` 出现了新落点 {hits} —— 若它已被真正写入，"
        "请连同 status_keys.BARE_STATUS_SITES 第 2 条的措辞一起更新"
    )
    history = json.loads((RESULTS / "history.json").read_text(encoding="utf-8"))
    values = {rec.get(LEGACY_STATUS) for rec in history}
    assert values == {"PASS"}, f"history 的 status 不再恒为 PASS：{values}（空转字段的说法需更新）"


# ================================================================ 7. 源码级守卫


def test_run_py_has_no_bare_status_literal():
    """层②③ 的唯一落盘者是 `run.py` ⇒ 它的字符串常量里不许再有 `"status"`。

    用 AST 取字符串常量而非 grep —— 注释里写「裸 `status`」不该误报。
    """
    tree = ast.parse((EVAL_MODULE_DIR / "run.py").read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == LEGACY_STATUS
    ]
    assert not offenders, f"run.py 第 {offenders} 行又出现了裸 'status' 字面量 —— 键名契约被破坏"


def test_report_anomaly_section_reads_through_dual_read():
    """报告「失败与异常附录」判的是**层③** ⇒ 不许再出现裸 `status` 字面量。"""
    src = (EVAL_MODULE_DIR / "report_gen.py").read_text(encoding="utf-8")
    start = src.index("# ---- 4. 失败与异常附录 ----")
    end = src.index("# ---- 7. 指标演进趋势表 ----", start)
    section = src[start:end]
    assert f'"{LEGACY_STATUS}"' not in section
    assert f"'{LEGACY_STATUS}'" not in section
    assert "read_metrics_status(" in section


def test_value_vocabularies_are_locked():
    """既然层②③ 同名了，两套词汇表就得**分别**钉住，防止「顺手把值也合并」。"""
    assert INVOKE_STATUS_RETURN_VALUES == {"ok", "failed"}
    assert INVOKE_STATUS_STORED_VALUES == {"done", "incomplete", "failed", "timeout"}
    assert METRICS_STATUS_VALUES == {"ok", "partial", "failed", "timeout"}
    assert len({INVOKE_STATUS, METRICS_STATUS, LEGACY_STATUS}) == 3
