# 项目状态看板（唯一真相源）

> **为什么有这份文档**：2026-09-16 盘点发现「文档状态漂移」已是项目最现实的管理风险 ——
> `docs/requirements/8-*.md` §1 元信息仍写「状态 = 草稿」、§1.1 全景表里 **before 基线与 Arm 1 仍标 ⬜**（实际已完成），
> 而同一份文档后半部分的 DoD 明细却标了完成。**两张口径打架 ⇒ 「已完成的重复做、未完成的被当成完成」**。
>
> **规则**：本文件是**当前状态**的唯一真相源。其余设计与需求文档（含 `8-*.md`、`.workbuddy/deepresearch-plan.md`）
> 只保留**历史决策与设计依据**，不再承担看板职责。

| 项 | 值 |
| --- | --- |
| 更新 | **2026-09-19**（after 基线以 3 轮结案、**D-18 已拍板不续跑**；确定性 DoD 8/8 完成；本地 `dev/master` 均指向 `53ad3b1`，代码冻结于 `f723c2d`） |
| 阶段 | W1~W7 已收官；**W8 收尾中（Arm 1~7 + 命名三分 + 台账 review + after 基线 3 轮全部结案；确定性 DoD 8/8 完成；剩余 = 对外材料）** |
| 最近 CI | **Py3.11/3.12/3.13 + ruff + pytest 三档全绿**（零 LLM、零 key），且**新增** `Eval 产物白名单纪律（Arm 7）` 步骤。**PR #7 这一轮**：dev push `35312917303` + PR 事件 `35312919557` + master 合并后 `35313178353` —— 全 success。⚠️ 该轮**第一跳 `35312654571` 三档同时红**（`test_before_baseline_runs_are_intact` 把「只在本机存在、未入库的 before 基线 run」当成了用例前提，CI 检出里没有 ⇒ 已改为「存在即校验、不存在则如实 skip」，见 `79845c6`）。其前一轮（`f2d5c3c`）为 `35310140078` / `35310228251` |
| 最近交付 | **W8 after 基线结案 + 确定性 DoD 验收表 ⇒ `53ad3b1`**（新增 `docs/eval-w8-after-baseline.md`、`docs/eval-w8-dod.md`，`tools/paired_before_after.py` 转正，**D-18 拍板不续跑**）｜**PR #5 / #6 / #7 均已合入 master ⇒ `65aabee`**　[#5](https://github.com/TianJinYing2006/DeepResearch/pull/5) / [#6](https://github.com/TianJinYing2006/DeepResearch/pull/6) / [#7](https://github.com/TianJinYing2006/DeepResearch/pull/7)（PR #7 = W8 命名三分。**看板自身不单独开 PR**，随下一次合并并入） |
| 最近基线 | **after 基线**（2026-09-19，20 题 × 3 runs，冻结 `f723c2d`，20/20 零异常）；对照 **before 基线**（2026-09-16，20 题 × 3 runs）。**四指标全部不可判定** ⇒ 结论见 `docs/eval-w8-after-baseline.md` |
| 测试基线 | **330 全绿**（命名三分 **+26** = `tests/test_status_naming.py`，已由 CI 三档确认）。历史：W7 期 131 → Arm 1 +26 → Arm 2 +39 → Arm 3 +20 → Arm 4 +20 → Arm 5 +18 → Arm 6 +22 → Arm 7 +13（⚠️ 期间另有若干非 Arm 归属的增量，故逐项相加与当期总数并不严格相等，勿据此推导） |
| Python | **仅支持 3.11~3.13**（以 CI matrix 为准）。本机实测用 venv `.workbuddy/binaries/python/envs/default` 的 **3.13.14**（与 before / after 基线记录的 `python_version` 一致；系统 3.14.4 缺 langgraph，不要用）：**pytest 330 全绿 + ruff All checks passed 均在本机跑过**。<br>⚠️ 跑 pytest 必须加 `--basetemp=<干净的新目录>`，且每次换新目录（不加会被沙箱批量删除守卫卡在临时目录 GC 上；复用非空目录会报假 ERROR） |
| 结论文档 | W7：`docs/eval-w7-conclusion.md`；W8 设计：`docs/requirements/8-fault-transparency-and-reproducibility.md`；**W8 after 基线结论**：`docs/eval-w8-after-baseline.md`；**W8 确定性 DoD 验收表**：`docs/eval-w8-dod.md` |

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

