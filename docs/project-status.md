# 项目状态看板（唯一真相源）

> **为什么有这份文档**：2026-09-16 盘点发现「文档状态漂移」已是项目最现实的管理风险 ——
> `docs/requirements/8-*.md` §1 元信息仍写「状态 = 草稿」、§1.1 全景表里 **before 基线与 Arm 1 仍标 ⬜**（实际已完成），
> 而同一份文档后半部分的 DoD 明细却标了完成。**两张口径打架 ⇒ 「已完成的重复做、未完成的被当成完成」**。
>
> **规则**：本文件是**当前状态**的唯一真相源。其余设计与需求文档（含 `8-*.md`、`.workbuddy/deepresearch-plan.md`）
> 只保留**历史决策与设计依据**，不再承担看板职责。

| 项 | 值 |
| --- | --- |
| 更新 | **2026-09-18**（代码核到 `7a4124c` + Arm 6 未提交改动） |
| 阶段 | W1~W7 已收官；**W8 进行中（Arm 1~6 已落地，Arm 7 未开始）** |
| 最近 CI | **Py3.11/3.12/3.13 + ruff + pytest 三档全绿**（3.11 41s / 3.12 43s / 3.13 48s，零 LLM、零 key） |
| 最近交付 | **PR #3 已合并**（dev → master，rebase）⇒ master = `7a4124c` <https://github.com/TianJinYing2006/DeepResearch/pull/3> |
| 最近基线 | **before 基线**（2026-09-16，20 题 × 3 runs，`research_engine/` 零改动） |
| 测试基线 | **291 全绿**（W7 期 131 → Arm 1 +26 → Arm 2 +39 → Arm 3 +20 → Arm 4 +20 → Arm 5 +18 → **Arm 6 +22**） |
| Python | **仅支持 3.11~3.13**（跑批与验收请以 CI matrix 为准，本机 3.14 不作验证依据） |
| 结论文档 | W7：`docs/eval-w7-conclusion.md`；W8：`docs/requirements/8-fault-transparency-and-reproducibility.md` |

## 决策记录

| 编号 | 决策 | 日期 | 依据 |
| --- | --- | --- | --- |
| **D-01** | **采用本文件作为唯一实时状态看板**。`docs/requirements/8-fault-transparency-and-reproducibility.md` 只维护设计要求、DoD 与验收原则，并在 §1.1 声明指向本文件，**不再重复维护 Arm 1~7 的实时勾选状态** | 2026-09-17 | 两份文档生命周期不同（设计低频 / 状态高频）。实测反例：§5.4.2 落点清单行号已全部过期，证明设计文档不适合承担实施看板 |
| **D-02** | **Arm 4 为 RAG 引入独立 `RetrieveResponse`**，不做 researcher 层临时适配 | 2026-09-17 ✅ 已落地 | `HybridRetriever.retrieve()` 原返回裸 `List[dict]`，`[]` 无法区分「正常无命中 / 未配置 / 向量库不可用 / embedding 失败 / 解析失败 / 部分 backend 失败」。临时包裹会在信息到达 researcher 前就丢掉事实，与「故障可归因」的主目标冲突 |
| **D-03** | **`empty_result`（零命中）不算故障**：记在 `failure_reason` 供 planner 当轮决策（换源 / 改查询），但**不进 `degradation_log`、不推导 `run_status`** | 2026-09-17 ✅ 已落地 | 算术理由：`resolve_run_status()`（`state.py:229`）是「`degradation_log` 非空 + 有报告 ⇒ `degraded`」。多跳检索里零命中几乎必然发生，若算降级 ⇒ **几乎每轮 run 都是 `degraded`**，`run_status` 不再是健康度信号，Arm 1 的「故障可归因」退化成噪声。**一处定义**：`failure_reasons.NON_FAULT_REASONS` + `is_fault_reason()`；要翻转口径只需改这一处 |
| **D-04** | Arm 4 后：provider **不再靠抛异常表达失败**；冒泡到 researcher 的未预期异常一律归为**非工具类**（`internal` 等），**不得**再被猜成 `provider_error` | 2026-09-17 ✅ 已落地 | 工具层是 5 值的唯一产生点（B4 单向派生契约）。把 provider 自身的 bug（如 `TypeError`）伪装成 `provider_error`，会让「真故障」与「工具不可用」混为一谈 |
| **D-05** | **指标未算出（缺失 / 非数值）不触发 `suspicious`**：这类情况由 `metrics_partial` / `metrics_failed` 表达；`verdict` 只判断**已经成功算出**的指标是否落入异常区间 | 2026-09-17 ✅ 已落地 | 语义分工：`suspicious` = 运行完成了但**结果质量可疑**；`partial/failed` = **评测本身没完整完成**。两者混在一起会让质量闸同时表达「结果质量」与「执行完整性」，后续统计失真（一次「指标没算出来」会被误记成「被测变差」）。实现见 `eval/quality.py::_below()` —— 非数值直接返回 False |

