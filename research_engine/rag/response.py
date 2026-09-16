"""本地检索的结构化响应（W8 Arm 4，决策 **D-02**）。

背景
----
改造前 ``HybridRetriever.retrieve()`` 返回裸 ``List[dict]``，于是 ``[]`` 这一个值
同时表达了六种互不相干的事实：

1. 正常检索，但确实没命中；
2. RAG 未配置（没 API key / 没向量库地址）；
3. 向量库不可用（连不上 / 冷却中）；
4. embedding 调用失败；
5. store 查询失败；
6. 某个 hybrid backend 失败，另一个成功了。

**信息在到达 researcher 之前就已经丢失**，所以 researcher 只能写一个说不清原因的
降级条目，故障不可归因 —— 这与 W8 的主目标（故障透明、可归因）正面冲突。

与 ``SearchResponse`` 的关系
----------------------------
``SearchResponse``（``research_engine/search/base.py``）表达的是**外部搜索 provider**
的响应（Bocha / arXiv）；``RetrieveResponse`` 表达的是**本地 / 向量 / 混合检索器**的
响应。两者**分离但共享**：

* 共用 ``research_engine.failure_reasons.FailureReason`` 一张枚举表；
* 共用 ``failure_detail`` / ``ok`` 与「单向派生到 ``DegradationEntry``」的语义。

不复用 ``SearchResponse`` 是因为强行共用一个类会在后续扩展 rerank / source score /
document metadata 时持续变形（``results`` 与 ``items`` 命名打架、provider 级错误与
retriever 级错误混在一起）。

消费方约定
----------
``failure_reason`` 是**工具层唯一产生点**；researcher 侧**单向派生**
（``DegradationEntry(reason=resp.failure_reason, ...)``），**禁止手写第二个字面量**。

⚠️ **`empty_result` 不上抛为 run 级降级**（决策 D-03）：``resolve_run_status()``
（``state.py:229``）的规则是「``degradation_log`` 非空 + 有报告 ⇒ ``degraded``」，
若把「检索零命中」也算降级条目，几乎每轮 run 都会变 ``degraded``，``run_status``
就不再是健康度信号。**零命中是结果、不是故障**，它记在 ``failure_reason`` 里供
planner 当轮决策（换源 / 改查询），但不进 ``degradation_log``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from research_engine.failure_reasons import FailureReason


@dataclass
class BackendFailure:
    """单个 hybrid backend 的失败事实（部分失败时保留，不被「另一路有结果」吞掉）。

    ``backend`` 取 ``"vector"`` / ``"bm25"`` —— 混合检索允许一路失败、另一路成功，
    这种情况**不能**表示成 ``failure_reason = None`` 了事，否则失败事实被吞。
    """

    backend: str
    #: ``FailureReason`` 工具类 5 值之一
    reason: str
    detail: str = ""


@dataclass
class RetrieveResponse:
    """本地混合检索的结构化响应。

    判定矩阵（实现与测试共同遵守）：

    | 情况                       | items | failure_reason     | backend_failures | 是否进 degradation_log |
    | -------------------------- | ----- | ------------------ | ---------------- | --------------------- |
    | 正常命中                   | 非空  | ``None``           | 空               | 否                    |
    | 检索成功但零命中           | 空    | ``empty_result``   | 空               | **否**（是结果非故障）|
    | RAG 未配置                 | 空    | ``not_configured`` | 视情况           | 是                    |
    | 向量库不可用 / 查询失败     | 空    | ``provider_error`` | 视情况           | 是                    |
    | 解析失败                   | 空/部分 | ``parse_error``  | 视情况           | 是                    |
    | 部分 backend 失败但有结果   | 非空  | ``None``           | 非空             | **是**（逐条 backend）|
    """

    query: str
    #: 融合后的命中项 ``[{text, score, source, doc}]``，与改造前的 ``List[dict]`` 元素同构
    items: List[dict] = field(default_factory=list)
    #: :class:`~research_engine.failure_reasons.FailureReason` 工具类 5 值；成功时为 ``None``
    failure_reason: Optional[str] = None
    #: 自由文本补充（原始异常摘要等），仅用于排障，不参与枚举统计
    failure_detail: str = ""
    #: 部分失败时逐 backend 保留失败事实（不被另一路成功吞掉）
    backend_failures: List[BackendFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """是否无任何失败原因（含「零命中」也视为不 ok —— 它没交付证据）。"""
        return self.failure_reason is None

    @property
    def has_results(self) -> bool:
        """是否检索到了命中项。"""
        return bool(self.items)

    @property
    def has_fault(self) -> bool:
        """是否存在**故障**（区别于「零命中」这种结果）。

        ``empty_result`` 不算故障 —— 检索链路是好的，只是没命中。
        消费方用它决定要不要写 ``degradation_log``（D-03）。
        """
        return self.failure_reason is not None and self.failure_reason != FailureReason.EMPTY_RESULT.value

    def note_backend_failure(self, backend: str, reason: str, detail: str = "") -> None:
        """追加一条 backend 失败事实（工具层内部用）。"""
        self.backend_failures.append(BackendFailure(backend=backend, reason=reason, detail=detail))

    def faults(self) -> List[BackendFailure]:
        """返回所有应上抛为 run 级降级的故障条目（**每次检索至多一条，避免重复记账**）。

        * 整体失败（无结果且存在故障原因）⇒ 一条 ``backend="all"`` 的汇总故障；
        * 部分失败（有结果，但某路 backend 挂了）⇒ 逐 backend 一条；
        * 零命中 / 完全正常 ⇒ 空列表（不写 ``degradation_log``）。
        """
        if self.has_fault:
            return [BackendFailure(backend="all", reason=self.failure_reason, detail=self.failure_detail)]
        return list(self.backend_failures)