| **D-09** | **`.gitignore` 的出处置于独立注释行，绝不跟在 `!` 规则行尾** | 2026-09-18 ✅ 已落地 | 实测：`.gitignore` 只认**行首** `#`，`!path/  # 出处` 会把注释吞进 pattern ⇒ **`!` 规则整条失效**（去掉注释即放行、带上仍被忽略）。这正是原三条白名单「看着有注释、实际不匹配」的根因。且**空行会打断注释归属** ⇒ 出处必须写在规则**紧邻上方**（已固化进单测 `test_blank_line_breaks_comment_ownership`） |
| **D-10** | **自动枚举的文档不构成「引用」**：`docs/eval-report.md` 不算入库证据 | 2026-09-18 ✅ 已落地 | `report_gen.py` 自动写出的报告实测含 **74 个**互不相同的 run id（≈全量枚举），而手写结论 `docs/eval-w7-conclusion.md` 只提 **4 个** ⇒ 若承认自动表，「引用即入库」判据自我作废。要点在一件事：**只有「被人挑选过」才算数**。口径由 `AUTO_ENUMERATED_DOCS` 常量单点定义，白名单裁判与台账用 `spec_from_file_location` 加载同一份，避免两处分叉 |
| **D-11** | **Arm 7 双轨的处置纪律不同：git 侧可以动手（可回溯），磁盘侧只能出账（不可回溯）** | 2026-09-18 ✅ 已落地 | git 侧误伤可用 `git checkout` 恢复；磁盘侧 `results/raw/*.raw.json` 是 W7「零成本可复算」的唯一证据源（`tools/w7_backfill_*.py` 直接读），误删等于销毁证据且**无任何回滚**。⇒ 第一刀只做只读台账，清理须在 review 后单独拍板 |

| **D-12** | **做过「移出索引」类操作（`git rm --cached`）后，本机不得再跨该边界 checkout** —— master 与 dev 必须**同指向** | 2026-09-18 ⚠️ **用事故换来** | `git rm --cached` **只保护执行它那一刻的工作树**：这些路径变成「被忽略」，对后续 checkout / reset / pull 而言它们**属于旧版本**。实测：Arm 7 把 189 个跟踪文件降到 8 条白名单后，一次跨边界 `git checkout` 让 git 把产物**从磁盘物理删除**（删目录时连未跟踪的 raw 一起带走）⇒ `results/` **143.6 MB → 31 MB**。本地已用 `git branch -f master dev` 消除边界（**⚠️ 远端无法维持同指向，见 D-15**）。**推广**：任何「让已跟踪文件转为未跟踪」的治理动作，都必须配套「禁止跨边界切换」的纪律，否则等于给协作者埋雷 |
| **D-13** | **台账的「有手写引用」要区分「引用含结论文档/代码」与「引用仅来自看板/需求文档」，且后者只是软标记、不得当作删除依据** | 2026-09-18 ✅ 已落地 | 两个相反方向的陷阱：① **登记的反转** —— 治理过程把产物名写进处置记录，于是「描述过」被当成「有证据」（实测手写引用数 17 → 18 → 20）；② 反向误判更危险 —— 自动把「仅被看板/需求文档引用」判成「不算证据」时，**before 基线三个 run**（`run_20260916_001005` / `011813` / `022440`，靠 §10.4/§10.5.8 验收记录存续）会被误标为可删。故该分类只做提示（`tools/w8_artifact_ledger.py` `GOVERNANCE_DOCS`），**删除仍须人工确认** |