| **D-06** | **`prompt_hash` 覆盖【运行时渲染结果】，不是常量模板**：5 个 system 构建器抽成纯函数作唯一产生点，指纹对「开关翻转但代码未变」敏感 | 2026-09-18 ✅ 已落地 | 反例即动机：`critic_gap_enabled` 往 critic 提示词插裁决标准、writer/validator 开关在两套提示词间选版 —— 这些**都在** `config_snapshot` 里，但要回答「提示词变了吗」还得再推一层（开关→分支→哪套）。哈希常量模板会得到「开关翻了指纹没变」的假象。user 段含运行时数据（topic/findings），**按设计排除**，其模板由 `git_commit` 兜 |
| **D-07** | **历史 raw 缺 Arm 6 新字段时如实为 `None`，不回填当期值** | 2026-09-18 ✅ 已落地 | 回填会抹掉「这批 run 产生于 Arm 6 之前」这个事实 —— 那正是 §10.4/Arm 6 要消灭的问题。报告对应显示「❔ 未记录」而非假装数字有效。实测 66 个历史 run 全判为未记录 |
| **D-08** | **`citation_judge_independent` 是事实判断而非配置项**（恒 False） | 2026-09-18 ✅ 已落地 | 评测层不重跑裁判，直读主链路 validator 产物（`metrics.compute_citation` 注释明文写了「不再重跑 validator」）。想变 True 必须**先改评测实现**引入独立复判，不能只改常量——否则常量与实现对不上就是自欺 |

`SearchResponse`（外部搜索：Bocha、arXiv）与 `RetrieveResponse`（本地向量 / 混合检索）**分离但共享** `FailureReason`、`failure_detail`、`ok` 与统一的 `degradation_log` 派生语义 —— 两者语义不同（外部 provider 响应 vs 本地检索器响应），强行复用会在后续扩展 rerank / source score / document metadata 时持续变形。

## 模块状态

| 范围 | 状态 | 证据 |
| --- | --- | --- |
| W1~W6（MVP 主链路 / Langfuse / 评测 / 开源） | ✅ 完成 | `.workbuddy/deepresearch-plan.md`，六周 DoD 全绿 |
| W7（6 臂对照实验） | ✅ 完成（**结论为否定**） | `docs/eval-w7-conclusion.md` |
| W8 前置 §10.4（config_snapshot 记效值） | ✅ 完成 | `research_engine/eval/provenance.py` |
| W8 before 基线 | ✅ 完成（3 轮） | W8 §10.5.8 |
| W8 Arm 1（状态三态 + 异常退出契约） | ✅ 完成 | `state.py` / `graph.py` / `failure_reasons.py` / `tests/test_arm1_run_status.py` |
| W8 Arm 2（代码执行注入） | ✅ 完成 | 注：**实际是功能性修复**，见下方已知缺陷 D2 |
| W8 Arm 3（依赖锁定三刀） | ✅ 完成 | `requirements-lock.txt` + CI 三档证据（`0477ea3` / `9698182`） |
| W8 Arm 4（provider 失败原因结构化） | ✅ **已落地**（2026-09-17） | `rag/response.py` 新增 + `bocha.py`/`arxiv.py`/`retriever.py` 三路真填充 + 删 `classify_tool_exception`；受控对照以 mock 形式落在 `tests/test_arm4_failure_reasons.py`（零网络更稳） |
| W8 Arm 5（评测口径 / 质量闸 / stderr） | ✅ **已完成**（含历史回填，2026-09-17） | `eval/quality.py`（闸）+ `eval/stats.py`（bootstrap）+ `eval/aggregate.py`（聚合一处定义）+ `tools/w8_backfill_stderr.py`（回填）。**DoD 全部闭环**，回填记录见下节 |
| W8 Arm 6（可复现元数据） | ✅ **已落地**（2026-09-18） | `eval/prompt_hash.py` 新增（6 slot）；provenance 从 4 字段扩到 **10 字段**（+`prompt_hash`/`prompt_slots`/`scorer_version`/`citation_judge_model`/`coverage_judge_model`/`citation_judge_independent`）；raw 三条落盘路径统一走 `raw_provenance_fields()`；报告**在指标表之前**显著呈现裁判独立性；趋势表新增「尺子变了」检查。当前 `prompt_hash=cf95dafc78f98348`、`scorer_version=w8.1` |
| W8 Arm 7（产物治理双轨） | ⬜ 未开始 | `.gitignore` 三条 `!` 白名单**均无注释** |
| W8 after 基线 | ⬜ 阻塞中 | 依赖 Arm 1~7 全部落地 + 代码冻结 |

## Arm 5 历史回填记录（2026-09-17 迁移，已完成）

