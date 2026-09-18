"""W8 §10.4：run 级 provenance 一处定义 + 配置唯一真相源。

覆盖 `docs/requirements/8-fault-transparency-and-reproducibility.md` §9.1 的 Q8 新增行：
- `code_revision()` 一处定义（三处调用方共享同一产生点）
- 同一 run 内所有 raw 的 `config_snapshot` 逐字节相同
- `config_snapshot` 含 `validator_model` 顶层字段且跟随 config
- raw 记录中不存在平铺的 `model_name` / `search_provider` / `python_version` / `max_step_budget` / `experiment`
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from config import config
from research_engine.eval import provenance
from research_engine.eval.provenance import (
    EXPERIMENT_SWITCHES,
    RAW_PROVENANCE_KEYS,
    UNKNOWN,
    code_revision,
    config_snapshot,
    experiment_snapshot,
    run_provenance,
)
from research_engine.eval.report_gen import _git_head as report_git_head
from research_engine.eval.run import _provenance_from_raw, _run_one
from research_engine.eval.run import git_head as run_git_head
from research_engine.eval.w7_experiment import _git_rev

# Q8 = C6-A：配置类字段一律内嵌 config_snapshot，不得在 raw 顶层平铺
BANNED_FLAT_KEYS = ("model_name", "search_provider", "python_version", "max_step_budget", "experiment")


# ---------- code_revision：一处定义 ----------

def test_code_revision_returns_triple():
    rev = code_revision()
    assert set(rev) == {"git_commit", "git_dirty", "git_diff_hash"}
    assert rev["git_commit"] == UNKNOWN or len(rev["git_commit"]) == 40
    assert isinstance(rev["git_dirty"], bool)


def test_code_revision_survives_without_git(tmp_path):
    """非 git 目录 / 无 git 命令：如实返回 unknown，绝不抛异常（eval 不能因元数据挂掉）。"""
    rev = code_revision(tmp_path)
    # 恰好落在 git 仓库内的临时目录仍可能取到值；关键是三键齐全且不抛
    assert set(rev) == {"git_commit", "git_dirty", "git_diff_hash"}
    assert isinstance(rev["git_dirty"], bool)
    assert isinstance(rev["git_diff_hash"], str)


def test_three_git_helpers_share_one_source(monkeypatch):
    """Q8 = C6-A 核心验收：三处调用方底层都调 provenance.code_revision，无第 4 份实现。"""
    calls = []

    def fake(repo_root=None):
        calls.append(repo_root)
        return {"git_commit": "a" * 40, "git_dirty": True, "git_diff_hash": "deadbeefdeadbeef"}

    monkeypatch.setattr(provenance, "code_revision", fake)
    monkeypatch.setattr("research_engine.eval.report_gen.code_revision", fake)
    monkeypatch.setattr("research_engine.eval.run.code_revision", fake)
    monkeypatch.setattr("research_engine.eval.w7_experiment.code_revision", fake)

    assert run_git_head() == "a" * 40
    assert report_git_head() == "a" * 40
    assert _git_rev() == "aaaaaaaa+dirty"
    assert len(calls) == 3


def test_git_rev_keeps_dirty_suffix_format(monkeypatch):
    """`+dirty` 字符串格式被既有 W7 产物的区块续跑比对依赖，改格式 = 历史实验无法续跑。"""

    def fake(repo_root=None):
        return {"git_commit": "b" * 40, "git_dirty": False, "git_diff_hash": ""}

    monkeypatch.setattr("research_engine.eval.w7_experiment.code_revision", fake)
    assert _git_rev() == "bbbbbbbb"  # 干净：不带后缀

    fake_dirty = lambda repo_root=None: {  # noqa: E731
        "git_commit": "c" * 40, "git_dirty": True, "git_diff_hash": "x" * 16,
    }
    monkeypatch.setattr("research_engine.eval.w7_experiment.code_revision", fake_dirty)
    assert _git_rev() == "cccccccc+dirty"


def test_run_provenance_calls_git_once(monkeypatch):
    """run_provenance 一次算好 git + config，供整轮 run 复用（不按条目重复调）。"""
    n = {"i": 0}

    def fake(repo_root=None):
        n["i"] += 1
        return {"git_commit": "d" * 40, "git_dirty": False, "git_diff_hash": ""}

    monkeypatch.setattr(provenance, "code_revision", fake)
    prov = run_provenance()
    assert n["i"] == 1
    # W8 Arm 6：provenance 从 4 字段扩到 10 字段；这里锁的是**字段集**，
    # 新增 provenance 字段必须同时改 RAW_PROVENANCE_KEYS 与本行（防漏登记）。
    assert set(prov) == set(RAW_PROVENANCE_KEYS), "run_provenance 与 RAW_PROVENANCE_KEYS 出现分叉"


# ---------- config_snapshot：唯一真相源 ----------

def test_config_snapshot_has_validator_model_top_level():
    """W7 唯一被换掉的模型旋钮（arm6 = 全开关 + qwen-turbo）必须在顶层，不能只藏在 experiment 段。"""
    snap = config_snapshot()
    assert snap["validator_model"] == config.llm.validator_model
    assert "experiment" in snap and "validator_model" not in snap["experiment"]


def test_config_snapshot_follows_validator_model(monkeypatch):
    """快照必须跟随 config，不是硬编码。"""
    monkeypatch.setattr(config.llm, "validator_model", "qwen-turbo")
    assert config_snapshot()["validator_model"] == "qwen-turbo"


def test_config_snapshot_no_zero_info_keys():
    """恒值 / 重复字段明确不记（Q8 = C6-A）—— 记了等于给未来制造考古题。"""
    snap = config_snapshot()
    for k in ("embedding_model", "strategic_model", "temperature"):
        assert k not in snap, f"{k} 是恒值或 planner_model 的别名，不该进快照"


def test_experiment_snapshot_keys_and_types():
    snap = experiment_snapshot()
    assert set(snap) == set(EXPERIMENT_SWITCHES)
    assert all(isinstance(v, bool) for v in snap.values())


def test_experiment_snapshot_follows_effective_values(monkeypatch):
    """记的是运行期生效值（读 config.experiment），不是环境变量原始串。"""
    monkeypatch.setattr(config.experiment, "validator_trim_enabled", False)
    assert experiment_snapshot()["validator_trim_enabled"] is False


# ---------- raw 记录：内嵌同一对象 ----------

def _fake_graph(status="done"):
    state = MagicMock()
    state.model_dump.return_value = {"citations": [], "findings": []}
    state.token_used = 1
    state.status = status
    state.error = None
    g = MagicMock()
    g.run.return_value = state
    return g


def test_raw_embeds_identical_config_snapshot(tmp_path):
    """同一 run 内所有 raw 的 config_snapshot 必须逐字节相同（内嵌同一对象，不是手抄副本）。"""
    prov = run_provenance()
    rows = [
        {"id": "q_001", "query": "问题一", "difficulty": "easy", "type": "factual"},
        {"id": "q_002", "query": "问题二", "difficulty": "easy", "type": "factual"},
    ]
    graph = _fake_graph()
    snaps = []
    for row in rows:
        res = _run_one(row, {"version": "1.1"}, tmp_path, graph, prov)
        raw = res["raw"]
        # 配置类字段不得在 raw 顶层平铺
        for k in BANNED_FLAT_KEYS:
            assert k not in raw, f"raw 顶层出现平铺字段 {k}"
        assert raw["git_commit"] == prov["git_commit"]
        assert raw["git_dirty"] == prov["git_dirty"]
        snaps.append(json.dumps(raw["config_snapshot"], ensure_ascii=False, sort_keys=True))
    assert len(set(snaps)) == 1, "同一 run 内 config_snapshot 出现分叉"
    assert json.loads(snaps[0])["validator_model"] == config.llm.validator_model


def test_raw_failure_path_still_records_provenance(tmp_path):
    """失败条目也要有配置快照 —— 失败恰是要诊断的场景，没配置就无法归因。"""
    prov = run_provenance()
    g = MagicMock()
    g.run.side_effect = RuntimeError("boom")
    # 重试 1 次后仍失败；RETRY_SLEEP_S=2s 会拖慢测试，故直接打 sleep
    import research_engine.eval.run as run_mod

    orig = run_mod.RETRY_SLEEP_S
    run_mod.RETRY_SLEEP_S = 0
    try:
        res = _run_one({"id": "q_009", "query": "x"}, {"version": "1.1"}, tmp_path, g, prov)
    finally:
        run_mod.RETRY_SLEEP_S = orig
    assert res["invoke_status"] == "failed"
    assert res["raw"]["config_snapshot"] == prov["config_snapshot"]
    assert res["raw"]["git_commit"] == prov["git_commit"]


# ---------- _provenance_from_raw：读跑批时刻，不读汇总时刻 ----------

def test_provenance_from_raw_reads_run_time_not_now(tmp_path):
    """W7 教训：补跑时工作树已变，若汇总时现抓 git 记的是「汇总时刻」而非「跑批时刻」。"""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    snap = {"planner_model": "qwen-plus", "validator_model": "qwen-plus", "experiment": {}}
    (raw_dir / "q_001.raw.json").write_text(
        json.dumps({"q_id": "q_001", "git_commit": "e" * 40, "git_dirty": True,
                    "git_diff_hash": "f" * 16, "config_snapshot": snap}),
        encoding="utf-8",
    )
    prov = _provenance_from_raw(tmp_path)
    assert prov["git_commit"] == "e" * 40
    assert prov["git_dirty"] is True
    assert prov["config_snapshot"] == snap


def test_provenance_from_raw_missing_is_unknown_not_fabricated(tmp_path):
    """raw 缺失 / 为 §10.4 之前的历史产物：如实返回 unknown + None，不伪造。"""
    prov = _provenance_from_raw(tmp_path)
    assert prov["git_commit"] == UNKNOWN
    assert prov["config_snapshot"] is None