| **D-14** | **命名三分的方向 =「写侧只出新键 + 读侧 dual-read」，历史产物一律不回填** | 2026-09-18 ✅ 已落地 | 三条理由：① 历史 raw/eval 是 **Arm 7 台账的取证对象**，产物只读；② `tools/w7_backfill_*.py` 的「W7 零成本可复算」**依赖 raw 逐字节不变**；③ 回填会抹掉「这批数产生于旧口径」这个事实（与 **D-07** 拒回填 `prompt_hash` 同源）。⚠️ **与 §5.5.2 的「旧键同时写入」方向相反，这是刻意的**：那里产物**可重写**（summary 每次重算），所以把兼容成本压在写侧；这里产物**不可重写**，所以兼容责任落在读侧。代价已实测可接受：**1,498 raw + 1,443 eval 经 dual-read 全部读出、取值零越界** |

| **D-15** | **远端 master 与 dev 长期分叉：master 恒为 merge 提交 ⇒ D-12 的「同指向」只能在本机成立** | 2026-09-18，处置方式由于晏拍板（**选 merge 提交**，不重写远端历史） | **发现（PR #7 合并时暴露）**：PR #5 / #6 实际是以 **merge 提交**合入的（`184f11b` 双父 `[0144705, ce0b8ba]`、`5f9aece` 双父 `[184f11b, f2d5c3c]`）⇒ 远端 master 的尖端落在 dev 祖先链**之外**，对 dev 做 `PATCH force:false` 会得 `422 not a fast forward`；且 `5f9aece` 这个提交在本地仓库**根本不存在**。**内容层面零风险（已实测）**：`tree(5f9aece) == tree(f2d5c3c) == a831cd09` —— master 上没有任何 dev 历史里没有的东西，merge 提交只是把 dev 侧裹了壳；PR #7 合入后的 `3736bee` 其 tree 也 `== tree(79845c6)`。**⇒ D-12 口径据此收窄**：`git rm --cached` 边界的危险性靠**本机** master/dev 同指向消除（本次已 `git branch -f master dev` ⇒ 两者均 `79845c6`），远端 master 是否线性**不影响本机 checkout 安全**。**已知代价**：每次合并都会让远端分叉再深一层；将来若要线性历史，需另开议题（改走 rebase 合并，或一次性 force 对齐） |
| **D-16** | **台账判据补第四档「结构登记」；review 结案 = 维持现状、不删任何产物** | 2026-09-18 ✅ 已落地并结案 | **发现（review 前的判据自检）**：`tools/w8_artifact_ledger.py` 的 `SCAN_SKIP_DIRS` 含 `results` ⇒ 整目录跳过引用扫描。原注释理由是「产物自己会引用自己」—— 那只对**自指**成立；`w7_experiment_*/manifest.json` 的 `runs[].run_dir` 是**包含 / 溯源登记**，不是自引用 ⇒ **58 个 run / 110.0 MB 被误判成「无引用」**。⚠️ 若按原口径治理，会销毁 W7 配对实验的证据基座（ρ=0.551 / MDE=12.38pp 的逐题输入就在这批 run 的 `eval/*.eval.json` 里）。**修法**：走进去扫 + `_results_owner()` 逐条排除自指；新增「结构登记」独立列；软标记 `governance_only` **只看手写引用**（结构登记**不得**压掉它 —— 实测踩到：before 基线三个 run 会因 `history.json` 登记了 run_id 而失去「只有处置记录撑着」的人工确认提示）。**结果**：候选 83 条 / 128.2 MB ⇒ **15 条 / 4.2 MB**；`.workbuddy` 仍**必须**跳过（事故物理备份 + 逐日流水会把 run 名当处置记录写入）。**结案理由**：真正可清的量级约 **2.0 MB**（2 个零字节空目录 + `_tmp_backup_not_committed` + `*_DISCARDED`），而 `results/` 共 143.5 MB、D 盘可用 29 GB ⇒ **清理收益为零**，却要再承担一次 D5 那类「跨边界删除产物」的风险 ⇒ **维持现状**。另两条硬约束：10 个 `w7_experiment_*` 是 W7 实验记录**本体**（arm 定义 + notes，即使只有几 KB 也保留）；`run_20260916_*` 三条 before 基线 run 是对照臂，由 `history.json` 与 §10.4 验收记录双重支撑 |
| **D-17** | **`results/history.json` 的空转 `status` 字段：删（不是改名）** | 2026-09-19 ✅ 已落地 | 该字段自称「回归判定」，实际写 `summary.struct.regression`，而全仓**从未有任何代码写入 `summary.struct`**（只有 `run.py` 空结果分支写过 `"struct": {}`）⇒ 恒为 `"PASS"`。⚠️ **差点误判**：`report_gen.py:250` 的 `rec.get("status")` 看着像读取方，实则读的是 **W7 manifest 的 `runs[].status`**（登记表第 3 条落点），数据源不同 —— 逐条核对确认**零读取方**后才动手。**处置**：① 写侧 `append_history` 不再产出；② 已跟踪的 `history.json` **75 条记录全量剥离该键**（外科式文本删除 ⇒ diff 恰 75 行删除、零其他字节变化，保住了 CRLF 与其余内容逐字节一致）；③ `status_keys.BARE_STATUS_SITES` **移除**该条 —— 收敛后不留「已解决」说明，否则 `len(BARE_STATUS_SITES)` 永远虚高、还让人误以为有活体落点；④ 单测改名 `test_history_has_no_vacuous_status_field`，方向反转成锁「不再存在」。**教训**：`report_gen.py` 的注释里**不能写出那个键名的英文原文** —— 新用例靠「全仓搜该词零落点」锁这次删除，注释写它会自打脸（已踩到并修） |
| **D-18** | **after 基线：以 3 轮结案，不续跑后 6 轮** | 2026-09-19 ✅ **已拍板** | 实测**四指标全部不可判定**：coverage Δ=−5.1pp（SE 3.81 / MDE 10.66）、citation +3.1pp（9.21）、retrieval −2.5pp（3.88）、steps −1.2（1.36）。**关键发现：精度瓶颈在 before 侧** —— `SE = sqrt(σ_b²/R_b + σ_a²/R_a)/√n` 里 `R_b=3` 固定，而 before 基线已永久错过（代码已改，不可重采）⇒ `σ_b²/3` 不随 after 轮次下降；coverage 的 before 侧方差占比 **73%**，即便 after 跑到无穷轮 MDE 也只从 10.66 降到 9.13。**外推到 9 轮**：MDE 仅降 9~25%，三指标仍不可判定；唯一勉强过线的 steps（9 轮时 MDE 1.18 vs \|Δ\| 1.22）按 **D2 必须归因于 Arm 2 的功能性修复**，不得计入质量提升。**⇒ 修正 §10.5 的隐含假设**：「唯一出路是加 runs（不是加题）」只对**两侧同时加**成立；只加 after 侧会被 before 侧的固定方差锁死。**建议不续跑**：再花 ¥6~8 + 6~8h 换「大概率仍不可判定」不划算；且 W8 验收本就用 **A 类确定性断言（故障可归因率 0%→100%，零噪声）**，不依赖基线统计。**顺带的观察（标为观察、不作结论）**：after 三轮的轮次间极差 **2.92pp vs before 12.50pp**、σ_within 16.93 vs 23.96、ρ 0.793 vs 0.551 ⇒ 系统的**方差**显著变小（不是均值）；但 σ 只由 3 个轮次对估出、自由度极小（F≈2.0 处于边缘），要坐实就得加轮次 —— 而那恰是上面算过性价比极差的事。详见 `docs/eval-w8-after-baseline.md` |

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
| W8 Arm 6（可复现元数据） | ✅ **已交付**（2026-09-18，PR #4 已合并 ⇒ `fa66d27`） | `eval/prompt_hash.py` 新增（6 slot）；provenance 从 4 字段扩到 **10 字段**（+`prompt_hash`/`prompt_slots`/`scorer_version`/`citation_judge_model`/`coverage_judge_model`/`citation_judge_independent`）；raw 三条落盘路径统一走 `raw_provenance_fields()`；报告**在指标表之前**显著呈现裁判独立性；趋势表新增「尺子变了」检查。当前 `prompt_hash=cf95dafc78f98348`、`scorer_version=w8.1` |
| W8 Arm 7（产物治理双轨） | ✅ **已交付**（2026-09-18） | **轨道 1**：`.gitignore` 白名单重写为「引用即入库」+ 出处独立注释行 ⇒ `git ls-files research_engine/eval/results` 顶层 **恰 8 项 ≡ 白名单集合**（原 94 项偏离全收敛）；`history_bak_v10.json` 因零引用 `git rm --cached`（磁盘保留）。新增单点裁判 `tools/check_results_whitelist.py`（**已接 CI**，变异测试验证非空转）+ 13 条单测。**轨道 2**：`tools/w8_artifact_ledger.py`（只读）⇒ `docs/eval-artifact-ledger.md`（100 条目 / 143.5 MB；引用四档：手写 20 个、**结构登记 76 个 / 131.1 MB**、无任何证据 **15 个 / 4.2 MB**）。**✅ 台账 review 已于 2026-09-18 结案：维持现状、不删除任何产物**（判据修正与结案理由见决策记录 **D-16**）；review 全程未删除/移动/重命名任何产物。**⚠️ 中途出过一次数据事故（已恢复，见缺陷 D5）** |
| W8 命名三分（Arm 1 遗留项） | ✅ **已交付并合入 master**（2026-09-18，PR #7 ⇒ `65aabee`） | 键名契约唯一真相源 `research_engine/eval/status_keys.py`（新增）：层② `invoke_status`（raw 顶层）/ 层③ `metrics_status`（eval 顶层）/ `LEGACY_STATUS` 只读。`run.py` **7 处落盘键**改名；读侧（`phase2` / `_summarize` / `report_gen` 异常附录 / `tools/measure_paired_rho.py`）全部 dual-read。**核心决策：历史产物一律不回填**（取证对象 + W7 零成本复算）⇒ 全量只读对账 **1,498 raw + 1,443 eval 全部读出、取值零越界**。新增 `tests/test_status_naming.py` **26 条**（含端到端落盘扫描 + 两份反向守卫 + AST 源码守卫）。**副作用登记**：发现 `history.json` 的 `status` 是恒为 `"PASS"` 的空转字段（见下方「未决 / 待拍板」） |
| W8 after 基线 | ✅ **已结案**（2026-09-19，3/3 轮） | 四指标**全部不可判定**；外推显示跑满 9 轮 MDE 仅降 9~25%，仍判不动 ⇒ **D-18 拍板不续跑**。20/20 零异常 ×3、`git_dirty=false` ×3、¥2.5 / 2h29m。结论见 `docs/eval-w8-after-baseline.md` |
| W8 确定性 DoD | ✅ **8/8 完成** | 故障可归因 / 状态分层 / 异常退出 / 成本守恒 / 质量闸 / 可复现元数据 / 产物治理 / 历史兼容 —— 逐项有测试与 CI 证据（**330 全绿**），见 `docs/eval-w8-dod.md`。⚠️ 与统计侧严格分开：**机制可验收 ≠ 均值质量提升成立** |

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
| ~~D3~~ | ~~裸 `status` 字段在三层的语义混用（run / invoke / metrics）~~ | ✅ **已修**（2026-09-18，命名三分）：层② `invoke_status` / 层③ `metrics_status` 落盘，读侧 dual-read；其余四处裸 `status` 已逐条登记在 `status_keys.BARE_STATUS_SITES` 并由单测验证，见「W8 命名三分」行 |
| **D5** | 🚨 **Arm 7 期间的数据事故（已恢复，但非 100%）**：一次**跨 Arm 7 边界**的 `git checkout` 让 git 把「旧 HEAD 跟踪、新 HEAD 不再跟踪」的路径连同其目录下**未跟踪**的 raw/eval 一起删除 ⇒ `results/` 从 **143.6 MB 掉到 31 MB**。**恢复**：① 从 D 盘回收站按 `$I` 元数据里的原始路径定向还原 **2,418 个文件**；② 用 git index 补回被删除的 tracked 文件；③ 从物理备份回拷 ⇒ 现 **3,206 文件 / 151.8 MB**（台账口径 143.5 MB）。**永久丢失 5 个文件**（≈0.1 MB）：`run_20260910_033127/raw/q_014.raw.json`、`run_20260911_004201/eval/q_016` / `q_018` / `q_019` / `q_020.eval.json` —— 均属无引用、非白名单的历史 run，**不影响** W7 权威容器与任何结论文档。**副作用**：还原把全部结果文件 mtime 刷成恢复时刻（台账该列失去取证价值）；上述两 run 的 `summary.json` 仍写 `complete=20/20` ⇒ **文件缺失与 summary 不自洽** | 已立铁律（决策 D-12）；台账已自述该列失效；两 run 若要继续引用需重新生成 raw |
| D4 | 不要用 coverage 提升验收 W8：实测 MDE=12.38pp > 预期效应 4~10pp，**统计上不可判定**。W8 的验收口径是**确定性断言（故障可归因率 0%→100%）** | 验收口径 |