| 项 | 结果 |
| --- | --- |
| 迁移对象 | `research_engine/eval/results/*/summary.json` — **75 个 run，全部回填成功，0 跳过、0 异常** |
| 新增字段 | `metrics_stderr`（含 `n` / `stderr` / `ci95`）、`verdict`、`verdict_reasons`、`metrics_ok` / `metrics_partial` / `metrics_failed`、`deprecated_keys` |
| `verdict` 分布 | **ok 73 / suspicious 1 / broken 1** |
| 回归验收（真实 run） | `run_20260910_173540` ⇒ **suspicious** ✅；`run_20260910_171054` ⇒ **broken** ✅ |
| D1 修复 | **2 个 run**：`run_20260916_022440`（token 0 → **1,017,784**）、`run_v11_compare`（0 → **683,649**）；均改为 `cost_yuan=None` + `cost_degraded=true` + `cost_basis=raw_token_sum` |
| 幂等性 | 回填后再次 dry-run ⇒ **75 个全部 `unchanged`**，D1 标记清零 |
| 迁移日志 | `_arm5_backfill_20260917_0111.log`（含迁移前 git 状态 / dry-run / apply / 复核全过程） |
| 备份 | `_arm5_backup_20260917_0111/`（75 份迁移前 summary.json，**验收通过后应删除**） |
| 测试 | ruff 全绿；pytest **269 全绿**（267 → +2 条回填回归：D1 修复 + 幂等 + 跳过无 eval 的 run） |

## 已知缺陷（登记待修）

| 编号 | 缺陷 | 处置归属 |
| --- | --- | --- |
| D1 | ~~**成本静默归零**：`phase1_global_stats.json` 缺失时返回 `{}` ⇒ 成本直接取 0，且 **shape 与真实结果完全相同、无任何标记**。已在 before 基线第 3 轮发生（cost=¥0 而 raw token=1,017,784）~~ | ✅ **已修**（Arm 5 附带，2026-09-17）：降级为 raw 累加 token + `cost_yuan=None` + `cost_degraded=true` + `cost_basis`；报告层显示「⚠️ 不可重建」而非 ¥0。⚠️ **历史污染未清**：dry-run 扫出 2 个 run（`run_20260916_022440`、`run_v11_compare`）记的 token 少于 raw 实际 |
| D2 | Arm 2 是**功能性修复而非纵深防御**：旧模板引号叠加 ⇒ 对任何 query 都 `SyntaxError` ⇒ 基线三轮 code_exec 144 条、**成功 0**。⇒ **after 基线里 code_exec 会真的产出结果，这部分差异必须单独归因，不得计入 W8 质量提升** | 归因口径 |
| D3 | 裸 `status` 字段在三层的语义混用（run / invoke / metrics）—— 涉及文件与行数见任务清单，且受历史产物兼容约束 | Arm 1 命名三分（残余） |
| D4 | 不要用 coverage 提升验收 W8：实测 MDE=12.38pp > 预期效应 4~10pp，**统计上不可判定**。W8 的验收口径是**确定性断言（故障可归因率 0%→100%）** | 验收口径 |

## 执行顺序（按顺序串行，勿并行铺开）

1. ✅ **已完**：文档状态同步 → Arm 4 真填充 → 删临时异常映射 → 补测试（20 条）
2. ✅ **已完**：Arm 5 质量闸 → **D1 成本守恒** → stderr/bootstrap → 字段重命名 → 报告带 ±stderr 与 §0 质量闸小节 → **75 个历史 run 回填完成并验收**（见上节）
3. ✅ **已完**：Arm 6 可复现元数据 —— 5 个提示词构建器抽为唯一产生点 → `prompt_hash`（对开关敏感）→ 裁判模型/独立性结构化 → `scorer_version` → raw/summary/history/report 四处落地 + 22 条测试
4. ⬜ **Arm 7 产物治理双轨**：gitignore 白名单注释 + 本地只读台账
5. ⬜ **命名三分**：`invoke_status` / `metrics_status`（受历史产物兼容约束，`tools/w7_backfill_*.py` 读旧 key）
6. ⬜ **冻结代码后做实验**：固定代码 → after 基线（**20 题 × 9 runs ≈ ¥11 / 6.5h / ±7.3pp**）→ 只报有统计支撑的结论
6. ⬜ **最后对外材料**：README 能力与限制 → 博客③ → 引用链接

## 未决 / 待拍板

| 项 | 说明 |
| --- | --- |
| W7 五开关去留 | `CRITIC_GAP_ENABLED` 等五个开关默认全开、主链路静默吃默认值，`.env.example`/README 零提及。见 `docs/w7-switch-disposition.md` |
| GitHub 凭据链修复 | `~/.gitconfig` 的 helper 写成反斜杠路径导致推送取不到凭据，永久修法 `gh auth setup-git`（改全局配置） |
| Gitee 凭据存储 | 是否把令牌存入 wincred（安全决策） |
| 状态看板形态 | 本文件 vs 就地更新 8-*.md §1.1（二选一后需同步删除另一处口径） |
