# 需求-5-eval-and-metrics

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 本稿为 **grill 定稿版（2026-09-06 Q1~Q8 全部拍板，TBD 清零）**：全部设计决策已定案，新增/修订细节以**加粗**标注。
> 飞书镜像：**第五周需求文档** https://wcnnpvbxd7li.feishu.cn/docx/SqpDd7AuZoN80Sx5Cc1cNNwdnbd（同步于 DeepResearch 需求文档 /，2026-09-06；2026-09-07 修复删除线误渲染——`~` 转义为 `\~`，见工作流文档三坑）
> **grill 记录：Q1~Q8（TBD-1/2/3/4/5/6/7/8/9/10）全部定案（2026-09-04~09-06）；否决史保留在各条括号内备查。**

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | #5 |
| 标题 | eval 数据集 + 量化指标（NFR2 / DoD） |
| 优先级 | P1 |
| 状态 | **定稿（2026-09-06 grill Q1~Q8 全部拍板，TBD 清零）** |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #5（待建） |
| 关联 PR | |
| 创建 / 更新 | 2026-09-04 |

## 2. 问题背景
- `deepresearch-plan.md` 缺口 4：**eval 有脚手架但没有 ≥20 条标注集 + 量化指标表**（NFR2 / DoD）——"效果不错"目前拿不出数字。
- 唯一的数字是 W1 的收敛性单测（`tests/test_graph_loop.py`）与 W4 e2e 冒烟（4 例），都不是**可复跑的评测体系**。
- 面试叙事目标：「量化评测（≥20 条）」是简历四项缺口里能"一拳打实"的一项——面试官问"你怎么证明它效果好"，答不上 = 前面 W1~W4 的工程叙事全部打折扣。
- 与 W1~W4 的正交与依赖：eval 是**纯消费方**，不新增任何 state 字段/节点/工具；全部输入来自既有产物（report + state + validator + pricing 表）。

## 3. 需求分析
- 目标：把"效果不错"变成**一条命令可复跑的数字**，为后续任何改动（模型档位/工具策略/prompt）提供回归基线。
- 成功定义（可量化）：
  - ① `python -m research_engine.eval.run --dataset dataset.jsonl` 一条命令，跑完标注集，输出 **7 项指标**的 markdown 表（六项 plan.md §5 口径 + **检索命中率第 7 项**（Q8）；目标值：完成率 ≥90% / 引用准确率 ≥85% / 覆盖度 ≥90% / 反思有效性 **critic_stop 占比 ≥90%**）；
  - ② 人工抽检 20% 记录落 `docs/eval-report.md`，引用准确率有"机器口径 + 人类口径"两个数字，且能解释差异；
  - ③ 基线存档：本次结果作为 v0 基线，之后任何改动可复跑同一命令做前后对比；
  - ④ 不新增 state 字段、不侵入 graph/agents 主链路（消费既有产物）。

## 4. 当前设计（代码现状）
- `research_engine/eval/retrieval_eval.py:34-47`：`RetrievalEvaluator` 用**关键词命中**判定（`kw in hit_texts`），非标注命中率（无 ground-truth 文档 ID 集合）；top_k 固定 5。
- `research_engine/eval/citation_eval.py:21-59`：`CitationEvaluator` 已对齐 W2 口径——existence/fidelity 双口径 + `by_source_type`（web/rag 拆分，W4 后含 arxiv/code）+ `failed_note_distribution` + `is_meta_hits`。**引用侧评估基本就位**。
- `research_engine/eval/report_eval.py:29-48`：`ReportEvaluator` LLM-as-judge（RACE 四维 0-100），走 `strategic_json`，异常时返回 `{"overall": 0, "comment": "评测失败"}`——**失败即 0 分，无重试、无留痕**。
- **缺失（本需求主体）**：
  - 无 `dataset.jsonl`（≥20 条标注集）；
  - 无 `run.py` 编排入口（三件套各自独立，无统一聚合）；
  - 无"完成率/覆盖度/平均步数/反思有效性/token 成本"的实现（plan.md §5 表 2 仅口径未落地）；
  - 无人工抽检流程与 `docs/eval-report.md` 记录格式。