## 执行顺序（按顺序串行，勿并行铺开）

1. ✅ **已完**：文档状态同步 → Arm 4 真填充 → 删临时异常映射 → 补测试（20 条）
2. ✅ **已完**：Arm 5 质量闸 → **D1 成本守恒** → stderr/bootstrap → 字段重命名 → 报告带 ±stderr 与 §0 质量闸小节 → **75 个历史 run 回填完成并验收**（见上节）
3. ✅ **已完**：Arm 6 可复现元数据 —— 5 个提示词构建器抽为唯一产生点 → `prompt_hash`（对开关敏感）→ 裁判模型/独立性结构化 → `scorer_version` → raw/summary/history/report 四处落地 + 22 条测试
4. ✅ **已完**：Arm 7 产物治理双轨 —— 白名单判据升级为「引用即入库」→ 出处写法踩坑修正（行尾 `#` 废规则）→ 引用分两档（自动枚举不算证据）→ 单点裁判 + 13 条单测 + 接进 CI → 只读台账出账（**未删任何产物**）
5. ✅ **已完**：台账 review 结案（2026-09-18）—— 修掉判据盲区（第四档「结构登记」）⇒ 候选从 83 条 / 128.2 MB 收敛到 **15 条 / 4.2 MB** ⇒ 拍板「维持现状、不删任何产物」（D-16）
6. ✅ **已完**：命名三分 —— `invoke_status` / `metrics_status` 落盘（写侧只出新键）+ 读侧 dual-read；**历史产物不回填**（Arm 7 取证纪律 + `tools/w7_backfill_*.py` 零成本复算）；新增 26 条单测（含端到端落盘扫描与两份反向守卫）
7. ✅ **已完**：冻结代码 `f723c2d` → after 基线 **3 runs**（48~51 分钟/轮、¥0.79~0.92/轮，远快于原估 1.2h）→ 四指标**全部不可判定** → **D-18 拍板：以 3 轮结案、不续跑后 6 轮**。⚠️ 原估「±7.3pp / ¥11 / 6.5h」均被实测推翻（coverage 的 MDE(3v9) 实测 **9.66pp** > 预期效应 4~10pp；实际 3 轮仅 ¥2.5 / 2h29m）
8. ✅ **已完**：**确定性 DoD 验收表**（`docs/eval-w8-dod.md`，8/8 项有测试 + CI 证据）+ 实测结果**回写设计文档**（新增 §3.3.1-b「瓶颈在 before 侧」、§10.5.6 补实测行、§10.5.7 拍板处补执行更正）
9. ⬜ **最后对外材料**：README 能力与限制 → 博客③ → 引用链接

