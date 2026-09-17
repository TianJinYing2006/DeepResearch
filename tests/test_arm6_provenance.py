# -*- coding: utf-8 -*-
"""W8 Arm 6：可复现元数据完整性 —— prompt_hash / 裁判模型 / 裁判独立性 / scorer_version。

这批用例回答一个问题：**把任意一份 `summary.json` 单独拿出来，能不能回答
「这批数字是谁裁的、用的哪套提示词、尺子是不是同一把」**。在 Arm 6 之前答案是不能 ——
「裁判兼任被测」这个 W7 核心结论只写在结论文档里，产物层面看不出来。

锁定的行为：
- `prompt_hash` 覆盖的是**运行时渲染结果**，不是常量模板 ⇒ 开关翻转必须改变指纹
- 五个 system 构建器是唯一产生点 ⇒ 生产路径发出去的串与指纹用的串逐字相同
- 裁判模型走单一产生点，且 `citation_judge_independent` 恒为 False（这是事实不是配置）
- raw 的**三条**落盘路径（ok / failed / timeout）都带走全部 provenance 字段
- 历史 raw 缺失新字段时如实为 None，**不回填当期值**
- 报告必须在**指标表之前**显著呈现 `citation_judge_independent`
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from config import config
from research_engine.agents.planner import build_planner_system, build_replan_system
from research_engine.agents.validator import build_validator_system, validator_model_name
from research_engine.agents.writer import build_writer_system
from research_engine.critic import build_critic_system
from research_engine.eval.prompt_hash import PROMPT_SLOT_BUILDERS, prompt_hash, prompt_slots
from research_engine.eval.provenance import (
    CITATION_JUDGE_INDEPENDENT,
    CITATION_JUDGE_NOTE,
    PROVENANCE_DEFAULTS,
    RAW_PROVENANCE_KEYS,
    judge_provenance,
    provenance_from_raw_record,
    raw_provenance_fields,
    run_provenance,
)
from research_engine.eval.run import _provenance_from_raw, _run_one
from research_engine.state import ResearchFinding

EXPECTED_SLOTS = {"planner", "planner_replan", "critic", "writer", "validator", "coverage_judge"}


# ---------- 1. prompt 指纹：覆盖运行时渲染，不是常量模板 ----------

def test_prompt_slots_covers_all_six():
    """新增提示词必须在 PROMPT_SLOT_BUILDERS 登记，否则指纹会静默漏掉一段。"""
    slots = prompt_slots()
    assert set(slots) == EXPECTED_SLOTS
    assert all(len(v) == 16 for v in slots.values())
    assert len(set(slots.values())) == len(slots), "有两个 slot 渲染出相同内容，疑似登记错函数"


def test_prompt_hash_is_deterministic():
    """同一时刻两次调用必须相同 —— 否则跨 run 比对毫无意义。"""
    assert prompt_hash() == prompt_hash()
    assert prompt_hash(prompt_slots()) == prompt_hash()


def test_critic_gap_switch_changes_hash(monkeypatch):
    """核心：`critic_gap_enabled` 翻转 ⇒ critic slot 指纹变、其它 slot 不变。

    这一条正是 Arm 6 存在的理由 —— 哈希常量模板会得到「开关翻了但指纹没变」的假象。
    """
    base = prompt_slots()
    monkeypatch.setattr(config.experiment, "critic_gap_enabled", False)
    off = prompt_slots()
    assert off["critic"] != base["critic"]
    for k in EXPECTED_SLOTS - {"critic"}:
        assert off[k] == base[k], f"{k} 不该被 critic 开关影响"
    assert prompt_hash(off) != prompt_hash(base)


def test_writer_switch_selects_different_prompt(monkeypatch):
    monkeypatch.setattr(config.experiment, "writer_sectioned_feed_enabled", True)
    on = prompt_slots()["writer"]
    monkeypatch.setattr(config.experiment, "writer_sectioned_feed_enabled", False)
    off = prompt_slots()["writer"]
    assert on != off


def test_validator_switch_selects_different_prompt(monkeypatch):
    monkeypatch.setattr(config.experiment, "validator_fixes_enabled", True)
    on = prompt_slots()["validator"]
    monkeypatch.setattr(config.experiment, "validator_fixes_enabled", False)
    off = prompt_slots()["validator"]
    assert on != off


def test_planner_renders_max_subquestions(monkeypatch):
    """占位符要真的被渲染进 pHash 覆盖范围 —— 改预算必须改指纹。"""
    monkeypatch.setattr(config.research, "max_subquestions", 5)
    five = prompt_slots()["planner"]
    monkeypatch.setattr(config.research, "max_subquestions", 9)
    nine = prompt_slots()["planner"]
    assert five != nine
    # 渲染的是**数字本身**，占位符不能残留
    assert "{max_subquestions}" not in build_planner_system()


def test_validator_prompt_is_fully_rendered():
    """渲染后不得残留 `{min_sources}` 占位符。"""
    rendered = build_validator_system()
    assert "{min_sources}" not in rendered
    assert str(config.research.min_sources_for_crosscheck) in rendered


def test_critic_gap_extra_present_only_when_enabled(monkeypatch):
    monkeypatch.setattr(config.experiment, "critic_gap_enabled", True)
    assert "Bug-4 修复补充" in build_critic_system()
    monkeypatch.setattr(config.experiment, "critic_gap_enabled", False)
    assert "Bug-4 修复补充" not in build_critic_system()


# ---------- 2. 构建器 = 生产路径发出的串（无第二次 format）----------

def test_validator_sends_exactly_the_built_system():
    """回归防线：Arm 6 途中踩过一次「渲染后再 .format() 一遍」——
    模板里残留的 JSON 花括号会 KeyError，却被 except 兜成「LLM 调用失败」，
    报出来的症状与病根毫无关系。这里直接比对生产路径发出的 system 串。
    """
    from research_engine.agents.validator import Validator

    report = "Transformer 使用自注意力机制。[来源: 1]"
    findings = [
        ResearchFinding(content="Transformer 使用自注意力。",
                        source="https://example.com/t", source_type="web")
    ]
    with patch("research_engine.agents.validator.LLMClient") as MockClient:
        inst = MagicMock()
        inst.chat_json.return_value = {"citations": []}
        MockClient.return_value = inst
        Validator().validate(report, findings)
    sent_system = inst.chat_json.call_args[0][0][0]["content"]
    assert sent_system == build_validator_system()


# ---------- 3. 裁判 provenance ----------

def test_judge_provenance_single_source():
    jp = judge_provenance()
    assert jp["citation_judge_model"] == config.llm.validator_model
    assert jp["coverage_judge_model"] == config.llm.smart_model
    assert jp["citation_judge_independent"] is False


def test_citation_judge_model_follows_config(monkeypatch):
    monkeypatch.setattr(config.llm, "validator_model", "qwen-turbo")
    assert judge_provenance()["citation_judge_model"] == "qwen-turbo"
    assert validator_model_name() == "qwen-turbo"


def test_citation_judge_independent_is_false_with_reason():
    """这是**事实判断**：评测层不重跑裁判，直读主链路产物。改它必须先改实现。"""
    assert CITATION_JUDGE_INDEPENDENT is False
    assert "7.53pp" in CITATION_JUDGE_NOTE  # 理由必须能被机器/人查到依据


# ---------- 4. run_provenance / raw 落盘 ----------

def test_run_provenance_keys_match_raw_keys():
    prov = run_provenance()
    assert set(prov) == set(RAW_PROVENANCE_KEYS)
    picked = raw_provenance_fields(prov)
    assert set(picked) == set(RAW_PROVENANCE_KEYS)
    assert picked["prompt_hash"] == prov["prompt_hash"]
    assert picked["config_snapshot"] is prov["config_snapshot"]  # 同一对象，不手抄


def test_raw_records_all_provenance_fields(tmp_path):
    """ok 路径必须带齐全 10 个字段。"""
    prov = run_provenance()
    state = MagicMock()
    state.model_dump.return_value = {"citations": [], "findings": []}
    state.token_used = 1
    state.status = "done"
    state.error = None
    graph = MagicMock()
    graph.run.return_value = state
    res = _run_one({"id": "q1", "query": "x"}, {"version": "1.1"}, tmp_path, graph, prov)
    raw = res["raw"]
    for k in RAW_PROVENANCE_KEYS:
        assert k in raw, f"raw 缺少 provenance 字段 {k}"
    assert raw["citation_judge_independent"] is False


def test_raw_failure_path_carries_provenance(tmp_path):
    prov = run_provenance()
    g = MagicMock()
    g.run.side_effect = RuntimeError("boom")
    import research_engine.eval.run as run_mod

    orig = run_mod.RETRY_SLEEP_S
    run_mod.RETRY_SLEEP_S = 0
    try:
        res = _run_one({"id": "q9", "query": "x"}, {"version": "1.1"}, tmp_path, g, prov)
    finally:
        run_mod.RETRY_SLEEP_S = orig
    assert res["status"] == "failed"
    assert res["raw"]["prompt_hash"] == prov["prompt_hash"]
    assert res["raw"]["scorer_version"] == prov["scorer_version"]


def test_historical_raw_reads_none_not_fabricated(tmp_path):
    """Arm 6 之前的历史 raw：新字段一律 None。**绝不回填当期值** ——
    回填会抹掉「这批 run 产生于旧口径」这个事实。
    """
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "q_001.raw.json").write_text(
        json.dumps({"q_id": "q_001", "git_commit": "e" * 40, "git_dirty": True,
                    "git_diff_hash": "f" * 16, "config_snapshot": {"a": 1}}),
        encoding="utf-8",
    )
    prov = _provenance_from_raw(tmp_path)
    assert prov["git_commit"] == "e" * 40  # 老字段照旧读得到
    assert prov["config_snapshot"] == {"a": 1}
    for k in ("prompt_hash", "prompt_slots", "scorer_version",
              "citation_judge_model", "coverage_judge_model", "citation_judge_independent"):
        assert prov[k] is None, f"{k} 被伪造出来了"


def test_provenance_defaults_cover_every_key():
    assert set(PROVENANCE_DEFAULTS) == set(RAW_PROVENANCE_KEYS)
    assert provenance_from_raw_record({}) == PROVENANCE_DEFAULTS


# ---------- 5. 报告：显著呈现 ----------

def _minimal_summary() -> dict:
    return {
        "run_dir": "run_test", "total": 1,
        "metrics_ok": 1, "metrics_partial": 0, "metrics_failed": 0,
        "verdict": "ok", "verdict_reasons": [],
        "metrics_mean": {"coverage": 0.8}, "metrics_stderr": {},
        "cost_phase1_total": {"total_tokens": 10},
        "git_commit": "a" * 40, "git_dirty": False, "git_diff_hash": "",
        "config_snapshot": {"planner_model": "x"},
        "dataset_version": "1.1", "generated_at": "2026-09-17T00:00:00",
    }


def _minimal_results() -> list:
    return [{
        "q_id": "q1", "status": "ok",
        "metrics": {
            "completion": {"complete": True}, "citation": {"fidelity_rate": 0.9},
            "coverage": {"coverage": 0.8}, "retrieval_hit": {"retrieval_hit_rate": 0.7},
            "steps": {"steps": 4}, "reflection": {"stop_type": "critic_stop"},
            "insufficient": {"marker_ratio": 0.1, "section_count": 3,
                             "marked_sections": 1, "placeholder_count": 0},
        },
    }]


def test_report_prominently_shows_judge_independence(tmp_path, monkeypatch):
    """验收项：`citation_judge_independent = false` 必须在报告里显著可见，
    且必须出现在**指标表之前**（写在最后一节等于没写）。
    """
    from research_engine.eval import report_gen

    monkeypatch.setattr(report_gen, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(report_gen, "EVAL_REPORT_PATH", tmp_path / "eval-report.md")
    (tmp_path / "results" / "run_test").mkdir(parents=True)

    summary = _minimal_summary()
    summary.update(judge_provenance())
    summary["prompt_hash"] = prompt_hash()
    summary["prompt_slots"] = prompt_slots()
    summary["scorer_version"] = "w8.1"
    report_gen.generate_report(
        tmp_path / "results" / "run_test", summary, _minimal_results(), {"meta": {"version": "1.1"}}
    )
    text = (tmp_path / "eval-report.md").read_text(encoding="utf-8")

    assert "citation_judge_independent" in text
    assert "false" in text
    assert "7.53pp" in text
    assert text.index("citation_judge_independent") < text.index("## 3. 指标表")
    assert summary["prompt_hash"] in text
    assert "w8.1" in text
    assert summary["citation_judge_model"] in text


def test_report_marks_unrecorded_provenance(tmp_path, monkeypatch):
    """历史产物没有这些字段 ⇒ 报告要明说「不可追溯」，不能留空假装没事。"""
    from research_engine.eval import report_gen

    monkeypatch.setattr(report_gen, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(report_gen, "EVAL_REPORT_PATH", tmp_path / "eval-report.md")
    (tmp_path / "results" / "run_test").mkdir(parents=True)

    report_gen.generate_report(
        tmp_path / "results" / "run_test", _minimal_summary(), _minimal_results(),
        {"meta": {"version": "1.1"}},
    )
    text = (tmp_path / "eval-report.md").read_text(encoding="utf-8")
    assert "未记录" in text


def test_ruler_notes_detect_changed_ruler():
    """趋势表下的「尺子变了」检查：口径一变，delta 就不再构成趋势。"""
    from research_engine.eval.report_gen import _ruler_notes

    same = [
        {"run_id": "r1", "prompt_hash": "aaa", "scorer_version": "w8.1", "citation_judge_independent": False},
        {"run_id": "r2", "prompt_hash": "aaa", "scorer_version": "w8.1", "citation_judge_independent": False},
    ]
    assert any("✅" in line for line in _ruler_notes(same))

    changed = [
        {"run_id": "r1", "prompt_hash": "aaa", "scorer_version": "w8.1", "citation_judge_independent": False},
        {"run_id": "r2", "prompt_hash": "bbb", "scorer_version": "w8.1", "citation_judge_independent": False},
    ]
    notes = _ruler_notes(changed)
    assert any("尺子变了" in line for line in notes)
    assert any("`r1` → `r2`" in line for line in notes)


# ---------- 6. scorer_version ----------

def test_scorer_version_recorded_and_nonempty():
    from research_engine.eval.aggregate import SCORER_VERSION

    assert SCORER_VERSION
    assert run_provenance()["scorer_version"] == SCORER_VERSION


def test_builders_are_registered_slots():
    """登记表名必须与实际构建器一致（防改名后登记失效却还在跑）。"""
    _ = prompt_slots()  # 触发延迟登记
    for name in EXPECTED_SLOTS:
        assert name in PROMPT_SLOT_BUILDERS
    assert callable(PROMPT_SLOT_BUILDERS["planner_replan"]) is True
    # 构建器必须能独立调用且与哈希路径一致
    assert build_replan_system() == build_replan_system()
    assert build_writer_system() == build_writer_system()