## 5. 优化方案（grill 前初始版，TBD-N 待拍板）
1. **数据集 `research_engine/eval/dataset.jsonl`**：≥20 条，字段按 plan.md §5：`id, difficulty(易/中/难), type(单轮/多轮/计算/学术), query, followups?, expected_subquestions?, gold_sources?`。
   - **✅ TBD-1 数据集输入契约（grill Q1 定案，2026-09-04）**：
     - **query 来源 = 混合**：真实运行记录锚点（保底 ~30%）+ 人工编写补齐四类覆盖。
     - **标注流程 = AI 草案 + 人工校准（双审制工作流，简化落库）**：强模型（qwen-max / deepseek-r1）对每条 query 生成 `expected_subquestions`（2~4 条）+ `gold_keywords`（5~8 个）初稿，人工逐条校准（删不合理子问题 / 补遗漏关键词 / 修表述）；**落库仅保留 `"annotation": "ai_draft + human_calibration"` 字符串，不落两版 JSON**（draft 版字段否决——数据集膨胀、低频展示场景，防守型资产只做廉价版）。
     - **分桶标注（防选择性偏倚，回归测试集与能力评估集分离）**：
       - **回归锚点桶 6~8 条**：仅取 W4 运行中 `validator.verified == True` 且 `existence == True` 的 query，用途 =「本次改动是否把以前答对的题答错」（回归稳定性，单独报告）；
       - **能力评估桶 12~14 条**：冷启动、混合难度（易/中/难）、覆盖单轮/多轮/计算/学术四类，不带"系统已知会答"信息，用途 = 完成率/引用准确率/覆盖度的真实能力线；
       - **锚点记录如实核对**：`trace_id` / `git_commit_hash` 以日志时间反查 `git log` 补录；核实不到则如实声明"按日志时间推断"，**不允许空字段**。
     - **版本冻结 = 双锚**：`dataset.jsonl` 头部含 `version / created_at / anchor_samples`（数据集自身锚点）；**eval 结果文件（results jsonl）必须记录运行时 `git rev-parse HEAD` + dataset version**（subprocess 调 git ≈5 行零依赖），使指标变化可归因于"系统变了"而非"数据集变了"。
     - **已否决：语义回退双模匹配（E++ 升级点 2）**——本项目评估侧语义判定已有 validator LLM 对查（faithful），再叠嵌入相似度阈值（0.75）引入新调参点 + 口径混合使指标不可解释；如 `retrieval_eval.py` 改造确需（关键词命中→关键词+嵌入），归 TBD-10 且**单独报告、不混引用准确率/覆盖度口径**。
