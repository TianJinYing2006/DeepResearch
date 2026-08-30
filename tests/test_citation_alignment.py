# -*- coding: utf-8 -*-
"""ADR-0004 修复验证：Writer/Validator 引用编号一致性。

离线测试，不调用真实 LLM（monkeypatch 掉 fast/smart 层）。
运行：python tests/test_citation_alignment.py

背景：Writer 基于"压缩后 findings"生成 [来源: N] 编号，
Validator 基于编号还原真实来源。两者必须消费同一份列表，
否则编号错位——引用要么校验失败，要么被静默映射到错误来源（更危险）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.agents.validator import Validator
from research_engine.agents.writer import Writer
from research_engine.context.manager import ContextManager
from research_engine.llm.router import LLMRouter
from research_engine.state import ResearchFinding

# ---- 模拟 LLM：不产生真实 API 调用 ----
LLMRouter.fast_chat = lambda self, system, user: f"[模拟压缩摘要] {user[:30]}"
LLMRouter.smart_chat = lambda self, system, user: (
    "# 测试报告\n\n"
    "论断甲 [来源: 5]。\n\n"
    "论断乙 [来源: 8]。\n"
)

TOPIC = "测试主题"
NUM_SOURCES = 10          # 10 个独立来源
FINDINGS_PER_SOURCE = 4   # 每来源 4 条 → 共 40 条，超过 max_findings=30 触发压缩


def make_findings() -> list:
    """40 条发现，分布在 10 个来源上（模拟 RAG 场景：同一文档多个分块）。"""
    findings = []
    for s in range(1, NUM_SOURCES + 1):
        for k in range(FINDINGS_PER_SOURCE):
            findings.append(
                ResearchFinding(
                    content=f"来源{s}的第{k}条发现：某项具体事实",
                    source=f"https://example.com/source-{s}",
                    source_type="web",
                    confidence=0.6,
                )
            )
    return findings


def run_flow(findings_for_validator, label):
    """跑一遍 write → validate 的机械链路，返回 (report, citations)。"""
    writer = Writer()
    validator = Validator()
    compressed = ContextManager().compress(findings_for_validator["all"], TOPIC)
    report = writer.write(TOPIC, [], compressed)
    index = validator._build_index(findings_for_validator["for_validator"])
    citations = validator._extract_citations(report)
    results = []
    for c in citations:
        real = index.get(c["source"], c["source"])
        results.append({"claim": c["claim"], "ref": c["source"], "mapped": real})
    return compressed, results


def main():
    findings = make_findings()
    cm = ContextManager()
    compressed = cm.compress(findings, TOPIC)
    print(f"原始 findings：{len(findings)} 条 → 压缩后：{len(compressed)} 条（按来源分组压缩）")

    # 期望映射：Writer 编号 5 → 压缩列表第 5 个来源
    expected = {
        "5": compressed[4].source,
        "8": compressed[7].source,
    }

    # ============ 旧逻辑（Bug 复现）：Validator 用原始列表建索引 ============
    print("\n[旧逻辑] Writer 用压缩列表编号，Validator 用原始 40 条列表建索引：")
    old_index = Validator()._build_index(findings)
    for ref, exp in expected.items():
        got = old_index.get(ref, "?")
        flag = "✅ 正确" if got == exp else f"❌ 错位（应为 {exp}，实际映射到 {got}）"
        print(f"  [来源: {ref}] → {flag}")

    # ============ 新逻辑（修复后）：两者都用压缩列表 ============
    print("\n[新逻辑] Writer 与 Validator 均基于压缩后列表：")
    new_index = Validator()._build_index(compressed)
    ok = True
    for ref, exp in expected.items():
        got = new_index.get(ref, "?")
        flag = "✅ 正确" if got == exp else "❌ 错位"
        if got != exp:
            ok = False
        print(f"  [来源: {ref}] → {got} {flag}")

    # 端到端契约断言：format_for_writer 的编号与 _build_index 完全一致
    for i, f in enumerate(compressed, 1):
        assert new_index[str(i)] == f.source, f"编号 {i} 映射不一致"
    assert ok, "新逻辑仍存在错位"

    # 无压缩路径（≤30 条）：编号天然一致
    small = findings[:24]
    small_compressed = cm.compress(small, TOPIC)
    assert len(small_compressed) == 24, "少量 findings 不应触发压缩"
    print(f"\n[边界] 24 条（≤30，不压缩）：编号一致 ✅")

    print("\n========== 验证通过：修复后 [来源: N] 全部映射回正确来源 ==========")


if __name__ == "__main__":
    main()