## W8 收尾纪律：产物冻结（2026-09-19，D-18 拍板后生效）

**以下对象不再改动**，除非另开议题：

| 对象 | 冻结范围 |
| --- | --- |
| `research_engine/eval/results/` 历史产物 | 不再删除 / 移动 / 改名 / **回填**（含 before 三个 run 与 after 三个 run 的 `raw` / `eval` / `summary`） |
| before / after 原始 `raw` | W7「零成本可复算」的**唯一证据源**（`tools/w7_backfill_*.py` 直接读它） |
| 冻结代码 `f723c2d` | after 三轮均产自该树（产物落盘提交 `462e318` / `6dc6173` / `84fed03` 只改 `docs/eval-report.md` 与 `results/history.json`，`research_engine/` 树未动） |
| 已落盘的 summary / history / report | 不再人工编辑（它们由 `run.py` 自动整覆盖） |
| W8 关键统计数字 | coverage Δ=−5.1pp（SE 3.81）、citation +3.1pp（3.29）、retrieval −2.5pp（1.39）、steps −1.2（0.48）；MDE(3v3) = 10.66 / 9.21 / 3.88 / 1.36 |

**后续只允许做对外材料**：README 能力与限制 → 博客③ → 引用链接与出处整理。

## 未决 / 待拍板

