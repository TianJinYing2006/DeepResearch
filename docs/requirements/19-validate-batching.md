# 需求 19：validator 分批并行（标准档耗时优化之一）

> 状态：**草稿**（随 PR 合入后置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：L3-A 内测实测（2026-09-30，run `86431ca78a6c`）：标准档 302s 中 **validate 157s（52%）**。
> 本文件对应 Issue #76 的 **validate 分批并行** 部分；**报告流式**另行评估（见 §7 备注）。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 19 |
| 标题 | validator 分批并行（断言语义不变） |
| 优先级 | P1（性能 / 等待体感） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #76（Part of） |
| 关联 PR | 本 PR |
| 创建 / 更新 | 2026-09-30 |

## 2. 问题背景（实测基线）

标准档 `86431ca78a6c` 逐节点耗时：validate **156,968ms（52%）**、write 73,617ms（24%）、
critic 55,306ms（18%）、检索合计 1,797ms。validate 是单次 LLM 调用对全部 37 条引用做
「存在性 AND 忠实度」判定，引用数越大单次调用越慢（quick 档 7 条引用仅 6.8~11.6s）。

## 3. 业内调研（2026-09-30）

- **Sharding Prevents LLM Oversight Failures**（arXiv 2608.06422）：把 K 个判定拆给 S 次调用
  并行执行，**不降低反而提升一致性**（32 判定/批 κ=0.86 vs 单条/批 κ=0.73），且输入 token
  约为逐条判定的 1/23；「capacity-sized grouping」优于极端拆散。
- **Batched Self-Consistency**（EMNLP 2025）：批式 pointwise 判定在保持/提升相关性判定质量的同时
  把延迟降一个数量级。
- **工程模式**：NVIDIA SkillSpector 用「批量分块 + 并发信号量」；Pydantic Evals 用
  `max_concurrency` 控制并发（限流/成本/内存三类约束）。
- 结论：**分批 + 有限并发**是正确的方向；重点是「判定口径逐条一致」「失败隔离」「上下文与记账正确」。

## 4. 当前设计（代码位置）

- `research_engine/agents/validator.py::validate()`：阶段 2 把全部 `to_check` 放进**一次**
  `LLMClient.chat_json`（`CitationVerdict` schema），任一批异常 → 整单 `llm_failed` →
  全部降级「按存在性通过」；
- `research_engine/llm/client.py::_accumulate_usage()`：`tokens_total/model_stats/role_stats/
  model_io_stats/state.token_used` 均为无锁读-改-写；
- `research_engine/usage.py`：记账 sink 是 **contextvars**（线程不继承）；运行档位
  （`runtime_profile`）同样依赖 contextvars。

## 5. 优化方案（本 PR）

1. **分批**：`DR_VALIDATE_BATCH_SIZE`（默认 16，≤0 关闭分批）；`to_check` 按序切批；
2. **并行**：`DR_VALIDATE_CONCURRENCY`（默认 3，clamp 1..4），`ThreadPoolExecutor`；
   批间**顺序无关**（结果按 finding_id/claim 对齐，与串行同构）；
3. **上下文注入**：`contextvars.copy_context()` + 每批 `ctx.copy()` 提交，
   把 usage sink / 运行档位（模型选择）带进工作线程；
4. **记账并发安全**：`LLMClient._stats_lock` 包住类级计数与 `state.token_used` 的更新；
5. **失败隔离**：批异常 → 该批引用单独降级「LLM 校验失败，降级为存在性判定」并逐批
   `degradation_log`（existence_only）留痕；成功批不受影响（旧行为是整单一起降级）。

## 6. 设计策略

- **判定口径零变化**：system prompt / schema / claim 字段 / claim_echo 核对 / 宽松口径
  （verified_relaxed）与串行路径完全同构；单批时走原路径（零行为变化）。
- **可回退**：`DR_VALIDATE_BATCH_SIZE=0` 即回到单次调用（对照/排障开关）。
- 不引入新依赖（标准库 `contextvars` + `concurrent.futures`）。

## 7. 验收标准（DoD）

- [x] 分批等价单测：6 条引用批 2 → 3 次调用，verified 序列与串行一致
- [x] `BATCH_SIZE=0` 回退单次调用（单测）
- [x] 批失败隔离：失败批降级 + 留痕；成功批结果保留（单测）
- [x] usage sink 注入线程：3 批 → 3 条记账（单测，防 contextvars 漏账）
- [ ] 线上实测：标准档同量引用（~37 条）validate 墙钟下降 ≥50%（**待真实 run 回填**；
      预计 157s → ~60~80s，取决于供应商并发限流）
- [ ] 全量 pytest + CI 四 job 零回归

> **备注（报告流式，未含在本 PR）**：writer 流式需要 SSE 协议层与前端渲染改造、
> 并可能触碰 W8 评测口径 ⇒ 依「先对照、后合并」纪律**单独立项**（Issue #76 保留跟踪）；
> 本 PR 完成后在 #76 评论区登记拆分状态。

## 8. 影响范围与风险

- 模块：`research_engine/agents/validator.py`、`research_engine/llm/client.py`、`tests/`；
- 风险：低——默认批 16 且并发 3，仅在引用数 >16 时才走并行；供应商突发限流时批失败会被
  隔离并留痕（不会伪装成忠实度通过）；
- 记账：并发写已加锁；usage sink 经 contextvars 显式注入。

## 9. 测试策略

- 新增 `tests/test_validator_batching.py`（4 条，零 API）；
- 回归：`tests/test_validator_fixes.py` + 全量 pytest；ruff。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-30 | 优化 | 标准档 validate 占 52% 墙钟（Issue #76） | 分批 + 并发 + 上下文注入 + 记账加锁 + 批失败隔离 | 本 PR |