2. **编排入口 `research_engine/eval/run.py`**：加载 dataset → 逐条驱动 `graph.run()` → 收集 report + state 快照 → 聚合指标 → 输出指标表（json + markdown）→ 人工抽检阶段 → 汇总 `docs/eval-report.md`。
   - **✅ TBD-2 指标计算口径（grill Q2 定案，2026-09-04）+ 判定器分层原则**：**一个指标只挂一个判定器；自动能算的不上 LLM；人工只做认证不做事先判定**（Q1 否决语义回退的同构延续）。
     - **完成率 = 自动四条件全自动，不加 LLM 门槛**：① `status == "done"`（graph.py:255/271 终态，非异常退出）② `error is None`（state.py:88 单字段；**非虚构的 errors 列表**）③ 报告 `len(report.content) ≥ 300`（samples 实测 480~1100 字，300 为宽松下限只防空转/空报告；**否决 500**——会误伤 rag_sample 级短报告）④ **章节完整性 = 新增轻量机械检查：报告含 ≥2 个 `#`/`##` markdown 标题**（真实报告恒 4 个；**否决 E++ 必填四章节 ["摘要","背景","分析","结论"]**——writer prompt 无此模板、真实报告无此结构，属虚构依赖第二次，按此判完成率归零）。**完成率 ≠ 报告质量**：质量归 report_eval 独立维度，不混判。
     - **引用准确率 = 自动（validator `verified` 口径，W2 已升格存在+忠实）+ 人工抽检 20% 认证比对**，不新增第三层 judge（citation_eval.py 现有即可）；目标 ≥85%。
     - **信息覆盖度 = LLM 对查**（1 prompt/条：`expected_subquestions` vs findings 摘要，逐子问题判覆盖，复用 validator faithful 对查同构逻辑）；目标 ≥90%。
     - **token 成本 = 自动：eval run 前后 `LLMClient.tokens_total` 类级计数器差值**（**数据源不能用 `state.token_used`——validator.py:197 直建 LLMClient 且不传 state，读 state 会漏计 validator token**；W3 Q3=D' 差值机制正好覆盖）+ **双轨分桶**：模型名桶（`model_stats[model]`，类级 ≈5 行，成本 = Σ token×各模型单价）**+ 职责桶**（`role_stats[role]`，**5 个实例化点传 role**：router×3 + critic.py:111 直建 + validator.py:197 直建，≈10 行；granularity 如实声明：writer 与 researcher 共用 router._smart 合入 smart 桶）；目标 = 记录基线迭代降。
     - **平均步数 = 自动：`len(reflection_log)`**（critic 决策轮数，graph.py:193 每轮追加）；目标 = 记录基线。
   - **✅ TBD-8 反思有效性不做"该停时停"的 ground truth 标注（否决期望跳数——2 小时无效猜测），改为结构性 + 交叉信号 + 人工质量面**：
     - **代码依据**：`hard_gate`（critic.py:36-53）纯确定性，终态判 `depth ≥ max_total_hops` / `token_used ≥ token_budget` / `replan_count ≥ max_replan` 任一触顶 = hard_stop（预算耗尽）；未触顶且 `critic_signal == "stop"` = critic_stop（主动停）。
     - **自动面**：`critic_stop 占比 ≥ 90%`（目标）；hard_stop 出现即记录为"预算耗尽"失败事件（含触顶原因：跳数/预算/replan），逐例留痕分析。
     - **交叉信号（否决 E++ 0.8×/1.5× 动态系数——findings/轮数与子问题数无实证比例，伪精确）**：**早停候选 = critic_stop ∧ 该条覆盖度 < 90%**（"子问题没答完就停"，复用 LLM 对查结果零新代码）；**晚停候选 = critic_stop ∧ len(reflection_log) > 5**（W4 实测 2~4 轮收敛，5 为固定启发式上界，如实声明）。两标签仅"筛给人工抽检的候选"，不判失败。
     - **人工质量面**：抽检 4~5 条 critic_stop 案例，判断"当时信息是否真够"（早停/误停）；≥2 条被判停得不好 → eval-report 声明倾向。
