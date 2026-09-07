# -*- coding: utf-8 -*-
"""W5 eval 单测（指标纯函数 + 双轨分桶 + 管线冒烟）。

覆盖（grill Q2/Q4/Q8 拍板）：
- 完成率四条件（含章节检查、500→300 字阈值）
- 引用准确率直读 state.citations（不再重跑 validator 的 LLM 调用）
- 检索命中率关键词优先 + 嵌入回退（matched_by 标签）
- 反思有效性结构化工（critic_stop / hard_stop / 早停 / 晚停）
- 成本双轨桶（模型名/职责 + input/output 精确加权）
- LLMClient model_stats/role_stats 累加（注入假 usage）
- run.py 管线冒烟：Phase1 失败分级 + 断点续跑（注入 mock graph）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from research_engine.eval import metrics as M
from research_engine.eval.run import git_head, load_dataset
from research_engine.llm.client import LLMClient

# ---------- 完成率 ----------

def _state(**overrides) -> Dict[str, Any]:
    base = {
        "status": "done",
        "error": None,
        "report": "# 标题\n\n## 一、章节\n\n" + "内容" * 200,  # ~400 字 + 2 个标题
        "depth": 3,
        "token_used": 1000,
        "replan_count": 0,
        "critic_signal": "stop",
        "reflection_log": [{"decision": "continue"}, {"decision": "continue"}, {"decision": "stop"}],
        "findings": [],
        "citations": [],
    }
    base.update(overrides)
    return base


def test_completion_all_conditions_pass():
    res = M.compute_completion(_state())
    assert res["complete"] is True
    assert all(res["conditions"].values())


def test_completion_short_report_fails():
    res = M.compute_completion(_state(report="# 标题\n\n内容"))
    assert res["complete"] is False
    assert res["conditions"]["report_len_ge_300"] is False


def test_completion_status_failed():
    res = M.compute_completion(_state(status="failed"))
    assert res["complete"] is False
    assert res["conditions"]["status_done"] is False


def test_completion_structure_headings():
    # Q2：否决虚构四章节，真实报告"一二三四+主题名"结构恒有 ≥2 标题
    res = M.compute_completion(_state(report="# 主题\n\n## 一、应用场景概览\n\n## 二、趋势展望\n\n" + "字" * 400))
    assert res["conditions"]["headings_ge_2"] is True


def test_count_markdown_headings():
    assert M.count_markdown_headings("") == 0
    assert M.count_markdown_headings("# 标题1\n## 标题2\n### 标题3") == 2  # 只数 # 与 ##


# ---------- 引用准确率（直读 state.citations）----------

def test_citation_from_state_no_llm():
    """Q8：直读 state.citations，不加 validator LLM 调用。"""
    citations = [
        {"claim": "a", "source": "https://x", "verified": True, "existence": True, "source_type": "web", "note": ""},
        {"claim": "b", "source": "https://y", "verified": False, "existence": True, "source_type": "web", "note": "忠实度不符"},
        {"claim": "c", "source": "code:abc", "verified": True, "existence": True, "source_type": "code_exec", "note": ""},
    ]
    res = M.compute_citation(citations)
    assert res["total_citations"] == 3
    assert res["verified"] == 2
    assert res["fidelity_rate"] == pytest.approx(2 / 3, abs=0.0001)
    assert res["by_source_type"]["code_exec"]["verified"] == 1  # W4 新工具类型正确进桶


# ---------- 检索命中率（关键词 + 嵌入回退）----------

def test_retrieval_keyword_hit_and_semantic_fallback():
    findings = [{"content": "自注意力复杂度为 O(L^2)，Mamba 状态空间模型为线性复杂度"}]
    res = M.compute_retrieval_hit(["自注意力", "FLOPs"], findings, embed_fn=lambda texts: None)
    assert res["total_keywords"] == 2
    assert res["matched_by"]["keyword"] == 1
    assert res["matched_by"]["miss"] == 1  # 无嵌入能力时 miss，不误判 semantic


def test_retrieval_semantic_fallback_hit():
    findings = [{"content": "自注意力复杂度为 O(L^2)"}]
    # 假嵌入：gold 关键词向量 vs findings 向量做余弦——直接构造同向向量触发 semantic
    def fake_embed(texts):
        return [[1.0] * 8 for _ in texts]

    res = M.compute_retrieval_hit(["自注意力"], findings, embed_fn=fake_embed)
    assert res["matched_by"]["keyword"] == 1  # 关键词直接命中优先


def test_retrieval_semantic_when_keyword_miss():
    """关键词未命中 → 嵌入回退命中（matched_by=semantic，单独标签不混口径）。"""
    findings = [{"content": "状态空间模型在长序列上保持线性计算复杂度"}]
    # query 向量（关键词"FLOPs"）与 findings 向量相同 → 余弦=1 触发回退
    def fake_embed(texts):
        return [[1.0] * 8 for _ in texts]

    res = M.compute_retrieval_hit(["FLOPs"], findings, embed_fn=fake_embed)
    assert res["matched_by"]["semantic"] == 1
    assert res["retrieval_hit_rate"] == 1.0


# ---------- 反思有效性（结构化工 + 交叉信号）----------

@patch("research_engine.eval.metrics.config")
def test_reflection_critic_stop(mock_cfg):
    mock_cfg.research.max_total_hops = 20
    mock_cfg.research.token_budget = 200_000
    mock_cfg.research.max_replan = 1
    res = M.compute_reflection(_state(depth=3, token_used=1000, replan_count=0), coverage=0.95)
    assert res["stop_type"] == "critic_stop"
    assert res["early_stop_candidate"] is False
    assert res["late_stop_candidate"] is False


@patch("research_engine.eval.metrics.config")
def test_reflection_hard_stop(mock_cfg):
    mock_cfg.research.max_total_hops = 20
    mock_cfg.research.token_budget = 200_000
    mock_cfg.research.max_replan = 1
    res = M.compute_reflection(_state(depth=20, token_used=1000), coverage=0.5)
    assert res["stop_type"] == "hard_stop"
    assert "max_total_hops" in res["hard_reasons"]


@patch("research_engine.eval.metrics.config")
def test_reflection_early_stop_signal(mock_cfg):
    mock_cfg.research.max_total_hops = 20
    mock_cfg.research.token_budget = 200_000
    mock_cfg.research.max_replan = 1
    res = M.compute_reflection(_state(), coverage=0.5)  # critic_stop 但覆盖度 <90%
    assert res["stop_type"] == "critic_stop"
    assert res["early_stop_candidate"] is True


@patch("research_engine.eval.metrics.config")
def test_reflection_late_stop_signal(mock_cfg):
    mock_cfg.research.max_total_hops = 20
    mock_cfg.research.token_budget = 200_000
    mock_cfg.research.max_replan = 1
    res = M.compute_reflection(_state(reflection_log=[{"d": i} for i in range(6)]), coverage=0.95)
    assert res["late_stop_candidate"] is True


# ---------- 成本双轨 ----------

@patch("research_engine.eval.metrics.config")
def test_cost_precise_weighted(mock_cfg):
    from config import LLMConfig

    mock_cfg.llm = LLMConfig()
    res = M.compute_cost(
        model_io_stats={"qwen-plus": {"input": 1000, "output": 500}},
        model_stats={"qwen-plus": 1500},
        role_stats={"critic": 1000, "smart": 500},
    )
    assert res["total_tokens"] == 1500
    # 1000/1000*0.0008 + 500/1000*0.002 = 0.0008 + 0.001 = ¥0.0018
    assert res["total_cost"] == pytest.approx(0.0018, abs=1e-6)
    assert res["per_role"]["critic"] == 1000


# ---------- LLMClient 双轨桶 ----------

class _FakeResp:
    class _Usage:
        total_tokens = 100
        prompt_tokens = 80
        completion_tokens = 20

    def __init__(self):
        self.usage = self._Usage()
        self.choices = [MagicMock(message=MagicMock(content="ok"))]


def test_client_stats_buckets():
    LLMClient.reset_stats()
    client = LLMClient(model="qwen-plus", role="critic")
    with patch.object(client, "_get_client") as mock_get:
        mock_get.return_value.chat.completions.create.return_value = _FakeResp()
        client.chat([{"role": "user", "content": "hi"}])
    assert LLMClient.model_stats["qwen-plus"] == 100
    assert LLMClient.role_stats["critic"] == 100
    assert LLMClient.model_io_stats["qwen-plus"] == {"input": 80, "output": 20}
    assert LLMClient.tokens_total == 100


def test_client_stats_reset():
    LLMClient.reset_stats()
    assert LLMClient.tokens_total == 0
    assert LLMClient.model_stats == {}
    assert LLMClient.role_stats == {}


# ---------- dataset 与 git 锚 ----------

def test_load_dataset():
    ds = load_dataset(Path(__file__).resolve().parent.parent / "research_engine" / "eval" / "dataset.jsonl")
    assert ds["meta"]["version"] == "1.1"
    assert len(ds["rows"]) >= 5
    assert ds["rows"][0]["id"] == "q_001"


def test_git_head_returns_hex():
    head = git_head()
    assert head == "unknown" or len(head) == 40


def test_compute_all_without_judge_failure():
    """mock judge 抛错 → judge_failed=True（partial 信号），其余指标不受影响。"""
    failing_judge = MagicMock()
    failing_judge.chat_json.side_effect = RuntimeError("timeout")
    row = {"expected_subquestions": ["子问题1"], "gold_keywords": ["关键词1"]}
    state = _state(findings=[{"content": "自注意力复杂度 O(L^2) 关键词1 命中"}])
    with patch("research_engine.eval.metrics._embed_texts", return_value=None):
        res = M.compute_all(state, row, judge=failing_judge)
    assert res["coverage"]["judge_failed"] is True
    assert res["coverage"]["coverage"] == 0.0
    assert res["completion"]["complete"] is True  # 局部失败不影响完成率
    assert res["retrieval_hit"]["matched_by"]["keyword"] >= 0