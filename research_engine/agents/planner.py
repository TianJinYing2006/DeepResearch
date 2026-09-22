"""Planner Agent：将研究主题分解为子问题。

使用 strategic 层 LLM 做高层规划，输出结构化子问题列表。
"""
from __future__ import annotations

from typing import Any, Dict, List

from config import config
from research_engine.failure_reasons import FailureReason  # W8 Arm 1
from research_engine.llm.router import get_router
from research_engine.state import DegradationEntry, DegradationSink, SubQuestion

PLANNER_SYSTEM = """你是一位资深研究规划专家。你的任务是将用户的研究主题分解为若干相互独立、可执行的子问题。

要求：
1. 每个子问题应聚焦一个可独立检索的方面
2. 子问题之间尽量不重叠
3. 数量控制在 {max_subquestions} 个以内
4. 为每个子问题说明研究它的理由
5. 严格按重要性降序输出，列表越靠前优先级越高
6. 不要为凑数量输出低价值或重复的子问题；只能拆出 2 个就输出 2 个
7. 超过数量上限时，**尾部**子问题会被系统丢弃，最重要的内容必须放在前面

请以 JSON 格式输出，结构如下：
{{
  "subquestions": [
    {{"id": "q1", "question": "子问题内容", "rationale": "研究理由"}}
  ]
}}
"""


REPLAN_SYSTEM = """你是一位资深研究规划专家。之前的子问题分解在研究中被 critic 判定为方向跑偏。
请基于【研究主题】【现有子问题】【已收集发现】【重规划原因】重新分解出更优的子问题集合。

要求：
1. 数量控制在 {max_subquestions} 个以内
2. 修正之前方向跑偏的问题，保留仍有价值的角度
3. 每个子问题聚焦一个可独立检索的方面
4. 严格按重要性降序输出；超过上限时**尾部**会被系统丢弃

请以 JSON 格式输出：
{{
  "subquestions": [
    {{"id": "q1", "question": "子问题内容", "rationale": "研究理由"}}
  ]
}}
"""


def build_planner_system(cfg=None) -> str:
    """Planner system 提示词的**唯一产生点**（W8 Arm 6）。

    为什么要抽成纯函数：`prompt_hash` 必须覆盖「**实际跑起来的**提示词」，
    而 planner 的提示词含 `max_subquestions` 占位符 —— 哈希常量模板会漏掉
    「配置改了但模板没改」这类变更。抽成 ``cfg`` 的纯函数后，指纹模块只要
    喂同一份 config 就能拿到与生产逐字相同的串。
    """
    c = cfg if cfg is not None else config
    return PLANNER_SYSTEM.format(max_subquestions=c.research.max_subquestions)


def build_replan_system(cfg=None) -> str:
    """重规划 system 提示词的唯一产生点（同上）。"""
    c = cfg if cfg is not None else config
    return REPLAN_SYSTEM.format(max_subquestions=c.research.max_subquestions)