3. **运行策略（性能/成本天花板）**：
   - **✅ TBD-3 串行/并发 / 失败分级 / 断点续跑（grill Q4 定案，2026-09-04）**：
     - **并发 = 有限并发 3（ThreadPoolExecutor，与主链路同步化一致，不引 asyncio）**：墙钟 20 条串行 2~3h → 并发 3 压到 **40~60min**；token 总量不变成本不变；arXiv RateLimiter（3s min_interval）不构成瓶颈。
     - **失败三档分级（采纳 E++ 分级，否决降级重试）**：**Level 1 瞬态**（限流/网络抖动）→ 重试 1 次；**Level 2 局部失败**（judge 环节超时但完成率/成本已得）→ **不重试**，记 `status="partial"` + `missing_metrics` 字段，已有指标照常落盘；**Level 3 致命失败**（graph 异常/OOM）→ 重试 1 次 + 完整错误栈，仍失败记 `status="failed"`。报告口径：「N 完整 / M 部分 / K 失败」，**不混数字**。
     - **否决 E++ 降级重试（移到队尾 + fast 模型降级做 judge）**：judge 降级撞 Q3 拍板（fast 对查数字不可信）；主体研究无"宽松配置"概念；局部数据保留已由 partial 覆盖，降级重试收益≈0。
     - **任务级超时 15min（采纳，换实现）**：ThreadPoolExecutor + `future.result(timeout=900)` 捕获 TimeoutError → 记 `status="timeout"` 释放逻辑槽位（Python 线程杀不掉，超时=放弃等待，eval 单进程可接受）；**根因治理 = judge LLM 调用设显式 `timeout=60s`**（openai SDK 支持；LLM 层现无超时，SDK 默认 600s 是主要僵尸来源——搜索层已有 15s/arxiv 超时，不是根源）。
     - **断点续跑（采纳）**：结果按条落盘 `eval/results/run_{ts}/q_{id}.json`，run.py 启动时跳过已完成的条。
   - **✅ TBD-4 评估预算与 judge 档位（grill Q3 定案，2026-09-04）**：
     - **judge 档位 = smart（qwen-plus）**：覆盖度对查 + 报告打分统一 smart 档（`report_eval.py:38` 由 `strategic_json` 改 `smart_json`；默认与 strategic 同模型成本不变，但未来 strategic 升 qwen-max 时 judge 不随涨）。理由：A→B 只差 ¥0.01 买"覆盖度/报告判定数字可信"；C（strategic/qwen-max）贵 3 倍，smart 已够用。
     - **预算三层（标红不中止）**：**硬闸 = 200k token/run（config.py:99 已有，state 内生效，最后防线）**；**单条软上限 60k**（超限标红 `⚠️ 成本异常`，不中止——保留"为什么这么贵"的诊断价值）；**总量软上限 600k**（20 条 × 均值 20k × 1.5，超限标红检测整体偏移，不中止）。
     - **成本账（修正墙钟）**：20 条 × ~20k ≈ 400k token ≈ **¥0.48/轮**（qwen-plus 均价）；**单条墙钟实测 6~9 min**（W3 真实 trace 8m50s / W4 e2e 6m53s）→ **20 条串行 ≈ 2~3 小时**（非 30~60 分钟，旧话术撤回）——压墙钟靠 Q4 并发决策，不靠预算。
   - **✅ TBD-5 环境与可复现（grill Q5 定案，2026-09-04）**：
     - **运行环境 = 全真实 API（A 方案）**：博查/arXiv/Qdrant 真跑，能力线 = 真实数字（面试主卖点）；**不做 mock 评估轨**（mock 只用于单测注入模式，现有 `llm_fn` 注入保留）——mock 省 ¥0.5/轮但丢真实能力证据，不值。
     - **漂移容忍（3 条重跑校验）**：v0 后选**易/中/难各 1 条共 3 条重跑 1 次**（并发不影响漂移逻辑），算单条 Δ；**容忍线：单条 Δ ≤ 10%、整体均值 Δ ≤ 5%**；超线不判失败，eval-report「可复现性」节如实记录漂移幅度 + 归因（真实 API 非确定性；绝对复现只有 mock 能做到，但那不测能力）。
     - **落痕 = 全量**：`run_{ts}/q_{id}.json` 存 report 全文 + state 快照（findings/citations/depth/reflection_log）+ 六指标 + status + 异常栈 + **双锚**（Q1 定的 git commit / dataset version）——面试追溯链（"这条为什么失败"→ 打开 q_007.json 看 error 栈）；~20 条 × 几十 KB ≈ 1~2 MB。
