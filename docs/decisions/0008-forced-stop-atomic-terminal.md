# ADR-0008：修复强制收口与终局写入的竞态（终局原子化）

## 基本信息

- **编号**：0008
- **标题**：修复强制收口与终局写入的竞态（终局帧 + 结果 + 状态同锁原子化）
- **日期**：2026-09-24
- **状态**：已采纳
- **涉及模块**：`web/backend/runner.py`、`tests/test_web_guardrails.py`

## 背景

P1 运行护栏（超时闸 + 协作式取消）引入了一个**硬截止**兜底：节点内部挂死、协作式停止失效时，由传输层补一帧 `RUN_ERROR(stop_forced)` 收口（`force_stop_if_overdue()`）。设计前提写得很清楚——**Python 线程无法被 kill**，所以后台线程可能在传输层收口之后仍继续收尾。

为防止后台线程在收口后追加帧，`_emit()` 检查 `run_id in self._forced` 后丢弃；`_emit_terminal()` 在入口也做了一次同样检查，注释明确声明「late result must never become a second terminal event **or a report** after the forced transport close」。

## 问题 / 动机

2026-09-24 收口复核发现：这两处「先检查、后写入」的检查点与 `force_stop_if_overdue()` 之间存在 **TOCTOU 竞态**，且可用确定性交错稳定复现：

1. **迟到报告 + 状态覆盖**：工作线程通过 `_emit_terminal()` 入口的 `_forced` 检查后、进入 `_results` 写入前，强制收口插进来（同锁两次获取之间的窗口）。此后后台线程仍会写入 `_results`，并且 `_finish_status()` 无条件把 `stop_reason` 覆盖成正常值。
   - 复现结果：`force_stop 返回 True`，但 `has_result=True`（迟到报告可导出）、`snapshot.stop_reason="completed"`。
2. **双终局**：`RUN_FINISHED` 已经追加入 `_frames`、但 `_finished` 尚未置位（原实现要等 `finally`）时强制收口。`force_stop_if_overdue()` 只检查 `_forced` / `_finished`，两个都未命中 ⇒ 再补一帧 `RUN_ERROR(stop_forced)`。
   - 复现结果：流里同时存在 `RUN_FINISHED` 与后续 `RUN_ERROR(stop_forced)`。

两处都与文档宣称的行为矛盾（「不会生成迟到报告」「运行状态不会被后台线程覆盖」「不会再出现迟到的 RUN_FINISHED」）。窗口极窄，但属于「一旦命中就是错误语义」，不能靠概率。

## 方案

把**终局帧、结果存储、状态置位**收进同一把 `condition` 锁（与 `force_stop_if_overdue()` 互斥），并前移 `_finished` 置位时机：

- 抽出 `_append_locked()`（要求已持锁）：编号 + 入帧 + 更新 `event_count` / `last_event_type`；`_emit()` 复用它，且改为同时拒绝 `_forced` **与** `_finished` 之后的帧。
- 新增 `_emit_terminal_frame()`：在 `with self._condition` 内依次完成 ①`_forced` / `_finished` 检查 → ②`_results` / `_reports` / `_meta` 写入 → ③追加终局帧 → ④状态置 `finished` + `_finished_monotonic` → ⑤`_finished.add` + `_active.discard` → ⑥`notify_all`；队列投递仍在锁外。
- `_emit_terminal()` 的 `STOP_ERROR` 与正常/取消/超时两条路径、以及 `_worker` 的 `runner_crash` 兜底路径全部改走该方法。
- 删除 `_finish_status()`（职责并入 `_emit_terminal_frame`）。

新增 2 条确定性交错回归测试（`tests/test_web_guardrails.py`）：

- `test_forced_stop_before_late_terminal_write_cannot_expose_report`：monkeypatch `_estimate_cost_cny` 把线程钉在终局写入前的窗口，收口后断言 `has_result=False`、`export_payload=None`、`stop_reason=None`。
- `test_force_stop_cannot_append_second_terminal_after_run_finished`：monkeypatch `_append_locked` 把线程钉在「已写终局、尚未置位 finished」的临界区，收口线程阻塞在同一把锁上；断言收口返回 `None`、流内恰 1 帧 `RUN_FINISHED`、无 `RUN_ERROR`。

## 理由与取舍

- **为什么用同一把 condition 锁**：`RunManager` 的 `self._lock` 是非重入 `Lock`，`self._condition = Condition(self._lock)`。因此不能「持 `_lock` 时调用会再次加锁的 `_emit()`」（死锁），只能把写帧逻辑抽成 `_append_locked()` 在锁内复用。这是实现约束决定的形态，不是随手重构。
- **为什么把 `_finished` 提前到终局帧产生时置位**：原先它由 `finally` 置位，留下「帧已发、finished 未置」的窗口。语义上 `_finished` 表示「终局已产生」，提前置位与 `wait_for_frame()` 的消费顺序兼容（同锁内先追加帧、后置位，唤醒者先看到帧）。
- **为什么同时在 `force_stop_if_overdue()` 之外补齐**：收口侧无需改动——终局路径持有同一把锁，收口要么先执行（终局被丢弃），要么看到 `_finished`（放弃补帧）。改单侧即可闭环。
- **备选方案（已否决）**：让 `force_stop_if_overdue()` 顺手清理 `_results`。它只覆盖「收口发生在存储之后」，存储与发帧之间的窗口仍在，无法闭环。
- **代价**：终局临界区稍微变长（写结果 + 入帧 + 状态，μs 级）；`_finished` 的语义从「线程已收尾」变为「终局已产生」。并发槽释放时机同步前移（终局产生即释放，不再等 `finally`），与强制收口路径的既有行为一致（`active_runs` 语义是「活跃 run」，不是「线程存活数」）。
- **可读性收益**：`_finish_status()` 与 `_emit()` 的部分职责合并进单一入口，终局路径只有一处写结果、一处发帧。

## 影响

- `web/backend/runner.py`：新增 `_append_locked()` / `_emit_terminal_frame()`；`_emit()`、`_emit_terminal()`、`_worker()` 异常分支改造；删除 `_finish_status()`。
- 行为变化：强制收口后后台线程的任何迟到写入（结果、状态、帧）被完全丢弃；`snapshot()` 在终局帧产生即可见 `finished` 与 `stop_reason`。
- 测试：`tests/test_web_guardrails.py` 24 → 26 条；全量 439 → **441 全绿**（Python 3.13.14）；浏览器 E2E 8/8 复验通过。
- 文档：`README.md` 与 `docs/project-status.md` 的测试基线数字同步更新。

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-09-24 | 初始记录：终局帧 / 结果 / 状态同锁原子化，关闭两个 TOCTOU；新增 2 条确定性交错回归 |
