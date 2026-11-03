"""注入确定性预检与输出泄漏过滤（P2-1a）。

行业共识（OWASP LLM01:2026 / Prompt Injection Cheat Sheet）：
- 拦截式防御不可靠，但**确定性层**仍有价值：输入侧的显式注入模式预检、
  输出侧对**有限密钥/系统提示片段集合**的泄漏扫描（实测最稳的一层）；
- 检索内容中的注入字样**不作为整单拒绝依据**（误伤面大，且正常讨论注入的文章
  也会命中）——由 `strip_invisible` 净化 + 调用方按需记录。

输出泄漏标记的**单一来源**（审计 #333）：标记从 `research_engine` 里真实的 system
提示词派生（取每个提示词首句），而不是在 web 层写死文案——旧实现写死的
「你是一名严谨的研究报告撰写者」全仓不存在（死标记），导致 Writer/Validator/Critic
三个主提示的泄漏扫描完全失效；提示词演进时本模块的标记自动跟随。
部署方仍可用 `DR_OUTPUT_LEAK_MARKERS`（逗号分隔）追加自定义标记。

边界：本模块是纵深的一层，不是安全边界；真正的边界是工具最小权限、
确定性中介（凭据/状态变更留在应用代码）与人工复核（见上线清单 §3.5）。
"""
from __future__ import annotations

import os
import re
from typing import Iterable, Optional

#: 输入侧注入模式（窄口径：显式指令覆盖/系统提示索取；中英文）
_INPUT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ignore_instructions", r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions"),
    ("disregard_instructions", r"(?i)disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)"),
    ("override_instructions", r"(?i)(your\s+new\s+instructions|new\s+system\s+prompt)"),
    ("reveal_system_prompt", r"(?i)(reveal|show|print|repeat)\s+(me\s+)?(your\s+)?(system\s+)?(prompt|instructions)"),
    ("zh_ignore", r"忽略(以上|之前|前面|先前).{0,12}(指令|指示|要求|规则|提示)"),
    ("zh_reveal", r"(输出|显示|告诉|重复|泄露).{0,10}(系统提示|系统指令|system\s*prompt)"),
    ("zh_override", r"(从现在开始|接下来).{0,8}(你是|扮演|新的(角色|指令))"),
)

_COMPILED_INPUT = tuple((name, re.compile(pattern)) for name, pattern in _INPUT_PATTERNS)


def _prompt_marker(prompt: Optional[str], *, min_chars: int = 8,
                   max_chars: int = 40) -> str:
    """取系统提示词的**首句**作为确定性泄漏标记（首句不足时取前 max_chars）。

    标记必须原样出现在泄漏文本中才能命中，因此取提示词开头的固定片段（不跨行、
    不做归一化）；提示词演进时标记自动跟随（单一来源）。
    """
    first_line = next(
        (line.strip() for line in (prompt or "").splitlines() if line.strip()), "")
    head, sep, _ = first_line.partition("。")
    marker = head + sep if sep else first_line[:max_chars]
    return marker if len(marker) >= min_chars else ""


#: 派生标记缓存（惰性 import research_engine 提示词，避免 web/backend 模块导入期耦合）
_SYSTEM_PROMPT_MARKERS: Optional[tuple[str, ...]] = None


def _system_prompt_markers() -> tuple[str, ...]:
    """从真实 system prompt 单一来源派生泄漏标记（审计 #333）。

    惰性 import：`injection_guard` 属 web/backend，提示词属 research_engine；
    单向依赖（engine 不 import web），但延迟到首次扫描可避免导入期把引擎依赖
    拖进纯 web 模块。prompt 常量变更时这里自动跟随（测试保证每个提示词可命中）。
    """
    global _SYSTEM_PROMPT_MARKERS
    if _SYSTEM_PROMPT_MARKERS is None:
        from research_engine.agents.clarifier import CLARIFIER_SYSTEM
        from research_engine.agents.planner import (
            PLANNER_SYSTEM,
            REPLAN_SYSTEM,
            REVISE_SYSTEM,
        )
        from research_engine.agents.validator import (
            VALIDATOR_SYSTEM,
            VALIDATOR_SYSTEM_LEGACY,
        )
        from research_engine.agents.writer import (
            WRITER_SYSTEM,
            WRITER_SYSTEM_LEGACY,
        )
        from research_engine.critic import CRITIC_SYSTEM_BASE

        prompts = (
            PLANNER_SYSTEM, REPLAN_SYSTEM, REVISE_SYSTEM, CLARIFIER_SYSTEM,
            WRITER_SYSTEM, WRITER_SYSTEM_LEGACY,
            VALIDATOR_SYSTEM, VALIDATOR_SYSTEM_LEGACY,
            CRITIC_SYSTEM_BASE,
        )
        _SYSTEM_PROMPT_MARKERS = tuple(dict.fromkeys(
            marker for marker in (_prompt_marker(prompt) for prompt in prompts) if marker))
    return _SYSTEM_PROMPT_MARKERS


def scan_injection(text: Optional[str]) -> list[str]:
    """输入侧注入模式预检；返回命中的模式名（不返回原文）。"""
    if not text:
        return []
    hits: list[str] = []
    for name, pattern in _COMPILED_INPUT:
        if pattern.search(text) and name not in hits:
            hits.append(name)
    return hits


def _leak_markers(extra: Optional[Iterable[str]] = None) -> tuple[str, ...]:
    configured = tuple(
        item.strip() for item in (os.getenv("DR_OUTPUT_LEAK_MARKERS") or "").split(",")
        if item.strip())
    return _system_prompt_markers() + tuple(extra or ()) + configured


def scan_output_leak(text: Optional[str], *, extra_markers: Optional[Iterable[str]] = None) -> list[str]:
    """输出泄漏扫描（确定性）：正文包含系统提示片段 ⇒ 返回命中标记。

    只用于**有限集合**（系统提示独特片段 / 部署方扩展标记），不做通用正则猜测；
    命中即按 P0-4 输出闸处置（脱敏 + flagged + 人工复核）。
    """
    if not text:
        return []
    hits: list[str] = []
    for marker in _leak_markers(extra_markers):
        if marker and marker in text and marker not in hits:
            hits.append(marker)
    return hits