| 项 | 说明 |
| --- | --- |
| after 基线后 6 轮是否续跑 | ✅ **已结案**（2026-09-19 拍板 = **不续跑**，见 **D-18**）：四指标**全部不可判定**，且外推显示跑满 9 轮仍判不动（`R_b=3` 锁死 before 侧方差）。**以 3 轮结案**。⚠️ 若将来推翻本决策，driver 仍在 `.workbuddy/after_baseline_driver.sh`，且**配对时必须排除冒烟 run `run_20260919_004620`**（pilot 2 题，会混进 `run_20260919_*`） |
| W7 五开关去留 | ✅ **已文档化并保留当前默认**：五个开关仍默认全开；`.env.example` 已记录有效值、成本与待 W8 重测项，README 已补充跑批时的 provenance 记录要求。运行行为暂不改，后续仅在 W8 重测后再裁定。见 `docs/w7-switch-disposition.md` |
| GitHub 凭据链修复 | `~/.gitconfig` 的 helper 写成反斜杠路径导致推送取不到凭据，永久修法 `gh auth setup-git`（改全局配置） |
| Gitee 凭据存储 | 是否把令牌存入 wincred（安全决策） |
| 台账 review | ✅ **已结案**（2026-09-18，见 **D-16**） | `docs/eval-artifact-ledger.md`：判据补第四档「结构登记」后候选从 83 条 / 128.2 MB 收敛到 **15 条 / 4.2 MB**，主理人拍板 **维持现状、不删任何产物**。⚠️ 两条反向陷阱仍然有效：① **引用只增不减是有偏的** —— 治理过程会把产物名写进处置记录，「写了说明」≠「原本被引用」；② **「引用仅来自看板/需求文档」也不等于可删**（before 基线三个 run 正是靠 §10.4 验收记录存续）。②③ 之外新增第四条：**「扫不到引用」也不等于可删** —— `tools/measure_paired_rho.py --runs` 是 argv 传参、从不落盘，那批 run 名在仓库里天生无迹可循 |
| 事故残留清理 | `.workbuddy/_arm7_results_backup/`（**3,206 文件 / 151.8 MB** 物理备份）与 `.workbuddy/restore_results_from_recycle.py`（一次性还原脚本）—— 事故已恢复完毕，**待主理人确认后**才可删（`git rm --cached` 边界已用 `git branch -f master dev` 消除） |
| **其余裸 `status` 是否收敛** | ✅ **已处置**（2026-09-19，**D-17**） | 层②③ 之外残留的裸 `status` 原登记 4 条，`history.json` 那条已**删除字段**（写侧不再产出 + 75 条记录全量剥离 + 登记表移除该条 + 单测反向锁「不再存在」）；`BARE_STATUS_SITES` 现为 **3 条**，全部建议保持：`state.status`（graph 流转状态，嵌在 `state` 命名空间内，改名会与 Pydantic schema 脱钩）、W7 manifest 的 `runs[].status`（历史证据，口径以 W7 结论文档为准）、Langfuse trace 的 `status`（外部字段）。⚠️ 查证 `history.json` 是否真「零读取方」时踩到一次假阳性：`report_gen.py:250` 的 `rec.get("status")` 读的其实是 **W7 manifest 的 `runs[]`**，不是 history —— 数据源不同，逐条核对才确认零读取方 |现状已由 `tests/test_status_naming.py::test_history_status_is_vacuous` 锁住，不会悄悄漂移 |