4. **反思有效性（✅ 已由 TBD-8 拍板吸收——结构性口径 + 交叉信号 + 人工质量面，详见 §5.2）**：不标期望跳数，不引入新 ground truth。
5. **人工抽检（✅ TBD-6 定案，grill Q6 拍板，2026-09-04）——两级抽检 + 单人声明 + 锚点复核**：
   - **报告级抽检（4~5 份，随机抽完整报告通读）**：一次性覆盖 ① 报告质量观感 ② 反思质量面（该批早停/晚停标签是否合理，联动 TBD-8）③ 每份报告内再抽 2~3 条关键引用精读；工时 15~25 分钟/份。
   - **引用级抽检（10~15 条，跨报告随机抽论断核验引用真实性）**：引用准确率"人类口径"的独立样本（与机器 verified 口径对比）；3~5 分钟/条。
   - **单人标注软肋处理 = 声明 + 接口**：如实声明"单人抽检，判定以标注规范为准"，每条记录判定依据；**记录格式预留 `reviewer` 字段**，可随时补第二位 reviewer 独立复审。
   - **回归锚点桶人工复核（采纳）**：锚点桶（W4 已验证通过 query）eval 重跑后若出现答错 → **最贵回归信号**，人工复核 6~8 条确认"真回归 vs 数据漂移"，结论进 eval-report。
   - 成本账：报告级 4~5 份 × 15~25min + 引用级 10~15 条 × 3~5min + 锚点 6~8 条 ≈ **2~3 小时人工**（否决"一级抽 20% 报告全量引用精读"= 5h+）。
