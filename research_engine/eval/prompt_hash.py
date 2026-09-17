"""W8 Arm 6：提示词指纹（prompt_hash）——「这次跑的到底是哪套提示词」的唯一答案。

动机
----
§10.4 已经把「代码修订（`git_commit` + `git_diff_hash`）」和「配置生效值
（`config_snapshot`）」都记进了 provenance，看起来提示词应该已经被兜住了。
**但这恰恰是本模块存在的理由**：

1. **提示词的选版逻辑藏在代码分支里，不体现在任何配置字段上。**
   `critic_gap_enabled` 打开时会往 critic 提示词里插一段裁决标准；
   `writer_sectioned_feed_enabled` 会在两套 writer 提示词间切换；
   `validator_fixes_enabled` 会在两套判据间切换（`VALIDATOR_SYSTEM` vs `_LEGACY`）。
   这三个开关**都在** `config_snapshot["experiment"]` 里 —— 但读者要回答
   「提示词变了吗」必须自己再推一层：开关 → 分支 → 哪套提示词。**推导链越长越容易断**，
   而 W7 的教训正是「跨区块口径不一致，直到分析阶段才发现」。

2. **要的是「能直接比对的一个值」。** 前后基线要判断「提示词是否一致」，
   逐字 diff 六段中文不现实。`prompt_hash` 把这件事压成一个 16 位串：
   相同 ⇒ 提示词逐字相同；不同 ⇒ 必有一处变了，再看 `prompt_slots` 定位哪一段。

覆盖范围（**诚实标注**）
------------------------
- ✅ **覆盖**：所有 prompt slot 的 **system 段渲染结果**。全部与配置相关的变体
  （开关选版、`max_subquestions`、`min_sources` 渲染、critic 的 `gap_extra`）
  都发生在 system 段 ⇒ 指纹对「配置变了但代码没变」敏感。
- ❌ **不覆盖**：user 段。它含运行时数据（topic / findings / 第几轮反思），
  逐条都不同，哈希它得到的不是「提示词指纹」而是「输入数据指纹」，
  失去跨 run 可比性。user 段的**模板**是代码里的常量，由 `git_commit` 兜住。

契约
----
- slot 名 → 构建器只在此处登记；新增提示词必须在这里加一行（否则指纹会静默漏掉它）。
- 构建器一律是 `cfg` 的纯函数，零 LLM、零网络、零副作用。
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict, Optional

# slot 名 → 构建器。**新增提示词必须在此登记**（测试会断言 slot 数不回退）。
PROMPT_SLOT_BUILDERS: Dict[str, Callable[[], str]] = {}


def _register() -> None:
    """延迟登记：避免在 import 期就把 4 个 agent 模块拉进来（保持 eval 侧轻量）。"""
    if PROMPT_SLOT_BUILDERS:
        return
    from research_engine.agents.planner import build_planner_system, build_replan_system
    from research_engine.agents.validator import build_validator_system
    from research_engine.agents.writer import build_writer_system
    from research_engine.critic import build_critic_system
    from research_engine.eval.metrics import build_coverage_judge_system

    PROMPT_SLOT_BUILDERS.update(
        {
            "planner": build_planner_system,
            "planner_replan": build_replan_system,
            "critic": build_critic_system,
            "writer": build_writer_system,
            "validator": build_validator_system,
            "coverage_judge": build_coverage_judge_system,
        }
    )


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prompt_slots() -> Dict[str, str]:
    """每个 slot 渲染后 system 提示词的 sha256 前 16 位。"""
    _register()
    return {name: _sha16(fn()) for name, fn in sorted(PROMPT_SLOT_BUILDERS.items())}


def prompt_hash(slots: Optional[Dict[str, str]] = None) -> str:
    """全局提示词指纹 = 各 slot 哈希的**有序**二次哈希（有序 ⇒ 改名/增删会改变结果）。"""
    s = slots if slots is not None else prompt_slots()
    canonical = "\n".join(f"{k}:{v}" for k, v in sorted(s.items()))
    return _sha16(canonical)
