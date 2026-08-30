# -*- coding: utf-8 -*-
r"""ADR-0005 修复验证：多编号引用拆分。

离线测试，不调用真实 LLM。
运行：python tests/test_multi_number_citation.py

背景：Writer 的提示词要求 [来源: N] 单编号引用，但 LLM 实际输出中
经常出现 [来源: 5, 72, 77]、[来源: 5、8]、[来源: 3和7] 等多编号变体
（ADR-0002 已记录：实测 67 条引用仅 17 条通过校验）。旧正则
\[来源:\s*([^\]]+)\] 把 '5, 72, 77' 当成整体字符串去查索引，必然 miss。

原则：永远不要按理想格式假设 LLM 的输出——协议解析必须鲁棒。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.agents.validator import Validator
from research_engine.state import ResearchFinding


def make_findings(n: int = 10) -> list:
    return [
        ResearchFinding(
            content=f"来源{i}的发现内容",
            source=f"https://example.com/source-{i}",
            source_type="web",
            confidence=0.6,
        )
        for i in range(1, n + 1)
    ]


def extract(report: str) -> list:
    """提取引用并完成编号->来源映射（复刻 validate() 的存在性校验逻辑）。"""
    v = Validator()
    index = v._build_index(make_findings())
    known = set(index.values())
    out = []
    for c in v._extract_citations(report):
        real = index.get(c["source"], c["source"])
        out.append({"ref": c["source"], "mapped": real, "verified": real in known})
    return out


def main():
    print("========== 场景 1：英文逗号多编号 [来源: 5, 72, 77] ==========")
    rows = extract("某个关键论断得到了多个来源支持 [来源: 5, 72, 77]。")
    assert len(rows) == 3, f"应拆分为 3 条，实际 {len(rows)}"
    assert rows[0] == {"ref": "5", "mapped": "https://example.com/source-5", "verified": True}
    assert rows[1] == {"ref": "72", "mapped": "72", "verified": False}  # 越界编号：存在性校验失败
    assert rows[2] == {"ref": "77", "mapped": "77", "verified": False}
    for r in rows:
        print(f"  ref={r['ref']:>3} → {r['mapped']:<35} verified={r['verified']}")

    print("\n========== 场景 2：中文分隔符 [来源: 5、8] / [来源: 3和7] ==========")
    rows = extract("论断A [来源: 5、8]。论断B [来源: 3和7]。")
    assert [r["ref"] for r in rows] == ["5", "8", "3", "7"], [r["ref"] for r in rows]
    assert all(r["verified"] for r in rows)
    print(f"  顿号/和 均正确拆分：{[r['ref'] for r in rows]}，全部 verified=True ✅")

    print("\n========== 场景 3：单编号不受影响 [来源: 5] ==========")
    rows = extract("单编号引用 [来源: 5]。")
    assert len(rows) == 1 and rows[0]["verified"] and rows[0]["ref"] == "5"
    print(f"  ref=5 → {rows[0]['mapped']} ✅")

    print("\n========== 场景 4：URL 协议兼容（不拆分） ==========")
    rows = extract("直接引用真实来源 [来源: https://example.com/source-3]。")
    assert len(rows) == 1
    assert rows[0]["verified"] and rows[0]["mapped"] == "https://example.com/source-3"
    print(f"  URL 原样保留且校验通过 ✅")

    print("\n========== 场景 5：数字+URL 混合（不拆分，整体视为来源） ==========")
    rows = extract("混合内容 [来源: 5, https://example.com/source-5]。")
    assert len(rows) == 1, "含非数字 token 时不应拆分"
    print(f"  整体作为单一来源处理：{rows[0]['ref']}（verified={rows[0]['verified']}）✅")

    print("\n========== 场景 6：旧 bug 复现对照 ==========")
    import re
    old_pattern = r"\[来源:\s*([^\]]+)\]"
    m = re.search(old_pattern, "对照 [来源: 5, 72, 77]。")
    old_ref = m.group(1).strip()
    print(f"  旧逻辑捕获整串：'{old_ref}' → 索引 miss → verified=False ❌")
    print(f"  新逻辑拆分后：5 ✅ / 72 ❌ / 77 ❌（各自独立校验）")

    print("\n========== 全部断言通过：多编号引用拆分正确 ==========")


if __name__ == "__main__":
    main()
