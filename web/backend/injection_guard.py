"""注入确定性预检与输出泄漏过滤（P2-1a）。

行业共识（OWASP LLM01:2026 / Prompt Injection Cheat Sheet）：
- 拦截式防御不可靠，但**确定性层**仍有价值：输入侧的显式注入模式预检、
  输出侧对**有限密钥/系统提示片段集合**的泄漏扫描（实测最稳的一层）；
- 检索内容中的注入字样**不作为整单拒绝依据**（误伤面大，且正常讨论注入的文章
  也会命中）——由 `strip_invisible` 净化 + 调用方按需记录。

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

#: 输出泄漏标记（有限集合：本系统提示词的独特片段；可由环境变量扩展）
_SYSTEM_PROMPT_MARKERS: tuple[str, ...] = (
    "你是一位资深研究规划专家",
    "PLANNER_SYSTEM",
    "REPLAN_SYSTEM",
    "CRITIC_SYSTEM",
    "VALIDATOR_SYSTEM",
    "你是一名严谨的研究报告撰写者",
    "You are a deep research assistant",
)


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
    return _SYSTEM_PROMPT_MARKERS + tuple(extra or ()) + configured


def scan_output_leak(text: Optional[str], *, extra_markers: Optional[Iterable[str]] = None) -> list[str]:
    """输出泄漏扫描（确定性）：正文包含系统提示片段 ⇒ 返回命中标记名。

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