class Planner:
    """研究规划器。"""

    def __init__(self):
        # W8 Arm 1：降级记录缓冲区（同 Researcher，由 graph 节点 drain 后入 state）
        self.degradations = DegradationSink()

    def drain_degradations(self) -> List[DegradationEntry]:
        """取走并清空降级记录（graph 节点调用）。"""
        return self.degradations.drain_degradations()

    # ---- 输出收口：解析规范化 + 数量截断（plan / replan 共用，避免只修一处）----

    def _parse_subquestions(self, data: Dict[str, Any]) -> List[SubQuestion]:
        """把 LLM 的 JSON 解析成规范化子问题（过滤空问题 + 重写重复 ID）。

        重复 ID 是隐性 bug 源：`per_subq_hop` 按 ``sq_id`` 计数，两个子问题共用
        ``q1`` 会**共享跳数配额**（错误限流），且 findings 的 ``sq_id`` 归属会混淆。
        """
        result: List[SubQuestion] = []
        used_ids: set[str] = set()
        for item in data.get("subquestions", []):
            question = str(item.get("question", "")).strip()
            if not question:
                continue  # 空问题无检索意义，直接丢弃（尾部兜底见 _bound_*）
            raw_id = str(item.get("id", "")).strip()
            sq_id = raw_id or f"q{len(result) + 1}"
            if sq_id in used_ids:
                index = len(result) + 1
                sq_id = f"q{index}"
                while sq_id in used_ids:
                    index += 1
                    sq_id = f"q{index}"
            used_ids.add(sq_id)
            result.append(
                SubQuestion(
                    id=sq_id,
                    question=question,
                    rationale=str(item.get("rationale", "")).strip(),
                )
            )
        return result

    def _bound_subquestions(self, subs: List[SubQuestion], phase: str) -> List[SubQuestion]:
        """按 ``max_subquestions`` 截断并**留痕**（plan / replan 唯一收口点）。

        截断一律记 ``planner_output_truncated``：项目原则是「降级是信号，不静默」
        （见 failure_reasons.D-03 的反向论证）。detail 用 ``k=v; k=v`` 稳定格式而非
        自由中文，便于后续统计 / 告警（需要更强可观测时再给 DegradationEntry 加
        ``metadata`` 字段）。
        """
        limit = config.research.max_subquestions
        if len(subs) <= limit:
            return subs
        kept = subs[:limit]
        self.degradations._record_degradation(
            component="planner",
            reason=FailureReason.PLANNER_OUTPUT_TRUNCATED.value,
            detail=(f"phase={phase}; returned={len(subs)}; accepted={len(kept)}; "
                    f"dropped={len(subs) - len(kept)}; limit={limit}"),
            fallback_action="truncate_subquestions",
            node="planner",
        )
        return kept

    def plan(self, topic: str, user_instructions: str = "", state: Any = None) -> List[SubQuestion]:
        router = get_router()
        system = build_planner_system()
        user = f"研究主题：{topic}\n"
        if user_instructions:
            user += f"用户附加要求：{user_instructions}\n"
        user += "请分解为子问题。"

        try:
            data = router.strategic_json(system, user, state=state)
            subs = self._parse_subquestions(data)
            if not subs:
                # 解析后一个不剩（空列表 / 全是空 question）：与 LLM 失败后果相同 ——
                # frontier 空 ⇒ critic 立刻 stop ⇒ 报告空跑。必须退化到「主题即子问题」。
                self.degradations._record_degradation(
                    component="llm",
                    reason=FailureReason.LLM_ERROR.value,
                    detail="planner 返回的子问题解析后为空",
                    fallback_action="topic_only",
                    node="planner",
                )
                return [SubQuestion(id="q1", question=topic, rationale="主题本身作为研究问题")]
            return self._bound_subquestions(subs, phase="plan")
        except Exception as e:  # noqa: BLE001
            # 降级：把主题本身作为唯一子问题
            # W8 Arm 1：原先静默降级、事后不可归因 ⇒ 留痕（llm_error，非工具类枚举）
            self.degradations._record_degradation(
                component="llm",
                reason=FailureReason.LLM_ERROR.value,
                detail=str(e),
                fallback_action="topic_only",
                node="planner",
            )
            return [SubQuestion(id="q1", question=topic, rationale="主题本身作为研究问题")]

    def replan(
        self,
        topic: str,
        subs: List[SubQuestion],
        findings: List[Any],
        reason: str,
        state: Any = None,
    ) -> List[SubQuestion]:
        """方向跑偏时的全量重分解（Q2-B 兜底，受 max_replan 限次）。"""
        router = get_router()
        system = build_replan_system()
        subs_text = "\n".join(f"- {s.id}: {s.question}" for s in subs) or "（无）"
        find_text = "\n".join(f"- {getattr(f, 'content', '')[:150]}" for f in findings[:12]) or "（无）"
        user = (
            f"研究主题：{topic}\n\n"
            f"现有子问题：\n{subs_text}\n\n"
            f"已收集发现：\n{find_text}\n\n"
            f"重规划原因：{reason}\n\n请重新分解。"
        )
        try:
            data = router.strategic_json(system, user, state=state)
            new_subs = self._parse_subquestions(data)
            if not new_subs:
                return subs  # 解析后为空 ⇒ 沿用旧子问题，不空转
            return self._bound_subquestions(new_subs, phase="replan") or subs
        except Exception:  # noqa: BLE001
            # 降级：沿用旧子问题，不空转
            return subs