6. **产出呈现（✅ TBD-7/9 定案，grill Q7 拍板，2026-09-04）**：
   - **指标表格式**：json（机器可读）+ markdown（人读）；成本呈现 = 模型名桶 + 职责桶双轨、总/单条/合计、按各模型单价加权人民币价（TBD-2 已定）。
   - **基线 = `baseline.json` 冻结快照（采纳 E++ 内核，修正 2 处）**：含 `baseline_id`（v0）/ git_commit / dataset_version / **config_snapshot（planner/critic/fast 模型、token_budget、max_hops——注意：config_snapshot 每轮 run 都记进 run 级结果，baseline.json 只是 v0 的那份；示例默认值 qwen-plus 对齐 W4 Q7，非 qwen-max）** / eval_env（python/os/墙钟）/ metrics / hash（**语义 = 完整性校验防手误/损坏，非"防篡改"**——单人项目无攻击者，话术勿夸）。
   - **历史趋势 = `history.json` 追加式（采纳）**：每次 run 追加 1 条（run_id/git/config_snapshot/6 指标/**delta 用百分点 pp——否决 -5% 相对值歧义**）；与 `run_{ts}/q_{id}.json`（Q4 条级全量）**两层定位钉死**：条级=断点续跑/追溯，run 级=趋势/归因。
   - **回归触发 = 人工判断 + 功能周底线，否决 eval_trigger.py 自动检测脚本**：无 CI（W6 才引入）；pre-commit 跑 40~60min 拖死提交；`git diff("HEAD~1")` 有跨 commit 漏判 + 未提交工作区不捕捉缺陷；替代 = **看 `history.json` 尾行日期/commit**（5 行，功能周底线可检查）。
   - **`docs/eval-report.md` 8 节骨架**：① 数据集说明（version/created_at/anchor_samples/标注方法学）② 运行环境与双锚（git commit/dataset version/墙钟/并发）③ 指标表（六项：目标 vs 本轮 vs 上轮 vs 达标状态；成本双轨）④ 失败与异常附录（N 完整/M 部分/K 失败 + 60k/600k 标红 + hard_stop 事件 + 落盘路径）⑤ 人工抽检记录（两级表 + reviewer 字段 + 锚点复核"真回归 vs 数据漂移"）⑥ 可复现性（3 条重跑 Δ 表 + 归因）⑦ **指标演进趋势表（自 v0 多轮演进，pp 口径，history.json 驱动）** ⑧ 已知局限（单人标注/网络非确定性，如实声明）。
7. **与既有三件套的关系（✅ TBD-10 定案，grill Q8 拍板，2026-09-06）——两阶段管道 + 检索升第 7 指标 + 组合不重构**：
   - **两阶段管道（运行/评估解耦，采纳 E++ 内核）**：**Phase 1 `--run-only`** 并发跑 Graph 只存**原始产出**（report/findings/state.citations/cost/trace_id/双锚 → `results/run_{ts}/raw/q_{id}.raw.json`）；**Phase 2 `--eval-only --run-dir=…`** 读 raw 跑全部评估器产出指标（→ `results/run_{ts}/eval/q_{id}.eval.json` + 汇总）；**默认无参数 = 两阶段连续**（Q3 DoD"一条命令"保持）。收益：评估器失败/换 judge 模型/调试评估器均不重跑 Graph（省 6~9min/条），**话术 ="不重跑 Graph"而非"秒级"**（judge 调用仍烧 LLM）。与 Q4 断点续跑叠加：**条级断点（跳过已有 raw）+ 阶段级恢复（raw 齐但 eval 缺 → 只跑 Phase 2）**双恢复。
   - **关键修正（E++ 漏掉的坑）：`citation_eval.py:23` 不再重新 `validator.validate()`（那是又一次 LLM 对查）——改直读 `state.citations` 做统计**（主链路 validate 节点已产 verified/existence/fidelity/note）；保留 `--force-revalidate` 逃生门（独立评测脚本场景）。
   - **① 检索命中率升为正式第 7 指标（采纳）**：Q1 的 `gold_keywords`（5~8 个）即 ground truth，`run.py` 逐条记 `retrieval_hit_rate`（命中关键词数/总数）随七项指标一起出表——gold_keywords 不浪费；与引用准确率构成"上游原料 vs 下游产出"两个漏斗环节数字，面试叙事完整。
   - **② 组合不重构（采纳）**：run.py 直接 import 三个既有类做薄聚合层（3 个调用点 + 指标字典合并）；否决共享基类/注册表（接口本就不同，强拧统一接口为抽象而抽象）。
   - **③ 关键词优先 + 嵌入回退（采纳，仅此一处合法落点）**：检索命中率是唯一无 LLM 判定介入的机械指标，嵌入回退不造成判定器叠层；关键词 = 精确哨兵，未命中时 `gold_embedding` 余弦 >0.75 判"语义命中"，打 `matched_by: keyword|semantic` 标签**单独报告不混口径**（收 Q1 E++ 在此的预留账）；离线嵌入 ~1s、运行时零调用。

## 6. 设计策略
- **纯消费方零侵入**：eval 只读 graph 产出（report、state 既有字段），不动 graph/agents/state——防止"为评测改主链路"污染 W1~W4 已定契约。
- 复用 W2 验证器口径（existence/fidelity/by_type 已就位）与 W3/W4 成本表（per-model token 计数为 W4 Q7 依赖项，未落地则成本指标先缺位并显式标注）。
- 复用 `cli.py`/`run_e2e_smoke.py` 的注入模式（`llm_fn` 注入 / provider 可注入）保证可测性。
- 面试口径：eval 是"工程闭环的证据链"——数字 + 抽检 + 基线存档三位一体。

## 7. 验收标准（DoD）
- [ ] `python -m research_engine.eval.run --dataset research_engine/eval/dataset.jsonl` 一条命令出指标表（**7 项指标齐全**：完成率/引用准确率/覆盖度/**检索命中率**/成本/步数/反思有效性）
- [ ] dataset.jsonl ≥20 条，覆盖易/中/难 + 单轮/多轮/计算/学术，字段符合 plan.md §5
- [ ] 人工抽检 20%（≥4 条）记录落 `docs/eval-report.md`，含机器 vs 人类口径差异说明
- [ ] 目标值达成或如实记录未达成（完成率 ≥90% / 引用准确率 ≥85% / 覆盖度 ≥90% / 反思有效性 critic_stop 占比 ≥90%）
- [ ] 基线存档：eval 结果 json + markdown 双格式留存，可复跑对比
- [ ] 主链路零侵入：graph/agents/state 无改动（diff 为空）
- [ ] 新增 eval 单测（指标计算纯函数 / run.py 编排冒烟）全绿

