"""需求 36 H1：澄清判定 Agent（提交后、规划前；TBD-1 / §3）。

职责（单点）：
- 判定「当前研究范围是否清晰到可以直接规划」；
- 不清晰 ⇒ 生成 1~3 个澄清问题（作为 interrupt 卡片的 content）。

边界（纪律）：
- **失败即跳过**（零阻塞）：LLM/解析失败 ⇒ `needed=False + failed=True`，由图节点记
  治理事件（`planner_events` 同构，不进 `degradation_log`、不推 `degraded` —— TBD-1）；
- 本 Agent 不做轮次控制：轮次上限由 graph 条件边按 `config.hitl.max_clarify_rounds` 执行；
- 关闭态（开关 false）不进入图，本模块不被调用（关闭态零漂移）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from research_engine.llm.router import get_router
from research_engine.usage import UsageSinkError

CLARIFIER_SYSTEM = """你是一位资深研究规划专家。研究即将开始前，你需要判断「研究范围是否清晰」。

判断标准（满足任一即视为**不清晰**，需要澄清）：
1. 主题过于宽泛，存在多个明显不同的研究方向（需要用户选择侧重）；
2. 关键约束缺失且会显著影响结论（如时间范围、地域范围、口径定义、对象范围）；
3. 用户附加要求与主题之间存在需要对齐的模糊点。

不要为了提问而提问：能合理默认的直接默认，**只有确实会影响研究方向的歧义才提问**。
若此前的澄清回答已经消除歧义，必须判定为不需要再问。

请以 JSON 格式输出：
{
  "needed": true/false,
  "questions": ["澄清问题1（不超过3个，每个问题一句话）"],
  "reason": "为什么需要/不需要澄清（一句话）"
}
"""

#: 单轮问题数上限（>0 且 ≤3；§3「可一次含 1~3 问」）
MAX_QUESTIONS = 3


def build_clarifier_system() -> str:
    """澄清系统提示词的唯一产生点（W8 Arm 6 口径：提示词指纹依赖纯函数）。"""
    return CLARIFIER_SYSTEM


def _prior_clarification_text(clarification: Optional[Dict[str, Any]]) -> List[str]:
    """把已有澄清轮次渲染成提示词片段（空则返回空列表，不污染 prompt）。"""
    clarification = clarification or {}
    lines: List[str] = []
    questions = [str(q).strip() for q in (clarification.get("questions") or []) if str(q).strip()]
    if questions:
        lines.append("上一轮问题：" + "；".join(questions))
    for item in clarification.get("answers") or []:
        text = str((item or {}).get("text") or "").strip()
        if text:
            lines.append(f"用户回答：{text}")
    return lines


class Clarifier:
    """澄清判定器（失败即跳过；不做轮次控制）。"""

    def decide(
        self,
        topic: str,
        user_instructions: str = "",
        clarification: Optional[Dict[str, Any]] = None,
        state: Any = None,
    ) -> Dict[str, Any]:
        """判定是否需要澄清。

        返回统一形状：`{"needed": bool, "questions": [str], "reason": str, "failed": bool}`。
        `failed=True` 表示判定失败（按「跳过澄清」继续，由图节点记治理事件）。
        """
        user = f"研究主题：{topic}\n"
        if user_instructions:
            user += f"用户附加要求：{user_instructions}\n"
        prior = _prior_clarification_text(clarification)
        if prior:
            user += "已进行的澄清：\n" + "\n".join(prior) + "\n"
        user += "请判断是否需要进一步澄清。"

        try:
            data = get_router().strategic_json(build_clarifier_system(), user, state=state)
        except UsageSinkError:
            # F06：strict 记账失败必须上抛（不得被当成「跳过澄清」溶掉）
            raise
        except Exception as exc:  # noqa: BLE001 —— 失败即跳过（零阻塞，TBD-1）
            return {"needed": False, "questions": [],
                    "reason": f"{type(exc).__name__}: {exc}"[:200], "failed": True}

        if not isinstance(data, dict):
            return {"needed": False, "questions": [], "reason": "非 JSON 对象输出",
                    "failed": True}
        needed = bool(data.get("needed"))
        out_questions = [
            str(q).strip()[:500]
            for q in (data.get("questions") or [])
            if str(q).strip()
        ][:MAX_QUESTIONS]
        reason = str(data.get("reason") or "").strip()[:300]
        if needed and not out_questions:
            # 判定要问但没给出问题：按「跳过」处理（不可让空卡阻塞流程）
            return {"needed": False, "questions": [],
                    "reason": reason or "needed=true 但无问题", "failed": True}
        return {"needed": needed, "questions": out_questions, "reason": reason,
                "failed": False}
