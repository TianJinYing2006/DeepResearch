# ADR-0004：修复 Writer/Validator 引用编号错位（压缩导致的状态分叉）

## 基本信息

- **编号**：0004
- **标题**：修复 Writer/Validator 引用编号错位（压缩导致的状态分叉）
- **日期**：2026-08-20
- **状态**：已采纳
- **涉及模块**：`research_engine/graph.py`、`research_engine/agents/writer.py`、`research_engine/context/manager.py`（未改动，仅涉及契约）

## 背景

ADR-0002 建立了编号引用协议：`ContextManager.format_for_writer` 给每个研究发现分配编号 `[N]`，Writer 输出 `[来源: N]`，Validator 用 `_build_index` 把编号映射回真实来源做存在性校验。

该协议隐含一个前提：**Writer 编号时用的列表和 Validator 建索引时用的列表必须是同一份**。但 ADR-0001 设计上下文管理时，压缩（compress）被放在了 Writer 内部执行：

```python
# writer.py（旧）
findings = self.context.compress(findings, topic)   # 本地变量，压缩结果不回写 state
context_text = self.context.format_for_writer(findings)
```

而 Validator 拿到的是 `state.findings`（原始未压缩列表）。

## 问题 / 动机

代码审查发现：当 findings 超过 `max_findings`（30 条）触发压缩时，两边编号体系错位：

- Writer 基于压缩后列表编号（如 10 条 → 编号 1-10）
- Validator 基于原始列表建索引（40 条 → 编号 1-40）
- `[来源: 5]` 会被 Validator 还原成**错误来源**，且存在性校验照样通过

这比"校验失败"更危险——**静默的溯源错误**。ADR-0002 记录的 17/67 低通过率中，除多编号未拆分外，此错位也是嫌疑因素。该 bug 一直未暴露的原因：只有 findings > 30 的大任务才触发，小规模端到端测试全部通过。

离线测试（`tests/test_citation_alignment.py`）复现：40 条发现压缩为 10 条后，`[来源: 5]` 被映射到 source-2 而非 source-5。

## 方案

把压缩从 Writer 内部移到 graph 的 `write` 节点，压缩结果**写回 state**：

```python
# graph.py（新）
compressed = self.context.compress(state.findings, state.topic)
report = self.writer.write(state.topic, state.subquestions, compressed)
state.findings = compressed
return {"report": report, "findings": compressed, ...}
```

Writer 退化为纯粹的"格式化 + 生成"组件，不再私自变换数据。

## 理由与取舍

- **为什么在 graph 层压缩而非让 Writer 返回压缩结果**：LangGraph 的设计哲学是"节点返回值更新共享状态"。压缩后的列表是 Writer 和 Validator 的共同输入，属于**共享状态**，应该由编排层产生并持久化到 state，而不是藏在某个 Agent 的局部变量里
- **放弃了"Writer 返回 (report, findings) 元组"方案**：改变了 write() 签名，且把状态管理责任留在 Agent 内部，治标不治本
- **代价**：state.findings 的语义从"原始检索结果"变为"压缩后的检索结果"。原始全量结果不再保留在 state 中。当前无下游消费原始列表，可接受；若未来需要，可增加独立字段（如 raw_findings）

## 影响

- `graph.py`：`_write` 节点增加压缩步骤并回写 findings；`__init__` 增加 ContextManager
- `writer.py`：write() 不再内部压缩，签名不变
- `validator.py`：无改动（契约的另一端自然对齐）
- 新增 `tests/test_citation_alignment.py`：离线验证编号映射一致性（monkeypatch LLM，无真实 API 调用），同时复现旧 bug 作为对照
- ADR-0002 的遗留问题（多编号拆分、编号越界校验）仍未解决，另行处理

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-08-20 | 初始记录：压缩上移至 graph 层并回写 state，修复编号错位 |