## 8. 影响范围与风险
- `research_engine/eval/`：新增 `dataset.jsonl` / `run.py`；改造 `retrieval_eval.py`（TBD-10）；复用 `citation_eval.py` / `report_eval.py`。
- 新产出 `docs/eval-report.md`；如需对比基线则存 `research_engine/eval/results/` 或等价位置（TBD-9）。
- 回归面：W1 硬闸（token/步数指标数据源）、W2 验证器（引用口径）、W3 观测（eval run 是否进 Langfuse，TBD-9）、W4 工具（eval 触发 arxiv/code 的用例）。
- 风险：① eval 跑真实 API 的墙钟与成本失控（TBD-3/4）；② ~~指标定义不严谨~~（TBD-2 已定案：判定器分层 + 全部判据对齐实际代码，虚构依赖清零）；③ ~~反思有效性 ground truth 难标~~（TBD-8 已定案：结构性 + 交叉信号，不引入主观标注）；④ 环境漂移导致基线不可比（TBD-5）。

## 9. 测试策略
- 单测：指标聚合纯函数（注入伪造 state/report 验证各指标计算）；run.py 编排冒烟（mock 数据集 2 条 + 注入 llm_fn）；retrieval_eval 改造后命中率计算。
- 集成：真实小集（3~5 条）端到端跑一次，验证输出表结构与 `docs/eval-report.md` 落盘。
- 不依赖真实 API 的路径：provider 注入沿用既有模式；真实 API 冒烟单独标记（网络类）。

## 10. 变更记录
| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-04 | 建稿 | W5 启动 | 初始版：plan.md §5 口径落文档 + 现状盘点；优化方案留 TBD-1~10 待 grill | |
| 2026-09-06 | 定稿 | grill 收官 | **Q1~Q8 全部拍板、TBD 清零**：完整决策链见上 8 行；飞书镜像「第五周需求文档」已同步 | |
| 2026-09-04 | 拍板 | grill Q1 | **TBD-1 定案（E++ 裁决）**：混合来源 + AI 草案/人工校准（简化落库，否决两版 JSON 字段）+ **分桶**（回归锚点 6~8 条 vs 能力评估 12~14 条，防 selection bias）+ **双锚版本冻结**（dataset 头 + eval 结果运行时 commit）；**否决语义回退双模匹配**（validator LLM 对查已是语义级，0.75 引入新参数、口径混合不可解释；如 retrieval_eval 需要则单独报告，归 TBD-10） | |
| 2026-09-04 | 拍板 | grill Q2 | **TBD-2/8 定案（修正版 + E++ 二次裁决）**：判定器分层"一指标一判定器"；完成率 = done + error is None + ≥300 字 + 章节≥2 标题四条件全自动（**否决 500 字**误伤短报告；**否决 E++ 必填四章节**——虚构依赖第二次，真实报告为"一二三四+主题名"结构）；覆盖度 = LLM 对查；成本 = 类级差值 + **模型名桶/职责桶双轨**（5 实例化点传 role，含 validator 直建修正；数据源不用 state.token_used 防漏计 validator）；平均步数 = len(reflection_log)；反思有效性 = **结构性 critic_stop 占比≥90% + hard_gate 终态判定 hard_stop + 交叉信号**（早停=critic_stop∧覆盖度<90%，晚停=critic_stop∧轮数>5，**否决 E++ 0.8/1.5 动态系数**——伪精确）+ 人工抽检质量面；**否决期望跳数 ground truth** | |
| 2026-09-04 | 拍板 | grill Q3 | **TBD-4 定案**：judge 档位 = smart（qwen-plus，`report_eval` 改 `smart_json`）；预算三层（硬闸 200k/run 已有 + 单条 60k 标红 + 总量 600k 标红，**均不中止**——保留"为什么贵"诊断价值）；成本账 ¥0.48/轮；**墙钟修正：单条实测 6~9min → 串行 2~3h**（撤回 30~60min 话术，压墙钟归 Q4 并发） | |
| 2026-09-04 | 拍板 | grill Q4 | **TBD-3 定案（E++ 裁决）**：并发 3（ThreadPoolExecutor 不引 asyncio，墙钟 40~60min）+ **失败三档分级**（瞬态重试 1 / 局部 partial+missing_metrics 不重试 / 致命重试 1 + 错误栈）+ **任务超时 15min**（future.result(900) 换掉 asyncio.wait_for）+ **断点续跑**（按条落盘 run_{ts}/q_{id}.json）；**否决降级重试**（judge 撞 Q3，partial 已覆盖）；**根因治理 judge LLM timeout=60s**（LLM 层现无超时=主要僵尸源） | |
| 2026-09-04 | 拍板 | grill Q5 | **TBD-5 定案**：运行环境 = 全真实 API（不做 mock 评估轨，mock 只留单测注入）；漂移容忍 = 3 条（易/中/难）重跑 1 次，单条 Δ≤10% / 均值 Δ≤5%，超线如实记录不判失败；落痕 = 全量（report + state 快照 + 指标 + 异常栈 + 双锚，1~2MB） | |
| 2026-09-04 | 拍板 | grill Q6 | **TBD-6 定案**：两级抽检（报告级 4~5 份通读覆盖质量+反思面+2~3 条精读 / 引用级 10~15 条跨报告核验）+ 单人声明 + reviewer 字段接口 + 锚点桶人工复核确认"真回归 vs 数据漂移"；工时 2~3h | |
| 2026-09-06 | 拍板 | grill Q7 | **TBD-7/9 定案（E++ 二次裁决）**：基线 = `baseline.json` 冻结快照（采纳，修正：config_snapshot 每轮 run 记 + 默认值 qwen-plus 对齐 W4 Q7 + hash 降级"完整性校验"）；趋势 = `history.json` 追加式 + 报告第 7 节演进表（采纳，delta 用**百分点 pp** 否决相对 % 歧义；条级/run 级两层定位钉死）；触发 = **否决 eval_trigger.py**（无 CI、pre-commit 拖死、git diff 漏判），人工 + history 尾行检查；eval-report 8 节骨架定稿（含演进趋势表） | |
| 2026-09-06 | 拍板 | grill Q8 | **TBD-10 定案（两阶段管道裁决）收官**：**两阶段 = --run-only（存 raw）/ --eval-only（读 raw 跑评估）/ 默认连续**（一条命令 DoD 保持）+ 条级/阶段级双断点；**citation_eval 改读 state.citations**（E++ 漏坑：重新 validate = 又一次 LLM 对查，吃掉两阶段收益一半；保留 --force-revalidate 逃生门）；**检索命中率升正式第 7 指标**（gold_keywords 作 ground truth）+ **关键词优先/嵌入回退仅此一处**（Q1 预留落点，matched_by 标签单独报告）+ 组合不重构（薄聚合层）。**TBD 全部清零，W5 需求定稿** | |
| 2026-09-07 | 镜像修复 | W5 收尾 | **飞书镜像删除线误渲染修复**：overwrite 转义版（`~`→`\~`、有意 `~~删除线~~` 保留、代码内不转义）+ 注入 `<title>` 保显示名 + §7 追加验收状态注记（W5 已收官 commit f1e9a21/9fc2cf1）；验收标准 7 条全部达成，指标与抽检见 `docs/eval-report.md` | 9fc2cf1 |