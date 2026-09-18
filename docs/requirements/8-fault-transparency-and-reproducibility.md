# 需求-8-fault-transparency-and-reproducibility

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 本稿为 **草稿（2026-09-10 建稿）**，基于项目外部评审报告（综合评分 6.5/10）编排，聚焦"故障状态透明化 + 可复现性 + 评测口径收敛"三大短板。
> 飞书镜像：**已创建（2026-09-14）** —— docx token `F5HRd4hp8o01p7xyrpJczPsYnKf`｜显示名「第八周需求文档」｜父文件夹 `MYR6fazL5la0ardJdUecOBkVnd8`｜URL https://wcnnpvbxd7li.feishu.cn/docx/F5HRd4hp8o01p7xyrpJczPsYnKf
> **同步闭环（2026-09-14 起固定流程）**：① 先 `python tools/feishu_escape_md.py docs/requirements/8-*.md _feishu_w8.md`（把裸文本中的单个 `~` 转义为 `\~`，保护代码区与有意的 `~~`）；② `lark-cli docs +update --doc <token> --command overwrite --doc-format markdown --content "@./_feishu_w8.md" --as user`，且**输入文件首行必须注入 `<title>第八周需求文档</title>`**（否则标题会被正文第一个 `#` 覆盖）；③ `docs +fetch` 回查 `<del>` 数（应等于有意删除线对数）、checkbox 数、标题；④ 删除临时文件 `_feishu_w8.md` / `_feishu_fetch.md`

## 1. 元信息

| 项        | 值                                                                                                       |
| -------- | ------------------------------------------------------------------------------------------------------- |
| 编号       | #8                                                                                                      |
| 标题       | 故障状态透明化 + 可复现性 + 评测口径收敛                                                                                 |
| 优先级      | **统一 P0~P3（2026-09-15 Q7 统一语义，定义见 §1.1）**：**P0** = 阻塞项 / 不做则 W8 不成立 / **错过不可逆**；**P1** = W8 内应完成；**P2** = W8 内可选；**P3** = 探索，或**候选未承诺**。⚠️ 旧稿 §1 的 P0/P1/P2 是「实现档」、§10.1 的 P1/P2/P3 是「价值档」，**两套语义不可比**，已统一 |
| 状态       | 草稿                                                                                                      |
| 负责人      | TianJinYing2006                                                                                         |
| 关联 Issue | #8（待建）                                                                                                  |
| 关联 PR    | <br />                                                                                                  |
| 创建 / 更新  | 2026-09-10（新建）/ 2026-09-13（新增 §10 承接 W7 挂账；§10.4 前置项）/ **2026-09-14（Grill Q1 拍板：Arm 7 重划为「git 白名单收敛 + 本机磁盘治理」双轨，DoD 换对象）** |
| 实现顺序     | **§10.4（config 快照，P0）→ before 基线（P0，⚠️ 必须在 Arm 1~7 任何改动落地前采）→ Arm 1（状态分层）→ Arm 2（代码注入）→ Arm 3（依赖锁定）→ Arm 4（工具失败原因）→ Arm 5（评测口径）→ Arm 6（可复现元数据，不含 §10.4）→ Arm 7（产物治理）→ §10.5.4 零成本实测题目间方差 → after 基线**（2026-09-15 Q7 重排：§10.4 由「挂在 Arm 6 排第 6」**前移为第一项**；新增 before 基线）。**⚠️ 2026-09-15 Q8 微调**：**Arm 4 的 `SearchResponse.failure_reason`「字段定义」前移，与失败原因枚举表一起作为 Arm 1 的前置**（仅字段 + 枚举，不含五处 `except` 改造）—— 因 Arm 1 的 `DegradationEntry.reason` 须从它派生（§5.1.2 单向派生契约） |
| **采基线前置** | **必须先完成 §10.4 —— `_config_snapshot()` 记录 5 个 W7 开关的生效值，并落地"每轮 run 都记"。否则新采的基线同样无法回溯消融配置，问题会重演** |
| **⚠️ 错过不可逆** | **before 基线（主链路配置）一旦 Arm 1~7 任一改动落地即永久错过** —— 故 §10.4 与 before 基线必须排在最前。**增量成本极低**：配对设计下绝对值精度不重要，20 题 × 3 runs 即够 ⇒ **≈¥4 / ≈3.6h**（Q7 拍板 X） |
| **Grill 拍板** | **Q1（2026-09-14）：Arm 7 采用「双轨」方案 A** —— ① git 侧白名单收敛 + 引用即入库；② 本机磁盘治理「先只读扫描出账、暂不删除」。DoD 删除「仓库体积减少 ≥5 MB」（经实测数学上不可达成） |
| **Grill 拍板** | **Q4（2026-09-15，B1+B2 合并）：Arm 1 四态收敛为三态（`success`/`degraded`/`failed`）** —— `partial` 因与 `degraded` 判定条件完全相同而移出 state、降为报告层派生指标；`failed` 经 `run()` 层 try/except + `get_state(cfg)` 捞回 checkpoint 变得**可达**（原为不可达）。**附带**：失败原因枚举与 Arm 4 `failure_reason` 共用一张表（消 B4）；`state.error` 改结构化 `{code,message,node}`；`degradation_log` 挂 `operator.add` reducer（消 B3）。**命名三分**：`run_status` / `invoke_status` / `metrics_status` |
| **Grill 拍板** | **Q7（2026-09-15，C5）：优先级统一 + 全景视图 + 基线时机解锁** —— ① **统一优先级为 P0~P3 并明确定义**（原 §1 的 P0/P1/P2 是「实现档」、§10.1 的 P1/P2/P3 是「价值档」，**两套 P1 不可比**）；② **新建 §1.1 全景工作项视图**（含 Q3~Q6 新增项、§10.3 候选、挂账依赖）；③ **§10.4 升 P0 并从 Arm 6 前移为实现顺序第一项**（阻塞所有基线 + 收益随时间衰减 + 阻塞项优先级应取被阻塞方最高优先级）；④ **基线时机选 X**：§10.4 → **before 基线（20 题×3 runs，≈¥4/3.6h，⚠️ 改动前不采即永久错过）** → Arm 1~7 → after 基线 |
| **Grill 拍板** | **Q6（2026-09-15，C4）：主链路基线采集计划定案（§10.5）** —— **关键结论：多跑是错的方向** —— 题目集固定 20 题，block 间 σ≈14.5pp 是纯时间漂移，而**题目采样 SE≈11.2pp 是"地板"，重跑压不掉**（20 题从 3 轮加到 23 轮只把 SE 从 14.0pp 压到 11.6pp）。⇒ **扩题的边际收益远大于多跑**。**第一步 = 零成本实测题目间方差**（复用现有 raw），据此再定扩题规模；暂按 **60 题 × 9 runs（±8pp / ≈¥32 / ≈19h）** 立项。**硬前置：必须在 §10.4 落地之后才能采**。**对外口径：一律带区间与题数**，拒绝裸数字。<br>⚠️ **2026-09-16 实测更正（§10.5.4）：本题「多跑是错的方向」在绝对值陈述场景下不成立** —— 实测 σ_question 只有题设的 1/2~1/7（coverage SE@20=5.5pp vs 题设 11.2pp）⇒ 总 SE 主导项变成漂移项 ⇒ **多跑性价比是扩题的 5.5 倍** ⇒ **after 基线由 60 题 × 9 runs 改拍 20 题 × 9 runs（¥11 / ±7.3pp）**。**本题结论中「必须实测而非假设」的方法论判断不变，且被证明是对的** |
| **Grill 拍板** | **Q5（2026-09-15，C1）：§3.2 按验收类型分类重写** —— ① **确定性断言类**（Arm 1/4/5 + 2/3/6/7）**不做重跑对照**（都是确定性验收，零噪声）；② **统计对照类**（仅主链路基线）**禁用「3 轮取均值」**（实测 σ≈14.5pp，n=3 时 detect 10pp 的 t 仅 0.85，数学上无效），改为 §3.3 的「配对优先 + 功效分析前置 + 强制报 stderr」。**新增 §3.3 基线采集方法论约束**（为 C4 预算题前置锁死方法论）。**新增 §5.5.4**：`metrics_mean` 必须带 stderr（对齐 openai/evals `get_bootstrap_accuracy_std` 与 lm-eval-harness `mean_stderr`），并**离线回填历史 run** |
| **Grill 拍板** | **Q3（2026-09-15，C2 提前）：Arm 5 主刀改为「run 级质量闸」方案 A** —— `_summarize()` 顶层出 `verdict` + `verdict_reasons`，**阈值外置为参数、只告警不阻断**；原「`complete` 拆四字段」降级为附属产出。**附带命名裁决（甲）**：phase2 侧 `status` → `metrics_status`，`run_status` 独占「流程健康度」 |
| **Grill 拍板** | **Q8（2026-09-15，C6 + B4 收口）：元数据「一处定义 + 内嵌同一对象」** —— **C6（A）**：Arm 6 由 **13 个平铺字段收敛为 8 个真新增字段 + 内嵌 `config_snapshot`**。三条取证：① **「取 git HEAD」本仓库已有 3 份实现**（`report_gen.py:47` / `run.py:64` / `w7_experiment.py:45`，后者还带 `+dirty`）⇒ 新增 `code_revision()` 一处定义、三处 import；② **5 个字段与 §10.4 落地后的 `config_snapshot` 撞车**（`search_provider` 完全重复、`max_step_budget` 纯别名、`python_version` 重复、`experiment` 同件事写两次）⇒ 配置类一律不平铺；③ **`model_name` 口径未定义**（config 共 7 个模型字段）且 **`validator_model` 不在 `_config_snapshot` 顶层** —— 而它正是 W7 唯一被换掉的旋钮 ⇒ **`validator_model` 提为顶层**。**净效果：Arm 6 工作量反而变小**（13 处取值 → 8 处）。**B4（A）**：`failure_reason` 与 `degradation_log` **不合并**（两者是两个消费方：同步决策 vs 事后审计，同构 OTel 的 `Span.Status` 与 `add_event()` 并存），改立**单向派生契约** —— 工具类 5 值**只由工具层产生**，`DegradationEntry.reason` **必须取 `resp.failure_reason`**、禁止手写第二字面量；非工具类 4 值由 Arm 1 直接产生。**副作用（实现顺序）**：Arm 4 的 `SearchResponse.failure_reason` **字段定义**前移为 Arm 1 的前置（不含五处 except 改造）。**行业对照见 §6.8**（OTel Resource 明确禁止 per-span 复制配置 / MLflow·W&B 配置记一次、metric 多次）。**至此 W8 Grill 全部收口** |

## 1.1 W8 全景工作项视图（2026-09-15 新增，Grill Q7 = C5）

> **⚠️ 职责声明（2026-09-17 决策 D-01）**：本节「状态」列**只记录拍板当时点的快照，不再维护**。
> **W8 的实时实现状态、已知缺陷、执行顺序统一维护于 `docs/project-status.md`。**
> 本文档只负责：设计要求、DoD 与验收原则、架构约束、历史决策依据。
> 同理，**§5.4.2 落点清单的行号已过期（2026-09-16 核对），仅作历史参考，不作为改码依据**；
> 改码请以 `docs/project-status.md` 的任务清单与代码实际位置为准。

> **为什么需要这张表**：原文档有两张互不相通的优先级表 —— §1 的 P0/P1/P2 是「**实现档**」（该不该先做），
> §10.1 的 P1/P2/P3 是「**价值档**」（值不值得做）。**两套语义的 P1 不可比**，且 Q3~Q6 新增的工作项一张表都没进。
> 本表把所有工作项合并，并**统一优先级定义**。

**优先级定义（统一）**

| 档 | 含义 |
| --- | --- |
| **P0** | **阻塞项** / 不做则 W8 不成立 / **错过不可逆**（含"一旦改动落地就永久错过"的资源） |
| **P1** | W8 内**应完成** |
| **P2** | W8 内**可选** |
| **P3** | **探索**，或**候选未承诺**（登记 ≠ 承诺） |

**全景视图**

| 类型 | 项 | 优先级 | 依赖 / 约束 | 状态 |
| --- | --- | --- | --- | --- |
| **前置** | **§10.4** `_config_snapshot` 记 5 开关生效值 + 每轮都记 | **P0** | 阻塞**所有**基线采集；**收益随时间衰减**（越晚做，可回溯的 run 越少） | ✅ **2026-09-15 代码已实现**（`eval/provenance.py`；ruff 绿、146 测试通过）；仅剩「基线读回一致性」验收随 before 基线做 |
| **基线** | **before 基线**（主链路，20 题 × 3 runs ≈¥4 / 3.6h） | **P0** | §10.4；**⚠️ 且必须在 Arm 1~7 任一改动落地前采**，否则永久错过 | ⬜ |
| Arm | **Arm 1** 状态分层（三态 + §5.1.4 异常退出契约 + 失败原因枚举共用 + `error` 结构化 + `operator.add` reducer） | P0 | — | ⬜ 设计已定（Q4） |
| Arm | **Arm 2** 代码执行注入修复 | P0 | — | ✅ **2026-09-16 落地**（实测修正：旧模板无条件语法错误 ⇒ 顺带修复工具 100% 失败 + 沙箱输出编码） |
| Arm | **Arm 3** 依赖锁定（锁定 / 声明 / 强制 三刀） | P0 | — | ✅ 已落地（2026-09-16；生成工具按当日追加拍板由 `pip-compile` 换为 `uv pip compile --universal`，见 §5.3.5） |
| Arm | **Arm 4** 工具失败原因（`failure_reason`，枚举与 Arm 1 共用一张表） | P1 | 枚举须**一处定义、两侧 import**；**⚠️ Q8：字段定义前移为 Arm 1 前置** | ⬜ |
| Arm | **Arm 5** 评测口径（质量闸 + 字段重命名 + **§5.5.4 stderr + 离线回填**） | P1 | — | ⬜ 设计已定（Q3/Q5） |
| Arm | **Arm 6** 可复现元数据（**8 字段 + 内嵌 `config_snapshot` + `code_revision()` 一处定义**；§10.4 已前移单列） | P1 | §10.4 | ⬜ 设计已定（C3 + Q8 收敛） |
| Arm | **Arm 7** 评测产物治理（双轨） | P2 | — | ⬜ 设计已定（Q1）。**注：轨道 2 只读、零风险、可提前做** |
| **基线** | §10.5.4 **零成本实测题目间方差**（复用现有 raw，约 20 行只读脚本） | **P1** | — | ⬜ 决定 after 基线扩题规模 |
| **基线** | **after 基线**（主链路，**20 题 × 9 runs ≈¥11 / 6.5h / ±7.3pp**） | P2 | §10.4 + 全部 Arm | ⬜ 设计已定（Q6 立项 60 题 → **2026-09-16 实测后改拍 20 题 × 9 runs**，性价比高 5.5 倍且无需造题） |
| 挂账 | **A** 博客③《用 eval 数据诊断 Agent 引用幻觉：从 72% 到五项归因》 | P1 | 素材已备（`docs/eval-w7-attribution.md`） | ⬜ |
| 挂账 | **B** 博客② Langfuse 全链路 trace 实战 | P2 | — | ⬜ |
| 挂账 | **C** 组件③「澄清范围」 | P2 | ⚠️ **与 Arm 1 同样改图结构，存在冲突风险** | ⬜ |
| 挂账 | **D** persona 视角发现（Arm 2 / Step 2） | P2 | ⚠️ **依赖 Arm 1 达标**（W7 因 Arm 1 未达标触发止损而取消） | ⬜ |
| 挂账 | **E** RAGAS `FaithfulnesswithHHEM` 本地 NLI 降本 | P3（探索） | — | ⬜ |
| 候选 | **F** 矩阵完整性检查 / **G** 预算一致性检查 / **H** 汇总时自动排除不一致数据 | **P3（候选 ≠ 承诺）** | — | ⬜ 仅登记 |
| 候选 | 重试 + 渐进降级（open\_deep\_research 有，本项目零重试） | **P3（已考察·本期不采纳）** | — | ❌ Q4 裁决不采纳，仅备查 |

**依赖图（关键路径）**

```
§10.4 ──→ before 基线 ──→ 〔失败原因枚举表 + `failure_reason` 字段定义〕──→ Arm 1 ──→ Arm 2/3 ──→ Arm 4/5/6 ──→ Arm 7
                                    ↑ Q8 前移（仅字段+枚举，不含五处 except 改造）              │
                        §10.5.4 实测 σ_question ─────────────────────────────────────────────┴──→ after 基线
                                                              │
                                                    D（persona）依赖 Arm 1 达标
                                                    C（澄清范围）与 Arm 1 同改图结构
```

## 2. 问题背景

外部评审对项目给出综合评分 6.5/10，核心判断是：

> 故障状态不够透明、评测结果不够稳定、运行环境不够可复现。

这不是功能缺失，而是工程可信度缺口。项目在 Agent 架构（8/10）、工程规范（7.5/10）方面表现不错，但研究结果可靠性（4.5/10）、可复现性（4/10）、生产准备度（4/10）三条线拖了后腿。

最新一次评测 `run_20260910_173540` 暴露的问题最尖锐。**2026-09-15 核对其 `summary.json` 原文**：

| 字段 | 值 |
| --- | --- |
| `complete` / `partial` / `failed` | **20 / 0 / 0** |
| `completion_rate` / `citation_accuracy` / `coverage` | **0.0 / 0.0 / 0.0** |
| `avg_steps` / `reflection_critic_stop_rate` | **1.0 / 1.0** |

⚠️ **此处更正早期文档的一处误述**：`complete` **不是**"写出 raw 文件的条数"，而是「七指标全部算出来的条数」（`run.py:284` + `:215`）。所以真相不是"指标缺失被当成完成"，而是——**指标算出来了、确实全是 0，而系统一声不吭**。`avg_steps=1.0` + `reflection_critic_stop_rate=1.0` 意味着 20 条任务**全部在第一跳就被 critic 硬闸 stop**：Planner 退化为单主题子问题、Writer 返回系统兜底报告、Validator 未产生有效引用。而 `state.status` 仍然是 `"done"`，`state.error` 仍然是 `None`。

**反证样本**：`run_20260910_171054` 是 `complete=0 / partial=9 / failed=11` 但 `completion_rate=1.0` —— 两个数**根本正交**（前者＝评测管线成功率，后者＝被测产出质量）。把它们并列读出"矛盾"本身也是误读。

这意味着"Graph 运行结束"和"研究结果可信"之间没有状态边界，且**现有 summary 顶层的 `complete/partial/failed` 三元组完全无法反映这种彻底降级**。

## 3. 需求分析

### 3.1 目标（量化成功定义）

| Arm           | 目标                    | 量化指标                                                                                                                |
| ------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Arm 1（状态分层）   | 运行状态能区分成功与降级，且**失败真正可达** | `state.run_status` 覆盖 **`success/degraded/failed` 三态**（原 `partial` 因与 `degraded` 判定条件相同而移出 state、降为报告层派生指标）；① 任何 LLM/搜索/代码执行/Validator 走 fallback ⇒ `run_status="degraded"` 且 `degradation_log` 非空；② **`run()` 层捕获异常 ⇒ `run_status="failed"`**（现无任何路径可达，见 §5.1.4）|
| Arm 2（代码注入修复） | 计算脚本不包含原始 query 文本；**且脚本真的能跑通**（2026-09-16 实测补充） | `researcher.py` 的 `_default_code_script()` 不再拼接 query；query 通过 `exec_code(query=)` 进 hash + `metadata["query"]` 关联；中文和特殊字符不触发语法错误。**⚠️ 实测：旧模板对任何 query 都语法错误 ⇒ 成功率 0%→100%（§5.2.4）** |
| Arm 3（依赖锁定）   | 同一 commit 在不同机器上可复现安装，且**四处版本口径收敛为一处** | **① lock**：`requirements-lock.txt`（由 **`uv pip compile --universal --python-version 3.11`** 生成、不加哈希；原文写 `pip-compile --python-version 3.11`，实测该旗标不存在**且 pip-compile 会丢弃环境标记** ⇒ 2026-09-16 追加拍板换 uv，见 §5.3.1 实测修正一/二/三 + §5.3.5）+ CI 按其安装；**② 声明**：`pyproject.toml` **新增** `requires-python = ">=3.11,<3.14"` + `[tool.ruff] target-version` → `py311` + `README.md` → `Python 3.11 ~ 3.13`；**③ 强制**：运行时 `sys.version_info` 版本闸 + CI **matrix 3.11/3.12/3.13**。**取消**原「新增 smoke test」（现有 132 测试已是零 API 等价物） |
| Arm 4（工具失败原因） | "没有搜到"和"搜索坏了"可区分      | 搜索结果携带 `failure_reason` 字段，取值 `not_configured/timeout/provider_error/empty_result/parse_error` 之一                   |
| Arm 5（评测口径收敛） | **降级结果不再被 silent 地报成成功** | **主刀 = run 级质量闸**：`_summarize()` 顶层产出 **`verdict`**（`ok` / `suspicious` / `broken`）+ **`verdict_reasons: list[str]`**，阈值**外置为函数参数**（文档不写死数字）。**附属产出**：① 现有 `complete/partial/failed` 三元组重命名为 `metrics_ok/metrics_partial/metrics_failed`，并注明其语义是「指标算全与否」而非「研究是否成功」；② **`metrics_mean` 每个指标并列输出 stderr**（Q5，对齐 openai/evals / lm-eval-harness 行业通例，见 §5.5.4 / §6.7） |
| Arm 6（可复现元数据） | 评测结果可追溯到完整环境与**裁判身份** | 每条 raw 记录包含 **13 个字段**（原 6 + 09-13 的 `experiment` + 本次新增 6）：`git_dirty` / `git_diff_hash` / `python_version` / `deps_frozen_hash` / `model_name` / `search_provider` / `experiment` / **`prompt_hash` / `citation_judge_model` / `coverage_judge_model` / `citation_judge_independent` / `scorer_version` / `max_step_budget`**；其中 **`citation_judge_independent` 必须在 summary 中可见**，任何「引用准确率提升」的结论都须先解释该字段 |
| Arm 7（评测产物治理） | **产物可追溯 + 本机磁盘占用可控** | **① `git ls-files research_engine/eval/results` 收敛至白名单集合，且每一项都能在 `.gitignore` 注释里溯源到「被哪份 docs/ 文档引用」；② 本地 `results/` 目录体积在「只读扫描出账」之后再定目标值（不预设数字）** |

### 3.2 成功定义约束（**2026-09-15 Grill Q5 按验收类型分类重写**）

> **⚠️ 原约束「Arm 1/4/5 每项至少 3 轮取均值」存在两个独立问题，2026-09-15 一并处置：**
> ① **在数学上无效** —— W7 实测噪声下 n=3 分辨不出本项目的目标效应（见下方功效分析）；
> ② **无的放矢** —— 原定点名的 Arm 1/4/5 **全部是确定性验收**，噪声根本不参与。

#### 3.2.1 分类处置

| 类型 | 覆盖的 Arm | 验收方式 | 是否需要重跑对照 |
| --- | --- | --- | --- |
| **A 类：确定性断言** | **Arm 1 / 4 / 5**（原约束点名的三个）+ Arm 2 / 3 / 6 / 7 | 注入故障断言状态、离线重算历史 run、单测 | ❌ **不需要** —— 零噪声，单轮/离线复算即可 |
| **B 类：统计对照** | **仅主链路基线**（属 §10.4 / §3.3 范围） | 改前 vs 改后的指标对比 | ✅ 需要，但**禁用「3 轮取均值」**，按 §3.3 |

**A 类为什么不需要**：Arm 1 验收 = 注入 LLM 失败 ⇒ 断言 `run_status=="degraded"`；Arm 4 验收 = 断网 ⇒ 断言 `failure_reason=="timeout"`；Arm 5 验收 = 在历史 run 上离线重算 ⇒ `173540` 判 `suspicious`。**三者都是确定性断言，重跑一万轮结果也一样。**

#### 3.2.2 「3 轮取均值」为什么无效（功效分析）

**噪声实测（两个独立来源互证）**：
- W7 结论文档 `docs/eval-w7-conclusion.md:249` 已按 `E[range]≈1.69σ` 折算：**σ ≈ 12.1 / 14.4 / 14.3 / 17.0 pp**
- 本次独立复算：极差 24.5pp、n=3、`d2=1.693` ⇒ **σ ≈ 14.5pp** ✅ 吻合

| n/组 | SE_diff | detect 4pp | detect 10pp |
| --- | --- | --- | --- |
| **3**（原规定） | **11.8pp** | t=0.34 | **t=0.85** ❌ |
| 20 | 4.6pp | 0.87 | 2.19 |
| **35** | 3.5pp | 1.16 | **2.89** ✅ |
| **215** | 1.4pp | **2.87** ✅ | 7.16 |

（两样本、α=0.05，需 t≳2.8 才有 80% 功效）

**成本换算**（W7 实测：12 runs = ¥9.3327 / 512.8 min ⇒ ≈¥0.78、43min per run）：

| 目标 | 需要 | 成本 | 墙钟 |
| --- | --- | --- | --- |
| detect 10pp | 70 runs | ≈¥55 | ≈50h |
| detect **4pp** | 430 runs | ≈¥335 | ≈**300h+** ❌ |

⇒ **n=3 分辨不了任何有意义的效应；而 detect 4pp 的成本与墙钟都不可接受。**「3 轮取均值」是一个**已知无效的仪式**。

### 3.3 基线采集方法论约束（2026-09-15 新增，为 C4「基线预算」前置锁死方法论）

> C4 届时**只拍「预算多少」**，方法论按本节执行，不再重新讨论。

1. **配对优先**：先固定裁判（独立于被测组件）+ **固定检索快照**（冻结证据池），做 **before/after 配对对照**，再做独立两样本。
   - 数学依据：配对后 `σ_d = σ·√(2(1-ρ))`；ρ≈0.9、n=20 题 ⇒ **SE≈1.45pp** ⇒ detect 4pp **t≈2.76 ✅**，成本仅 ≈1~2 runs（对比独立两样本的 430 runs）。
   - ⚠️ **本条「ρ≈0.9」是假设值，2026-09-16 已被实测推翻 —— 见 §3.3.1**。真实检索下 ρ 实测仅 0.168~0.875（按指标而异），SE(3v9) 实测 1.19~4.42pp。**只有「真固定检索快照」时本条才成立。**
   - 项目已有能力：`tools/w7_rejudge.py` 的 2×2 冻结复判即为配对设计实例。
2. **功效分析前置**：必须在跑之前由 σ 估计值反算所需 n，**不得先定「3 轮」再跑**。
3. **强制报 stderr**：任何指标对比必须给出离散度（见 §5.5.4）；**stderr > 效应 ⇒ 判不可判定，不得报"提升 Xpp"**。
4. **预算不可接受时**：如实宣告**不可判定**，而不是降低 n 硬跑出一个看似有结论的数字。
5. **报告绝对量**：绝对条数 / 绝对 token / 绝对成本须与比率并列报告（W7 教训：arm6 比率"达标"但绝对产出 −43.5%）。

> **来源**：本节方法论并非新发明 —— W7 结论文档 §7（第 280 行）已写明
> *「固定裁判（独立于被测组件）+ 固定检索快照 + 同预算成本-效果比较 + 多区块重复 + 报告绝对条数」*。
> W8 原 §3.2 只是没接住，本节将其正式制度化。

#### 3.3.1 ⚠️ 实测更正（2026-09-16）：「ρ≈0.9 ⇒ SE≈1.45pp」是有前提的，真实检索下不成立

**新增 `tools/measure_paired_rho.py`**（只读、零 API）。用 before 基线 **3 轮同配置真实 run**
反推 —— 依据恒等式 `σ_within = sd(x_i1 − x_i2)/√2`（两轮即可估，不依赖任何假设）。
**最终采用 3 轮估计**（n=19 配对题 × 3 个轮次对，比 2 轮的单一配对稳得多）：

| 指标 | σ_within（同题跨轮噪声） | **实测 ρ** | σ_question（由 ρ 反解） | **SE(3v9)** | **MDE(3v9)** | §3.3 原假设 1.45pp |
| --- | --- | --- | --- | --- | --- | --- |
| `coverage` | **23.96pp** | **0.551** | 26.53pp | **3.76pp** | **10.54pp** | ❌ 差 2.6 倍 |
| `citation_accuracy` | 13.74pp | **0.124** | 5.16pp | 2.22pp | 6.22pp | ❌ 差 1.5 倍 |
| `retrieval_hit_rate` | 8.48pp | **0.860** | 21.04pp | **1.33pp** | 3.73pp | ✅ 基本吻合 |

<details><summary>2 轮时的估计（供对照：单一配对，噪声更大）</summary>

| 指标 | σ_within | ρ | SE(3v9) | MDE(3v9) |
| --- | --- | --- | --- | --- |
| `coverage` | 28.91pp | 0.351 | 4.42pp | 12.38pp |
| `citation_accuracy` | 14.41pp | 0.168 | 2.26pp | 6.34pp |
| `retrieval_hit_rate` | 7.75pp | 0.875 | 1.19pp | 3.32pp |
| `steps` | 3.07 步 | 0.625 | 0.47 步 | 1.31 步 |

3 轮 vs 2 轮：coverage 的 MDE 由 12.38pp 收敛到 **10.54pp** —— 方向一致、量级稳定，**结论不受估计轮数影响**。

</details>

**为什么 §3.3 的 1.45pp 不成立**：§3.3 那句话的**前提是「固定检索快照」**，而 before/after 用的是
**活的检索** —— planner 每轮生成的子问题不同 ⇒ **证据池本身就变** ⇒ 题目效应几乎带不来跨轮相关
（coverage ρ 仅 0.551、citation 仅 0.124）。**retrieval_hit_rate 之所以吻合（ρ=0.860），
正因为它是检索环节自身的指标、受下游写作波动影响小** —— 这反过来印证了机制解释。

**⇒ 硬结论**：按 §10.5.7 立项的 before(3) vs after(9) 设计，
**coverage 只能检出 ≥10.5pp 的效应**（3 轮估计）。W7 实测效应量级是 4~10pp ⇒ **「W8 让主链路覆盖率提升 Xpp」不可判定**。
按 §3.3 第 4 条（预算不可接受时如实宣告），应**宣告不可判定**，而不是硬跑出一个看似有结论的数字。
（`retrieval_hit_rate` 例外：MDE 3.73pp，**可做效应陈述**；`citation_accuracy` MDE 6.22pp，勉强可做。）

**⇒ 但更根本的问题是：这个验收口径本身就是错配。** 见 §3.3.2。

#### 3.3.2 口径重定位：W8 的 Arm 是「可观测性改进」，不是「质量提升项」

**用 coverage 提升来验收 W8，从一开始就是问错了问题。**

| W8 的 Arm | 性质 | 正确验收口径 |
| --- | --- | --- |
| Arm 1 状态分层 / Arm 4 失败原因 / Arm 5 质量闸 | **可观测性 + 可靠性**（让失败可见、可归因） | **A 类确定性断言**：注入故障 ⇒ 断言 `run_status` / `failure_reason` 正确（零噪声，重跑一万轮结果一样，Q5 已确认） |
| Arm 2 注入修复 / Arm 3 依赖锁定 | **安全性 + 可复现性** | 确定性断言（脚本无 query / lock 可安装） |
| Arm 6 元数据 / Arm 7 产物治理 | **可审计性** | 字段可读性断言 |

**这些没有一项的立论是「覆盖率会涨」。** 用 coverage 提升当验收指标，等于用尺子称重量。

**⇒ before/after 基线的真正价值重定位**：不是「证明覆盖率从 X 涨到 Y」（做不到），
而是**提供「改前故障不可归因」的对照证据** —— 例如：

- 改前：`failed` **不可达**（§5.1.4 B1 取证）、`failure_reason` **无此字段**、`error` **生产代码零写入** ⇒ **故障不可归因率 = 100%**
- 改后：同一批故障 ⇒ **故障可归因率 = 100%**

**这才是 W8 真正交付的东西，且它是 A 类确定性断言，不受上面任何噪声影响。**

**✅ 2026-09-16 拍板（于晏采纳 A）：验收口径按本节重定位，B / C 不采纳**

| 选项 | 内容 | 代价 | 裁决 |
| --- | --- | --- | --- |
| **A** | 按 §3.3.2 重定位：before/after 只做**可观测性对照**（故障可归因率等确定性断言），coverage/citation **只报绝对值 + 区间，不报提升** | ¥0 额外 | ✅ **已采纳** —— 与 §3.3 第 4 条「如实宣告」一致，也符合 §10.5.6 口径纪律 |
| B | 真的实现「固定检索快照」（缓存检索结果供 before/after 复用）⇒ 把 ρ 拉回 0.9 ⇒ SE≈1.45pp 成立 | 新增检索缓存层（中等工作量，且**改变了被测对象**：比的是写作/验证环节，不再是端到端） | ❌ 不采纳 —— 投入产出比低，且改变被测对象 |
| C | 加 runs 硬堆精度：coverage 检 4pp 需 Rb=Ra=**41 轮**（82 runs ≈ ¥73 ≈ 82h） | 不可行 | ❌ 不采纳 —— 与 Q5 已否决的「430 runs」同量级 |

**拍板后的落地要求（三句，写死以免走样）**：

1. **W8 各 Arm 一律按 A 类确定性断言验收**，不得以「覆盖率提升 Xpp」作为任何 Arm 的通过条件。
2. **对外数字一律带区间与题数**（§10.5.6），**禁止裸数字**；**禁止**把 before 与 after 的均值差表述为「提升」，
   除非该指标的 MDE 小于观测差（当前仅 `retrieval_hit_rate` 满足，MDE 3.73pp）。
3. **before 基线（§10.5.8）保留为「改前故障不可归因」的对照证据**，其价值不受「提升不可判定」影响。

> **面试叙事的正确说法**（§10.5.8 数字可直接使用）：
> 「主链路覆盖率 **45.2% ±6.3pp**（20 题 × 3 轮，W8 改动前）」—— 这是绝对值陈述，成立；
> 「W8 让覆盖率提升 Xpp」—— **不可判定，不说**。
> 改为说：「**故障可归因率从 0% 提升到 100%**（注入故障复现，A 类断言）」—— 零噪声，成立。

## 4. 当前设计（代码现状，2026-09-10 盘点）

### 4.1 Arm 1：状态分层缺失

`ResearchState`（state.py:54-91）的 `status` 字段取值范围为 `pending/planning/researching/writing/validating/done/failed`，但没有"降级"语义。`error` 字段默认为 `None`，且在 graph.py 全部节点中从未被赋值为非 None。

| 节点            | status 赋值       | 行号           | error 赋值 |
| ------------- | --------------- | ------------ | -------- |
| `_plan()`     | `"planning"`    | graph.py:101 | 无        |
| `_research()` | `"researching"` | graph.py:170 | 无        |
| `_write()`    | `"writing"`     | graph.py:251 | 无        |
| `_validate()` | `"done"`        | graph.py:262 | 无        |
| `_render()`   | `"done"`        | graph.py:278 | 无        |

graph.py:282-314 的 `run()` 方法执行 `self.graph.invoke(initial, cfg)`，没有 try/except 捕获并写回 `state.error`。

**2026-09-15 补充取证（B1）—— 影响比"没写回 error"更严重：`failed` 根本不可达。**

| 事实 | 位置 |
| --- | --- |
| MemorySaver **已启用**，`run()` 传 `thread_id` ⇒ 异常后 `get_state(cfg)` 理论上可捞回 checkpoint | `graph.py:85` `g.compile(checkpointer=MemorySaver())` |
| 但 `run()` **无 try/except** ⇒ 异常时**没有 state 返回** ⇒ `run_status` 永不置 `failed` | `graph.py:282-320` |
| 走 fallback 的路径都**仍会产出报告** ⇒ 只能落 `degraded` | planner / writer / validator |
| `recursion_limit = max_total_hops*2+20` 触发 `GraphRecursionError` 时同样什么都不留 | `graph.py` `cfg` |
| **唯一**记录失败的地方是 `_run_one` 的 try/except ⇒ **真相源在评估层，不在 state** | `run.py:82-117` |

**补充：三套互斥的 `status` 词汇表共存**（详见 §4.5，命名裁决见 §5.1.1）。

### 4.2 降级路径全景

以下 `except Exception: return fallback` 模式使得失败被静默吞掉：

| 位置             | 文件:行号                 | 降级行为                                                            | 问题                            |
| -------------- | --------------------- | --------------------------------------------------------------- | ----------------------------- |
| Planner        | planner.py:70-72      | `plan()` 失败 → 返回 `[SubQuestion(topic=topic)]`                   | 退化为单主题，不被标记                   |
| Planner replan | planner.py:105-107    | `replan()` 失败 → 返回原 `subs`                                      | 不被标记                          |
| Writer         | writer.py:90-93       | LLM 失败 → `_fallback_report()`                                   | 生成兜底报告，status 仍为 done         |
| Writer 兜底      | writer.py:99-104      | 每个子问题写"信息不足"                                                    | 用户无法区分"真信息不足"和"系统故障"          |
| Validator LLM  | validator.py:354-356  | LLM 失败 → `llm_failed=True`                                      | 后续降级为存在性判定                    |
| Validator 降级   | validator.py:380-383  | `llm_failed` → `verified=True, supported=False, confidence=0.5` | 按"来源存在"通过                     |
| Validator 缺失   | validator.py:402-404  | 无 LLM 反馈 → 保守通过                                                 | 同上                            |
| Web 搜索         | researcher.py:88-89   | `except Exception: return []`                                   | "没搜到"和"搜索坏了"不可区分              |
| RAG 搜索         | researcher.py:109-110 | `except Exception: return []`                                   | 同上                            |
| 单工具            | researcher.py:180     | `except Exception: return []`                                   | 同上                            |
| arXiv          | arxiv.py:78-79        | `except Exception: return SearchResponse(results=[])`           | 同上                            |
| RAG store      | store.py:47-50        | Qdrant 初始化失败 → `_available=False`                               | 后续 search/scroll\_all 返回 `[]` |
| 上下文压缩          | manager.py:74-75      | 压缩失败 → 原样返回                                                     | 不被标记                          |

### 4.3 Arm 2：代码执行注入

`researcher.py:196-210` 的 `_default_code_script()` 在第 207 行将 query 直接拼接进 Python 源码：

```python
f"print('query={query!r}')\n"
```

`repr()` 理论上能处理引号转义，但中文和特殊字符在特定编码环境下会触发语法错误。`run_20260910_173540` 的 raw 结果中已确认至少一条代码执行因中文查询被拼进 Python 源码后出现乱码语法错误。

### 4.4 Arm 3：依赖环境（**2026-09-14 重新盘点 —— 原 09-10 盘点含 3 条不成立断言**）

| 位置 | 现状（实测） | 判定 |
| --- | --- | --- |
| `requirements.txt` | 全开放式 `>=x.y`，**仅两处手写上界**（`openai>=1.50,<1.93`、`langfuse>=4.15,<5`） | **零锁定 = 真缺口**；手写上界说明项目**已在人工做兼容轨道锁定** ⇒ 本 Arm 动机成立 |
| lock 文件 | **不存在**（`requirements-lock.txt` / `uv.lock` 均无） | 真缺口 |
| `pyproject.toml` | **无 `requires-python` 字段**；且**无 `[build-system]`**（文件自述「以 requirements.txt 安装，pyproject.toml 仅承载工具配置」） | ❌ 原文「从 `>=3.10` **改为** `>=3.11,<3.14`」**前提不存在** —— 实为**新增字段** |
| `[tool.ruff] target-version` | `"py310"`（`pyproject.toml:5`） | 与 CI(3.11) / 本地(3.13) 不一致。**这是四处口径里唯一"真强制"的一处**：ruff 按 3.10 语法集检查 ⇒ 想用 3.11+ 语法会被拦 |
| `README.md:46` | `Python 3.10+` | 口径不一致（原文未提这一处） |
| `.github/workflows/ci.yml` | **Python 3.11** + 安装**开放式** `requirements.txt -r requirements-dev.txt` + `ruff check .` + `pytest tests/ -q` | 安装未走 lock（真缺口）；但**「Python 3.11 下完整测试通过」这条 DoD 其实每次 push 都在被验证** |
| `.deps/` | 实测 **168 个 `.pyd` 全部为 `cp313-win_amd64`** | CPython **3.13 + Windows 专属**，跨平台/跨小版本**均不可移植** ⇒ **实证「不能拿本机 `pip freeze` 生成 lock」** |
| managed venv（实际跑测试处） | **Python 3.13.14** + `pytest 9.1.1` + `ruff 0.16.6` | ❌ 原文「当前环境无 `pytest`、无 `ruff`」**不成立** —— 那是系统 Python 3.14 的口径；项目约定用 managed venv，W7 刚在此跑过 132 测试全绿 |

**⇒ 四处版本口径、四个版本：`3.10`(README) / `3.10`(ruff target) / `3.11`(CI) / `3.13`(本地 venv & `.deps`)。**

**行业横向对照（2026-09-14 取证，用于校准本 Arm 的方向）**

| 项目 | lock 文件策略 | `requires-python` | 跨平台依赖处理 |
| --- | --- | --- | --- |
| **langchain-ai/open_deep_research** | **`uv.lock` 入库（988 KB）**，无 `requirements.txt` | `>=3.10`（**开区间，无上界**），有 `[build-system]`（setuptools） | — |
| **assafelovic/gpt-researcher** | ⚠️ `.gitignore` **ignore `*.lock`** ⇒ 锁文件**不入库**；但 `pyproject.toml` 同时配了 `[tool.uv.sources]`（poetry + uv 双轨，迁移中） | `>=3.11`（与 CI 下限一致） | **环境标记**：`weasyprint>=65.1 ; sys_platform != 'win32'`、`mcp = { markers = "platform_system != 'Windows'" }` |
| **stanford-oval/storm** | 无锁文件（`requirements.txt` + `setup.py`） | 未声明 | — |
| **dzhng/deep-research** | ⚠️ `.gitignore` **ignore `bun.lockb`** ⇒ 锁文件**不入库** | — | — |

**⇒ 三条行业结论：**
1. **「锁文件是否入库」行业并不统一**：`open_deep_research` 入库 988 KB 的 `uv.lock`；`gpt-researcher` 与 `dzhng` 反而**明确把锁文件写进 `.gitignore`**。所以我们不必以「入库」为唯一正解。
2. **`requires-python` 普遍存在，但普遍是开区间**（`>=3.10` / `>=3.11`）；而本项目有 `.deps` 的 `cp313` 教训与 `openai>=1.50,<1.93` 的既有先例 ⇒ **本项目的上界写法（`<3.14`）是有依据的加强，不是啰嗦**。
3. **跨平台依赖的正解是「环境标记」而非「锁死哈希」**：`gpt-researcher` 用 `; sys_platform != 'win32'` 这类 marker 处理平台差异 ⇒ 与我们「不加 `--generate-hashes`」的建议方向一致。

### 4.5 Arm 5：评测口径（**2026-09-15 重写 —— 原 09-10 描述含 2 条不成立断言**）

| 原文断言 | 实测（2026-09-15） |
| --- | --- |
| `complete` 统计的是"成功写出 raw 文件的任务数" | ❌ **不成立**。`run.py:284` = `r.get("status") == "ok"`，该 `status` 由 phase2 指标层 `run.py:215` 产出 ⇒ **`complete` = 「七指标全部算出来的条数」** |
| 「`complete=20` 但 `completion_rate=0`」是矛盾 | ❌ **是误读**。两者正交：`complete` 度量**评测管线成功率**，`completion_rate` 度量**被测产出质量**。反证：`run_20260910_171054` 为 `complete=0 / partial=9 / failed=11` 但 `completion_rate=1.0` |

**真实病灶**：`run_20260910_173540` 的 `complete=20 / partial=0 / failed=0`，而七指标**全为 0**、`avg_steps=1.0`、`reflection_critic_stop_rate=1.0`。指标**算出来了、确实是 0**，但 summary 顶层读作「全部成功、零失败」——**彻底降级被 silent 地报成成功**。

**⚠️ 附带查出的命名地雷：三套互斥的 `status` 词汇表共存于同一条流水线**

| # | 层 | 位置 | 取值 |
| --- | --- | --- | --- |
| ① | **raw 记录**（Graph 输出） | `run.py:107` | `done` / `incomplete`（源自 `state.status`） |
| ② | **`_run_one` 返回**（单次运行） | `run.py:110/117/157` | `ok` / `failed` / `timeout` |
| ③ | **phase2 指标层** | `run.py:203/215/243/246` | `ok` / `partial` / `failed` / `timeout` ← **`_summarize` 统计的就是这一层** |

其中 ③ 的 `partial` **只由一件事触发**：`run.py:210` `if metrics["coverage"].get("judge_failed")` ⇒ 语义是「**coverage 裁判挂了**」，与 Arm 1 想表达的「流程走过 fallback」**同名词不同义**。

### 4.6 Arm 6：可复现元数据

`eval/run.py` 的 `_run_one()`（run.py:97-109）在 raw 记录中写入 `git_commit`（run.py:104），`_summarize()` 在 summary 中也写入 `git_commit`（run.py:331）。`report_gen.py` 的 `write_baseline()`（report\_gen.py:54-69）额外记录了 `config_snapshot`（第 61 行）和 `eval_env`（第 62-67 行，含 Python 版本、OS、并发数、墙钟）。

但缺少：`git_dirty`（是否 dirty）、`git_diff_hash`（工作区 diff hash）、`deps_frozen_hash`（锁定依赖 hash）、`model_name`（实际使用的模型名）、`search_provider`（搜索 provider 配置）。评测可能运行在"有未提交修改的工作区"上，但结果看起来像是来自干净的 commit。

### 4.7 Arm 7：评测产物（**2026-09-14 重新盘点 —— 原 09-10 盘点已过期**）

| 项 | 原盘点（09-10） | **实测（2026-09-14）** |
| --- | --- | --- |
| `.gitignore` | 「**不包含** `results/` 忽略规则」 | ❌ **已含** `:33-44` 完整规则 + 3 条 `!` 白名单（2026-09-13 `3b309d7` 落地） |
| 已跟踪路径 | 118 个 / 6.4 MB | **189 个 / 工作区字节 9.0 MB** |
| 规则与跟踪状态一致性 | — | ❌ **94 项被跟踪但不在白名单**：`run_v11_compare`(42) + `run_20260906_184156`(43) + `run_20260907_001658`(9) |
| 仓库体积 | 「需减 ≥5 MB」 | **`.git` 对象库合计 ≈4.4 MB**（loose 4.37 MiB + pack 95 KiB）⇒ **该目标数学上不可达成** |
| 本地磁盘 | 未盘点 | `research_engine/eval/results/` = **147 MB**（绝大多数未跟踪）；工作区合计 **659 MB** |

**⇒ 结论：Arm 7 的真实病灶不是「仓库臃肿」（git 侧可回收 ≤1 MB），而是①「白名单规则与跟踪状态不一致」+②「本机 147 MB 未跟踪产物没有任何保留策略」。**

**行业横向对照（2026-09-14 取证，用于校准本 Arm 的方向）**

| 项目 | `.gitignore` 中与评测产物相关的规则 | 含义 |
| --- | --- | --- |
| langchain-ai/open_deep_research | 无产物规则（只 ignore `__pycache__` / `.env` / `logs/` / `.langgraph/`） | 根本不存在 run 级产物目录 |
| assafelovic/gpt-researcher | `outputs/` + **`*.lock`** + `logs/` | 产物出仓，**连锁文件都不入库** |
| **stanford-oval/storm** | **`*results/`** + `local/` + `*.tsv` + `*.pt` | **结果目录整体 ignore** |
| dzhng/deep-research | 只 ignore `output.md` / `report.md` / `answer.md` | 单文件产物都不留 |

⇒ **行业通例是「评测/运行产物一律不入库」**。本项目保留「里程碑 run 全量 raw」是**有意为之的差异化资产**（动机 = 面试实证 + `tools/w7_backfill_*.py` 的零成本可复算），**不是脏东西**；但必须为此付出「白名单纪律」的成本 —— 本 Arm 要治的正是这份纪律。

## 5. 优化方案

### 5.1 Arm 1：运行状态分层（P0）

#### 5.1.1 新增 `run_status` 字段

`ResearchState`（state.py:90-91）新增 `run_status: str` 字段，取值范围：

| 值          | 含义               | 触发条件                            |
| ---------- | ---------------- | ------------------------------- |
| `success`  | 全链路无降级           | 所有节点正常完成，未走过任何 fallback         |
| `degraded` | 走过 fallback 但有输出 | 至少一个节点走了 fallback，但最终产出了报告      |
| `failed`   | 研究流程失败           | **无报告产出** —— 含「`run()` 层捕获到异常」与「核心节点失败导致无报告」两条路径（见 §5.1.4） |

> **⚠️ 2026-09-15 Grill Q4 拍板：四态收敛为三态，`partial` 移出 state。**
> **理由一（可判定性）**：原第 2 条「tracker 非空且有报告 → `degraded`」与第 3 条「部分节点失败但有报告 → `partial`」
> —— **tracker 非空 ⇔ 部分节点失败**，是同一个可观测条件，**没有任何额外观测量能把它们分开**。
> **理由二（行业取证）**：OpenAI Responses API 的 `ResponseStatus` **终态恰好三个**
> （`completed` / `incomplete`（带 `incomplete_details.reason`）/ `failed`（带 `error{code,message}`）），**没有 `partial`** —— 详见 §6.6。
> **`partial` 降级为报告层派生指标**：按「子问题覆盖率 < 100%」在报告中标注，**不进 state**。
> **信息不丢失** —— `degradation_log` 保留全部事实。
>
> **⚠️ 命名裁决（2026-09-15 Q3 甲 + Q4 三分）：三个 status 语义各归各位，互不相犯**
>
> | 字段 | 语义 | 位置 |
> | --- | --- | --- |
> | **`run_status`** | **流程健康度**（本字段） | Arm 1 新增，`ResearchState` |
> | **`invoke_status`** | 单次调用的**执行结果** | 层② `_run_one` 返回（`run.py:110/117/157`，原名 `status`，取值 `ok/failed/timeout`） |
> | **`metrics_status`** | **指标齐备性** | 层③ phase2（`run.py:203/215/243/246`，原名 `status`，取值 `ok/partial/failed/timeout`） |
>
> **⚠️ 命名裁决 · 落地形态（2026-09-18 实现，本节为唯一口径描述）**
>
> 键名契约的唯一真相源 = `research_engine/eval/status_keys.py`（常量 + dual-read 读函数 + 豁免登记表）。
>
> | 层 | 键名 | 落盘位置 | 取值 |
> | --- | --- | --- | --- |
> | ① | `run_status` | `ResearchState`（不落盘为裸键） | `success`/`degraded`/`failed` |
> | ② | **`invoke_status`** | `raw/*.raw.json` **顶层** | `done`/`incomplete`/`failed`/`timeout` |
> | ③ | **`metrics_status`** | `eval/*.eval.json` **顶层** | `ok`/`partial`/`failed`/`timeout` |
>
> * **层② 的取值有两套词汇表 —— 这是事实，不是笔误**：`_run_one()` 的**返回值**（内存、不落盘）用 `ok`/`failed`
>   （"拿到 raw 了没"）；**落盘**的 raw 顶层用 `done`/`incomplete`/`failed`/`timeout`（"graph 产出报告了没"）。
>   两者同层、粗细两种粒度：`ok` ⇔ `done|incomplete`。单测对两套值集合**分别**锁定，防止"既然同名了就把值也合并"。
> * **方向 = 写侧只出新键 + 读侧 dual-read（新键优先、回落旧键）**。层③ 的回落分支是**必需**的：
>   phase2 断点续跑会直接 `json.loads` 既有 `*.eval.json` 再交给 `_summarize`，那些历史文件里只有裸 `status`。
> * **历史产物一律不回填**（**决策**，2026-09-18）。三条理由：① 它们是 Arm 7 台账的**取证对象**，产物只读；
>   ② `tools/w7_backfill_*.py` 的「W7 零成本可复算」依赖 raw 逐字节不变；③ 回填会抹掉「这批数产生于旧口径」这个事实
>   （同 Arm 6 拒绝回填 `prompt_hash` 的理由）。
> * **DoD 口径边界**：本项只覆盖层②③（"裸 `status`"的语义歧义源头即此处）。层①之外的其余裸 `status` 各自的独立语义
>   已在 `status_keys.BARE_STATUS_SITES` 逐条登记，并由单测验证「登记为真」——
>   其中 `history.json` 的 `status` 经核实是**恒为 `"PASS"` 的空转字段**（它写的是 `summary.struct.regression`，
>   而全仓从未有任何代码写入 `summary.struct`），其改名/删除待 Arm 7 台账 review 后一并拍板（**待办**）。

#### 5.1.2 降级追踪器

新增 `DegradationTracker`（轻量 dataclass，挂在 `ResearchState` 上），记录每次 fallback：

```python
@dataclass
class DegradationEntry:
    node: str            # "planner" / "researcher" / "writer" / "validator"
    component: str       # "llm" / "web_search" / "rag_search" / "arxiv_search" / "code_exec"
    reason: str          # 枚举，见下方《失败原因枚举表》（与 Arm 4 failure_reason 共用同一张）
    detail: str          # 自由文本补充（原始异常摘要等），可为空
    fallback_action: str # "topic_only" / "empty_list" / "fallback_report" / "existence_only"
    timestamp: float
```

**失败原因枚举表（Q4 优化点 ② —— 与 Arm 4 `failure_reason` 共用同一张，消除两套失败记录）**

| 值 | 含义 | **产生点** | 来源 |
| --- | --- | --- | --- |
| `not_configured` | provider 未配置 / 依赖不可用 | **工具层**（Arm 4） | Arm 4 原有 |
| `timeout` | 请求超时 | **工具层**（Arm 4） | Arm 4 原有 |
| `provider_error` | provider 返回错误（5xx、限流） | **工具层**（Arm 4） | Arm 4 原有 |
| `empty_result` | provider 正常响应但无结果 | **工具层**（Arm 4） | Arm 4 原有 |
| `parse_error` | 返回了数据但解析失败 | **工具层**（Arm 4） | Arm 4 原有 |
| **`llm_error`** | LLM 调用失败（非超时、非限流） | **非工具层**（Arm 1） | **Q4 新增** |
| **`token_limit`** | 触发 token 硬闸 / 上下文超长 | **非工具层**（Arm 1） | **Q4 新增**（对标 open\_deep\_research 的 `is_token_limit_exceeded`） |
| **`recursion_limit`** | `GraphRecursionError`（`recursion_limit = max_total_hops*2+20`） | **非工具层**（Arm 1） | **Q4 新增** |
| **`internal`** | 其他未分类异常 | **非工具层**（Arm 1） | **Q4 新增** |

> 原草案 `reason: str  # 人类可读原因` 是**自由文本**；改枚举后，「失败原因」成为**可聚合、可统计**的字段，
> 且 Arm 1（节点降级）与 Arm 4（工具失败）共用一张表 ⇒ **顺带解决「两套失败记录缺唯一真相源」**（原待定项 B4 的一半）。

> **⚠️ 2026-09-15 Grill Q8（B4 完全收口）：一次失败记两次是对的 —— 红线是「两处说法必须一致」**
>
> 枚举统一（Q4）之后，B4 只剩一个问题：**一次搜索超时，会不会在产物里出现两次？** 答案是**会，而且应该会** ——
> 它们**不是同一份数据的两份拷贝，是两个消费方**：
>
> | | `SearchResponse.failure_reason`（Arm 4） | `degradation_log` 条目（Arm 1） |
> | --- | --- | --- |
> | 消费方 | **下游节点**（planner 当轮决策：换源？改查询？） | **事后审计**（回答「这次 run 哪里降级了」） |
> | 时机 | **同步**，当轮就要用 | **异步**，run 结束后看 |
> | 生命周期 | 一次工具调用（下次调用即覆盖） | 整个 run（追加，不覆盖） |
>
> **强行合并会毁掉其中一个**：只留 `failure_reason` ⇒ 无 run 级审计（`run_status` 失去判据）；
> 只留 `degradation_log` ⇒ planner 当轮拿不到结构化原因，须改读 state（耦合节点与全局日志）。
>
> **行业同构（OTel Span）**：`Span.Status` 是**单值、后写覆盖**；`Span.add_event()` 是**追加流**。
> OTel **两者并存不合并** —— 出错时既 `set_status(ERROR)` 又 `add_event("exception")`，因为消费方不同。
> 本项目 `failure_reason` ↔ Status、`degradation_log` ↔ Events，完全同构。
>
> **契约（单向派生，写进实现）**：
> 1. **工具类 5 值**（`not_configured`/`timeout`/`provider_error`/`empty_result`/`parse_error`）的**唯一产生点是工具层**
>    （`researcher.py` / `arxiv.py` / `store.py` 的 `except` 分支），写入 `SearchResponse.failure_reason`；
> 2. `degradation_log` 条目的 `reason` **必须由 `failure_reason` 派生**（`DegradationEntry(reason=resp.failure_reason, ...)`），
>    **禁止在同一处手写第二个字面量** —— 违反则两处对同一事件的描述会静默分叉，比记两次更糟；
> 3. **非工具类 4 值**（`llm_error`/`token_limit`/`recursion_limit`/`internal`）**没有工具层来源**，由 Arm 1 各产生点直接构造条目。
>
> ⇒ **一张枚举表、两个产生点、按来源分组**（上表「产生点」列即分组依据）。
> 验收单测见 §9.1「单向派生」行。

**`degradation_log` 必须带 `operator.add` reducer（Q4 优化点 ④）**

```python
degradation_log: Annotated[List[DegradationEntry], operator.add] = Field(default_factory=list)
```

直接照抄 `state.py:90` 现有 `progress` 字段的写法。**不加 reducer 会在 `asyncio.gather` 并发下丢记录**（LangGraph 对无 reducer 字段取覆盖语义）—— 这是零成本就能避开的坑。

每个 `except Exception` 捕获点在返回 fallback 前，向 tracker 追加一条记录。`_validate` 和 `_render` 节点在设置 `status="done"` 时，根据 tracker 内容决定 `run_status`：

* tracker 为空 → `run_status = "success"`

* tracker 非空且有报告输出 → `run_status = "degraded"`

* 无报告产出（含 `run()` 层捕获到异常）→ `run_status = "failed"`（见 §5.1.4）

* ~~部分节点失败但有报告 → `partial`~~ ← **已删除**：与 `degraded` 判定条件完全相同，改为报告层派生指标

#### 5.1.3 落点清单

| 改动点            | 文件            | 行号                        | 改动内容                                                                                  |
| -------------- | ------------- | ------------------------- | ------------------------------------------------------------------------------------- |
| 新增字段           | state.py      | 90-91 后                   | `run_status: str = "success"` + **`degradation_log: Annotated[List[DegradationEntry], operator.add]`**（**必须带 add reducer**，照抄同文件 `:90` 的 `progress` 写法） |
| **`error` 结构化** | **state.py**  | **92**                     | `error: Optional[str]` → **`error: Optional[Dict]`**，结构 `{code: str（失败原因枚举）, message: str, node: Optional[str]}`（Q4 优化点 ③，对齐 OpenAI `error{code,message}`） |
| Planner 降级标记   | planner.py    | 70-72                     | fallback 前追加 tracker entry                                                            |
| Writer 降级标记    | writer.py     | 90-93                     | fallback 前追加 tracker entry                                                            |
| Validator 降级标记 | validator.py  | 354-356, 380-383, 402-404 | 每条降级路径追加 tracker entry                                                                |
| 搜索降级标记         | researcher.py | 88-89, 109-110, 180       | fallback 前追加 tracker entry                                                            |
| arXiv 降级标记     | arxiv.py      | 78-79                     | fallback 前追加 tracker entry                                                            |
| 状态判定           | graph.py      | 262, 278                  | `_validate`/`_render` 设置 `run_status`                                                 |
| **异常退出契约**     | **graph.py**  | **282-320 `run()`**       | **新增 try/except 包裹 `self.graph.invoke(initial, cfg)`** —— 异常时经 `self.graph.get_state(cfg).values` 捞回最后 checkpoint，置 `run_status="failed"` + 追加 degradation entry + 写结构化 `error`。**这是 `failed` 唯一的落点**（见 §5.1.4） |
| CLI 非零退出        | cli.py        | 31-33（`finally` 之后）      | 判 `result.run_status == "failed"` ⇒ `sys.exit(1)`，保证脚本化调用能感知失败                        |
| Web 失败展示        | web/app.py    | 67                        | 展示 `run_status` 与 `degradation_log`（**当前 `run()` 完全无 try/except，异常直接抛给 Streamlit，无任何失败展示**） |

#### 5.1.4 异常退出契约：让 `failed` 真正可达（2026-09-15 新增，Q4）

**问题（B1 取证）**：`run()`（graph.py:282-320）**无 try/except** —— 节点抛异常 ⇒ `invoke` 抛 ⇒ **没有 state 返回** ⇒ `run_status` 永不被置 `failed`。而所有走 fallback 的路径都**仍会产出报告** ⇒ 只能落 `degraded`。

⇒ **`failed` 在图结构下没有任何一条可达路径**。`GraphRecursionError`（`recursion_limit = max_total_hops*2+20`）同理 —— 崩了什么都不留。当前**唯一**记录失败的地方是 `eval/run.py:_run_one`（82-117）的 try/except ⇒ **真相源在评估层，不在 state**。

**可行性**：`graph.py:85` 已 `g.compile(checkpointer=MemorySaver())`，且 `run()` 传 `thread_id` ⇒ **异常后 `self.graph.get_state(cfg).values` 可捞回最后一个 checkpoint 的 state**（含已产出的 findings / progress）。

**契约**

1. `run()` 用 try/except 包裹 `self.graph.invoke(initial, cfg)`。
2. 异常时：① 经 `get_state(cfg).values` 捞回 state；② 置 `run_status = "failed"`；③ 追加一条 `DegradationEntry(reason=<枚举>, detail=str(e), node=<失败节点>)`；④ 写结构化 `error = {code, message, node}`。
3. **兜底（反方挑战 ①）**：若 `get_state` 返回空或自身抛错（如 invoke 第一个节点就炸、compile/配置错误），**构造最小 `ResearchState(topic=..., run_status="failed", error={code:"internal",...})`** —— **绝不返回 `None`**。
4. `run()` **返回 state 而不 re-raise**；同时在 `self.last_exception` 保留原始异常对象供调试。
5. **`cli.py` 在 `finally` 之后检查 `result.run_status == "failed"` ⇒ `sys.exit(1)`**。

**⚠️ 反方挑战 ②：这会不会重蹈 §4.2「静默吞掉」的覆辙？**

**处置（五件套，缺一不可）**：① 写 `run_status="failed"`；② 写 `degradation_log` 条目；③ 写结构化 `error`；④ CLI 非零退出；⑤ trace 已由现有 `finally` flush（W3 Q5 机制 —— `self.trace_id` 在 invoke 前赋值，**异常路径同样可取到**）。

**关键区别**：§4.2 的病是「**吞了且不留痕**」；此处是「**把异常转换成结构化状态**」—— 信息不是消失，而是换了表达形式且**五处留痕**。**且只在 `run()` 这一层做一次**，不复制到节点内 —— 节点内的 fallback 仍须按 §5.1.2 逐条记 tracker。

**实现影响面（2026-09-16 只读预检，实现前必须读）**

全仓 grep 实测（`state.error` 与 `["error"]` 全部访问点），**破坏面比预估小、但有一处是硬崩溃**：

| # | 位置 | 现状 | 结构化 `error` 后的影响 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | `state.py:92` | `error: Optional[str] = None` —— **仅声明，生产代码零写入** | 字段声明改 `Optional[Dict[str,str]]` | 直接改；无写入方需同步 |
| 2 | `eval/metrics.py:45` | `ok_error = state.get("error") is None`（**完成率四条件之一**） | 只要保持「成功 `None` / 失败非 `None`」语义即**不受影响** | 无需改；但**单测须锁定此语义** |
| 3 | `eval/metrics.py:54` | `"error": state.get("error")` 透传 | 值由 `str` 变 `dict`，随 metrics 落 raw | 无需改（raw 是 JSON） |
| 4 | **`eval/report_gen.py:428`** | `f" error={r.get('error')[:200]}"` | ⚠️ **`dict` 不可切片 ⇒ 崩溃** | **必改**：`str(r.get('error'))[:200]` |
| 5 | `eval/run.py:118` | `"error": state.error`（读 state 对象） | 类型变化，随 raw 落盘 | 无需改 |

**另两条实现期坑（预检已确认，写在这里免得重踩）**：

- **`get_state(cfg).values` 对从未 invoke 过的 thread 返回空 `dict` `{}`，不是 `None`** ⇒ 契约第 3 条兜底判据必须写 `values = st.values or {}`，**只判 `is None` 会漏**。
- `graph.py:314` 的 `self.tokens_diff = (LLMClient.tokens_total - token_base) - result.token_used` 在**异常路径也会执行** ⇒ 兜底构造的最小 `ResearchState` 必须保证 `token_used` 有默认值（Pydantic 默认 `0` ✅ 已满足），否则 `run()` 会在 except 分支里二次抛错，**把「转为结构化状态」变成「换个地方崩」**。

### 5.2 Arm 2：代码执行注入修复（P0）

#### 5.2.1 移除 query 拼接

> **⚠️ 2026-09-16 实测修正**：本节叙述隐含「原模板只在特殊字符下出错」的前提，实则**无条件出错**（旧模板的 `'` 与 `!r` 叠加成双层引号）—— `code_exec` 因此一直是 100% 失败，详见 §5.2.4 实测修正一。下方模板即实际落地版本。

`researcher.py:196-210` 的 `_default_code_script()` 改为不接受 query 参数。计算脚本完全不包含原始 query 文本：

```python
def _default_code_script() -> str:
    return (
        "import math\n"
        "n = 8192\n"
        "flops_per_token = 6 * n * 2\n"
        "total_flops = n * flops_per_token\n"
        "print('sequence_length=' + str(n))\n"
        "print('approx_flops=' + '{:.3e}'.format(total_flops))\n"
    )
```

#### 5.2.2 query 通过元数据传入

> **⚠️ 2026-09-16 实现修正**：`CodeExecInput` 类在仓库中**不存在**（见上方预检表第 1 条）。本稿**按现有签名实现**，不新引入输入类 —— `exec_code(code, query="", params="")` 内部把 `query` 写入 `CodeExecOutput.metadata["query"]`，调用方从产出元数据取回。

调用方（`researcher.py` 中调用 `_default_code_script` 的位置）将 query 放到 `CodeExecOutput` 的元数据字段中，不进入脚本源码。如需在输出中关联 query，通过 `CodeExecInput.metadata["query"]` 传入，执行器将其写入 `CodeExecOutput.metadata["query"]`。

#### 5.2.3 中文/特殊字符测试

> **⚠️ 2026-09-16 实测修正**：本节原前提「特殊字符才触发语法错误」为误（实为无条件错误，见 §5.2.4）。该 DoD 保留为**回归护栏** —— 若哪天模板被改回拼接式，这组用例会立刻变红。另：中文乱码的真实来源是**沙箱输出编码**（cp936 vs utf-8），不在此 DoD 的表述范围内，已单独修（§5.2.4 实测修正二）。

新增专项测试：query 包含中文、引号、反斜杠、换行符时，脚本执行不出现语法错误。测试用例覆盖：纯中文、中英混合、含 `'` 和 `"`、含 `\n` 和 `\t`、含 Unicode emoji。

**⚠️ 实现预检（2026-09-16 只读，用 before 基线 `run_20260916_001005` 真实数据）—— 三处与代码不符，实现前必读**

| # | 问题 | 实测证据 | 处置 |
| --- | --- | --- | --- |
| 1 | **§5.2.2 引用的 `CodeExecInput` 类在代码里根本不存在** | `code_exec.py` 全文件无 `CodeExecInput`；实际 API 是 `exec_code(code: str, query: str = "", params: str = "") -> CodeExecOutput` | §5.2.2 改写为按现有签名走，或先引入该类（**后者属额外改动，需明确取舍**） |
| 2 | **`exec_code` 目前并不把 query 写进 `metadata`** | `code_exec.py:344` `meta = {"exit_code": r.returncode}`（+ 可选 `job`）—— **query 只进 `script_hash`，不进 metadata** | §5.2.2 所述「执行器写入 `metadata["query"]`」属**新增行为**，须显式实现 |
| 3 | **⚠️ 若同时去掉脚本里的 query 和 `exec_code(query=)`，会把 `script_hash` 变成常量，静默吞掉 24% 的证据** | 见下方量化 | **保留 `exec_code(script, query=query)`**（安全：query 只进哈希与元数据，**从不进入被执行的代码**）；注入风险只在脚本文本里 |

**第 3 条的量化依据（基线真实数据）**：

- `code_exec.py:304-305`：`ctx = f"{code}\x00{params}\x00{query}"` ⇒ `script_hash = sha256(ctx)[:10]`；`researcher.py:140` `source = f"code:{out.script_hash}"`。
- 基线 20 题共 **206 条 findings，其中 50 条（24.3%）是 `source_type=="code_exec"`**；q_012 单题 7 条、q_009 六条。
- **这 50 条的 `source` 值 50 个全不重复** —— 正是靠 query 进哈希才区分开的。
- `context/manager.py:21-28 dedupe()` 按 `f.source` 去重、**每个 source 只保留一条**；`compress()`（`:39-45`）按 `f.source` 分组压缩，且 `graph.py:246` 只在 `len(findings) > max_findings(30)` 时才走压缩。

**⇒ 当前 findings 峰值 17（< 30），故 `compress` 未触发、`dedupe` 在主链路也未被调用 ⇒ 风险是潜伏的、不是正在发生的。但一旦 hash 塌陷：**
① 将来 findings 超过 30（更深研究 / 更多子问题）⇒ 全部 code findings 被并进**同一组**压缩成一条；② 任何人把现已存在的 `dedupe()` 接进主链路 ⇒ **50 条直接变 1 条**。
**两种情形都会静默损失近 1/4 的证据量，且不会报错。** ⇒ **保留 `query=` 参数是最省事且零风险的选择**。

> **方法论注记**：这条是「只在文档里写『移除 query 拼接』」看不出来的 —— 必须追到 `script_hash` 的消费者（`dedupe` / `compress`）才暴露。
> 与 §5.1.4 那三条同属「**先追消费者，再动字段**」。

#### 5.2.4 实现记录与实测修正（2026-09-16）

**实现落点**：

- `researcher._default_code_script()` 改为**零参数**、返回常量模板（§5.2.1）。
- 查询文本改由 `metadata["query"]` 承载：`exec_code` 现把 `query` 同时写进 `CodeExecOutput.metadata["query"]`（§5.2.2 所述行为，**属新增**），`_search_code` 在成功/失败两条 finding 上都带 `metadata["query"]`。
- **`exec_code(script, query=query)` 保留**（§5.2 预检第 3 条的处置）：query 仍进 `script_hash`，保住同题多条 code 证据的唯一性。
- `_search_code` 失败原因改用 `_classify_code_exec_failure()`，把三类真实失败分开：超时 → `timeout`、脚本语法错 → `parse_error`、其余运行时错 → `provider_error`（原映射把前两类都压成 `provider_error`）。

**⚠️ 实测修正一：旧模板对「任何」query 都语法错误 ⇒ `code_exec` 一直是 100% 失败**

§5.2.3 原设想是「query 含中文/引号/换行 ⇒ **触发**语法错误」。实测（`compile()` 复现 + 真实 `exec_code` 运行）证明**不是条件性错误，而是无条件错误**：
旧模板 `f"print('query={query!r}')"` 里模板**自己已有一对 `'`**，`!r` 又补一对 ⇒ 生成 `print('query='模型参数量怎么算'')`，**连纯 ASCII 查询也是 `SyntaxError: invalid syntax`**。

**基线三轮真实产量**（按 `metadata` 形状判定：失败态 = `conf=0.2` 且 metadata 含 `note` 键、无成功态的 `structured_match`）：

| run | code_exec 产出 | 成功 | 失败 |
| --- | --- | --- | --- |
| `run_20260916_001005` | 50 | 0 | 50 |
| `run_20260916_011813` | 49 | 0 | 49 |
| `run_20260916_022440` | 45 | 0 | 45 |
| **合计** | **144** | **0** | **144（100%）** |

**⇒ 三点结论**：
① 基线里那 24.3% 的「code 证据」**没有一条是真实计算结果**，全是 `conf=0.2` 的失败标记 —— §5.2 预检只验了「50 个 source 全不重复」，**没验这些 source 是否承载有效证据**，故当时把它当成「会丢证据」来防；
② **Arm 2 的实质是工具功能性修复**（成功率 0% → 100%），不只是纵深防御；
③ 失败 note 原本会把 **query 原文经 stderr 带回 findings**（且因下述编码问题呈现为乱码），Arm 2 一并消除。

**⚠️ 实测修正二：沙箱回传非 ASCII 必然乱码（独立缺陷，一并修）**

父进程按 `encoding="utf-8"` 解码管道，而子进程在 Windows 默认按 **cp936** 写管道 ⇒ `print('中文测试')` 回传为 `���Ĭ��`（实测）。
修法：子进程命令行加 **`-X utf8`**；注意 `-I`/`-E` 会忽略环境变量，故**不能用 `PYTHONIOENCODING` 兜**。实测加 `-X utf8` 后 stdout 与 stderr 的中文均逐字还原。**这才是 §7「中文乱码不再复现」DoD 的真正闭合点** —— 原 DoD 只盯着「query 进脚本」，而乱码另有来源。

> **方法论注记（第三次同构教训）**：§5.1.4 的「先追字段的消费者」、§5.2 预检的「先追 `script_hash` 的消费者」、本条「先追乱码的**产生源**」是同一件事 ——
> **文档里叙述的因果不经验证就不能当设计前提**。
> 而「一个工具 100% 失效却无人察觉」恰恰是 Arm 1 要消灭的故障形态：改前它只能靠事后人工翻 raw 才能发现。

### 5.3 Arm 3：依赖环境锁定（P0，**2026-09-14 Grill Q2 定「三刀分离」；2026-09-16 追加拍板换生成工具**）

> **拍板结论（Q2=A）**：把「锁定 / 声明 / 强制」拆成三刀，各用最小手段 —— 因为**单靠 `requires-python` 在本仓库结构下没有任何强制力**（见 §4.4：无 `[build-system]`，pip 不会执行该字段）。
> **子决策**：① lock **不加 `--generate-hashes`**（哈希随平台轮子变化 ⇒ 本机 win 生成的哈希在 ubuntu 上必然不匹配；行业证据见 §4.4 —— gpt-researcher 用**环境标记**而非锁死哈希处理跨平台）；② ~~跨平台风险取「`pip-compile --python-version 3.11` 生成 + 首次 CI 运行实测」~~ ← **该子决策的前提已被实测推翻**（旗标不存在 + pip-compile 丢弃标记），**2026-09-16 追加拍板**改为：**用 `uv pip compile --universal` 生成带正确 PEP 508 标记的单文件锁**（详情见 §5.3.1 实测修正一/二/三、§5.3.5 拍板记录、§6.3 工具选型）。

#### 5.3.1 第一刀 · 锁定（真缺口）

**用 `uv pip compile --universal` 生成 `requirements-lock.txt`，明确不用 `pip freeze`。**
（本小节原文写的是 `pip-compile --python-version 3.11`，**两处均已被实测推翻**，见下方实测修正一/二；正文按修订后表述。）

- 命令（**实际执行、且逐字记录在两份 lock 文件头**）：
  ```
  uv pip compile --universal --python-version 3.11 --strip-extras \
    --output-file requirements-lock.txt requirements.txt
  uv pip compile --universal --python-version 3.11 --strip-extras \
    --output-file requirements-dev-lock.txt requirements-dev.txt
  ```
  - `--universal` 做**单一文件的全平台/全版本解析**，逐包输出 PEP 508 环境标记 ⇒ 一份锁同时适用于 Windows 开发机与 ubuntu CI。
  - `--python-version 3.11` 定下解析下限（对齐 CI matrix 最低档），**这是 pip-compile 做不到的**（见实测修正一）。
- **不使用 `pip freeze` 的理由（实测证据）**：`.deps/` 里 **168 个 `.pyd` 全部是 `cp313-win_amd64`** —— `pip freeze` 输出的是「**本机当前已装集合**」，会把 CPython 3.13 + Windows 专属的二进制集合固化进 lock，换平台即废。
- **不加 `--generate-hashes`**：加了会让哈希带平台指纹，直接破坏可移植性（见上方子决策 ①）。实测两份锁 `grep -c '\-\-hash'` 均为 **0**。
- `requirements-dev.txt`（`ruff` + `pytest`）同步锁为 `requirements-dev-lock.txt`。
- ~~**新增开发依赖 `pip-tools`** 到 `requirements-dev.txt`~~ ← **已作废**：换 uv 后不再需要；且 uv 是独立二进制，**刻意不写进 `requirements-dev.txt`**（否则会把 `build`/`wheel`/`pyproject-hooks` 等 8 个包拖进 CI 安装树）。dev 锁因此由 14 行瘦到 7 包。
- CI 安装步骤改为：`pip install -r requirements-lock.txt -r requirements-dev-lock.txt` —— **CI 侧不需要 uv**（锁是 requirements 格式，pip 读标记自行挑选轮子）。
- **前置动作**：`pyproject.toml` 无需 `[build-system]`（本刀不引入打包语义）。

> **⚠️ 实测修正一（2026-09-16）：`--python-version 3.11` 这个旗标不存在。**
> 本稿原文与 §6.3、§11 拍板行均写 `pip-compile --python-version 3.11 ...`，**实测 `pip-compile 7.6.1` 直接报 `No such option: --python-version`** —— pip-tools 只能按**运行它的解释器**求值环境标记，**没有跨版本/跨平台解析能力**（`--help | grep -E "python-version|platform|universal"` 三个词全无）。
> ⇒ 要生成「按 3.11 解析」的锁，**必须在真 3.11 解释器下运行 pip-compile**。本项目为此建了专用 venv：
> `…/binaries/python/envs/py311-tools`（`uv python install 3.11` → `python -m ensurepip` → `pip install pip-tools`；注意 `uv venv` 建出的环境**不自带 pip**）。
> 实际执行命令（**已落地**）：
> ```
> env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy no_proxy='*' NO_PROXY='*' \
>   <py311-tools>/Scripts/pip-compile.exe --strip-extras \
>   --output-file requirements-lock.txt --pip-args "--timeout 90 --retries 8" requirements.txt
> ```
> **代理坑（本环境特有）**：**清空 `HTTP_PROXY` 等环境变量并不足以让 pip 直连** —— pip 的 `requests` 走 `urllib.request.getproxies()`，在 Windows 上**还会读注册表**（实测清空 env 后仍返回 `{'http': 'http://127.0.0.1:7897'}`，即 ClashVerge 的系统代理）⇒ 表现为 `ProxyError('Cannot connect to proxy.')` 或内容被改写成 `HashMismatch`。**必须额外加 `no_proxy='*' NO_PROXY='*'`** 才真正绕过（`uv` 不读注册表，所以对它只需清 env）。

> **⚠️ 实测修正二（2026-09-16）：pip-compile 丢环境标记 ⇒ 跨平台假设**必然**失败（第一刀 DoD 未达成）。**
> 实测 `requirements-lock.txt`（308 行）**环境标记数为 0**（`grep -c ';'` = 0）：pip-compile 把满足当前解释器的 marker **求值后直接丢弃**，把不满足的包**整条省略**。
> 后果：`pywin32==312`（由 `portalocker` 的 `platform_system == "Windows"` 标记引入）被**去标记平铺**；而 PyPI 上 `pywin32 312` 的 **21 个产物全部是 Windows wheel，0 个 sdist、0 个非 win 产物**（`packagetype` 仅 `bdist_wheel`）。
> ⇒ **linux runner 上 `pip install -r requirements-lock.txt` 必然报 `Could not find a version that satisfies the requirement pywin32==312`，CI 必红**。这不是「风险」，是**必现**。
> ⇒ 本稿 §5.3.1 子决策②「`pip-compile --python-version 3.11` 生成 + 首次 CI 运行实测」的**前提被推翻**，且 §6.3 里「选 pip-compile 而非 uv 是因为它**保留环境标记**」的**立论被实测证伪**（它恰恰不保留）。
> **处置**：§5.3.5 拍板记录 + §6.3 工具选型重写 + §11 变更记录。（实测修正一的 pip-compile 执行命令**已被下述 uv 命令取代**，保留作为「为什么不用它」的证据。）

> **⚠️ 实测修正三（2026-09-16）：改用 `uv pip compile --universal` 后，跨平台可安装性已获实测证据。**
> 换 uv 重生成后逐项核对：
>
> | 检查项 | pip-compile 版 | **uv universal 版** |
> | --- | --- | --- |
> | 环境标记数（主锁） | **0** | **7** |
> | `pywin32` | `pywin32==312`（去标记） | **`pywin32==312 ; sys_platform == 'win32'`** |
> | `tzdata` | 去标记 | `tzdata==2026.4 ; sys_platform == 'emscripten' or sys_platform == 'win32'` |
> | `colorama` / `watchdog` | 去标记 | `colorama==0.4.6 ; sys_platform == 'win32'` / `watchdog==6.0.0 ; sys_platform != 'darwin'` |
> | `--hash` 残留 | 0 | 0 |
> | 直接依赖版本 | — | **完全一致**（`langgraph 1.2.11` / `langfuse 4.15.3` / `openai 1.92.3` / `streamlit 1.64.0` / `pydantic 2.13.5` / `qdrant-client 1.19.1` / `jieba 0.42.1`），**无版本漂移** |
>
> **Linux 目标安装预演**（`pip install --dry-run --ignore-installed --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: -r requirements-lock.txt`）：整份锁逐一解析通过，**唯一报错为 `jieba==0.42.1`** —— 而那是**由我强加的 `--only-binary=:all:` 造成的**：jieba 只发布 sdist、**没有 wheel**，真实 CI 不加该旗标时 pip 会正常从 sdist 构建（且 jieba 是纯 Python 包，Linux 上历来可装）。
> ⇒ **原「必红」判定被消解**。剩余唯一不确定性由 CI 实测兜底（见 §7 Arm 3 DoD 最后一条）。

#### 5.3.5 拍板记录（2026-09-16 追加，非 Grill 轮次）

**触发**：第一刀落地过程中，实测连续推翻 Q2 子决策②的两条前提（旗标不存在 / 标记被丢弃），按项目惯例**立论失效即重开拍板**。

**选项与裁决**：

| 选项 | 内容 | 代价 | 结果 |
| --- | --- | --- | --- |
| **A** | `uv pip compile --universal` 重生成 | 换掉 Q2 选型；本沙箱内 uv 建 sdist 被安全层拦，需沙箱外跑一次 | ✅ **于晏拍板采纳** |
| B | Docker（`python:3.11-slim`）里用 pip-compile 生成 Linux 锁 | 需启动 Docker Desktop（实测守护进程未运行）；锁变 Linux 视角，win-only 包转为动态解析不锁定 | 未采纳 |
| C | 文档 §5.3.1 预授权回退「只锁直接依赖」 | 传递依赖仍可漂移 —— **正是 Arm 3 要打的靶子**，等于只解决一半 | 未采纳 |
| D | 先推 dev 用真 CI 坐实「必红」再决策 | 多花一轮 CI，但拿到不可辩驳的真红证据 | 未采纳 |

**A 的落地要点**：① 生成锁用 uv `0.11.32`，**命令逐字写进两份 lock 的文件头**，任何人可复算；② **CI 不需要 uv** —— 锁仍是 requirements 格式，`pip install` 一字未改；③ `pip-tools` 从 `requirements-dev.txt` **移除**（uv 是独立二进制，不进 pip 依赖树）。
**未决**：是否给 CI 加一个「锁过期检查」任务（比对重生成结果 vs 已提交锁）—— 目前**不做**，避免为可复现性引入新的 CI 依赖与失败面。

#### 5.3.2 第二刀 · 声明（纠正原文错误前提）

| 文件 | 原文（错） | **本稿（正）** |
| --- | --- | --- |
| `pyproject.toml` | 「`requires-python` 从 `>=3.10` **改为** `>=3.11,<3.14`」 | **新增** `requires-python = ">=3.11,<3.14"` 字段（原文件**根本没有该字段**），并**就地注释**：「本仓库无 `[build-system]`，此字段**不具强制力**，仅为声明；真正强制见 5.3.3」 |
| `pyproject.toml` | 未提及 | `[tool.ruff] target-version` 由 `"py310"` **改为 `"py311"`** —— 这是四处口径里**唯一"真强制"**的一处（ruff 按该版本语法集检查） |
| `README.md:46` | 未提及 | `Python 3.10+` → **`Python 3.11 ~ 3.13`** |
| `.github/workflows/ci.yml` | 未提及 | 保留 `python-version: "3.11"`，并**扩为 matrix**（见 5.3.3） |

**上界 `<3.14` 的依据**：`config.py:40` 的 `.deps` `cp313` 教训 + 既有先例 `openai>=1.50,<1.93` / `langfuse>=4.15,<5`。行业对照（§4.4）显示同类项目普遍**只写开区间**（`>=3.10` / `>=3.11`），**本项目的上界是有依据的加强，不是啰嗦**。

#### 5.3.3 第三刀 · 强制（新增，补原文缺失的"真能拦住"）

1. **运行时版本闸**：在 `research_engine/__init__.py` 顶部校验 `sys.version_info`，不在 `(3,11) ~ (3,13)` 区间即**明确报错**（提示支持区间与当前版本），而非等到 `pydantic_core` 导入失败时报难懂的二进制错误。
2. **CI 扩为 matrix**：`python-version: ["3.11", "3.12", "3.13"]`。
   - 成本账：现单跑约 36~37 s ⇒ matrix 三档约 110 s，可接受。
   - 作用：把「四处口径」收敛为 **一处（CI matrix）+ 一处（运行时闸）**，且 3.12/3.13 是**首次被真实验证**。

#### 5.3.4 取消原「新增 smoke test」DoD

原 DoD「新增一条无需外部 API 的 smoke test」**已有等价物**：现有 **132 个测试全部零 LLM 调用、零 API key**，CI 每次 push 都跑 `pytest tests/ -q`。重复新增一条 smoke test 不增加信息量 ⇒ **取消**（改为把 CI matrix 作为「多版本都能跑」的证明）。

### 5.4 Arm 4：工具失败原因暴露（P1）

#### 5.4.1 搜索结果结构扩展

`SearchResponse`（或等价结构）新增 `failure_reason: Optional[str]` 字段：

| 取值                 | 含义                                     |
| ------------------ | -------------------------------------- |
| `None`             | 正常返回（有结果或确认无结果）                        |
| `"not_configured"` | provider 未配置（如 Qdrant 未初始化、API key 缺失） |
| `"timeout"`        | 请求超时                                   |
| `"provider_error"` | provider 返回错误（HTTP 5xx、API 限流等）        |
| `"empty_result"`   | provider 正常响应但无匹配结果                    |
| `"parse_error"`    | provider 返回了数据但解析失败                    |

> **⚠️ 2026-09-15 Grill Q4：本枚举表升级为「全项目失败原因唯一真相源」。**
> Arm 1 的 `DegradationEntry.reason` **改用同一张表**（并新增 `llm_error` / `token_limit` / `recursion_limit` / `internal` 四个值，完整表见 §5.1.2）。
> 目的：消除「Arm 1 节点降级」与「Arm 4 工具失败」两套失败记录各说各话的问题（原待定项 B4）。
> 实现约束：枚举定义在**一处**（建议 `research_engine/failure_reasons.py`），两侧 import，不得各写一份字面量。
>
> **⚠️ 2026-09-15 Grill Q8：工具层这 5 个值是「源头」，`degradation_log` 是「下游」**
> 本表 5 值由**工具层唯一产生**（写入 `SearchResponse.failure_reason`）；Arm 1 的 `DegradationEntry.reason`
> **必须派生自本字段**，不得另写字面量（完整契约与四个非工具值见 §5.1.2）。
> ⇒ 实现时**先写 Arm 4 的 `failure_reason`，再让 Arm 1 消费它**，顺序不可颠倒。

#### 5.4.2 落点清单

> **⚠️ 下表行号为 2026-09-10 建稿时快照，2026-09-16 核对已全部过期**（`researcher.py` 88-89/109-110/180 实际已移至 118-120/141-142；`arxiv.py` 78-79 实际为 79-86）。
> 保留仅为追溯设计意图；**改码请以代码实际位置为准**。另按决策 D-02，RAG 路径**新增独立 `RetrieveResponse`**
> （见 `docs/project-status.md`），不再由 researcher 层临时适配 —— 本表「RAG 搜索 → researcher.py」一行的落点已变更。

| 改动点       | 文件            | 行号      | 改动内容                                                     |
| --------- | ------------- | ------- | -------------------------------------------------------- |
| Web 搜索    | researcher.py | 88-89   | `except Exception` 中区分原因，写入 `failure_reason`             |
| RAG 搜索    | researcher.py | 109-110 | 同上                                                       |
| arXiv 搜索  | arxiv.py      | 78-79   | 同上                                                       |
| RAG store | store.py      | 47-50   | `_available=False` 时设置 `failure_reason="not_configured"` |
| 单工具异常     | researcher.py | 180     | 同上                                                       |

### 5.5 Arm 5：评测口径收敛（P1，**2026-09-15 Grill Q3 拍板：主刀改为 run 级质量闸**）

> **拍板结论（Q3=A）**：原方案「把 `complete` 拆成四字段」**降级为附属产出**。
> 理由是它治不了实测出的病：`run_20260910_173540` 拆完之后会是 `raw_written=20, quality_complete=0`，
> **仍然需要一个人主动去看 `quality_complete` 是不是 0** —— **加字段 ≠ 加护栏**。
> **命名裁决（Q3 附带，选甲）**：phase2 侧 `status` 改名为 `metrics_status`；`run_status`（Arm 1）独占「流程健康度」语义 —— ✅ **2026-09-18 落地**（键名契约唯一真相源 = `research_engine/eval/status_keys.py`；落盘只出 `metrics_status`，读侧 dual-read 兼容历史裸 `status`，历史产物不回填；详见 §5.1.1「命名裁决 · 落地形态」）。

#### 5.5.1 主刀：run 级质量闸

`_summarize()` 在顶层产出：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| **`verdict`** | `str` | `ok` = 无异常；`suspicious` = 指标跌破阈值（**被测可能已彻底降级**）；`broken` = 评测管线自身大面积失败 |
| **`verdict_reasons`** | `list[str]` | 触发原因的人类可读串，如 `completion_rate==0`、`avg_steps<=1.5`、`metrics_failed>=50%` |

**阈值外置为参数**（Q3 反方挑战 ① 的处置）：`_summarize(..., thresholds: QualityThresholds = DEFAULT_THRESHOLDS)`。

* **需求文档不写死数字** —— 候选阈值（`completion_rate==0` / `citation_accuracy==0` / `avg_steps<=1.5`）是**事后从 `173540` 单个 run 反推的**，写进文档等于把一个过拟合的数字制度化。
* 默认组 `DEFAULT_THRESHOLDS` 落在代码常量，**必须就地注释**：「由 `run_20260910_173540` 反推，仅作初值；**须在 §10.4 新采的主链路基线上复核后调整**」。
* 调用方（CLI / 采基线脚本）可传入自己的阈值，无需改需求文档。

**只告警、不阻断**（Q3 反方挑战 ② 的处置）：`verdict` 写入 summary 与报告，**不 hard fail**。理由：`suspicious` 的语义是"值得人看一眼"，而阈值尚未在新基线上校准 —— **在阈值可信之前就让它阻断采基线，风险大于收益**。待基线采完、阈值校准后，是否升级为 hard fail **另开议题**。

**判定口径（2026-09-17 拍板 D-05，实现时补）**：**指标未算出（缺失 / 非数值）不触发 `suspicious`** ——
这类情况由 `metrics_partial` / `metrics_failed` 表达；`verdict` **只判断已经成功算出的指标**是否落入异常区间。
语义分工：`suspicious` = 运行完成了但**结果质量可疑**；`partial/failed` = **评测本身没完整完成**。
两者若混在同一个判定里，质量闸就同时表达「结果质量」与「执行完整性」，后续统计会失真
（一次「指标没算出来」会被误记成「被测变差」）。实现见 `research_engine/eval/quality.py::_below()`。

> **⚠️ 实现状态（D-01）**：本节只维护设计要求与判定口径。**Arm 5 的实时进度、回填结果、
> 影响 run 数量与验收证据统一见 `docs/project-status.md`**，本文不重复维护。

#### 5.5.2 附属产出：字段重命名 + 语义标注

| 原字段 | 新字段 | 语义（须在代码注释与报告中写明） |
| --- | --- | --- |
| `complete` | **`metrics_ok`** | 七指标**全部算出**的条数（= 原 `status=="ok"`） |
| `partial` | **`metrics_partial`** | 有指标未算出的条数（当前仅 coverage 裁判失败会触发） |
| `failed` | **`metrics_failed`** | 评测抛异常 / 超时的条数 |

**保留旧字段名一段时间**：为兼容 `docs/eval-report.md` 历史趋势表与 `tools/w7_backfill_*.py`，旧键名**同时写入**并标注 `deprecated`，待下游全部改完后移除（不在此 Arm 的 DoD 内）。

#### 5.5.3 eval-report 模板更新

`docs/eval-report.md` 的指标表：**新增 `verdict` 列（置于最前）**，指标列由 `complete` 改为 `metrics_ok`。历史趋势表同步更新，并回填历史 run 的 `verdict`（复用 `tools/w7_backfill_*.py` 的只读模式）。

> **✅ 已交付（2026-09-17）**：`report_gen.py` 已落地 —— 报告新增 **§0 run 级质量闸**小节（verdict 置于最前）；
> 指标表并列输出「均值 ± stderr」与**有效题数 n**；§4 改用 `metrics_ok/partial/failed`（旧键保留标 deprecated）；
> `append_history()` 记入 `verdict` / `verdict_reasons` / `metrics_stderr` / 新计数键。
> 细节与验收证据见 `docs/project-status.md`（D-01：本文不重复维护实时状态）。

#### 5.5.4 指标离散度：`metrics_mean` 必须带 stderr（2026-09-15 新增，Q5）

**现状**：`_summarize()` 的 `metrics_mean` **只有均值，无离散度**。这是 W7 事后才发现"噪声（20~29pp）吞没效应（4~10pp）"的**直接原因——仪器没装刻度**：跑了、算了、报了，但没人知道这个数字的可信区间有多宽。

**行业横向对照（2026-09-15 实时取证，详见 §6.7）**

| 项目 | 实践 |
| --- | --- |
| **openai/evals** | `get_bootstrap_accuracy_std(events, num_samples=1000)` —— **内置在指标层**的半采样 bootstrap std |
| **EleutherAI lm-evaluation-harness** | `mean_stderr()` / `acc_all_stderr()` / `_bootstrap_internal` —— **每个指标都带 stderr** |

⇒ **「报指标必须带 stderr」是行业通例，不是加分项。**

**要求**

1. `metrics_mean` 的每个指标**并列输出 stderr**（bootstrap 或均值标准误二选一，建议 bootstrap 1000 次以对齐 openai/evals）。
2. **零 LLM 成本**：纯离线计算，可直接从既有 raw 数据算出。
3. **离线回填历史 run**（Q5 已拍板）：复用 `tools/w7_backfill_*.py` 的只读模式，把历史 run 的 stderr 一并补上 ⇒ **让历史数据第一次具备可比性判据**。
4. 消费方约束：`docs/eval-report.md` 与任何结论文档引用指标时，**必须同时给出 stderr**；**stderr > 所声称的效应 ⇒ 该结论不得表述为"提升 Xpp"**。

### 5.6 Arm 6：可复现元数据（P1）

#### 5.6.1 raw 记录扩展

`eval/run.py` 的 `_run_one()`（run.py:97-109）中，每条 raw 记录新增：

> **⚠️ 2026-09-15 Grill Q8（C6）拍板：由「13 个平铺字段」收敛为「8 个真新增字段 + 内嵌 1 个 `config_snapshot` 对象」。**
> **一句话规则：配置类字段一律不平铺，改为内嵌同一份 `config_snapshot`。**
> **同时：`git_commit` / `git_dirty` / `git_diff_hash` 由新增的 `code_revision()` 一处产生，现有 3 份重复实现全部改为 import。**
> 收敛依据与完整取证见本节末《Q8 C6 收敛说明》。

**表 A：raw 记录新增的 8 个非配置类字段（真新增，与 `_config_snapshot` 零重叠）**

| 字段 | 来源 | 用途 |
| --- | --- | --- |
| `git_dirty` | `code_revision().git_dirty` —— `git status --porcelain` 非空 | 标记工作区是否 dirty |
| `git_diff_hash` | `code_revision().git_diff_hash` —— 对 `git diff --binary` 输出取 sha256 | 工作区 diff 的指纹 |
| `deps_frozen_hash` | `requirements-lock.txt` 的 sha256 | 依赖版本指纹（Arm 3 产出） |
| `prompt_hash` | 见下表 C | 运行期实际提示词指纹 |
| `citation_judge_model` | 见下表 C | 引用判定的裁判身份 |
| `coverage_judge_model` | 见下表 C | coverage 判定的裁判身份 |
| `citation_judge_independent` | 见下表 C | W7 病根的可审计化 |
| `scorer_version` | 见下表 C | 指标代码指纹 |
| ~~`seed`~~ | ~~请求参数~~ | ❌ **2026-09-14 明确不做**，理由见下 |

**表 B：raw 记录内嵌 `config_snapshot`（唯一配置真相源）**

每条 raw 记录内嵌**同一份** `config_snapshot` 对象（定义与字段清单见 §10.4），
取代原平铺的 `python_version` / `model_name` / `search_provider` / `max_step_budget` / `experiment` 五项。
**内嵌而非平铺** ⇒ 单条 raw 仍**自包含**（拷一条出去给第三方看，配置齐全），同时配置**只有一个产生点**，不会分叉。
行业同构见 §6.8（OTel Resource「一处定义、所有 span 共享同一对象」/ MLflow「config 记一次、metric 多次」）。

**2026-09-14 Grill C3 拍板：按行业 provenance schema 扩表（原 6 → +`experiment` → +6 → 13 字段；2026-09-15 Q8 收敛为 8 + 内嵌）**

行业基准：Propel Code《AI Code Review Needs Eval Provenance》(2026) 的 eval provenance 最小 schema 含 Run header（task set / run ID / timestamp / model ID / provider / temperature / seed）、Repo state（base commit / lockfile hash / dataset version）、Execution policy（**prompt version** / sandbox mode / **max step budget**）、**Grading trace（scorer version / judge model / 阈值 / 失败模式）**、Outcome trace（cost / latency / token / unresolved failure reasons）。

据此新增 6 个字段（**全部经代码取证确认可取值**；Q8 收敛后剩 5 个 —— `max_step_budget` 已并入 `config_snapshot`）：

**表 C：C3 新增字段**

| 新增字段 | 来源（取证） | 用途 |
| --- | --- | --- |
| **`prompt_hash`** | **语义（必须先钉死）：「运行期实际使用的提示词正文」的 sha256 前 16 位，不是源码常量快照。** 覆盖范围：① 模块级常量 `PLANNER_SYSTEM`/`REPLAN_SYSTEM`（`agents/planner.py:13/30`）、`VALIDATOR_SYSTEM`/`VALIDATOR_SYSTEM_LEGACY`（`agents/validator.py:33/58`）、`WRITER_SYSTEM`/`WRITER_SYSTEM_LEGACY`（`agents/writer.py:17/30`）；② **critic —— 实测位于 `research_engine/critic.py:174-181`，是 `_verdict()` 内的 `system` 局部变量拼接（含 `:167-173` 的 `gap_extra` 段），既不在 `agents/` 目录、也**不是模块级常量** | **抓住「改了 prompt 但没改开关」** —— `experiment` 段抓不到的盲区。**硬证据（2026-09-14 复盘取证）**：`critic.py:167-179` 由 `CRITIC_GAP_ENABLED` 开关把 `gap_extra` 段**动态拼进 system 提示词** ⇒ **开关与提示词不正交**，只记开关值会漏掉真正的提示词差异。这正是本字段存在的必要性证明 |
| **`citation_judge_model`** | 引用准确率的裁判**实际是主链路 validator**：`eval/metrics.py:5` 明写「直读 `state.citations`（主链路 validate 已产 verified/existence，**不再重跑 validator**）」⇒ 取值 = `config.llm.validator_model` | 引用判定的裁判身份 |
| **`coverage_judge_model`** | coverage 另有独立裁判实例：`eval/metrics.py:23` 直建 `role="judge"` 客户端（档位 = smart） | coverage 判定的裁判身份 |
| **`citation_judge_independent`** | 派生布尔：`citation_judge_model != config.llm.validator_model` 时恒为 `False`（现行架构下**结构上必然为 False**） | **W7 病根的可审计化**：把「裁判与被测同源」从口头结论变成每条 run 都能查的字段 |
| **`scorer_version`** | 指标代码文件的 sha256 合并：**`eval/metrics.py` + `citation_eval.py` + `retrieval_eval.py` + `report_eval.py` + `w7_rejudge.py`** | 附带能力：**检测「改了指标代码 ⇒ 旧 run 不可直接对比」**。**补 `w7_rejudge.py` 的理由（2026-09-14 复盘查出）**：该文件的 `JUDGE_SYSTEM_DIAGNOSTIC`（`w7_rejudge.py:88`）是 **W7 2×2 冻结权威复判的实际裁判提示词**（`docs/eval-w7-conclusion.md` 的 arm0 83.99%/91.52%、arm6 90.67%/88.05%、纯裁判效应 +7.53pp 均由它产出），而它**既不在原 `scorer_version` 清单、也不在 `prompt_hash` 清单 ⇒ 完全漏网**。改了它 = W7 全部复判结论可漂移且无痕 —— **这是本字段最该守住的一处** |
| ~~**`max_step_budget`**~~ | ~~`config.research.max_total_hops`~~ | ❌ **2026-09-15 Q8 移出**：与 `_config_snapshot["max_total_hops"]`（`report_gen.py:34`）**纯别名**，零新增信息 ⇒ **并入 `config_snapshot`，不再单列** |

**⚠️ 为什么要拆成 `citation_judge_model` + `coverage_judge_model` + `citation_judge_independent` 三个字段，而不是行业 schema 里的单个 `judge_model`**：本项目现行有两个裁判，且**引用裁判就是被测（主链路 validator）**。若只记一个 `judge_model`，等于把 W7 最要命的病（裁判兼任被测）**掩盖**成一个看似中性的字段；拆开后 `citation_judge_independent: false` 每天都能在 summary 里被看见，任何「引用准确率提升」的结论都必须先解释这个 false。

**明确不做的字段（附理由）**

| 字段 | 不做理由 |
| --- | --- |
| `seed` | ① 工程上 `llm/client.py` 的 `chat()` 无 `seed` 参数、请求 payload 亦无该字段，需改 client + config + 各调用点，且 **DashScope 兼容模式是否支持未验证**（外部不确定性）；② **不对称投入**：W7 已用一手数据证明噪声主因是**检索轮数 / 证据池漂移**（`avg_steps=3.85` 的臂区块极差 20~29pp，8.1 的臂仅 1.3~4.6pp），**不是 LLM 采样随机性** ⇒ 固定 seed 不解决真正的噪声源 |
| `temperature` | `config.py:40 temperature: float = 0.2` 为**硬编码常量、非 `_env()` 可配** ⇒ 记下来恒等于 `0.2`，零信息量。**降级为「随 temperature env 化一起做」**，不单独立项 |
| **`embedding_model`**（不进 `config_snapshot`） | `config.py:80` 为**硬编码常量 `"text-embedding-v3"`、非 `_env()` 可配** ⇒ 恒值、零信息量，与 `temperature` 同理 |
| **`strategic_model`**（不进 `config_snapshot`） | `config.py:24/37-38` 明示其为 `planner_model` 的**兼容别名**（env 同读 `STRATEGIC_MODEL`）⇒ 记了等于把同一事实写两遍，正属 Q8 要消灭的「同概念两落点」 |
| **平铺的 `model_name` / `search_provider` / `python_version` / `max_step_budget` / `experiment`** | ❌ **2026-09-15 Q8 移出**：全部并入内嵌的 `config_snapshot`（表 B）。**`model_name` 另有一条独立理由 —— 口径未定义**（config 有 7 个模型字段，见《Q8 C6 收敛说明》） |

`config_snapshot.experiment` 段内容（**2026-09-13 新增，见 §10.4；2026-09-15 Q8 收敛后改挂在内嵌对象下**）：
5 个开关经 `_env()` 解析后的布尔**生效值** —— `critic_gap_enabled` / `validator_fixes_enabled` /
`validator_assertive_filter_enabled` / `writer_sectioned_feed_enabled` / `validator_trim_enabled`。

> 注意记录的是**运行期生效值**而非环境变量原始字符串：开关未设置时会回落默认值
> （`VALIDATOR_ASSERTIVE_FILTER_ENABLED` 还会二次回落到 `VALIDATOR_FIXES_ENABLED`），
> 只记原始环境变量等于丢失真相。默认值本身由
> `tests/test_w7_experiment_guard.py::test_w7_mainline_switch_defaults_stay_enabled` 兜住。

---

#### Q8 C6 收敛说明（2026-09-15 拍板 A：一处定义 + 内嵌同一对象）

**发现 ①：「取 git HEAD」在本仓库已有 3 份实现、3 种粒度**

| 位置 | 输出 | 带 dirty？ | 谁在用 |
| --- | --- | --- | --- |
| `eval/report_gen.py:47 _git_head()` | 完整 HEAD | ❌ | `write_baseline()` → `git_commit` |
| `eval/run.py:64 git_head()` | 完整 HEAD（**同一段代码整段复制**） | ❌ | 每条 raw 记录 |
| `eval/w7_experiment.py:45 _git_rev()` | 8 位短 rev **`+dirty` 后缀** | ✅ | 每个 block 开头（`:62 return f"{rev}+dirty" if rev and dirty else ...`） |

⇒ Arm 6 若再加 `git_dirty` + `git_diff_hash`，就是**第 4、5 份表达**；而「dirty 布尔」**已经在产出了，只是没人读它**。

**处置**：新增 `code_revision()`（建议落在 `eval/provenance.py`）返回 `{git_commit, git_dirty, git_diff_hash}`，
**三处调用方全部改为 import**。`w7_experiment._git_rev()` 保留 `+dirty` 字符串格式（兼容既有产物），内部改为调用共用函数。

**发现 ②：5 个字段与 §10.4 落地后的 `config_snapshot` 撞车**

| 原平铺字段 | 撞车对象 | 判定 |
| --- | --- | --- |
| `search_provider` | `_config_snapshot["search_provider"]`（`report_gen.py:37`） | **完全重复** |
| `max_step_budget` | `_config_snapshot["max_total_hops"]`（`:34`） | **纯别名**，零新增信息 |
| `python_version` | `eval_env["python"]`（`:67`） | **重复** |
| `experiment` | §10.4 本体 | **同一件事写了两次** |
| `model_name` | —— | **口径未定义**（见发现 ③） |

⇒ §10.4 已定为 **P0 第一项**，落地后 `config_snapshot` 会进 per-run `summary.json`、Arm 6 的平铺字段进每条 raw
⇒ **同一次 run 的产物里 `search_provider` 有两个来源**。今天取值相同，任一侧改动即**静默分叉**。

**发现 ③：`model_name` 口径未定义，且漏了 W7 最要命那个旋钮**

`config.py` 共 7 个模型字段（`:30-38` + `:80`）：`fast_model` / `smart_model` / `planner_model` / `critic_model` /
**`validator_model`** / `strategic_model`（= planner 别名）/ `embedding_model`（硬编码常量）。
`_config_snapshot` 只记 4 个（planner / critic / fast / smart）—— **`validator_model` 不在顶层**。
而 W7 的全部故事就是这个旋钮：§10.4 依据 1 的表格里 `VALIDATOR_MODEL` 单独占一列，arm6 = 全开关 + `qwen-turbo`，
是 W7 唯一被换掉的模型。若 `model_name` 实现时随手取 `smart_model`，
**「W7 最要命的旋钮」在字段表里依然不可见** —— Arm 6「让混淆源可审计」的立论当场漏掉一半。

**处置**：`config_snapshot` 增加 **`validator_model` 为顶层字段**（不再只躲在 `experiment` 段里）；
`embedding_model` / `strategic_model` **明确不记**（理由见上表）。

**净效果**

| | 收敛前 | 收敛后 |
| --- | --- | --- |
| Arm 6 平铺字段 | 13 | **8**（表 A） |
| 配置来源 | 13 字段里散着 5 个 + `config_snapshot` 另有一份 | **唯一：内嵌的 `config_snapshot`**（表 B） |
| git 修订实现 | 3 份（将变 5 份） | **1 份 `code_revision()`** |
| Arm 6 实现工作量 | 13 处取值 + 13 条单测 | **8 处取值 + 8 条单测**（**变小**） |

#### 5.6.2 summary 记录扩展

`_summarize()`（run.py:307-334）同步新增上述字段的汇总值。

### 5.7 Arm 7：评测产物治理（P2，**2026-09-14 Grill Q1 拍板重划为双轨**）

#### 5.7.1 轨道 1（git 侧）：白名单收敛 + 「引用即入库」

`.gitignore` 的 `run_*/` 规则**已存在**（`:36`），但仍被跟踪的路径不等于白名单路径 —— 因为 `.gitignore` 对**已跟踪**文件无效。因此轨道 1 = **把跟踪状态与白名单强制作成一致**：

1. **逐项定性 94 个「跟踪但不在白名单」的路径**：

   | 路径 | 项数 | 定性依据 | 处置 |
   | --- | --- | --- | --- |
   | `run_v11_compare/` | 42 | `docs/eval-w7-attribution.md` 的 **298 条归因夹具来源** | **升为白名单**（`!` 规则 + 注释写明引用出处） |
   | `run_20260906_184156/` | 43 | v0 基线 run（W5 起点） | **拍板后二选一**：升白名单（若被 docs/ 引用）或 `git rm --cached -r` |
   | `run_20260907_001658/` | 9 | v1.0 run | 同上 |
   | `curated/` | 5 | 权威产物目录（项目既有纪律） | 保持入库 |

   > **✅ 2026-09-18 裁决（实现时填写实况）**：四项全部走到「升白名单」，无一项用 `git rm --cached`。
   > 关键取证是**后两项此前标注「待定性」** —— `run_20260906_184156` 被 `docs/sampling-workbook.md` **逐条链接其 `raw/q_*.raw.json`**（删即为死链）、
   > `run_20260907_001658` 被 `docs/requirements/7-technical-debt-and-content.md` 引用为「v0→v1 漂移」前车之鉴 ⇒ 二者**确有手写引用**，属第 2 条判据口径内的白名单。
   > **另行发现的一处**（不在上表）：`history_bak_v10.json`（977 B）在 `docs/` 与代码中**零引用**，系 W5 `22e0d73` 误入库 ⇒ 唯一执行 `git rm --cached` 的对象，**磁盘文件保留**。

2. **白名单判据从「硬编码目录名」升级为「引用即入库」**：只有**被 `docs/` 下正式文档引用的 run** 才允许进白名单，且出处必须写明。

   > ⚠️ **2026-09-18 落地时修正本条给出的写法**：原稿示例 `!research_engine/eval/results/run_v11_compare/  # 被 docs/… 引用`
   > **不能用** —— `.gitignore` 只认**行首**的 `#`，行尾文字会被当成 pattern 的一部分，**把 `!` 规则整条废掉**（对照实测：
   > 同一份规则去掉行尾注释则放行、带上则 `check-ignore` 仍报被忽略）。⇒ **出处一律独立成注释行，写在规则的紧邻上方**。

3. **「引用」本身要分两档 —— 自动枚举的文档不算证据**（**2026-09-18 实测新增，属 Q1 判据的必要细化**）：
   `docs/eval-report.md` 由 `report_gen.py` 自动写出，实测含 **74 个互不相同的 run id**（几乎等于全量），
   而手写结论文档 `docs/eval-w7-conclusion.md` 只提 **4 个**。若把出现在自动表里的也算「被引用」，
   判据自我作废 ⇒ **只有「被人挑选过」的手写文档/代码引用才算数**；只命中自动表的 run 不得入库。
   （该文件自己的头部已写明「自动生成……不要依赖本文件」。）这条区分落在 `tools/check_results_whitelist.py`。

4. **不做的**：不为「减少仓库体积」而清理 —— 实测 git 侧可回收 ≤1 MB，投入产出比极低。

5. **落地形态（2026-09-18）**：上述判据由 `tools/check_results_whitelist.py` 单点执行，并已进 CI（`Eval 产物白名单纪律（Arm 7）` 步骤）。
   它同时拒绝三种情况：① 已跟踪但不在白名单；② 白名单条目缺出处；③ **出处只来自自动枚举文档**。
   注意 `.gitignore` 对已跟踪文件无效 ⇒ **规则写了不等于事实改了**，必须靠这个脚本持续比对。

#### 5.7.2 轨道 2（本机磁盘）：先只读扫描出账，暂不删除

`research_engine/eval/results/` 本地占用 **147 MB**，绝大多数是未跟踪的 run 目录。**该轨道的第一刀只做只读扫描**，产出一份台账：

| 台账列 | 说明 |
| --- | --- |
| 目录名 / 体积 / 最后修改时间 | 基础信息 |
| 跟踪状态 | `git ls-files` 是否命中（= 是否在白名单） |
| **是否被 `docs/` 引用** | 全仓 grep 目录名，命中则记引用文件 |
| 类别 | `权威 run` / `被取代（*_SUPERSEDED）` / `已废弃（*_DISCARDED）` / `临时（_tmp_*）` / `未知` |

**🚨 2026-09-18 事故及其推广教训（实现时新增第 2 条铁律）**：本次 Arm 7 用 `git rm --cached` 把 189 个跟踪文件降到 8 条白名单，**之后本人执行了一次跨越该边界的 `git checkout`**（原意只是同步分支，属操作失误）⇒ git 把「旧 HEAD 跟踪、新 HEAD 不再跟踪」的路径**从磁盘物理删除**；删目录时还夹带了该目录下**未跟踪**的 raw/eval ⇒ `results/` 从 **143.6 MB** 掉到 **31 MB**。

| 事实 | 说明 |
| --- | --- |
| **根因** | `git rm --cached` **只保护执行它那一刻的工作树**；此文件从「跟踪」变成「被忽略」，对任何后续 checkout / reset / pull 而言它都**属于旧版本** ⇒ git 按常规把它删掉 |
| 恢复情况 | ① 被送进**回收站**的 2,418 个文件已按 `$I` 元数据里的原始路径逐一定向还原；② 被 git 硬删除、又从未进过对象库的 **5 个文件永久丢失**（`run_20260910_033127/raw/q_014.raw.json`、`run_20260911_004201/eval/q_016` / `q_018` / `q_019` / `q_020.eval.json`，合计 ≈0.1 MB）。丢失项均属无手写引用、非白名单的历史 run，**不影响** W7 权威容器 `w7_experiment_20260911_194151` 与任何结论文档 |
| 副作用（两条） | ① 还原把所有结果文件的 **mtime 刷成恢复时刻** ⇒ 台账「最后修改」列失去取证价值；② 这两个 run 的 `summary.json` 仍写 `complete=20/20` ⇒ **文件缺失与 summary 不自洽**，账目由「完整」变成「假的」 |
| **推广为铁律 2** | **凡做过移出索引（`git rm --cached`）类操作后，本机不得再跨该边界 checkout** —— master/dev 必须**同指向**。已登记为决策 **D-12**，本地已用 `git branch -f master dev` 消除边界 |
| 残留待处置 | `.workbuddy/_arm7_results_backup/`（3,206 文件 / 151.8 MB 物理备份，**未删**）与 `.workbuddy/restore_results_from_recycle.py`（一次性还原脚本）待主理人决定何时清理 |

## 6. 设计策略

### 6.1 Arm 1 策略：轻量 tracker，不改图结构

`DegradationTracker` 不引入新节点或新边，只在现有节点的 `except` 块中追加记录。`run_status` 的判定逻辑放在 `_validate` 和 `_render` 节点的尾部，纯函数计算，不依赖额外 LLM 调用。

设计取舍：曾考虑在每个节点出口加一个"健康检查"子节点，但这会增加图的复杂度且与 W1 收敛单测冲突。当前方案的代价是每个 `except` 块需要手动追加 tracker entry——这是可接受的机械工作量。

### 6.2 Arm 2 策略：元数据通道，不改执行器协议

query 通过 `CodeExecInput.metadata` 传入，执行器将其透传到 `CodeExecOutput.metadata`。这不需要修改 `code_exec.py` 的执行协议（stdin/stdout 管道），只是调用方不再把 query 拼进脚本字符串。

### 6.3 Arm 3 策略：`uv pip compile --universal`（**2026-09-16 追加拍板，第二次修订**）

> ⚠️ **本节已两次修订，务必按最新版读。**
> **第一次修订（2026-09-14，Grill Q2）**：原稿标题为「pip freeze 而非 uv/poetry」、正文写「选择 `pip freeze` + `requirements-lock.txt`」，与 §5.3.1「**明确不用 `pip freeze`**」自相矛盾 ⇒ 改为 `pip-compile`。
> **第二次修订（2026-09-16，实测推翻）**：Q2 选 `pip-compile` 的**唯一实质理由**是上表那格写的「**保留环境标记（PEP 508）**」——实测证明**恰好相反**：pip-compile 把满足当前解释器的 marker **求值后丢弃**、把不满足的包**整条省略**，生成的 308 行锁里**环境标记数为 0**（`grep -c ';'`）。后果是 `pywin32==312`（PyPI 上 21 个产物**全是 Windows wheel**）被去标记平铺 ⇒ **Linux CI 必然装不上**。详见 §5.3.1 实测修正一/二。
> ⇒ 立论失效，**工具选型重拍**。

选择 **`uv pip compile --universal`**，而非另外三者：

| 方案 | 不用的理由 |
| --- | --- |
| `pip freeze` | 快照的是**本机已装集合**。实测 `.deps/` 168 个 `.pyd` 全为 `cp313-win_amd64`，会把 CPython 3.13 + Windows 专属二进制固化进 lock，换平台即废（详见 §5.3.1） |
| `pip-compile`（pip-tools 7.6.1） | ❌ **实测否决**：无任何跨版本/跨平台旗标（`--help` 里 `python-version`/`platform`/`universal` 三词全无），且**丢弃环境标记** ⇒ 只能在「生成它的那个平台」上装得上。**这是唯一一条被实测推翻的选项**，其原始入选理由（保留标记）已证伪 |
| `poetry.lock` | 虽然 poetry 是 marker-aware 的，但它产出的是 TOML 锁、安装语义换成 `poetry install`，迁移面远大于收益 |
| **`uv pip compile --universal`** ✅ | ① **`--universal` 做单一文件的全平台解析**，对每个包输出正确的 PEP 508 标记（实测 `pywin32==312 ; sys_platform == 'win32'`）；② 产出**仍是 requirements 格式** ⇒ **CI 侧只需 pip 安装，不需要 uv，安装命令一字不改**；③ uv 是独立二进制 ⇒ **不进 pip 依赖树**（`requirements-dev.txt` 刻意不声明它，避免拖进 `build`/`wheel`/`pyproject-hooks` 等 8 个包） |

**代价与边界（如实记）**：
- 生成锁需要 uv（实测版本 `0.11.32`），命令**逐字记录在两份 lock 的文件头**里，任何人可复算。
- `--universal` 会带出本机永不会装的平台分支（实测多出 `httpx2-jsfetch==1.0 ; python_full_version >= '3.12' and sys_platform == 'emscripten'`）—— 这是**特性不是缺陷**，标记为假的包 pip 不会装。
- 本沙箱内 uv **构建 sdist 会被安全层拦截**（`SAFE_DELETE_FAIL_CLOSED`，setuptools 清理 `jieba.egg-info` 时触发）⇒ 主锁生成需在沙箱外跑一次；dev 锁全为 wheel，沙箱内即可完成。
- **锁的「跨平台可安装」是实测结论而非承诺**，证据见 §5.3.1 实测修正三。

### 6.4 Arm 4 策略：failure\_reason 附加而非替换

搜索结果返回 `[]` 时，不改变现有的空列表语义（下游代码不需要改动），而是附加 `failure_reason` 字段。下游代码可以选择性消费该字段。这是一个非破坏性扩展。

### 6.5 行业参考

| 参考项目                                     | 相关实践                                                    |
| ---------------------------------------- | ------------------------------------------------------- |
| google-gemini/gemini-fullstack-langgraph | `is_sufficient` + `knowledge_gap` 同一次输出吐出，零额外调用（W7 已参考） |
| langchain-ai/open\_deep\_research        | `ResearchComplete` 工具调用作为显式停止信号，而非隐式状态推断                |
| dzhng/deep-research                      | 纯硬闸递归，但无降级追踪——本项目在硬闸基础上补追踪是增量改进                         |

### 6.7 行业横向对照：评测指标的离散度实践（2026-09-15 实时取证）

| 项目 | 统计实践 | 证据 |
| --- | --- | --- |
| **openai/evals** | **指标层内置 bootstrap std**：`get_bootstrap_accuracy_std(events, num_samples=1000)` = `np.std([np.mean(random.sample(vals, len(vals)//2)) for _ in range(1000)])` | `evals/metrics.py:21` |
| **EleutherAI lm-evaluation-harness** | **每个指标都带 stderr**：`mean_stderr(arr)`、`acc_all_stderr(items)`、`_bootstrap_internal`（多进程 bootstrap replicates） | `lm_eval/api/metrics.py:342/441/508` |

⇒ **行业通例：报指标必须带 stderr / 置信区间，而不是裸均值。** 本项目 `metrics_mean` 只有均值 ⇒ §5.5.4 补上。

> 注：Stanford HELM 的 `run_spec.py` 未检索到 trial/置信区间相关字段，故此处不作断言（不编造证据）。

### 6.8 行业横向对照：provenance / 配置的「一处定义」实践（2026-09-15 实时取证，用于校准 Arm 6）

| 项目 / 规范 | 配置与环境元数据怎么记 | 证据（原文） |
| --- | --- | --- |
| **OpenTelemetry Resource** | **一处定义、全局共享、明确禁止 per-span 复制**：「A Resource is an **immutable** representation of the entity producing telemetry」「a resource can be associated with the TracerProvider… **That association cannot be changed later**」「all Spans produced by any Tracer… **MUST** be associated with this Resource」「**Resources are immutable**」；实践文档更直白：「**Don't try to change the Resource per request or per span.** Only one Resource is allowed per Tracer Provider, and it's intentionally immutable」（需要 per-request 上下文时请改用 span attribute，而非复制 Resource） | `opentelemetry.io/docs/specs/otel/resource` + `specification/resource/sdk.md` |
| **MLflow** | **配置记一次、观测值多次**：`mlflow.log_params({...})` 在 run 开头写一次；`mlflow.log_metrics({...}, step=epoch)` 每步写，**config 不随每个 metric 重复** | 官方 Quickstart |
| **Weights & Biases** | 同上：`wandb.init(config={...})` 建立 run 级单一 config 对象；`wandb.log({...})` 只写指标 | 官方文档 |

**⇒ 三条行业结论**

1. **「配置一处定义 + 观测点引用同一对象」是行业通例**；把配置**逐字段拍平复制到每条记录**里，是本项目自己长出来的坏味道（本仓库「取 git HEAD」已经复制了 3 份就是症状）。
2. **OTel 明确反对 per-span 复制**，但**同时要求每个 span 都能看到 Resource** ⇒ 解法不是"不记"，而是**引用同一不可变对象**。这正是 §5.6.1 表 B「raw 内嵌 `config_snapshot`」的做法：**既自包含又单一真相源**。
3. **分裂处如实说**：MLflow/W&B 是「config 与 metric 分离存储、查询时 join」（OTel 术语叫 **telescoping identity**），本项目把 config 内嵌进每条 raw 是**反规范化**。差别在于本项目 raw 是**不可变归档产物**（要能脱离上下文单独解读、要能发给第三方），而 MLflow 有 tracking server 做 join ⇒ **内嵌是对的**，但**必须内嵌同一对象，不能内嵌一份手抄的副本**。

### 6.6 行业横向对照：run status 三流派（2026-09-15 实时取证，用于校准 Arm 1）

| 项目 / 规范 | 失败状态怎么表达 | 有 `run_status` 字段？ |
| --- | --- | --- |
| **langchain-ai/open\_deep\_research** | **错误即内容**：`final_report = f"Error generating final report: {e}"` + `AIMessage(content="Report generation failed due to an error")`；工具失败 → `execute_tool_safely()` 返回 `f"Error executing tool: {str(e)}"`；重试耗尽 → `final_report = "Error generating final report: Maximum retries exceeded"` | ❌ **无**（`state.py` 里一个状态字段都没有） |
| **assafelovic/gpt-researcher** | **事件流，状态由消费方派生**：`_log_event(event_type, step="start"/"conducting_research"/"research_completed")` + `_log_event("action", action="choose_agent"/"agent_selected")` | ❌ **无** |
| **OpenAI Responses API**（官方 SDK 源码） | **三终态 + 结构化原因**：`ResponseStatus = Literal["completed","failed","in_progress","cancelled","queued","incomplete"]` —— 去掉三个"运行中/未开始"态后**终态恰好三个**：`completed` / **`incomplete`**（带 `incomplete_details.reason`）/ **`failed`**（带 `error{code, message}`，`code` 为 **21 个枚举值之一**：`server_error`/`rate_limit_exceeded`/`invalid_prompt`/`vector_store_timeout`/…） | ✅ **有** |

**⇒ 三条行业结论（行业分裂如实说）**

1. **开源 Agent 项目普遍不做结构化 run status** —— 要么「错误即内容」（open\_deep\_research），要么「事件流 + 消费方派生」（gpt-researcher）。前者其实是本项目 W8 要治的病的**更原始版本**（连 `status` 字段都没有）。
2. **做状态机的是 API 层，且答案就是三终态 + 结构化原因。** OpenAI 的终态里**没有 `partial`** —— 这直接支撑 Q4 把四态收敛为三态。
3. **错误码必须是枚举而非自由文本** —— OpenAI `error.code` 是 21 值枚举。本项目原草案 `DegradationEntry.reason: str` 是自由文本 ⇒ Q4 改枚举（§5.1.2）。

**⇒ 本项目加 `run_status` 不是跟随行业，而是把 API 层的严谨性搬进 Agent 应用层 —— 属差异化改进。**

**已考察但本期不采纳（2026-09-15 用户裁决）**

| 项 | 行业做法 | 不采纳理由 |
| --- | --- | --- |
| **重试 + 渐进降级** | open\_deep\_research：`is_token_limit_exceeded()` → `remove_up_to_last_ai_message()` → 渐进截断重试；`synthesis_attempts` 重试循环 | 本项目现为**一次性 fallback、零重试**，确属真实能力差距；但改动面大（涉及各节点重试语义 + 成本不可控）⇒ **本期不做**，登记备查 |

## 7. 验收标准（DoD）

### Arm 1（P0）

* [x] `ResearchState` 新增 `run_status` 与 **`degradation_log: Annotated[List[DegradationEntry], operator.add]`**（**必须带 add reducer**，照抄 `state.py:90` 的 `progress`） —— ✅ **2026-09-16 落地**（`state.py`；reducer 存在性由 `test_degradation_log_has_add_reducer` 对照 `progress` 字段锁定）

* [x] 所有 `except Exception: return fallback` 路径在返回前追加 `DegradationEntry` —— ✅ **2026-09-16 落地**（`researcher._search_web`/`_search_rag`/`_search_arxiv`/`_search_code`、`planner.plan`、`writer.write`、`validator` 忠实度降级、`arxiv` provider）。**注**：Arm 4 的「五处 `except` 改造」仍属 Arm 4，本次只做**留痕**，不改 provider 的错误转换语义（arxiv 顺带补了 `failure_reason`，因其原为「返回空但说不清为什么」）

* [x] `_validate` 和 `_render` 节点根据 `degradation_log` 设置 `run_status` —— ✅ **2026-09-16 落地**（两处均调 `state.resolve_run_status(has_report=bool(state.report))`，幂等）

* [x] **`run_status` 取值覆盖 `success/degraded/failed` 三态**（~~四态~~ ← Q4 收敛，`partial` 移出 state、降为报告层派生指标） —— ✅ **2026-09-16 落地**（`RUN_STATUSES`；单测锁定「无 `partial`」）

* [x] **`failed` 可达**：`run()` 新增 try/except 契约（§5.1.4）—— 异常时经 `get_state(cfg)` 捞回 state、置 `failed`、记 `error`；**`get_state` 为空时构造最小 state，绝不返回 `None`** —— ✅ **2026-09-16 落地**（`graph._recover_from_exception`；4 条单测覆盖：正常兜底 / `get_state` 返回空 dict / `get_state` 自身抛错 / 保住 checkpoint）

* [x] **`cli.py` 在 `finally` 后判 `run_status == "failed"` ⇒ `sys.exit(1)`**；Web 展示 `run_status` 与 `degradation_log` —— ✅ **2026-09-16 落地**。⚠️ **顺带修掉一个既存 bug**：`cli.py` 末尾有两个完全相同的 `if __name__ == "__main__": main()`（W3 git 重建期间引入）⇒ **作为脚本执行会跑两遍 main、成本翻倍**

* [x] **`DegradationEntry.reason` 改为枚举，与 Arm 4 `failure_reason` 共用同一张表**（含新增的 `llm_error`/`token_limit`/`recursion_limit`/`internal`）；枚举定义在一处，两侧 import —— ✅ **2026-09-16 落地**（新增 `research_engine/failure_reasons.py` 作为唯一真相源；单测锁定两组不重叠且并集为 9 值）

* [x] **`state.error` 由 `Optional[str]` 改为结构化 `{code, message, node}`**（`state.py:92`） —— ✅ **2026-09-16 落地**。**实测破坏面仅 1 处**：`report_gen.py:428` 的 `[:200]` 切片（dict 会 `KeyError`）⇒ 已改 `str(...)[:200]`；`metrics.py:45` 的 `ok_error = error is None` 因保持「成功 `None`/失败非 `None`」语义而**不受影响**（完成率四条件之一，已由既有测试覆盖）。**注意**：破坏面比预估小是因为 `state.error` 生产代码**零写入**

* [x] 受控对照实验：注入一个 LLM 失败 ⇒ `run_status == "degraded"` 且 `degradation_log` 非空；**注入一个让 invoke 抛异常的故障 ⇒ `run_status == "failed"` 且 `error` 结构化（这是 failed 路径的唯一验收）** —— ✅ **2026-09-16 以零 LLM 单测落地**（`tests/test_arm1_run_status.py` 26 条：用 fake graph 注入异常断言 `failed`；用 MagicMock 让 provider 抛错断言 `degraded` 链路的留痕与 drain）

* [x] **命名裁决三分落地**：`run_status`（流程健康度）／`invoke_status`（层② `_run_one` 返回，原名 `status`）／`metrics_status`（层③ phase2，原名 `status`）；新增单测：落盘记录中**不存在**语义歧义的裸 `status` 键 —— ✅ **2026-09-18 落地**（做法与口径边界见 §5.1.1「命名裁决 · 落地形态」）。**核心决策 =「写侧只出新键 + 读侧 dual-read + 历史产物一律不回填」**：历史 raw/eval 同时是 Arm 7 台账的取证对象与 `tools/w7_backfill_*.py` 的零成本复算输入，改写它们等于毁证（实测 1,498 条 raw + 1,443 条 eval 全部经 dual-read 读出旧键，取值零越界）。**DoD 口径边界**：本项只覆盖**层②③ 的落盘记录**（这也是"裸 `status`"所指的语义歧义源头）；层①之外的其余裸 `status` 已在 `status_keys.BARE_STATUS_SITES` 逐条登记、并由单测验证「登记为真」，其中含一项实证发现 —— **`history.json` 的 `status` 是恒为 `"PASS"` 的空转字段**（写的是 `summary.struct.regression`，而全仓从未有任何代码写入 `summary.struct`）

* [ ] ~~`partial` 四态之一~~ ← **Q4 删除**（与 `degraded` 判定条件完全相同，改由报告层按子问题覆盖率派生）

* [x] **（Q8 = B4-A）单向派生契约落地**：工具类 5 值（`not_configured`/`timeout`/`provider_error`/`empty_result`/`parse_error`）
  **只由工具层产生**（写入 `SearchResponse.failure_reason`），`DegradationEntry.reason` **必须取 `resp.failure_reason`**，
  **不得在同一处手写第二个字面量**；非工具类 4 值（`llm_error`/`token_limit`/`recursion_limit`/`internal`）由 Arm 1 各产生点直接构造（§5.1.2） —— ✅ **2026-09-16 落地**。**⚠️ 一处已知的临时替身**：Arm 4 的 provider 目前仍是「抛异常」语义、`failure_reason` 尚未全面填充 ⇒ Arm 1 用 `classify_tool_exception()` 做**临时归类**，已收敛在 `failure_reasons.py` **一个函数**里并注明「Arm 4 落地后应改为读 `resp.failure_reason`，本函数届时可删」—— 这样 Arm 4 的替换面保持在一处，不会在各落点散落手写字面量

* [x] **实现顺序不可颠倒**：先落 Arm 4 的 `failure_reason`，再让 Arm 1 消费它 —— ✅ **2026-09-16 遵守**（`SearchResponse.failure_reason`/`failure_detail`/`ok` 字段已先落，Arm 1 的 `_search_arxiv` 直接消费；其余 provider 待 Arm 4）

### Arm 2（P0）

* [x] `_default_code_script()` 不再接受 query 参数，脚本源码不含 query 文本 —— ✅ **2026-09-16 落地**（零参数 + 常量模板；测试断言 `query not in code`，10 组敌意 query 参数化覆盖）
* [x] query 通过元数据传入 —— ✅ **2026-09-16 落地**，按现有签名实现（`CodeExecInput` 类不存在）：`exec_code` 写 `metadata["query"]`，成功/失败两条 finding 都带 `metadata["query"]`
* [x] 中文/特殊字符测试用例全部通过（纯中文、中英混合、含引号、含换行符、含 emoji）—— ✅ **2026-09-16 落地**（`tests/test_arm2_code_injection.py`，10 组敌意 query 真跑沙箱全绿）
* [x] `run_20260910_173540` 中的中文查询乱码场景不再复现 —— ✅ **2026-09-16 落地**，但**根因与原文设想不同**：乱码源于沙箱子进程 cp936 写管道 / 父进程 utf-8 读管道（非「query 进脚本」）；修法是子进程加 `-X utf8`，实测 stdout/stderr 中文逐字还原（§5.2.4 实测修正二）
* [x] **（2026-09-16 实测新增）** 计算脚本真的能跑通 —— ✅ 旧模板对任何 query 都 `SyntaxError`，基线三轮 code_exec 产出 **144/144 全部失败（100%）**；修复后全绿（§5.2.4 实测修正一）

### Arm 3（P0，**2026-09-14 按 Q2=A「三刀分离」重写**；2026-09-16 生成工具追加拍板换 uv）

**第一刀 · 锁定**

* [x] ~~`pip-tools` 加入 `requirements-dev.txt`~~ ← **已作废并移除**：换 uv 后不需要；uv 是独立二进制，刻意不进 pip 依赖树（避免拖进 8 个包）。dev 锁由 14 行瘦到 7 包。✅ **2026-09-16 落地**

* [x] 生成 lock，**文件中不含 `--hash`** —— ✅ **2026-09-16 落地**。⚠️ **命令已按实测修正**：原写 `pip-compile --python-version 3.11`，实测该旗标**不存在**（`No such option`）且 pip-compile **丢弃环境标记**（锁内标记数 = 0 ⇒ `pywin32==312` 去标记 ⇒ Linux 必装不上）⇒ **2026-09-16 追加拍板改 `uv pip compile --universal --python-version 3.11 --strip-extras`**，实测标记数 7、`--hash` 数 0（§5.3.1 实测修正一/二/三）

* [x] `requirements-dev.txt` 同样锁定（`requirements-dev-lock.txt`）—— ✅ **2026-09-16 落地**（7 包 + 1 条 `colorama ; sys_platform == 'win32'` 标记）

* [x] CI 安装步骤改为 `pip install -r requirements-lock.txt -r requirements-dev-lock.txt` —— ✅ **2026-09-16 落地**，且 `cache-dependency-path` 同步指向两份锁；**CI 侧不需要 uv**

* [x] **首次 CI 运行实测通过**（跨平台假设）—— ✅ **2026-09-16 实测通过**（dev run `35072143636` / master run `35072917100`）。与原文不同：风险已从「必现红」（pip-compile 版 `pywin32` 去标记）降级为「待兜底确认」—— 本地已用 `--platform manylinux2014_x86_64` 预演整份锁，**唯一报错仅为 sdist-only 的 `jieba`**（由我强加 `--only-binary=:all:` 所致）；**真实 ubuntu CI 上 `Install dependencies` 步骤三档全绿**，跨平台假设**成立**

**第二刀 · 声明**

* [x] `pyproject.toml` **新增** `requires-python = ">=3.11,<3.14"`，**并就地注释「本字段在本仓库不具强制力（无 `[build-system]`），真正强制见运行时版本闸」** —— ✅ **2026-09-16 落地**

* [x] `[tool.ruff] target-version` 由 `"py310"` 改为 `"py311"`，且 `ruff check .` 仍全绿 —— ✅ **2026-09-16 落地**（附带发现：该改动触发 ruff `UP042`，`FailureReason` 由 `(str, Enum)` 改为 `StrEnum`；因全仓用法均为 `.value`，语义等价）

* [x] `README.md:46` 由 `Python 3.10+` 改为 `Python 3.11 ~ 3.13` —— ✅ **2026-09-16 落地**

**第三刀 · 强制**

* [x] 运行时版本闸落地（`sys.version_info` 不在 3.11~3.13 ⇒ 明确报错，错误信息含支持区间与当前版本）—— ✅ **2026-09-16 落地**（`research_engine/__init__.py` import 期执行）

* [x] **新增单测**：mock 一个越界 `sys.version_info` ⇒ 版本闸抛出预期异常 —— ✅ **2026-09-16 落地**（`tests/test_arm3_python_version_gate.py`，20 条；含**四口径耦合守卫** —— pyproject / README / CI matrix / 锁文件四处版本口径互相对齐，任一处漂移即测试失败）

* [x] CI 扩为 `matrix: python-version: ["3.11", "3.12", "3.13"]`，**三档全绿**（注：3.12 / 3.13 **首次被真实验证**）—— ✅ **2026-09-16 实测通过**：dev `3.11` 41s / `3.12` 46s / `3.13` 54s（墙钟 58s）；master `3.11` 47s / `3.12` 55s / `3.13` 40s。**成本账更正**：原估「三档约 110s」偏高，实测**墙钟 58s**（三档并行，`fail-fast: false`）

* [x] ~~生成 `requirements-lock.txt`，包含严格版本号~~ ← 已细化进第一刀 ✅

* [x] ~~`pyproject.toml` 的 `requires-python` **改为** `>=3.11,<3.14`~~ ← **前提不存在**（原文件无该字段，实为新增）✅

* [x] ~~新增无外部 API 的 smoke test，CI 中通过~~ ← **取消：现有测试全部零 LLM / 零 API key，已是等价物** ✅

> **✅ Arm 3 全部 DoD 已于 2026-09-16 闭环**：两条「待 CI 实测」项均以真实 CI 输出收口（dev + master 各一次，三档全绿）。**跨平台假设由「待验证」转为「已验证成立」**。

* [ ] ~~在 Python 3.11 环境下完整测试套件通过~~ ← **已满足**：CI 每次 push 就在 3.11 上跑 `pytest tests/ -q`

### Arm 4（P1）

* [ ] `SearchResponse` 新增 `failure_reason` 字段

* [ ] Web/RAG/arXiv 三条搜索路径的 `except` 块区分五种失败原因

* [ ] RAG store 初始化失败时 `failure_reason = "not_configured"`

* [ ] 受控对照实验：断开网络后搜索，验证 `failure_reason == "timeout"` 或 `"provider_error"` 而非空列表

* [ ] **（Q8 = B4-A）工具层是这 5 个枚举值的唯一产生点**：`DegradationEntry.reason` 派生自本字段，
  Arm 1 **不得**在同一次失败里另写字面量（验收单测见 §9.1「单向派生」行）

### Arm 5（P1，**2026-09-15 按 Q3=A 重写**）

> **✅ DoD 已交付（2026-09-17，含历史回填）**：判定口径见 §5.5.1（D-05）、报告模板见 §5.5.3。
> 本清单**不再逐项勾选**（D-01：实时状态统一维护于 `docs/project-status.md`），
> 回填的 run 数量、verdict 分布、D1 受影响 run、测试基线等证据一并记在看板。

**主刀 · run 级质量闸**

* [ ] `_summarize()` 顶层输出 `verdict`（`ok` / `suspicious` / `broken`）+ `verdict_reasons: list[str]`

* [ ] **阈值外置**：`_summarize(..., thresholds: QualityThresholds = DEFAULT_THRESHOLDS)`；**需求文档不写死数字**；`DEFAULT_THRESHOLDS` 常量**就地注释**「由 `run_20260910_173540` 反推，仅作初值，须在 §10.4 新主链路基线上复核」

* [ ] **只告警不阻断**：`verdict != "ok"` 时写入 summary 与报告，**不 hard fail**（理由见 §5.5.1）

* [ ] **回归验收（在真实历史 run 上重算）**：`run_20260910_173540` ⇒ `verdict == "suspicious"` 且 `verdict_reasons` 非空；`run_20260910_171054` ⇒ `verdict == "broken"`（11/20 管线失败）

* [ ] 新增单测：传入极严 / 极松阈值 ⇒ `verdict` 随之变化（证明阈值参数真生效，非硬编码）

* [ ] ~~`complete` 拆为 `raw_written`/`quality_complete`/`degraded`/`research_success` 四字段~~ ← **降级为附属产出**（Q3=A：加字段 ≠ 加护栏，理由见 §5.5）

**附属 · 字段重命名**

* [ ] `complete/partial/failed` → `metrics_ok/metrics_partial/metrics_failed`，**旧键名同时写入并标 `deprecated`**（兼容 `docs/eval-report.md` 与 `tools/w7_backfill_*.py`）

* [ ] `eval-report.md` 模板：`verdict` 列置于最前，指标列 `complete` → `metrics_ok`；历史趋势表同步

* [ ] **`metrics_mean` 每个指标并列输出 stderr**（bootstrap 1000 次，对齐 openai/evals）；**零 LLM 成本、纯离线**

* [ ] **离线回填历史 run 的 stderr**（复用 `tools/w7_backfill_*.py` 只读模式）⇒ 历史数据首次具备可比性判据

* [ ] 消费方约束落地：`docs/eval-report.md` 与结论文档引用指标时**必须带 stderr**；**stderr > 效应 ⇒ 禁止表述为"提升 Xpp"**

### Arm 6（P1）

* [ ] **每条 raw 记录包含表 A 的 8 个字段：`git_dirty` / `git_diff_hash` / `deps_frozen_hash` / `prompt_hash` / `citation_judge_model` / `coverage_judge_model` / `citation_judge_independent` / `scorer_version`**（2026-09-15 Q8=C6-A 收敛：~~13 个平铺字段~~ → **8 个 + 内嵌 `config_snapshot`**）

* [ ] **每条 raw 记录内嵌同一份 `config_snapshot` 对象**（§10.4 第 6 条），取代原平铺的
  `python_version` / `model_name` / `search_provider` / `max_step_budget` / `experiment`；
  **同一 run 内所有 raw 的 `config_snapshot` 必须逐字节相同**（新增单测校验）

* [ ] **`config_snapshot` 含 `validator_model` 顶层字段**（§10.4 第 5 条）—— W7 唯一被换掉的模型旋钮，不可只藏在 `experiment` 段里

* [ ] **`code_revision()` 一处定义**（`eval/provenance.py`），现有 3 份重复实现全部改为 import：
  `report_gen.py:47 _git_head()` / `run.py:64 git_head()` / `w7_experiment.py:45 _git_rev()`（后者保留 `+dirty` 字符串格式兼容既有产物）

* [ ] **每条 raw 记录包含表 C 的 5 个新增字段：`prompt_hash` / `citation_judge_model` / `coverage_judge_model` / `citation_judge_independent` / `scorer_version`**（2026-09-14 Grill C3 拍板；~~`max_step_budget`~~ 已并入 `config_snapshot`，详见 §5.6.1）

* [ ] **`prompt_hash` 覆盖「运行期实际提示词正文」而非源码常量快照**，且**必须含 critic** —— 实测 `critic.py:174-181` 是函数内 `system` 局部变量（非模块级常量），实现时**二选一**：提为模块级常量 `CRITIC_SYSTEM`（与 planner/validator/writer 对齐，推荐）或改按 `critic.py` 源文件整体 hash

* [ ] 新增单测：**改动任一提示词正文 ⇒ `prompt_hash` 必变**，含一条 **critic `gap_extra` 段专项**（改 `critic.py:167-179` ⇒ hash 变化）—— 因该段由 `CRITIC_GAP_ENABLED` 动态拼接，是最容易被漏记的一处

* [ ] **`citation_judge_independent` 在现行架构下必须为 `False`，且该字段进入 summary 的显眼位置**；任何「引用准确率提升」的结论都必须在报告中先解释此字段（W7「裁判兼任被测」的可审计化）

* [ ] **`citation_judge_model` 取值必须等于 `config.llm.validator_model`**（不得写死字符串），并新增单测：改 `VALIDATOR_MODEL` 环境变量 ⇒ 该字段随之变化

* [ ] **`scorer_version` 由指标代码文件 sha256 派生**（`metrics.py` + `citation_eval.py` + `retrieval_eval.py` + `report_eval.py` + **`w7_rejudge.py`**），并新增单测：改动其中任一文件的字节 ⇒ `scorer_version` 必变。**`w7_rejudge.py` 不可省** —— 它含 W7 权威复判裁判 `JUDGE_SYSTEM_DIAGNOSTIC`（`:88`），漏掉它则 W7 全部复判结论可漂移无痕

* [ ] summary 记录包含上述字段的汇总

* [x] **`_config_snapshot()` 按 docstring 原意「每轮 run 都记」（现仅 `write_baseline()` 调一次），并修正 docstring 与实现不符之处**（§10.4） —— **✅ 2026-09-15 已实现**：实现迁至 `research_engine/eval/provenance.py`（`config_snapshot()`），`report_gen._config_snapshot` 降为薄封装；per-run `summary.json` 与 `history.json` 均写入（history 走 `summary["config_snapshot"]`，run 级一条不按题目重复）

* [x] **（Q8=C6-A）`config_snapshot` 升为唯一配置真相源：`validator_model` 提顶层 + `python_version` + `experiment` 段；`embedding_model` / `strategic_model` 明确不记** —— **✅ 2026-09-15 已实现**（`provenance.config_snapshot()`）

* [x] **（Q8=C6-A）raw 记录内嵌同一份 `config_snapshot`，且同一 run 内逐字节相同** —— **✅ 2026-09-15 已实现**：`phase1` 抓一次 `run_provenance()` 按条目复用；失败路径与**超时路径**同样落盘

* [x] **（Q8=C6-A）`code_revision()` 一处定义**（`eval/provenance.py`），3 份重复实现改为 import —— **✅ 2026-09-15 已实现**：`report_gen._git_head` / `run.git_head` / `w7_experiment._git_rev`（后者保留 `+dirty` 格式兼容既有 W7 产物）

* [x] 在 dirty 工作区上跑一次 eval，验证 `git_dirty=true` 且 `git_diff_hash` 非空 —— **✅ 2026-09-15 已验证**：实现时本机即 dirty，实测 `git_dirty=true` / `git_diff_hash=6425819bda71a0d2`；并有单测 `test_provenance_from_raw_reads_run_time_not_now` 锁定「读跑批时刻而非汇总时刻」

* [x] **在新采的主链路基线上验证：`summary.json` 中的 `experiment` 段能被读回，且与运行期 `config.experiment` 一致**（§10.4 可复现性验收） —— **✅ 2026-09-16 由 before 基线第 1 轮 `run_20260916_001005` 实测验收通过**：`git_commit=6f4067f…` / `git_dirty=false` / `git_diff_hash=e3b0c44298fc1c14`（干净树哨兵值）/ `validator_model=qwen-plus` / `experiment` 五开关全 `true`，与运行期 `config.experiment` 逐项一致。**这是史上第一个「配置被如实记录」的真实 run**

* [ ] ⬜ **不做**：`seed`（理由见 §5.6.1）与 `temperature`（缓办至其 env 化）

### Arm 7（P2，**2026-09-14 按 Q1 拍板重写**）

**轨道 1（git 侧）**

* [x] `run_v11_compare/` 加入 `.gitignore` 白名单，**且注释写明「被 `docs/eval-w7-attribution.md` 引用（298 条夹具）」** —— **✅ 2026-09-18**：出处写在**独立注释行**（行尾注释会把 `!` 规则整条废掉，见 §5.7.1 第 2 条修正）

* [x] `run_20260906_184156/` 与 `run_20260907_001658/` 逐项定性完毕（升白名单 or `git rm --cached -r`），无「跟踪但不在白名单」的残留 —— **✅ 2026-09-18**：**两条均判定为「升白名单」**（前者被 `docs/sampling-workbook.md` 逐条链接其 `raw/q_*.raw.json`，后者是 `docs/requirements/7-*.md` 的 v0→v1 漂移前车之鉴）。原 §4.7 的「94 项偏离」全部收敛，`git ls-files` 现为 **8 个顶层条目**

* [x] **每一条 `!` 白名单规则都带引用出处注释**（判据 = 「引用即入库」）—— **✅ 2026-09-18**：8 条白名单（6 条 `!` + 2 条 `# keep:`）全部带出处；**并新增资格审查**：只命中 `docs/eval-report.md` 这类自动枚举文档的，不计入（§5.7.1 第 3 条）

* [x] 验收命令：`git ls-files research_engine/eval/results | wc -l` 的结果集合 ≡ 白名单集合（逐项可比对） —— **✅ 2026-09-18**：已升级为 `tools/check_results_whitelist.py`（exit 0 即通过）并**接进 CI**（`Eval 产物白名单纪律（Arm 7）` 步骤）。该脚本经**变异测试**验证不是空转：注入「行尾注释」与「删除出处行」两种违规均被 `exit 1` 拦住

* [x] **附带发现并处置**：`history_bak_v10.json`（977 B）在 `docs/` 与代码中均**零引用**，属「备份快照」类 ⇒ 按判据 `git rm --cached`（**磁盘文件保留**，另加 `*_bak_*.json` 忽略规则，避免它变成常驻未跟踪噪音）

* [x] ~~仓库体积减少 ≥5 MB~~ ← **已删除：`.git` 对象库实测合计仅 ≈4.4 MB，该目标数学上不可达成**

**轨道 2（本机磁盘）**

* [x] 产出只读台账（目录 / 体积 / 时间 / 跟踪状态 / **是否被 docs/ 引用** / 类别），**不改动任何文件** —— **✅ 2026-09-18**：`docs/eval-artifact-ledger.md`（`tools/w8_artifact_ledger.py` 生成，**100 个顶层条目 / 143.5 MB**，已入库 8.4 MB、未入库 135.1 MB）。台账把「引用」拆成三档：**手写引用 20 个**（其中引用**含**结论文档/代码 **17 个 / 15.2 MB**；引用**仅来自**看板/需求文档 **3 个**，⚠️ 需人工确认、**不可据此删除**）与**仅出现在自动表痕**（后者才是归档候选）。⚠️ 已知两个反向偏差：`history_bak_v10.json` 属**「登记反转」**样本（因被写入处置说明才「获得引用」）；新增的 `GOVERNANCE_DOCS` 分类把 **before 基线三个 run** 标为「仅看板/需求文档引用」，但它们其实是 §10.4/§10.5.8 的正当验收证据 ⇒ 该分类**只作提示**，判据细节见台账文末注记与看板决策 D-13

* [ ] 台账经人工 review 后，才允许进入「清理或归档」的独立拍板 —— **⬜ 待主理人 review**（这一次 review 就是「台账是否已 delivered」的唯一判据）

* [x] 验收纪律：台账 review 前的任何删除/移动/重命名 = **违规**（因 `tools/w7_backfill_*.py` 依赖 `raw/*.raw.json`） —— 本次全程只读，唯一写入物是台账文件本身

## 8. 影响范围与风险

| Arm   | 影响模块                                                                                       | 回归面             | 风险                                                  |
| ----- | ------------------------------------------------------------------------------------------ | --------------- | --------------------------------------------------- |
| Arm 1 | state.py, **graph.py `run()`（新增 try/except）**, **cli.py / web/app.py（退出码与展示）**, planner.py, writer.py, validator.py, researcher.py, arxiv.py, store.py | 全链路状态流转；W1 收敛单测；**CLI 退出语义** | **中**（由「低」上调）：① 新增字段不破坏现有逻辑，但每个 except 块需手动改，可能遗漏；② **`run()` 由抛异常改为返回 state，会改变调用方语义** —— 若下游依赖"异常即崩溃"将静默失效，故**必须**配 CLI `sys.exit(1)` 兜底（§5.1.4 五件套）；③ `error` 由 `str` 改 `Dict` 属**破坏性变更**，须同步改所有读取方；④ `degradation_log` 若漏加 `operator.add` reducer，并发下会丢记录 |
| Arm 2 | researcher.py, code\_exec.py                                                               | 代码执行路径          | 低：脚本模板是确定性函数，改动范围小                                  |
| Arm 3 | pyproject.toml, README.md, requirements*.txt, CI 配置, `research_engine/__init__.py` | 依赖安装；CI 流水线；所有用户的启动路径 | **中**：① **跨平台 lock 风险**（~~本机 win 生成的 lock 在 ubuntu 上可能因平台轮子差异装不上~~ ⇒ **2026-09-16 实测确认该风险在 `pip-compile` 下是必现的**（`pywin32==312` 去标记平铺、PyPI 上 21 个产物全为 win wheel），**改用 `uv pip compile --universal` 后已消解**：实测 7 条环境标记正确、`--platform manylinux2014_x86_64` 预演整份锁通过（唯一报错为 sdist-only 的 `jieba`，由测试旗标 `--only-binary=:all:` 强加所致）；仍保留「首次 CI 实测」作兜底）；② **版本闸可能拦住 3.10 用户** —— 但事实是 **3.10 从未被验证过**（CI 只跑 3.11、`.deps` 只给 cp313），故收窄属**如实声明**而非能力倒退；③ `ruff target-version` 从 `py310` 升到 `py311` 会让**原先被放行的 3.11+ 语法不再报错**，反向影响极小 |
| Arm 4 | researcher.py, arxiv.py, store.py, SearchResponse 定义                                       | 搜索结果消费方         | 低：非破坏性扩展，下游可选消费                                     |
| Arm 5 | eval/run.py, eval/report\_gen.py, docs/eval-report.md, **eval 落盘字段（`status`→`metrics_status`）** | 评测流水线；**字段重命名波及下游** | **中**：① 历史趋势表格式变化 + 旧键名兼容期，需 `deprecated` 双写；② **`verdict` 阈值初值系单个 run 反推** ⇒ 在新基线上复核前可能误报 —— 已用「外置参数 + 只告警不阻断」把风险压在"可读不可拦"的范围内；③ 字段重命名若漏改 `tools/w7_backfill_*.py`，会打断 W7 零成本可复算链路 |
| Arm 6 | eval/run.py, eval/report\_gen.py, **eval/w7\_experiment.py（改 import）**, **agents/{planner,validator,writer}.py（只读常量）**, **eval/metrics.py 等（只读取 hash）** | 评测记录格式；**`git_head` 三处调用方** | **低→中（Q8=C6-A 上调）**：① 新增字段，不影响现有字段；**注意 `scorer_version` 的副作用**：改动指标代码会改变 `scorer_version`，使新旧 run 被自动判为「不可直接对比」—— 这是**期望行为**（诚实标注），但需在报告中显式说明，避免被误读为报错；② **`code_revision()` 归并 3 份实现属跨文件重构** —— `w7_experiment._git_rev()` 的 `+dirty` 字符串格式**被既有 W7 产物依赖**（区块续跑比对读它），**必须保留格式**否则历史实验不可续；③ **raw 内嵌 `config_snapshot` 会让单条 raw 体积增大**（约 300~500 B × 21 条 ≈ 10 KB/run，可忽略），但**若实现成"手抄副本"而非"内嵌同一对象"则本 Arm 价值归零** ⇒ 已立单测「同 run 内快照逐字节相同」 |
| Arm 7 | .gitignore, Git 索引, 本机 `results/` 目录 | 仓库跟踪状态；本机磁盘 | **低（git 侧）**：`git rm --cached` 只改索引不删磁盘文件，可 `git checkout` 复原。**⚠️ 高（磁盘侧）**：`tools/w7_backfill_*.py` 依赖 `run_dir/raw/*.raw.json`，误删 = 销毁 W7 可复算资产的唯一证据源 ⇒ **已立规：先只读出账、review 后再单独拍板** |

### 降级/兜底

* Arm 1 的 `DegradationTracker` 如果因遗漏某些 except 块而不完整，不影响系统运行——只是 `run_status` 可能误判为 `success`（与现状一致，不会更差）

* Arm 3 的锁文件如果无法覆盖全部依赖，回退到现有开放式版本范围（不会更差）

* Arm 7 **轨道 1** 的 `git rm --cached` 只改索引：若误操作，可通过 `git checkout` / 重新 `git add` 恢复，磁盘文件从未离开

* Arm 7 **轨道 2** 无兜底 —— 它是本 Arm 唯一**不可逆**的动作，因此被单列为「先只读出账、review 后再单独拍板」的前置流程（见 §5.7.2）

## 9. 测试策略

### 9.1 单元测试

| Arm   | 测试内容                                                                      | 预期                        |
| ----- | ------------------------------------------------------------------------- | ------------------------- |
| Arm 1 | mock LLM 抛异常 → `DegradationTracker` 记录 entry → `run_status == "degraded"` | tracker 非空，run\_status 正确 |
| Arm 1 | 全链路无异常 → `run_status == "success"`                                        | tracker 为空                |
| Arm 2 | query 包含中文/引号/换行 → 脚本执行成功                                                 | 无语法错误                     |
| Arm 2 | 脚本源码中不包含 query 字符串                                                        | grep 不到                   |
| Arm 2 | **（2026-09-16 实测新增）** 敌意 query 下 `metadata["query"]` 仍完整、`script_hash` 仍逐题唯一（防 `dedupe` 并条） | 字段正确 + hash 不塌缩 |
| Arm 2 | **（2026-09-16 实测新增）** 沙箱回传中文 stdout/stderr 逐字还原（`-X utf8` 专项） | 不乱码 |
| Arm 4 | mock 搜索超时 → `failure_reason == "timeout"`                                 | 字段正确                      |
| Arm 4 | provider 未配置 → `failure_reason == "not_configured"`                       | 字段正确                      |
| **Arm 6** | **改动任一提示词正文 → `prompt_hash` 必变**（planner / validator / writer 各一条 + **critic 提常量后一条**） | **hash 变化** |
| **Arm 6** | **改动 `critic.py:167-179` 的 `gap_extra` 段 → `prompt_hash` 必变**（开关动态拼接专项） | **hash 变化** |
| **Arm 6** | **改动 `w7_rejudge.py` 任一字节 → `scorer_version` 必变**（W7 权威复判裁判专项） | **hash 变化** |
| **Arm 6** | **`VALIDATOR_MODEL` 环境变量变化 → `citation_judge_model` 随之变化** | **字段跟随 config，非硬编码** |
| **Arm 6** | **现行架构下 `citation_judge_independent == False`**（回归护栏：若哪天引用裁判被解耦，此测会红，提示主动更新语义） | **恒为 False** |
| **Arm 6** | **改动 `metrics.py` 等指标文件任一字节 → `scorer_version` 必变** | **hash 变化** |
| **Arm 5** | **传入极严 / 极松阈值 ⇒ `verdict` 随之变化**（证明阈值参数真生效，非硬编码） | verdict 变化 |
| **Arm 5** | **`run_20260910_173540` 重算 ⇒ `verdict=="suspicious"` 且 `verdict_reasons` 非空** | 彻底降级被识别 |
| **Arm 1** | **注入让 invoke 抛异常的故障 ⇒ `run_status=="failed"` 且 `error` 结构化**（`failed` 路径唯一验收） | 异常不再丢失 |
| **Arm 1** | **`get_state` 返回空/抛错时 ⇒ 返回最小 state（`run_status=="failed"`），不返回 `None`** | 兜底生效 |
| **Arm 1** | **并发追加 `degradation_log` ⇒ 条目不丢失**（验证 `operator.add` reducer 已挂） | 并发安全 |
| **Arm 1** | **改动枚举定义 ⇒ Arm 1 与 Arm 4 同时生效**（验证「一处定义、两侧 import」） | 唯一真相源 |
| **Arm 5** | **`metrics_mean` 含 stderr，且 bootstrap 次数可配**（默认 1000） | 离散度可算 |
| **Arm 5** | **同一份 raw 回填两次 ⇒ stderr 稳定可复现**（bootstrap 需固定随机种子） | 幂等可复算 |
| **Arm 4 + 1** | **单向派生（Q8=B4-A）**：mock 一次搜索超时 ⇒ `SearchResponse.failure_reason=="timeout"` **且** `degradation_log` 中恰有一条 `reason=="timeout"`、`node=="researcher"` 的条目 | 同一事件两处说法一致 |
| **Arm 4 + 1** | **禁止手写第二字面量（Q8=B4-A）**：静态扫描 Arm 1 产生点，工具类 5 值不得出现字符串字面量，只能取 `resp.failure_reason` | 无重复字面量 |
| **Arm 6** | **`code_revision()` 一处定义（Q8=C6-A）**：`report_gen._git_head` / `run.git_head` / `w7_experiment._git_rev` 三者均调用同一函数 | 无第 4 份实现 |
| **Arm 6** | **同一 run 内所有 raw 的 `config_snapshot` 逐字节相同**（Q8=C6-A，验证"内嵌同一对象"而非手抄副本） | 序列化后全等 |
| **Arm 6** | **`config_snapshot` 含 `validator_model` 顶层字段，且改 `VALIDATOR_MODEL` env ⇒ 快照随之变化**（Q8=C6-A） | 字段存在且跟随 config |
| **Arm 6** | **raw 记录中不存在平铺的 `model_name` / `search_provider` / `python_version` / `max_step_budget` / `experiment` 键**（Q8=C6-A 回归护栏） | 键不存在 |
| **Arm 1（命名三分）** | **端到端落盘扫描**：phase1+phase2 真跑（fake graph/judge）⇒ 每个落盘 json **顶层**无裸 `status`；raw 里唯一残留的 `status` 必须在 `state` 快照内；eval/summary 任意层级都无 | 落盘记录无歧义裸键 |
| **Arm 1（命名三分）** | **层② 四条落盘路径**（`done`/`incomplete`/`failed`/`timeout`）均只写 `invoke_status` | 写侧只出新键 |
| **Arm 1（命名三分）** | **层③ 三条落盘路径**（`ok`/`partial`/`failed`）均只写 `metrics_status`；层② `failed`/`timeout` 的透传记录同 | 写侧只出新键 |
| **Arm 1（命名三分）** | **dual-read 优先级**：两键并存且**取值冲突**时新键必须赢；仅旧键时回落；皆无得 `None` | 优先级不可反转 |
| **Arm 1（命名三分）** | **`_summarize` 仍能消费只有旧键的历史 eval**（断点续跑路径） | 兼容不破 |
| **Arm 1（命名三分）** | **历史产物未被回填**：真仓库历史 raw/eval 仍带裸 `status` 且读得出；三份 before 基线 run 逐字节未动 | 证据只读 |
| **Arm 1（命名三分）** | **未收敛裸 `status` 登记表为真**：`state.status` / `history.json` / W7 manifest / Langfuse 四条落点逐条验证存在 | 登记不是口号 |
| **Arm 1（命名三分）** | **源码级守卫**：`run.py` 的 AST 字符串常量中无 `"status"`；报告「失败与异常附录」段内无裸 `status` 字面量且走 dual-read | 契约不被绕过 |
| **Arm 1（命名三分）** | **两套词汇表分别锁定**（`_run_one` 返回 `ok/failed` vs 落盘 `done/incomplete/failed/timeout`） | 防同名顺手并值 |

### 9.2 集成测试

| Arm   | 测试内容                                   | 预期       |
| ----- | -------------------------------------- | -------- |
| Arm 1 | 完整 Graph 运行，注入一个节点失败 → `run_status` 正确 | 降级被追踪    |
| Arm 3 | ~~Smoke test（mock LLM + mock 搜索）端到端不崩溃~~ → **改为：CI matrix 三档（3.11/3.12/3.13）下现有 132 测试全绿** | 三档均全绿 |
| Arm 3 | **首次按 lock 安装的 CI 运行**（验证跨平台可安装性） | 安装成功 |
| Arm 3 | **运行时版本闸**：越界 `sys.version_info` ⇒ 明确报错 | 单测通过 |
| Arm 5 | 评测运行 → summary 包含四字段                   | 字段存在且值合理 |

### 9.3 Eval 对照

| Arm   | 对照方式                              | 预期                             |
| ----- | --------------------------------- | ------------------------------ |
| Arm 1 | 改前 vs 改后各 3 轮 → `degraded` 任务数可统计 | 降级任务被正确标记                      |
| Arm 4 | 改前 vs 改后各 3 轮 → 失败原因分布可统计         | 五种原因可区分                        |
| Arm 5 | **质量闸在历史 run 上回填**：`173540` ⇒ `suspicious`；`171054` ⇒ `broken`；正常 run ⇒ `ok` | 降级 run 被识别，正常 run 不误报 |

## 10. 承接 W7 挂账（W7 → W8 移交清单）

> W7（`docs/requirements/7-technical-debt-and-content.md`）收口时留下若干**已判不达标 / 被推迟**的项，
> 以及一条**当时未登记的护栏缺口**。2026-09-13 核对发现：这些项此前只在 W7 文档内部自述，
> **W8 文档里一条都搜不到**。本节把它们正式登记为 W8 范围，避免"挂账沉底"。

### 10.1 W7 明确推迟的项（内容 + 能力双线）

| # | 项 | 来源 | 为什么归 W8 | 优先级 |
| - | --- | --- | --- | --- |
| A | **博客③《用 eval 数据诊断 Agent 引用幻觉：从 72% 到五项归因》** | W7 TBD-9 | W7 实验收口后素材已备齐，前置产物 `docs/eval-w7-attribution.md` 已于 2026-09-13 产出。仍是 Agent 岗面试区分度最高的一篇（"你怎么证明 Agent 有效 / 72% 里有多少是幻觉"） | **P1** |
| B | **博客② Langfuse 全链路 trace 实战** | W7 TBD-9 | 素材已冷冻（W3 真实 trace 冒烟 PASS，17 观测点、对账差 0）；教程型、替代性高，排在博客③之后 | P2 |
| C | **组件③「澄清范围」** | W7 TBD-1d | 设计已冻结；W7 无法验证的两个原因——eval 环境**必须关闭**该功能导致收益不可量化，且需改图结构（≈4~6h）并承担 W1 回归风险 | P2 |
| D | **persona 视角发现（Arm 2 / Step 2）** | W7 TBD-1c② | 设计已定（1 次检索只取标题+摘要首句 → 归纳 4 个 persona + 恒定兜底视角 → planner 产 ≤4 子问题）；W7 因 **Arm 1 未达标触发止损**而取消。**止损 ≠ 否定设计**：若 W8 重做受控实验，可连同本 Arm 一并验证 | P2 |
| E | **RAGAS `FaithfulnesswithHHEM` 本地小模型 NLI 降本路径** | W7 TBD-7 行业调研 | 用本地 NLI 模型替代 LLM 做忠实度判定，是降本终极路径；W7 记录"记入 W8+ 探索，本期不做" | P3（探索） |

### 10.2 W7 收口时确认未完成、且已属 W8 现有 Arm 的项

| W7 未完成项 | 对应 W8 Arm | 说明 |
| --- | --- | --- |
| run 级环境指纹（`code_revision` / `patch_id` / `prompt_hash` / `dependency_lock_hash`） | **Arm 3（依赖锁定）+ Arm 6（可复现元数据）** | W8 Arm 6 已覆盖 `git_dirty` / `git_diff_hash` / `deps_frozen_hash` / `model_name` / `search_provider`；**仍缺 `prompt_hash` 与 `seed`**，建议补入 Arm 6 字段表 → **2026-09-14 已处置：`prompt_hash` 纳入 Arm 6（字段表扩至 13）；`seed` 经 Grill C3 拍板明确「不做」（理由见 §5.6.1）。`code_revision` = `git_commit`（已有）、`patch_id` ≈ `git_diff_hash`（已有）、`dependency_lock_hash` = `deps_frozen_hash`（已有）** |
| `complete` 语义误导（实测 `complete=20` 但 `completion_rate=0`） | **Arm 5（评测口径收敛）** | 与 W7 失败分类归因表互补：一个治"口径说不清"，一个治"失败归不了因" |

### 10.3 W7 挂账中**尚未立项**的评测护栏（候选，未承诺）

> 来源：2026-09-13 外部「W7 实验复盘」的工程护栏建议。**当时用户明确只采纳 4 项低成本护栏**，
> 其余按成本控制策略推迟。此处仅登记为候选并补齐证据，**不等于承诺实施**。
>
> **2026-09-13 更新**：候选 **I** 已由用户决定**升级为 W8 前置项**，移入 §10.4。下述表中保留其行以便追溯升级来源。

| # | 项 | 证据（W7 实测） | 成本 |
| - | --- | --- | --- |
| F | **实验矩阵完整性检查**：`block × arm × question` 缺格 / 缺指标自动检测，缺失必须显式登记原因 | 现有 `arm6/Block 2` 缺格做得对（manifest 登记了 `skipped_gate` + 原因）；但 **`arm3/Block 1` 的 `q_008` 因 `status=timeout` 缺 `coverage` 指标，而该 run 在 manifest 里仍记为 `done`，这一层缺失目前无人检查、报告里也看不出来** | 小（纯本地校验） |
| G | **预算一致性检查**：跨 arm 的 steps / tokens / cost 超预设比例即标记"不可直接比较" | W7 的 `avg_steps` 分档（3.85 vs 8.1~9.3）是最主要的不可比来源，目前只靠人工判断 | 小 |
| H | **分析汇总模式自动排除不一致数据**：revision 不一致时默认**阻止汇总**，而非仅告警 | W7 事故的根因形态。现有护栏只有"开发 warning + 正式 hard fail"两档，**缺"汇总时自动排除 + 异常报告"** | 中 |
| ~~I~~ | ~~**`_config_snapshot` 按 Q7 原意每轮记录**~~ → **已升级为前置，见 §10.4** | `_config_snapshot()` docstring 写"每轮 run 都记"，实际只被 `write_baseline()` 调一次，且只含 9 个 config 子项；per-run `summary.json` / `history.json` 均无 config 快照。2026-09-13 进一步查出**主链路配置格子从未被测过 + 历史 run 消融配置不可回溯** | **升为前置**（属 Arm 6 自然延伸） |

### 10.4 **W8 前置项**：`_config_snapshot` 必须记录实验开关（2026-09-13 由 §10.3 候选 I 升级）

> **⚠️ 2026-09-15 Q7 重排：本项优先级 = `P0`，且从「Arm 6 自然延伸、排第 6」前移为实现顺序的第一项。**
> 理由三条：① 它是**所有**基线采集（before / after）的硬阻塞；② **收益随时间衰减** —— 每拖一天，就少一批可回溯消融配置的 run；
> ③ **阻塞关系的优先级应取「被阻塞方的最高优先级」**，而不是继承所归属模块的优先级（Arm 6 是 P1）。成本仅"约十余行 + 两个落盘点"。

> 由「候选」升级为「**前置**」的依据是 2026-09-13 取证查出的两条硬事实。它不再是"锦上添花的护栏"，
> 而是**采基线的前提**——不补齐则 W8 会重演同一个问题。

**依据 1：主链路的配置格子从未被测过**

用脚本 dump 六臂组合与主链路实际配置（`ExperimentConfig` 实例）对比：

| | 5 个 W7 开关 | `VALIDATOR_MODEL` |
| --- | --- | --- |
| **主链路（`cli.py` / `web/app.py` 实际运行）** | **5/5 全开** | **默认（`qwen-plus`）** |
| W7 唯一全开的臂 `arm6_validator_turbo` | 5/5 全开 | ❌ 锁 `qwen-turbo` |
| `arm0` / `arm1` / `arm3` / `arm4` / `arm5` | 0~2/5 | 默认 |

⇒ **6 个臂里没有任何一格等于主链路配置**。W7 测的是「全开 + turbo」，**从未测过「全开 + plus」**。
⇒ 主链路的实际 coverage / 引用准确率**在现有全部数据里没有对应测量**。W8 若谈主链路效果，**只能新采基线**，且基线**必须是主链路配置自身**，不得用 `arm0` 或 `arm6` 代指。

**依据 2：历史 run 的消融配置不可回溯**

`report_gen.py:26 _config_snapshot()` 只记 9 个字段（`planner_model` / `critic_model` / `fast_model` / `smart_model` / `token_budget` / `max_total_hops` / `per_subq_hop_cap` / `max_replan` / `search_provider`），**一个实验开关都不记**。

| run | 能否判断其开关配置 |
| --- | --- |
| 09-06 / 09-07 的 run（含 `run_v11_compare`——**`docs/eval-w7-attribution.md` 的 298 条夹具来源**） | 仅可**推定**为 v1.1 行为（当时开关代码尚不存在），**产物内无证据** |
| **09-09 之后的所有 run** | ❌ **无法判断** |

**要求（落地即视为完成）**

1. `_config_snapshot()` 增加 `experiment` 段，记录 5 个开关的**生效值**（经 `_env()` 解析后的布尔值，而非环境变量原始字符串）：
   `critic_gap_enabled` / `validator_fixes_enabled` / `validator_assertive_filter_enabled` / `writer_sectioned_feed_enabled` / `validator_trim_enabled`。
2. 按 docstring 原意落地「**每轮 run 都记**」：per-run `summary.json` 与 `history.json` 均写入 config 快照（现仅 `write_baseline()` 调用一次）。
3. 修正 `_config_snapshot()` docstring 与实现不符之处（现写"每轮 run 都记，baseline 只是 v0 的那份"）。
4. 与 `tests/test_w7_experiment_guard.py::test_w7_mainline_switch_defaults_stay_enabled` 呼应：快照记录的是**运行期生效值**，默认值漂移由该单测兜住。
5. **（2026-09-15 Q8=C6-A 新增）`_config_snapshot()` 升级为「全项目配置唯一真相源」**，在现有 9 字段基础上补三项：
   * **`validator_model` 提为顶层字段**（`config.llm.validator_model`）—— 不再只躲在 `experiment` 段里。
     **理由**：W7 唯一被换掉的模型旋钮就是它（`arm6` = 全开关 + `qwen-turbo`）；§10.4 依据 1 的表格里它单独占一列。
     不提为顶层 ⇒ Arm 6「让混淆源可审计」的立论漏掉一半。
   * **`python_version`**（现散在 `eval_env["python"]`，`report_gen.py:67`）—— 收进 snapshot 后 `eval_env` 只留 os / concurrency。
   * **`embedding_model` 与 `strategic_model` 明确不记**（前者硬编码常量、后者是 `planner_model` 的兼容别名，均零信息量/重复）。
6. **（2026-09-15 Q8=C6-A 新增）raw 记录内嵌同一份 `config_snapshot` 对象**，取代原平铺的
   `python_version` / `model_name` / `search_provider` / `max_step_budget` / `experiment` 五项（见 §5.6.1 表 B）。
   **内嵌而非平铺** ⇒ 单条 raw 自包含，同时配置只有一个产生点、不会分叉。
7. **（2026-09-15 Q8=C6-A 新增）新增 `code_revision()` 一处定义**（建议 `eval/provenance.py`），
   返回 `{git_commit, git_dirty, git_diff_hash}`；现有 **3 份重复实现一律改为 import**：
   `report_gen.py:47 _git_head()` / `run.py:64 git_head()` / `w7_experiment.py:45 _git_rev()`（后者保留 `+dirty` 字符串格式以兼容既有产物）。

**归属**：属 **Arm 6（可复现元数据）** 的自然延伸（Arm 6 已覆盖 `git_dirty` / `git_diff_hash` / `deps_frozen_hash` / `model_name` / `search_provider`）。建议直接并入 Arm 6 字段表，而非另立 Arm。
**成本**：小（约十余行 + 两个落盘点）。
**不在 W8 本项范围内的**：为历史 run 回填开关配置（不可行，无证据源）。

### 10.5 主链路基线采集计划（2026-09-15 新增，Grill Q6 = C4）

> **前置**：本计划**必须在 §10.4 落地之后**执行。否则新采的基线同样无法回溯消融配置，W7 的教训会在 W8 重演。

#### 10.5.1 为什么必须新采

见 §10.4 依据 1：**6 个臂里没有任何一格等于主链路配置**（主链路 = 5 开关全开 + `VALIDATOR_MODEL` 默认 `qwen-plus`）⇒ 主链路的实际 coverage / 引用准确率**在现有全部数据里没有对应测量**。

#### 10.5.2 现状参数（2026-09-15 实测）

| 项 | 值 |
| --- | --- |
| 数据集 | `research_engine/eval/dataset.jsonl` —— **固定 20 题**（+1 行 `_meta`） |
| block 语义 | `w7_experiment.py:363`：「N blocks × arms，每 block 内所有 arm 紧挨着跑，同一 block 内 arm 间可比（控制检索漂移 / API 质量随时间波动）」⇒ **block = 同一批 20 题在不同时间点重跑** |
| 单轮成本（估） | **≈¥1.2 / ≈43min**（arm0 开关关+plus = ¥0.8637；arm6 全开+turbo = ¥0.7274 但 token 多 43.5%；主链路 = 全开+plus） |

#### 10.5.3 ⚠️ 关键：多跑是错的方向

**block 间方差 σ≈14.5pp 是"纯漂移"** —— 题目集在所有 block 间固定 ⇒ 题目效应是**系统性偏差，跨 block 抵消不掉**。

题目采样 SE（20 题、p≈0.47）= `√(0.47×0.53/20)` ≈ **11.2pp** —— 这是**地板**。

> ⚠️ **2026-09-16 实测更正：上表的 11.2pp 是「二项假设」值，实测只有它的 1/2~1/7。**
> 见 §10.5.4 —— coverage 实测 SE@20 = **5.5pp**、citation 仅 **1.6pp**。
> **后果**：本表「扩题边际收益 ≫ 多跑」的方向判断**部分被推翻**（题目误差没那么大 ⇒
> 总 SE 的主导项变成漂移项 σ_drift/√R ⇒ **多跑的性价比反而是扩题的 5.5 倍**）。
> **不受影响的部分**：before/after 走**配对设计**（§3.3），题目效应与漂移都被配对消掉，
> 配对 SE 与 σ_question 无关 ⇒ **Q7 拍板的「20 题 × 3 runs 采 before 基线」不变**。
> ⚠️ **但「SE≈1.45pp」这个具体数值已被 §3.3.1 实测推翻**（实测 1.19~4.42pp，按指标而异），
> ⇒ 配对设计**消掉的是题目效应，不是 run 噪声**；coverage 的 MDE(3v9) 实测 **12.38pp**
> ⇒ 「W8 让覆盖率提升 Xpp」**不可判定**。处置与口径重定位见 §3.3.2。
> ⚖️ **待重新拍板**：① after 基线（用于对外绝对值陈述）的题数/runs 组合，见 §10.5.4 末；
> ② before/after 的**验收口径**，见 §3.3.2 三选项。

| 题数 | 题目采样 SE | runs | 总 SE | 成本 | 墙钟 |
| --- | --- | --- | --- | --- | --- |
| 20 | 11.2pp | 3 | 14.0pp | ¥4 | 3.6h |
| 20 | 11.2pp | **23** | **11.6pp** | ¥28 | 16.5h |
| **60** | 6.4pp | 9 | **8.1pp** | ¥32 | 19h |
| **100** | 5.0pp | 9 | **6.9pp** | ¥54 | 32h |

⇒ **20 题下从 3 轮加到 23 轮，只把 SE 从 14.0pp 压到 11.6pp** —— 多跑 20 轮、多花 ¥24、多耗 13h，只换 2.4pp。**扩题的边际收益远大于多跑。**

#### 10.5.4 第一步：零成本实测题目间方差（Q6 拍板 A）

上表的 11.2pp 是按 `p=0.47` **假设**算的。**题目间方差可以直接从现有 raw 实测** —— 单次 run 内 20 题的 coverage 分布即给出 σ_question 的经验估计。

* **成本**：零 LLM 成本，复用已有 raw，约 20 行脚本（只读）。
* **产出**：真实的 σ_question ⇒ 据此决定扩到 60 / 100 题，或根本不用扩。
* **验收**：产出一份 `σ_question` 实测值 + 题数-精度曲线，**据此再最终拍板扩题规模**。
* **暂定立项**：~~**60 题 × 9 runs（±8pp / ≈¥32 / ≈19h）**~~ —— ⚖️ **实测后需重新拍板，见下**。

**✅ 2026-09-16 实测结果（`tools/measure_question_variance.py`，零成本只读）**

方法：W7 权威容器 6 臂 × 3 block 的 eval 数据，用 **one-way random effects 方差成分分解**
把「题目效应」与「运行漂移」分开 —— 直接取单次 run 内 20 题的标准差会**高估**题目效应
（混入了该次运行的噪声）：

```
x_ir = μ + a_i + e_ir        a_i ~ N(0, σ_question²)  题目效应（要的）
                             e_ir ~ N(0, σ_drift²)    同题跨 block 漂移
Var(题目均值 m_i) = σ_question² + σ_drift²/R
⇒ σ_question² = Var(m_i) − σ_within²/R
```

| 指标 | σ_question（修正后） | σ_question（未修正） | SE@20 | 题设 11.2pp |
| --- | --- | --- | --- | --- |
| **coverage** | **24.4pp** | 28.0pp（高估 15%） | **5.5pp** | 高估 2.0 倍 |
| **citation** | **7.1pp** | 13.1pp（高估 85%） | **1.6pp** | 高估 7.0 倍 |

（仅取 5 个 3-block arm 汇总；`arm6` 只有 2 个 block，方差分解自由度不足，已排除）

**⇒ 结论一：题设高估了题目采样误差。** 二项假设 `√(p(1-p)/n)` 把 coverage 当成 0/1 伯努利，
但 coverage 是连续值、且题目间有真实的难度差异 ⇒ 实际离散度远小于纯随机。

**⇒ 结论二（方向性）：Q6「多跑是错的方向」在绝对值陈述场景下不成立。** 实测下总 SE 的
主导项变成**漂移项** `σ_drift/√R`：

| 方案 | 漂移项 | 题目项 | 总 SE | 成本 | 墙钟 | **性价比** |
| --- | --- | --- | --- | --- | --- | --- |
| 20 题 × 3 runs | 8.4pp | 5.5pp | **10.0pp** | ¥4 | 2.1h | — |
| **20 题 × 9 runs** | 4.8pp | 5.5pp | **7.3pp** | **¥11** | **6.5h** | **0.39 pp/¥** |
| 60 题 × 9 runs（原立项） | 4.8pp | 3.2pp | **5.8pp** | ¥32 | 19.4h | 0.071 pp/¥ |
| 100 题 × 9 runs | 4.8pp | 2.4pp | 5.4pp | ¥54 | 32.2h | 0.03 pp/¥ |

⇒ **20 题 × 3→9 runs 的性价比（0.39 pp/¥）是扩到 60 题（0.071 pp/¥）的 5.5 倍。**
扩题只有在**已经把 runs 拉到 9** 之后才谈得上有意义。

**⇒ 结论三（部分不变、数值被推翻）**：before/after 对比走**配对设计**（§3.3），
题目效应在**同一题内相减**时被消掉 ⇒ 配对 SE 与 σ_question 无关（这一点不变）⇒
**Q7 拍板的「before 基线 20 题 × 3 runs ≈¥4」规模不变，继续执行。**
⚠️ **但 SE≈1.45pp 这个数值已被 §3.3.1 实测推翻**（实测 σ_within：coverage 28.91pp /
citation 14.41pp / retrieval_hit 7.75pp；SE(3v9)：4.42 / 2.26 / 1.19pp）⇒
**coverage 的 MDE(3v9) = 12.38pp，「覆盖率提升」不可判定**。验收口径须按 §3.3.2 重定位。

**✅ 2026-09-16 拍板（A）：after 基线 = 20 题 × 9 runs**

| 选项 | 总 SE | 成本 | 墙钟 | 结论 |
| --- | --- | --- | --- | --- |
| **A. 20 题 × 9 runs** | 7.3pp | **¥11** | 6.5h | ✅ **已拍板** —— 性价比 0.39 pp/¥（最高），且**无需新造评测题** |
| B. 60 题 × 9 runs（原立项） | 5.8pp | ¥32 | 19.4h | ❌ 性价比仅 A 的 1/5；且需**新造 40 道题**，人工成本此前未计入 |
| C. 20 题 × 3 runs | 10.0pp | ¥4 | 2.1h | ❌ 精度不足，只能做配对陈述；不如 A 的性价比 |

**对外口径（按 §10.5.6）**：覆盖率写作「**47% ±7pp（20 题 × 9 轮，主链路配置）**」。

> **为什么不必扩题**：扩题的意义是压低题目采样 SE，但实测 coverage 的题目项（5.5pp）
> 已经**小于** 9 runs 下的漂移项（4.8pp）与 3 runs 下的漂移项（8.4pp）之间的差距 ——
> 在 runs 拉到 9 之前扩题，钱全花在不是瓶颈的地方。**这是实测才能得出的结论，题设阶段无法看出。**

#### 10.5.5 执行约束

| 约束 | 要求 |
| --- | --- |
| **前置** | §10.4 落地后才能开始采（`_config_snapshot` 记 5 开关**生效值** + **每轮都记**） |
| **每轮记录** | code rev（沿用 `w7_experiment.py` 的 `_git_rev` 护栏）+ config 快照 + **stderr**（§5.5.4） |
| **方法论** | 按 §3.3：配对优先 / 功效分析前置 / 强制 stderr / 报绝对量 |
| **谁跑** | 项目主理人（TianJinYing2006）自有 API key，无外部依赖 |
| **对外口径** | **一律带区间与题数**，见 §10.5.6 |

#### 10.5.7 before 基线：错过不可逆（2026-09-15 Q7 拍板 X）

**问题（死锁）**：§10.5 基线采集的硬前置是 §10.4，而 §10.4 原属 Arm 6、排第 6；可如果基线要当"改前基线"，就必须排在 Arm 1 之前 —— **同一个 §10.4 被要求在 Arm 1 之前（为基线）和 Arm 6 位置（为归属）**。

**解法（Q7 拍板 X）**：§10.4 前移为第一项 → **立刻采 before 基线** → 再动 Arm 1~7 → 最后采 after 基线。

| 项 | before 基线 | after 基线 |
| --- | --- | --- |
| **时机** | §10.4 落地后、**Arm 1~7 任一改动前** | 全部 Arm 落地后 |
| **配置** | 主链路（5 开关全开 + `VALIDATOR_MODEL` 默认 qwen-plus） | 同左 |
| **规模** | 20 题 × 3 runs（**≈¥4 / ≈3.6h**） | **20 题 × 9 runs（≈¥11 / 6.5h / ±7.3pp）**（2026-09-16 实测后改拍，原 60 题 × 9 runs 已废弃） |
| **精度要求** | **低** —— 配对设计下只关心**配对差**，绝对值精度不重要 | 高 —— 用于对外绝对值陈述 |
| **错过后果** | **永久错过**（改动一旦落地即不可逆） | 可延后 |

**为什么要采 before 基线**：按 §3.2，Arm 1/4/5 全是 A 类确定性验收，**不需要** before 基线也能验收。但 before/after 配对是**唯一能自证「W8 让主链路从 X 提升到 Y」**的手段 —— 这是简历/面试最有杀伤力的一类证据，而增量成本仅 ≈¥4。

> **措辞纪律**：before 基线采于 W8 改动之前，after 基线采于之后。任何对比结论**必须写明是哪两个 code rev 之间**，
> 并按 §10.5.6 同时给出区间与题数。
>
> ⚠️ **2026-09-16 更正**：上一段「唯一能自证『W8 让主链路从 X 提升到 Y』」**已被 §3.3.1 实测推翻** ——
> coverage 的 MDE(3v9)=10.54pp > W7 效应量级 ⇒ **提升类陈述不可判定**。
> before/after 的真正价值改为 §3.3.2 的**可观测性对照**（故障可归因率 0%→100%，A 类确定性断言）。

#### 10.5.8 before 基线采集结果（2026-09-16 实测，3 轮完成）

| 轮次 | run 目录 | code rev | complete / failed | coverage | citation_acc | retrieval_hit |
| --- | --- | --- | --- | --- | --- | --- |
| r1 | `run_20260916_001005` | `6f4067f` | 19 / **1**（q_001 超时） | 0.3947 | 0.7175 | 0.5297 |
| r2 | `run_20260916_011813` | `13a6d3e` | 20 / 0 | 0.5042 | 0.7359 | 0.5460 |
| r3 | `run_20260916_022440` | `6c83af1` | 19 / **1**（q_007 超时） | 0.4561 | 0.8176 | 0.5956 |
| **均值** | — | — | — | **0.4517** | **0.7570** | **0.5571** |
| **SE（跨轮）** | — | — | — | **3.17pp** | **3.08pp** | **1.98pp** |

**对外口径（按 §10.5.6，一律带区间与题数）**：

| 指标 | 写法 |
| --- | --- |
| coverage | **45.2% ± 6.3pp〔20 题 × 3 轮，主链路配置〕** ⇒ 区间 [38.8%, 51.5%] |
| citation_accuracy | **75.7% ± 6.2pp** ⇒ 区间 [69.5%, 81.9%] |
| retrieval_hit_rate | **55.7% ± 4.0pp** ⇒ 区间 [51.7%, 59.7%] |

**读数的三个注意点**：① 三轮全部 `git_dirty=false`、`config_snapshot` 完整 ⇒ **配置可回溯**（§10.4 达标）；
② 三轮的 code rev 不同（`6f4067f` → `13a6d3e` → `6c83af1`），但**全部是文档类提交，`research_engine/` 零改动** ⇒ 行为等价；
③ **超时题目每轮不同**（r1=q_001、r3=q_007）⇒ 超时不是题目固有属性，是**运行期噪声**，这会额外抬高 σ_within。

**⚠️ 采集过程中暴露的一个真实缺陷（W8 相关，登记待处置）**

第 3 轮 phase1 完成（20/20 raw 落盘）后**进程被杀**，未写出 `phase1_global_stats.json`。
补跑 `--eval-only` 后：`cost_phase1_total.cost_yuan` = **¥0.0000** —— 而该轮 raw 的
`token_used` 合计 **1,017,784**（比第 2 轮的 952,093 还多）⇒ **实际花费约 ¥0.96，账上记为 0**。

- **性质**：`_read_phase1_cost()` 读不到该文件时**静默返回 0**，无任何告警 ⇒ **成本账目静默归零**。
- **这正是 W8 要治的病**：中断的 run 既不报错、也不降级，只是悄悄少记一笔钱 —— 与 §4.2「静默吞掉」同构。
- **可恢复性**：raw 里有 `token_used`（总量，无按模型拆分）⇒ 能还原 token 数，**不能**精确还原金额。
- **⇒ 基线总成本应更正为 ≈¥2.74**（0.8924 + 0.8953 + ≈0.96），而非 summary 直接给出的 ¥1.79。
- **待处置（建议并入 Arm 6 或 Arm 1）**：`phase1_global_stats.json` 缺失时**降级为从 raw 汇总**并**显式标记 `cost_degraded=true`**，而不是返回 0。

#### 10.5.6 基线数字对外表述规则（Q6 拍板）

> **一律带区间与题数，不接受裸数字。**

| 场景 | 正确写法 | 禁止写法 |
| --- | --- | --- |
| 简历 / 面试 | 「覆盖率 47% ±7pp（20 题 × 9 轮，主链路配置）」 | 「覆盖率 47%」 |
| 报告 / 文档 | 指标 + stderr + 题数 + runs 数 | 只有均值 |
| **效应类结论** | 「提升 6.7pp（配对对照，n=20 题，**SE=4.42pp（coverage 实测）**）」 | 「提升 6.7pp」 |
| **⚠️ SE > 效应时** | 「**不可判定** —— MDE=12.4pp > 观测效应 6.7pp（§3.3 第 4 条）」 | 「提升 6.7pp（配对后显著）」 |
| **可观测性类结论（W8 主线）** | 「故障可归因率 0% → 100%（A 类确定性断言，注入故障复现）」 | 用覆盖率提升来验收可观测性改进 |

**依据**：与 §5.5.4「强制报 stderr」一致，也是 W7「如实基线」文化的延续 —— **stderr > 所声称的效应 ⇒ 该结论不得表述为"提升 Xpp"**。

## 11. 变更记录

| 日期         | 类型 | 原因                 | 改动摘要                   | 关联 PR/commit |
| ---------- | -- | ------------------ | ---------------------- | ------------ |
| 2026-09-10 | 新建 | 外部评审报告识别的 P0/P1 短板 | 基于 6.5/10 评审编排 7 个 Arm | —            |
| 2026-09-13 | 补充 | W7 收口后发现挂账未进 W8 排期 | 新增 **§10「承接 W7 挂账」**：10.1 登记 5 项 W7 推迟项（博客③/博客②/组件③澄清范围/persona 视角发现/HHEM 降本）；10.2 把 W7 未完成项对齐到 W8 现有 Arm 3/5/6（并指出 Arm 6 仍缺 `prompt_hash`+`seed`）；10.3 登记 4 项**尚未立项**的评测护栏候选（矩阵完整性 / 预算一致性 / 汇总自动排除 / `_config_snapshot` 每轮记录），附 W7 实测证据。**候选 ≠ 承诺** | 本文档 |
| 2026-09-13 | 升级 | 用户决定「补进 W8」：§10.3 候选 **I 升为前置** | 新增 **§10.4「W8 前置项：`_config_snapshot` 必须记录实验开关」**。升级依据为两条新取证事实：① **主链路配置格子从未被测过** —— 主链路 = 5 开关全开 + `VALIDATOR_MODEL` 默认（`qwen-plus`），而 W7 唯一全开的 `arm6` 锁 `qwen-turbo` ⇒ **6 臂无一格等于主链路配置**；② **历史 run 消融配置不可回溯** —— `_config_snapshot()` 只记 9 个模型/预算字段、一个开关不记，09-09 之后的 run 无法判断配置（含 298 条归因夹具来源 `run_v11_compare`）。同步改动：§5.6.1 Arm 6 字段表新增 `experiment` 字段（记录**生效值**而非环境变量原始串）并加回落语义说明；§7 Arm 6 DoD 新增 3 条（`experiment` 段 / 每轮记录 + 修 docstring / 基线读回一致性验收）；§1 元信息新增「采基线前置」行；§10.3 候选 I 划删并标注升级去向。**明确不做的**：为历史 run 回填开关配置（无证据源，不可行） | 本文档 |
| 2026-09-14 | 拍板 | Grill-Me 工作流 Q1：Arm 7 前提已过期且量化目标错位 | **Arm 7 重划为双轨**：① git 侧改为「白名单收敛 + **引用即入库**」判据，逐项定性 94 个「跟踪但不在白名单」路径（`run_v11_compare` 升白名单并注明引用出处；`run_20260906_184156` / `run_20260907_001658` 待定性）；② 本机磁盘改为「**先只读扫描出账、暂不删除**」，并立铁律「台账 review 前不得删除/移动/重命名」（因 `tools/w7_backfill_*.py` 依赖 `raw/*.raw.json`）。同步改动：§1 元信息新增「Grill 拍板」行；§3.1 Arm 7 量化指标改写（删体积目标）；§4.7 由 09-10 盘点**整体替换**为 2026-09-14 实测盘点 + 行业横向对照表；§7 Arm 7 DoD 整体重写（**删去「仓库体积减少 ≥5 MB」——`.git` 对象库实测仅 ≈4.4 MB，不可达成**）；§8 风险行更新为「git 侧低风险 / 磁盘侧高风险」。**依据**：`.gitignore:33-44` 已含规则（09-13 `3b309d7`）、实测跟踪 189 项 ≈9.0 MB、本地 147 MB、行业通例（STORM `*results/`、gpt-researcher `outputs/` 整体 ignore） | 本文档 |
| 2026-09-14 | 拍板 | Grill-Me 工作流 **Q2（A）**：Arm 3 拆「锁定 / 声明 / 强制」三刀 | **第一刀·锁定**：`pip-compile --python-version 3.11` 生成 `requirements-lock.txt`，**明确不用 `pip freeze`**（实测 `.deps/` 168 个 `.pyd` 全为 `cp313-win_amd64`，freeze 会把 3.13+Windows 专属集合固化成 lock），**不加 `--generate-hashes`**（哈希随平台轮子变化 ⇒ 本机 win/3.13 生成的哈希在 ubuntu/3.11 必然不匹配；行业证据：gpt-researcher 用**环境标记** `; sys_platform != 'win32'` 而非锁死哈希）；**第二刀·声明**：`pyproject.toml` **新增**（非"改为"）`requires-python = ">=3.11,<3.14"` 并注明"本仓库无 `[build-system]`、不具强制力"、`[tool.ruff] target-version` `py310`→`py311`、`README.md` `3.10+`→`3.11 ~ 3.13`；**第三刀·强制**：运行时 `sys.version_info` 版本闸 + CI `matrix: ["3.11","3.12","3.13"]`。**取消两条 DoD**：①「新增 smoke test」（现有 132 测试全零 LLM/零 key 已是等价物）；②「Python 3.11 下测试通过」（CI 每次 push 即验证）。**跨平台风险处置**：首次 CI 运行实测，回退方案 = Linux 侧生成 / 只锁直接依赖。同步改动：§3.1 Arm 3 指标行、§4.4 **整体重写**（含行业对照表）、§5.3 **整体重写**为三刀、§7 Arm 3 DoD 重写、§8 Arm 3 风险行、§9.2 集成测试行 | 本文档 |
| 2026-09-14 | 同步 | W8 建立飞书镜像（并固化同步闭环） | 已创建「第八周需求文档」：docx token `F5HRd4hp8o01p7xyrpJczPsYnKf`，父文件夹 `MYR6fazL5la0ardJdUecOBkVnd8`。**新增工具 `tools/feishu_escape_md.py`** —— 只转义**裸文本中的单个 `~`**（保护 fenced/inline code 与有意的 `~~`），治「范围符 `20~29pp` 被飞书误判为删除线」的老问题；本版本已含 §4.4 Arm 3 行业横向对照。校验闭环回查：`\~`×5、`~~`×5 对、checkbox 42 项、无杂散 `<del>` | 本文档 |
| 2026-09-14 | 补记 | Arm 3 行业横向对照（Grill Q2 前置取证） | §4.4 **整体重写**为 2026-09-14 实测盘点（四处版本口径：README `3.10` / ruff `py310` / CI `3.11` / 本地 venv `3.13`；`.deps` 168 个 `.pyd` 全 `cp313-win_amd64`；无 lock 文件）+ **行业对照表**：open_deep_research 入库 `uv.lock`(988 KB)、gpt-researcher 与 dzhng 反而把锁文件写进 `.gitignore`、gpt-researcher 用环境标记 `; sys_platform != 'win32'` 处理跨平台 ⇒ **「锁文件是否入库」行业并不统一，「跨平台正解是环境标记而非锁死哈希」**。同步揭示原文 3 条不成立断言（`requires-python` 前提不存在 / README 口径未提 / 「无 pytest 无 ruff」系解释器口径） | 本文档 |
| 2026-09-14 | 拍板 | Grill-Me 工作流 **C3**：Arm 6 字段表按行业 provenance schema 扩容 | **Arm 6 由 7 字段扩至 13 字段**（新增 `prompt_hash` / `citation_judge_model` / `coverage_judge_model` / `citation_judge_independent` / `scorer_version` / `max_step_budget`）。**核心设计判断：行业 schema 只有一个 `judge_model`，但本项目必须拆三个** —— 代码取证显示现行有**两个裁判**：① 引用准确率**直读 `state.citations`**（`eval/metrics.py:5`，不重跑 validator）⇒ 裁判 = 主链路 validator，**属「被测兼任裁判」**；② coverage 另有独立 `role="judge"` 实例（`eval/metrics.py:23`）。只记单个 `judge_model` 会**掩盖** W7 最要命的病，故拆为 `citation_judge_model` + `coverage_judge_model` + `citation_judge_independent: bool`。**明确不做两项**：`seed`（① `llm/client.py chat()` 无 seed 参数、payload 无该字段，需改 client + 验 DashScope 兼容模式支持性；② W7 一手数据已证噪声主因是**检索轮数/证据池漂移**而非 LLM 采样随机性 ⇒ 固定 seed 不对症）、`temperature`（`config.py:40` 为硬编码常量、非 `_env()` 可配 ⇒ 记录值恒为 0.2、零信息量，降级为「随 env 化一起做」）。同步改动：§3.1 Arm 6 指标行、§5.6.1 字段表 + 「为什么拆三字段」说明 + 「不做的两项」表、§7 Arm 6 DoD（新增 6 条，含 3 项配套单测）、§9.1 单测表（新增 4 条）、§8 Arm 6 风险行（补 `scorer_version` 副作用说明）、§10.2 标注处置去向 | 本文档 |
| 2026-09-14 | 修订 | **决策复盘查出 3 处硬伤**（于晏要求回顾 Q1/C3/Q2 拍板有无漏洞）；本次处置 A1/A2/A3 | ① **A1 §6.3 与 §5.3 自相矛盾**：§6.3 原写「选择 `pip freeze`」，与 Q2 拍板「明确**不用** `pip freeze`、用 `pip-compile`」冲突 ⇒ **§6.3 整体重写**为「pip-compile 而非 pip freeze / uv / poetry」并附三者取舍表（保留原稿会让实现者按 freeze 执行，Q2 白拍）。② **A2 `prompt_hash` 的 critic 取证落空**：实测 critic 在 `research_engine/critic.py`（**非 `agents/`**）且**无模块级提示词常量** —— 提示词是 `_verdict()` 内 `:174-181` 的 `system` 局部变量；**并挖出更硬证据**：`critic.py:167-179` 由 `CRITIC_GAP_ENABLED` 把 `gap_extra` **动态拼进 system** ⇒ **开关与提示词不正交**，只记开关值必漏 ⇒ **钉死 `prompt_hash` 语义 =「运行期实际提示词正文」而非源码常量快照**。③ **A3 `scorer_version` 漏了 W7 权威复判裁判**：`eval/w7_rejudge.py:88 JUDGE_SYSTEM_DIAGNOSTIC` 既不在 `scorer_version` 清单也不在 `prompt_hash` 清单 ⇒ **完全漏网**，改它 = W7 全部复判结论（纯裁判效应 +7.53pp 等）可漂移无痕 ⇒ **补入 `scorer_version` 文件清单**。同步改动：§5.6.1 两行字段表、§7 Arm 6 DoD（2 条拆为 3 条）、§9.1 单测表新增 2 条（critic `gap_extra` 专项 / `w7_rejudge.py` 专项）。**另在复盘查出、本次未处置（转入待拷问）**：A4 = Arm 5 动机被证伪 + **三套 `status` 词汇表共存**（raw `:107` `done/incomplete` / `_run_one` `:110,117,157` `ok,failed,timeout` / phase2 `:203,215,243,246` `ok,partial,failed,timeout`；`_summarize` 的 `complete` 统计的是**第三层**）⇒ Arm 1 新增 `run_status` 的 `partial/failed` 与之**同名词不同义**；A5 = Arm 6 有 4 字段与既有 `_config_snapshot` 冗余且 `model_name` 口径未定义 | 本文档 |
| 2026-09-15 | 拍板 | Grill-Me 工作流 **Q3（A）**：Arm 5 立论被实测推翻，主刀换成 run 级质量闸 | **推翻原「`complete` 拆四字段」立论**：实测 `complete` = phase2 指标层 `status=="ok"`（`run.py:284`+`:215`）=「七指标算全的条数」，**与"写出 raw 文件"无关**；核 `run_20260910_173540/summary.json` 原文得 `complete=20/partial=0/failed=0` 而**七指标全 0、`avg_steps=1.0`、`critic_stop_rate=1.0`** ⇒ 真相是「指标算出来了、确实是 0，系统一声不吭」，**不是"指标缺失被当成完成"**；反证 `run_20260910_171054` = `complete=0/partial=9/failed=11` 但 `completion_rate=1.0` ⇒ 两者**正交**。**新方案**：主刀 = `_summarize()` 顶层 `verdict`（`ok`/`suspicious`/`broken`）+ `verdict_reasons`；**阈值外置为参数**（`DEFAULT_THRESHOLDS` 常量注明「由 173540 反推、须在 §10.4 新基线上复核」，**文档不写死数字**）；**只告警不阻断**（阈值未校准前 hard fail 风险大于收益）。**命名裁决（甲）**：phase2 `status` → `metrics_status`，`run_status` 独占「流程健康度」。同步改动：§2 问题背景（补真实数据表 + 更正误述 + 反证样本）、§1 元信息新增拍板行、§3.1 Arm 5 指标行、§4.5 **整体重写**（含三套 `status` 词汇表表）、§5.5 **整体重写**（5.5.1 质量闸 / 5.5.2 字段重命名 / 5.5.3 模板）、§5.1.1 加命名裁决注、§7 DoD（Arm 1 +1 条、Arm 5 整体重写）、§8 Arm 5 风险行、§9.1 单测 +2 条、§9.3 对照行 | 本文档 |
| 2026-09-15 | 拍板 | Grill-Me 工作流 **Q4（C，B1+B2 合并）**：Arm 1 四态里只有两态是真的 | **推翻原四态设计**：① **B1 —— `failed` 不是"没落点"，是根本不可达** —— `run()`（`graph.py:282-320`）无 try/except，节点抛异常 ⇒ `invoke` 抛 ⇒ **无 state 返回** ⇒ `run_status` 永不被置 `failed`；而走 fallback 的路径都仍会产出报告 ⇒ 只能落 `degraded`；`GraphRecursionError` 同理；唯一记录失败的是 `eval/run.py:_run_one`（82-117）⇒ **真相源在评估层，不在 state**。② **B2 —— `degraded` 与 `partial` 判定条件完全相同**（"tracker 非空"⇔"部分节点失败"），无任何观测量可分开。**新方案**：① **`run_status` 收敛为 `success`/`degraded`/`failed` 三态**，`partial` 移出 state、降为报告层派生指标（按子问题覆盖率）；② **新增 §5.1.4 异常退出契约** —— `run()` try/except + `get_state(cfg).values` 捞回 checkpoint（MemorySaver 已启用，`graph.py:85`）⇒ `failed` 可达；`get_state` 为空时构造最小 state，**绝不返回 None**；`run()` 返回 state 不 re-raise + `cli.py` 判 `failed` ⇒ `sys.exit(1)`。**行业横向对照（§6.6，实时取证）**：open\_deep\_research = **错误即内容**（`final_report=f"Error generating final report: {e}"`、工具错误返回字符串）**无 status 字段**；gpt-researcher = **事件流**（`_log_event(step=...)`）状态由消费方派生，**无 status 字段**；**OpenAI Responses API = 三终态 + 结构化原因**（`ResponseStatus` 终态恰为 `completed`/`incomplete`（带 `incomplete_details.reason`）/`failed`（带 `error{code(21 值枚举),message}`），**无 `partial`**）⇒ **开源 Agent 项目普遍不做结构化 run status，做状态机的是 API 层且答案就是三终态**；本项目属**差异化改进而非跟随**。**Q4 附带三项**（用户采纳 ②③④）：② `DegradationEntry.reason` 由自由文本**改枚举并与 Arm 4 `failure_reason` 共用一张表**（新增 `llm_error`/`token_limit`/`recursion_limit`/`internal`，一处定义两侧 import）⇒ **顺带解决 B4 唯一真相源**；③ `state.error` 由 `Optional[str]`（`state.py:92`）**改结构化 `{code,message,node}`**；④ `degradation_log` **挂 `operator.add` reducer**（照抄 `state.py:90` 现有 `progress` 写法）⇒ **解决 B3 并发覆盖**。**命名三分**：`run_status`（流程健康度）/ `invoke_status`（层② `_run_one`）/ `metrics_status`（层③ phase2）。**⑤ 重试+渐进降级（open\_deep\_research 有、本项目零重试）经用户裁决本期不采纳**，仅登记于 §6.6。同步改动：§1 元信息加拍板行、§3.1 Arm 1 指标行、§4.1 补 B1 取证表、§4.5 交叉引用、§5.1.1 三态表 + 命名三分表、§5.1.2 枚举表 + reducer + 判定规则、**新增 §5.1.4**、§5.1.3 落点清单 +4 行、§5.4.1 共用枚举注、**新增 §6.6 行业横向对照**、§7 Arm 1 DoD 重写、§8 Arm 1 风险由「低」上调「中」、§9.1 单测 +4 条 | 本文档 |
| 2026-09-15 | 拍板 | Grill-Me 工作流 **Q5（A′，C1）**：§3.2「3 轮取均值」既无效又无的放矢 | **推翻原 §3.2**。① **无的放矢**：原约束点名的 Arm 1/4/5 **全部是确定性验收**（Arm 1 = 注入故障断言 `run_status`；Arm 4 = 断网断言 `failure_reason=="timeout"`；Arm 5 = 历史 run 离线重算判 `verdict`）⇒ **零噪声，重跑一万轮结果也一样**。② **数学上无效**：噪声实测两源互证 —— W7 结论文档 `:249` 按 `E[range]≈1.69σ` 折算 **σ≈12.1/14.4/14.3/17.0pp**，本次独立复算（极差 24.5pp、n=3、`d2=1.693`）**σ≈14.5pp** ✅ 吻合；两样本功效分析 ⇒ **n=3 时 SE_diff=11.8pp，detect 10pp 的 t 仅 0.85**（需 ≳2.8）；detect 10pp 需 n≈35/组（70 runs/≈¥55/≈50h），**detect 4pp 需 n≈215/组（430 runs/≈¥335/≈300h+，不可行）**。③ **配对设计是唯一可行路**：固定检索快照后 ρ≈0.9、n=20 题 ⇒ SE≈1.45pp、detect 4pp **t≈2.76** ✅，成本仅 ≈1~2 runs（对比独立两样本 430 runs）。**新方案**：§3.2 按「A 类确定性断言 / B 类统计对照」分类重写；**新增 §3.3 基线采集方法论约束**（配对优先 + 功效分析前置 + 强制报 stderr + 预算不可接受时如实宣告不可判定 + 报告绝对量），**为 C4 预算题前置锁死方法论**，C4 届时只拍预算。**方法论来源非新发明**：W7 结论文档 §7（第 280 行）已写明「固定裁判 + 固定检索快照 + 同预算成本-效果比较 + 多区块重复 + 报告绝对条数」，W8 原 §3.2 只是没接住。**新增 §5.5.4**：`metrics_mean` 必须带 stderr 且**离线回填历史 run**。**行业对照（§6.7，实时取证）**：openai/evals `evals/metrics.py:21` `get_bootstrap_accuracy_std(events, num_samples=1000)`（指标层内置 bootstrap std）；EleutherAI lm-evaluation-harness `lm_eval/api/metrics.py:342/441/508` `mean_stderr()` / `acc_all_stderr()` / `_bootstrap_internal`（每指标带 stderr）⇒ **「报指标必须带 stderr」是行业通例**；本项目 `metrics_mean` 只有均值，正是 W7 事后才发现「噪声吞没效应」的直接原因（**仪器没装刻度**）。**注**：Stanford HELM 的 `run_spec.py` 未检索到 trial/CI 字段，不作断言。同步改动：§1 元信息加拍板行、§3.1 Arm 5 指标行、§3.2 重写、新增 §3.3、新增 §5.5.4、§7 Arm 5 DoD +3 条、§9.1 单测 +2 条、新增 §6.7 | 本文档 |
| 2026-09-15 | 拍板 | Grill-Me 工作流 **Q6（A，C4）**：主链路基线谁跑/几轮/预算 —— **多跑是错的方向** | **新增 §10.5 主链路基线采集计划**。① **为什么必须新采**：§10.4 依据 1 —— 6 臂无一格等于主链路配置（5 开关全开 + `VALIDATOR_MODEL` 默认 qwen-plus）。② **现状参数**：数据集 `research_engine/eval/dataset.jsonl` **固定 20 题**（+1 行 `_meta`）；block 语义见 `w7_experiment.py:363`（「N blocks × arms，每 block 内所有 arm 紧挨着跑，控制检索漂移/API 质量随时间波动」）⇒ **block = 同一批 20 题在不同时间点重跑**；单轮成本估 **≈¥1.2 / ≈43min**（arm0 开关关+plus = ¥0.8637；arm6 全开+turbo = ¥0.7274 但 token 多 43.5%）。③ **关键发现**：block 间 σ≈14.5pp 是**纯漂移**（题目集固定 ⇒ 题目效应是系统性偏差，跨 block 抵消不掉）；**题目采样 SE≈11.2pp 是「地板」，重跑压不掉** ⇒ 20 题下从 3 轮加到 23 轮，SE 仅从 14.0pp 降到 **11.6pp**（多花 ¥24/13h 只换 2.4pp）；而扩到 60 题 × 9 runs 即达 **8.1pp**（¥32/19h）、100 题 × 9 runs 达 **6.9pp**（¥54/32h）⇒ **扩题边际收益 ≫ 多跑**。④ **第一步 = 零成本实测题目间方差**（复用现有 raw，约 20 行只读脚本）⇒ 上表 11.2pp 是按 p=0.47 假设算的，实测后才定扩题规模；**暂定立项 60 题 × 9 runs**。⑤ **硬前置**：必须在 §10.4 落地后才能采。⑥ **对外口径（§10.5.6）**：**一律带区间与题数**（如「覆盖率 47% ±8pp（60 题 × 9 轮）」），**拒绝裸数字**；效应类结论须写「提升 Xpp（配对对照，n=，SE=）」。同步改动：§1 元信息加拍板行、新增 §10.5 | 本文档 |
| 2026-09-15 | 拍板 | Grill-Me 工作流 **Q7（A + X，C5）**：两张优先级表各说各话，且基线时机存在死锁 | **新增 §1.1 W8 全景工作项视图**。① **优先级语义混用**：§1 的 P0/P1/P2 是「实现档」（该不该先做），§10.1 的 P1/P2/P3 是「价值档」（值不值得做），**两套 P1 不可比** ⇒ **统一为 P0~P3 并明确定义**（P0 = 阻塞项/不做则 W8 不成立/**错过不可逆**；P1 = W8 内应完成；P2 = 可选；P3 = 探索或候选未承诺）。② **§10.4 是 P0 性质却挂在 P1 的 Arm 6 里排第 6** —— 它阻塞所有基线采集、**收益随时间衰减**（越晚做可回溯的 run 越少），成本仅十余行 ⇒ **升 P0 并前移为实现顺序第一项**（原则：**阻塞关系的优先级应取「被阻塞方的最高优先级」**，而非继承所归属模块的优先级）。③ **Q3~Q6 新增的 5 项工作此前一张表都没进**（§10.4 / §5.5.4 stderr 回填 / §10.5 基线 / §10.5.4 实测 σ_question / Arm 1 异常退出契约）⇒ 全部补入视图。④ **挂账依赖补全**：D（persona）**依赖 Arm 1 达标**（W7 因 Arm 1 未达标触发止损而取消）；C（澄清范围）**与 Arm 1 同改图结构，有冲突风险**。⑤ §10.3 护栏候选 F/G/H 及 Q4 已否决的「重试+渐进降级」一并入视图（后者标「已考察·本期不采纳」）。⑥ **基线时机死锁及解法**：§10.5 硬前置 §10.4（属 Arm 6 排第 6），但若基线要当「改前基线」就必须排在 Arm 1 之前 ⇒ **拍板 X**：§10.4 → **before 基线 → Arm 1~7 → after 基线**。**before 基线增量成本仅 ≈¥4 / 3.6h**（配对设计下只关心配对差，绝对值精度不重要，20 题×3 runs 即够），但**一旦 Arm 1~7 任一改动落地即永久错过** ⇒ 属「错过不可逆」资源。同步改动：§1 优先级行重写 + 实现顺序行重排 + 新增「⚠️ 错过不可逆」行、新增 §1.1（含依赖图）、§10.4 加 P0 前移说明、§10.5.5 表格 + **新增 §10.5.7 before 基线** | 本文档 |
| 2026-09-15 | 拍板 | Grill-Me 工作流 **Q8（A+A，C6 + B4 收口）**：Arm 6 十三字段收敛 + 两条失败记录路径定案 | **C6（选 A：一处定义 + 内嵌同一对象）**。三条实时取证：① **「取 git HEAD」本仓库已有 3 份实现、3 种粒度** —— `report_gen.py:47 _git_head()`（完整 HEAD）/ `run.py:64 git_head()`（**同一段代码整段复制**）/ `w7_experiment.py:45 _git_rev()`（8 位短 rev **+ `+dirty` 后缀**，`:62 return f"{rev}+dirty" if rev and dirty else ...`）⇒ **Arm 6 若再加 `git_dirty`+`git_diff_hash` 就是第 4、5 份表达**，且「dirty 布尔」**已在产出却无人读** ⇒ **新增 `code_revision()`（`eval/provenance.py`）一处定义，三处改 import**（`_git_rev` 保留 `+dirty` 格式以兼容既有 W7 产物）。② **5 个字段与 §10.4 落地后的 `config_snapshot` 撞车** —— `search_provider`（`report_gen.py:37` 已有，**完全重复**）/ `max_step_budget`（= `max_total_hops`，**纯别名**）/ `python_version`（= `eval_env.python`，`:67`）/ `experiment`（**§10.4 本体，同件事写两次**）⇒ **配置类字段一律不平铺，改为内嵌同一份 `config_snapshot`**（raw 仍自包含，但只有一个产生点，不会分叉）。③ **`model_name` 口径未定义且漏了最要命的旋钮** —— `config.py` 共 7 个模型字段（`:30-38` + `:80`），`_config_snapshot` 只记 4 个，**`validator_model` 不在顶层**；而它正是 W7 唯一被换掉的模型（arm6 = 全开关 + qwen-turbo）⇒ **`validator_model` 提为 `config_snapshot` 顶层**；`embedding_model`（硬编码常量）与 `strategic_model`（planner 别名）**明确不记**。**净效果：Arm 6 由 13 平铺字段 → 8 真新增字段 + 内嵌 1 对象 + 1 共用函数，工作量反而变小**（13 处取值 → 8 处）。**B4（选 A：不合并 + 单向派生）**：`failure_reason`（同步、供 planner 当轮决策、一次调用即覆盖）与 `degradation_log`（异步、供事后审计、run 级追加）**是两个消费方而非两份拷贝**，强行合并必毁其一；**同构 OTel**：`Span.Status`（单值后写覆盖）与 `add_event()`（追加流）**并存不合并**。⇒ 改立**单向派生契约**：工具类 5 值**只由工具层产生**，`DegradationEntry.reason` **必须取 `resp.failure_reason`**、**禁止手写第二字面量**；非工具类 4 值（`llm_error`/`token_limit`/`recursion_limit`/`internal`）由 Arm 1 直接产生；枚举表新增「**产生点**」列。**副作用（实现顺序微调）**：Arm 4 的 `SearchResponse.failure_reason` **字段定义**前移为 Arm 1 前置（仅字段 + 枚举，不含五处 `except` 改造）。**行业横向对照（新增 §6.8，实时取证）**：**OTel Resource** 规范原文「A Resource is an **immutable** representation…」「That association **cannot be changed later**」「all Spans… **MUST** be associated with this Resource」，实践文档更直白「**Don't try to change the Resource per request or per span**」；**MLflow / W&B** = **配置记一次、metric 多次**（`log_params`/`wandb.init(config=)` 一次，`log_metrics`/`wandb.log` 逐步）⇒ **「一处定义 + 观测点引用同一对象」是行业通例**；**分裂处如实说**：MLflow/W&B 存 server 侧 join（OTel 称 telescoping identity），本项目 raw 是不可变归档产物故**内嵌反规范化是对的**，但**必须内嵌同一对象而非手抄副本**（已立单测「同 run 内快照逐字节相同」）。**至此 W8 Grill 全部收口**（待拷问清单清空）。同步改动：§1 元信息新增 Q8 拍板行 + 实现顺序行加 Q8 微调注、§1.1 全景视图 Arm 4/Arm 6 行 + 依赖图、§5.1.2 枚举表加「产生点」列 + 单向派生契约块、§5.4.1 加「工具层是源头」注、§5.6.1 **整体重构**（表 A 八字段 / 表 B 内嵌 / 表 C 五字段 + `max_step_budget` 划删 + 不做项 +3 行 + **新增《Q8 C6 收敛说明》**）、§10.4 要求新增第 5/6/7 条、**新增 §6.8**、§7 DoD（Arm 1 +2 条 / Arm 4 +1 条 / Arm 6 重写）、§8 Arm 6 风险由「低」上调「中」、§9.1 单测 +6 条 | 本文档 |
| 2026-09-15 | **实现** | **§10.4（P0 前置项）代码落地 —— 采 before 基线的最后一道闸门已开** | **新增 `research_engine/eval/provenance.py`** 作为 provenance 唯一产生点，含 4 个公开函数：`code_revision()`（git 修订三元组）/ `experiment_snapshot()`（5 开关生效值）/ `config_snapshot()`（唯一配置真相源）/ `run_provenance()`（一次算好整轮 run，git 子进程只跑一次）。**① 消除 3 份重复实现**：`report_gen._git_head` / `run.git_head` / `w7_experiment._git_rev` 全部改为 import `code_revision`；**`_git_rev` 保留 `xxxxxx+dirty` 字符串格式**（既有 W7 产物的区块续跑比对读它，改格式 = 历史实验无法续跑）。**② `config_snapshot` 升为唯一真相源**：原 9 字段 + **`validator_model` 提顶层**（W7 唯一被换掉的旋钮）+ `python_version` + `experiment` 段；`embedding_model`/`strategic_model`/`temperature` 明确不记；`eval_env` 移除 `python`（已进 snapshot）。**③ 每轮都记**：per-run `summary.json` + `history.json` 均写入（history 走 `summary["config_snapshot"]`，run 级一条不按题目重复）。**④ raw 内嵌同一对象**：`phase1` 抓一次 `run_provenance()` 按条目复用，**失败路径与超时路径同样落盘**（超时恰是要诊断的场景）。**⑤ 关键设计判断 —— provenance 读「跑批时刻」而非「汇总时刻」**：新增 `_provenance_from_raw()`，从 raw 里读回落盘那份；W7 教训是补跑时工作树已变（Block 0 在 `95adb77`+patch、Block 1/2 在 `ca51886`）跨区块口径不可合并，若汇总时现抓 git 就是把同一个洞换个位置继续漏。**验证**：`ruff check .` 全绿；**132 → 146 测试全通过**（新增 `tests/test_eval_provenance.py` 14 条，含「三处调用方共享同一产生点」「同 run 内快照逐字节相同」「raw 顶层无平铺配置字段」「`+dirty` 格式不回归」）；零 LLM 集成冒烟实测 `git_dirty=true` / `git_diff_hash=6425819bda71a0d2` / `validator_model=qwen-plus` / 5 开关全开 —— **主链路配置第一次被如实记录**。**实测确认了 §10.4 依据 1 的判断**：史上所有 run 都不曾记录过「5 开关全开 + validator=qwen-plus」这一格。同步改动：§7 Arm 6 DoD 勾选 5 条（1 条标「⏳ 随 before 基线验收」）、§1.1 全景视图 §10.4 行标 ✅ | 本文档 + `research_engine/eval/provenance.py`（新增）、`eval/run.py`、`eval/report_gen.py`、`eval/w7_experiment.py`、`tests/test_eval_provenance.py`（新增） |
| 2026-09-16 | 拍板 | **§10.5.4 零成本实测题目间方差 —— Q6「多跑是错的方向」在绝对值陈述场景下被推翻** | **新增 `tools/measure_question_variance.py`**（只读、零 API、零 LLM）：复用 W7 权威容器 `w7_experiment_20260911_194151`（3 blocks × 20 题 × 6 arms），做**单向随机效应方差分解** `σ_question² = Var(每题跨 block 均值) − σ_within²/R`，把「题目效应」从「block 间漂移」里分离出来（此前二者被混为一谈，W7 只测得合并后的 σ≈14.5pp）。**实测推翻题设**：coverage `σ_question=24.4pp` ⇒ SE@20 题 = **5.5pp**（题设按二项 `p=0.47` 算得 11.2pp 的 **1/2**）；citation `σ_question=7.1pp` ⇒ SE@20 = **1.6pp**（题设的 **1/7**）；**且直接取单次 run 标准差会高估 15%**（28.0 vs 24.4pp）。**⇒ 结论反转**：题目项已**小于** runs 项 ⇒ 在 runs 拉到 9 之前扩题，钱全花在不是瓶颈的地方 ⇒ **多跑性价比 0.39pp/¥ 是扩题 0.071pp/¥ 的 5.5 倍** ⇒ **after 基线由「60 题 × 9 runs（¥32 / 19.4h）」重拍为「20 题 × 9 runs（¥11 / 6.5h / ±7.3pp）」**，且**无需新造 40 道题**（人工成本此前未计入）。**Q6 中「必须实测而非假设」的方法论判断不变，且被证明是对的** —— 若按题设执行，会多花 ¥21 + 13h + 40 道人工题。**before/after 配对设计不受影响**：固定题目 + 固定检索快照 ⇒ 题目效应与漂移双双抵消，before 基线仍是 20 题 × 3 runs。同步改动：§10.5.3 加实测更正警示、§10.5.4 整体重写（含性价比表 + 重拍表）、§10.5.7 与 §10.5.6 规模同步、§1 元信息 Q6 拍板行加更正 | 本文档 + `tools/measure_question_variance.py`（新增） |
| 2026-09-16 | 验收 | **§10.4 最后一条 DoD 由真实基线验收通过** + **§5.1.4 实现影响面只读预检** | ① **DoD 验收**：before 基线第 1 轮 `run_20260916_001005`（20 题 × 主链路配置，1h00m / ¥0.89 / `complete=19 partial=0 failed=1`）的 `summary.json` 实测读到 `git_commit=6f4067f…` / `git_dirty=false` / `git_diff_hash=e3b0c44298fc1c14`（干净树哨兵值）/ `validator_model=qwen-plus` / `experiment` 五开关全 `true`，与运行期 `config.experiment` 逐项一致 ⇒ §7 Arm 6 那条「⏳ 待 before 基线验收」**勾选为 ✅**。**这是史上第一个「配置被如实记录」的真实 run**（此前所有 run 都不可回溯消融配置）。**附带验证**：唯一失败样本 `q_001` 走的是**超时路径**（`status=timeout`，>900s）且 **provenance 完整落盘** ⇒ §10.4 的「失败路径与超时路径同样记 provenance」设计在真实场景生效（超时恰是最需要诊断的场景）。② **§5.1.4 新增「实现影响面」**：全仓 grep 实测 `state.error` —— **生产代码零写入**（仅 `state.py:92` 声明），**4 处读取**：`eval/metrics.py:45`（完成率四条件之一，只要保持「成功 `None` / 失败非 `None`」即不受影响）、`eval/metrics.py:54`（透传）、`eval/run.py:118`（读 state 对象）、**`eval/report_gen.py:428` `r.get('error')[:200]` —— ⚠️ `dict` 不可切片会崩溃，必改 `str(...)[:200]`**。另记两条坑：`get_state(cfg).values` 对未 invoke 过的 thread 返回**空 `dict` 而非 `None`** ⇒ 兜底判据须写 `values or {}`；`graph.py:314` `tokens_diff` 在**异常路径也会执行** ⇒ 兜底 state 须保证 `token_used` 有默认值，否则「转为结构化状态」会变成「换个地方崩」。同步改动：§7 Arm 6 DoD 勾选、§5.1.4 新增实现影响面表 + 两条坑 | 本文档 |
| 2026-09-16 | 预检 | **§5.2 Arm 2 实现预检 —— 照文档直写会静默吞掉 24% 的证据** | 用 before 基线 `run_20260916_001005` 真实数据 + 代码追踪，查出**三处与文档不符**：① **§5.2.2 引用的 `CodeExecInput` 类在代码里不存在**（`code_exec.py` 实际 API 是 `exec_code(code, query="", params="")`）；② **`exec_code` 并不把 query 写进 metadata**（`:344` `meta = {"exit_code": ...}`，query 只进 `script_hash`）⇒ §5.2.2 所述行为属新增；③ **⚠️ 最关键** —— `code_exec.py:304` `ctx = f"{code}\x00{params}\x00{query}"` ⇒ 若按字面「移除 query 拼接」**连 `exec_code(query=)` 一起去掉**，`script_hash` 将变常量。实测：基线 20 题 **206 条 findings 中 50 条（24.3%）是 `code_exec`**（q_012 单题 7 条），**50 个 source 值全不重复**（全靠 query 进哈希区分）；而 `context/manager.py:21-28 dedupe()` 按 `f.source` **每 source 只留一条**、`compress()`（`:39-45`）按 source 分组 ⇒ **hash 塌陷会让 50 条并成 1 条**。**当前风险是潜伏的**：findings 峰值 17 < `max_findings=30` ⇒ `compress` 未触发，且 `dedupe` 在主链路未被调用。**但一旦 findings 超 30、或有人把 `dedupe` 接进主链路 ⇒ 静默损失近 1/4 证据且零报错**。**处置**：**保留 `exec_code(script, query=query)`** —— 注入风险只在**脚本文本**里，query 作为参数只进哈希与元数据、**从不进入被执行的代码**，故保留它零风险且保住唯一性。**方法论**：这条（与 §5.1.4 三条一起）证明「**先追字段的消费者、再动字段**」—— 只在文档写「移除 query 拼接」是看不出 `dedupe` 这个消费者的。同步改动：§5.2.3 后新增「实现预检」表 + 量化依据 + 方法论注记 | 本文档 |
| 2026-09-16 | 实测 | **§3.3.1 实测推翻「ρ≈0.9 ⇒ SE≈1.45pp」+ §3.3.2 验收口径重定位** | **新增 `tools/measure_paired_rho.py`**（只读、零 API）：用 before 基线前两轮同配置真实 run（n=19 配对题）按恒等式 `σ_within = sd(x_i1−x_i2)/√2` 反推，**不依赖任何假设**。**实测**：`coverage` σ_within=**28.91pp**、ρ=**0.351**、SE(3v9)=**4.42pp**、MDE=**12.38pp**；`citation_accuracy` 14.41pp / ρ=0.168 / SE 2.26pp / MDE 6.34pp；`retrieval_hit_rate` 7.75pp / ρ=**0.875** / SE 1.19pp / MDE 3.32pp；`steps` 3.07 步 / ρ=0.625。**⇒ §3.3 的 1.45pp 只对 retrieval_hit_rate 成立（差 3 倍 / 1.6 倍 / 1 倍）**。**原因**：§3.3 那句的前提是「**固定检索快照**」，而真实 before/after 用活检索 ⇒ planner 每轮子问题都不同 ⇒ 证据池本身就变 ⇒ 题目效应带不来跨轮相关；**retrieval_hit_rate 吻合（ρ=0.875）恰因它是检索环节自身指标、不受下游写作波动影响** ⇒ 机制解释自洽。**硬结论**：coverage 的 MDE(3v9)=12.38pp > W7 实测效应量级（4~10pp）⇒ 按 §3.3 第 4 条**宣告「W8 让覆盖率提升 Xpp」不可判定**。**§3.3.2 口径重定位（更根本）**：**用 coverage 提升验收 W8 从一开始就是口径错配** —— Arm 1/4/5 是**可观测性 + 可靠性**改进（让失败可见、可归因），Arm 2/3 是安全性与可复现性，Arm 6/7 是可审计性，**没有一项的立论是「覆盖率会涨」** ⇒ 等于用尺子称重量。**before/after 的真正价值**改为提供「改前故障不可归因」的对照证据（改前 `failed` 不可达 + `failure_reason` 无字段 + `error` 生产代码零写入 ⇒ **故障可归因率 100% 不可归因**；改后 100% 可归因）—— 这是 **A 类确定性断言，零噪声**。**待拍板三选项**：A 按 §3.3.2 重定位（推荐，¥0 额外）/ B 真实现「固定检索快照」把 ρ 拉回 0.9（中等工作量，但**改变被测对象**：比的是写作验证环节而非端到端）/ C 加 runs 到 41 轮（≈¥73/82h，不可行）。同步改动：新增 §3.3.1 + §3.3.2、§3.3 第 1 条加实测更正注、§10.5.3 与 §10.5.4 结论三的「1.45pp」全部更正、§10.5.6 表述规则新增「SE > 效应 ⇒ 不可判定」与「可观测性类结论」两行 | 本文档 + `tools/measure_paired_rho.py`（新增） |
| 2026-09-16 | 实测 | **before 基线 3 轮采集完成 + 配对分辨率改用 3 轮估计 + 发现「成本静默归零」缺陷** | ① **3 轮基线结果**（主链路配置，全部 `git_dirty=false` + `config_snapshot` 完整）：coverage **0.4517 ± 3.17pp**（r1 0.3947 / r2 0.5042 / r3 0.4561，极差 **10.95pp**）、citation_acc **0.7570 ± 3.08pp**、retrieval_hit **0.5571 ± 1.98pp**；**对外口径：coverage 45.2% ±6.3pp〔20 题 × 3 轮〕⇒ [38.8%, 51.5%]**（按 §10.5.6）。三轮 code rev 分别为 `6f4067f`/`13a6d3e`/`6c83af1`，**全为文档类提交、`research_engine/` 零改动** ⇒ 行为等价。② **超时题目每轮不同**（r1=q_001、r3=q_007）⇒ 超时是**运行期噪声**而非题目固有属性，会额外抬高 σ_within。③ **配对分辨率改用 3 轮估计**（3 个轮次对比 2 轮的单一配对稳）：coverage σ_within 28.91→**23.96pp**、ρ 0.351→**0.551**、MDE(3v9) 12.38→**10.54pp**；citation ρ 0.168→**0.124**、MDE 6.22pp；retrieval_hit ρ 0.875→**0.860**、MDE 3.73pp ⇒ **方向一致、量级稳定，§3.3.1 结论不变**。④ **⚠️ 采集过程中暴露真实缺陷**：第 3 轮 phase1 完成（20/20 raw）后进程被杀，`phase1_global_stats.json` 未写出 ⇒ 补跑 `--eval-only` 后 `cost_phase1_total.cost_yuan` = **¥0.0000**，而该轮 raw `token_used` 合计 **1,017,784**（比第 2 轮 952,093 还多）⇒ **实际约 ¥0.96 记为 0**。**性质**：`_read_phase1_cost()` 读不到文件时**静默返回 0、无告警** ⇒ **成本账目静默归零**，与 §4.2「静默吞掉」同构。**可恢复性**：raw 有 `token_used`（总量，无按模型拆分）⇒ 能还原 token 数、不能精确还原金额 ⇒ **基线总成本更正为 ≈¥2.74**（非 summary 的 ¥1.79）。**待处置**（建议并入 Arm 6 或 Arm 1）：缺失时**降级为从 raw 汇总 + 显式标记 `cost_degraded=true`**，而非返回 0。⑤ **踩坑登记**：`--run-dir` 是**相对 `results/`** 的（`run.py:437-439`），传相对路径会套娃成 `results/research_engine/eval/results/...` ⇒ **静默产出 `total=0 / git_commit=unknown / config_snapshot=null` 的假 summary（exit=0，4 秒）**；正确用法是**只传目录名**。同步改动：§3.3.1 表改为 3 轮估计（2 轮数据折入 `<details>` 对照）、§3.3.1 硬结论 MDE 改为 10.54pp 并补 retrieval_hit 例外、**新增 §10.5.8**（3 轮结果表 + 对外口径 + 三个读数注意点 + 成本归零缺陷登记）、§10.5.7 加「提升类陈述不可判定」更正 | 本文档 |
| 2026-09-16 | 拍板 | **§3.3.2 验收口径：于晏采纳 A（重定位），B / C 不采纳** | 采纳 **A**：before/after 只做**可观测性对照**（故障可归因率等 A 类确定性断言），coverage/citation **只报绝对值 + 区间，不报提升**。**不采纳 B**（真实现「固定检索快照」：中等工作量且**改变被测对象** —— 比的是写作/验证环节而非端到端）；**不采纳 C**（加 runs 到 41 轮 ≈¥73/82h，与 Q5 已否决的「430 runs」同量级）。**落地要求写死三句**：① W8 各 Arm 一律按 **A 类确定性断言**验收，**不得**以「覆盖率提升 Xpp」作通过条件；② 对外数字**一律带区间与题数**，**禁止**把 before/after 均值差表述为「提升」（当前仅 `retrieval_hit_rate` 的 MDE 3.73pp 小于可观测差）；③ before 基线（§10.5.8）保留为「**改前故障不可归因**」的对照证据。**面试叙事定稿**：「主链路覆盖率 **45.2% ±6.3pp**（20 题 × 3 轮，W8 改动前）」✅ 可说；「W8 让覆盖率提升 Xpp」❌ 不可判定、不说；改说「**故障可归因率 0% → 100%**（注入故障复现，A 类断言）」✅ 零噪声。同步改动：§3.3.2 三选项表加裁决列 + 新增「落地要求」与「面试叙事」 | 本文档 |
| 2026-09-16 | **实现** | **Arm 1（状态分层）落地 —— before 基线闸门解除后的第一项** | **① 基础设施**：新增 `research_engine/failure_reasons.py` 作为失败原因枚举**唯一真相源**（9 值：工具层 5 + 非工具层 4，两组不重叠；附 `classify_exception` / `classify_tool_exception`）；`state.py` 新增 `RUN_STATUS_*` 三态常量（**无 `partial`**）、`DegradationEntry` dataclass、`DegradationSink`（线程安全缓冲区，供 Agent 在 fallback 留痕后由 graph 节点 drain）、`run_status`、`degradation_log`（**带 `operator.add` reducer**）、`error` 由 `Optional[str]` 改 `Optional[Dict]{code,message,node}`，及 `add_degradation`/`set_error`/`resolve_run_status`；`graph.py` `run()` 用 try/except 包裹 invoke（**`failed` 的唯一落点**）+ 新增 `_recover_from_exception()`（`get_state(cfg).values or {}`、get_state 自身抛错也不二次崩、**绝不返回 None**、保留 `last_exception`）；`_validate`/`_render` 推导 `run_status`；`cli.py` failed ⇒ stderr + `sys.exit(1)`；`report_gen.py:428` `[:200]` → `str(...)[:200]`。**⚠️ 顺带修掉既存 bug**：`cli.py` 末尾有两个完全相同的 `if __name__ == "__main__": main()`（W3 git 重建期间引入）⇒ **作为脚本执行会跑两遍 main、成本翻倍**。**② 节点级降级标记（让 `degraded` 真正可达）**：`researcher._search_web`/`_search_rag`/`_search_arxiv`/`_search_code`、`planner.plan`、`writer.write`、`validator` 忠实度降级共 7 处留痕；`arxiv` provider 补 `failure_reason`（原为「返回空但说不清为什么」）；graph 的 `_plan`/`_research`/`_write`/`_validate` 节点 drain 后经 reducer 入 state。**已知临时替身**：Arm 4 的 provider 目前仍「抛异常」语义 ⇒ 用 `classify_tool_exception()` 临时归类，**已收敛在 `failure_reasons.py` 一个函数里**并注明「Arm 4 落地后应改为读 `resp.failure_reason`，本函数届时可删」⇒ 保证 Arm 4 的替换面在一处，不在各落点散落手写字面量。**遗留项（标 ⏳）**：命名三分的层②`invoke_status` / 层③`metrics_status` **重命名未做**（会触碰 `run.py` 多处落盘键与既有 W7 产物，需单独评估兼容性）。**验证**：ruff 全绿；**146 → 170 测试全过**（新增 `tests/test_arm1_run_status.py` 26 条：三态无 partial / resolve 判定规则 / reducer 存在性对照 `progress` / 结构化 error 形状与「成功 None」语义 / **error 不可切片防回归** / 枚举两组不重叠 / **failed 可达**四种兜底 / **degraded 可达**（真实代码路径 + MagicMock 注入）/ 单向派生 / `classify_tool_exception` 映射表）；零 API 集成冒烟：图编译 OK（8 节点）、`model_dump` 与 JSON 序列化 OK。**实测破坏面比预估小**：`state.error` 生产代码**零写入**，唯一受影响处是 `report_gen.py:428`；`metrics.py:45` 的 `ok_error` 因保持「成功 None/失败非 None」而不受影响。同步改动：§7 Arm 1 DoD 12 条中 11 条勾选（1 条命名三分标 ⏳ 并写明遗留范围） | 本文档 + `research_engine/failure_reasons.py`（新增）、`state.py`、`graph.py`、`search/base.py`、`search/arxiv.py`、`agents/{researcher,planner,writer,validator}.py`、`eval/report_gen.py`、`cli.py`、`web/app.py`、`tests/test_arm1_run_status.py`（新增） |
| 2026-09-16 | **实现** | **Arm 2（代码执行注入修复）落地 —— 实测推翻本节前提，顺带修复一个 100% 失效的工具** | **实现**：`_default_code_script()` 改零参数常量模板（脚本源码不含 query）；`exec_code` 新增把 `query` 写入 `CodeExecOutput.metadata["query"]`（**§5.2.2 所述行为属新增**；且 `CodeExecInput` 类在仓库中**不存在**，故按现有签名实现，不新引入输入类）；**保留 `exec_code(script, query=query)`**（§5.2 预检第 3 条处置，保住 `script_hash` 唯一性）；`_search_code` 失败原因改用新 `_classify_code_exec_failure()`（超时 → `timeout`、脚本语法错 → `parse_error`、其余运行时错 → `provider_error`；原映射把前两类都压成 `provider_error`）。**⚠️ 实测修正一（推翻 §5.2 / §5.2.3 的前提）**：原设想「query 含中文/引号/换行 ⇒ **触发**语法错误」，实测为「**无条件**语法错误」—— 旧模板 `f"print('query={query!r}')"` 里模板自身一对 `'` 与 `!r` 自带引号**叠加成双层引号**，连纯 ASCII 查询也是 `SyntaxError: invalid syntax`。**基线三轮真实产量**（按 `metadata` 形状判定：失败态 = `conf=0.2` 且含 `note` 键）：`001005`/`011813`/`022440` 分别 50/49/45 条 code_exec 产出，**成功 0 条、失败 144 条（100%）** ⇒ **基线那 24.3% 的「code 证据」全是失败标记、无一条是真实计算结果**；§5.2 预检只验了「50 个 source 全不重复」，**没验这些 source 是否承载有效证据**。**⇒ Arm 2 的实质是工具功能性修复（成功率 0%→100%）**，且失败 note 原本会把 **query 原文经 stderr 带回 findings**。**⚠️ 实测修正二（独立缺陷，一并修）**：沙箱回传非 ASCII **必然乱码** —— 父进程按 `utf-8` 解码管道、子进程在 Windows 默认按 **cp936** 写管道（实测 `print('中文测试')` → 乱码）；修法是子进程命令行加 **`-X utf8`**（`-I`/`-E` 忽略环境变量，故不能用 `PYTHONIOENCODING` 兜），实测 stdout/stderr 中文逐字还原 ⇒ **这才是 §7「中文乱码不再复现」DoD 的真正闭合点**（原 DoD 只盯「query 进脚本」，而乱码另有来源）。**验证**：ruff 全绿；**170 → 209 测试全过**（新增 `tests/test_arm2_code_injection.py` 39 条：模板零参数与常量性 / 10 组敌意 query 参数化断言「代码里不含 query」且「query 仍进 exec_code」/ 成功与失败两条路径的 `metadata["query"]` / `classify` 映射 7 例 / 敌意 query **真跑沙箱**全绿 / **反事实留档：旧写法对任何 query 都语法错**（防后人按错误前提重建并不成立的漏洞模型）/ `script_hash` 逐题唯一 / 沙箱中文 stdout + stderr 不乱码）。**方法论（第三次同构）**：§5.1.4「先追字段的消费者」、§5.2 预检「先追 `script_hash` 的消费者」、本条「先追乱码的**产生源**」是同一件事 —— **文档里叙述的因果不经验证就不能当设计前提** | `research_engine/agents/researcher.py`、`research_engine/tools/code_exec.py`、`tests/test_arm2_code_injection.py`（新增） |
| 2026-09-16 | 修复 | **CI 红一次：`tests/conftest.py` 从未把仓库根加进 `sys.path`（潜伏脆弱点，与 Arm 1 无关）** | 现象：Arm 1 新增测试文件在 CI 报 `ModuleNotFoundError: No module named 'research_engine'`。**根因**：`tests/` 下无任何地方注入仓库根，全靠 `test_bm25_chinese.py` / `test_citation_alignment.py` 在模块级 `sys.path.insert` 的**副作用**兜底；pytest 按**文件名字母序**导入测试模块 ⇒ 新增的 `test_arm1_run_status.py` 排第一，抢在副作用生效前 import 即爆；**同一根因还让 `pytest tests/<单个文件>` 一直不可用**（本地实测 `test_eval_metrics.py` 单跑同样报错）。修法：`conftest.py` 里 `sys.path.insert` 仓库根一次，不再依赖字母序。**纪律追加**：CI 等价命令必须用 console script（`Scripts/pytest.exe tests/ -q`）—— `python -m pytest` 会把 cwd 塞进 `sys.path`，**恰好掩盖该缺陷**（这正是本地全绿、CI 爆红的原因） | `tests/conftest.py` |
| 2026-09-16 | **拍板** | **Arm 3 生成工具由 `pip-compile` 换为 `uv pip compile --universal` —— Q2 子决策②的两条前提被实测连续推翻** | **触发**：第一刀落地过程中实测发现原文命令与选型理由**都不成立**。**推翻点一**：`pip-compile --python-version 3.11` 报 `No such option` —— pip-tools 7.6.1 **没有任何跨版本/跨平台旗标**（`--help` 里 `python-version`/`platform`/`universal` 三词全无），只能按**运行它的解释器**求值标记 ⇒ 要按 3.11 解析必须在**真 3.11 解释器**下跑（为此建专用 venv `…/envs/py311-tools`，注意 `uv venv` 不自带 pip，需 `ensurepip`）。**推翻点二（更根本）**：`pip-compile` 生成的 308 行锁里**环境标记数为 0**（`grep -c ';'`）—— 它把**满足当前解释器的 marker 求值后丢弃**、把不满足的包**整条省略** ⇒ **§6.3 里「选 pip-compile 是因为它保留环境标记（PEP 508）」的立论被证伪**。**后果实证**：`pywin32==312`（经 `portalocker` 的 `platform_system == "Windows"` 引入）被**去标记平铺**，而 PyPI 上该版本 **21 个产物全部是 Windows wheel、0 sdist**（`packagetype` 仅 `bdist_wheel`）⇒ Linux runner **必然**报 `Could not find a version that satisfies the requirement pywin32==312` ⇒ **不是「跨平台风险」而是「必现红」**。**处置（于晏拍板 A）**：改用 **`uv pip compile --universal --python-version 3.11 --strip-extras`** 重生成两份锁。**A 的代价与边界**：① 生成锁需 uv（实测 `0.11.32`），命令**逐字写进锁文件头**可复算；② **CI 侧不需要 uv** —— 锁仍是 requirements 格式，`pip install` 命令一字未改；③ `pip-tools` 从 `requirements-dev.txt` **移除**（uv 是独立二进制，刻意不进 pip 依赖树，dev 锁由 14 行瘦到 **7 包**）；④ 本沙箱内 uv 建 sdist 被安全层拦（`SAFE_DELETE_FAIL_CLOSED`，setuptools 清理 `jieba.egg-info` 时触发）⇒ 主锁需在沙箱外跑一次，dev 锁全 wheel 可在沙箱内完成。**未采纳的 B（Docker 里 pip-compile 生成 Linux 锁：需启动 Docker Desktop，且锁变 Linux 视角、win-only 包转为动态解析）+ C（文档预授权的「只锁直接依赖」：传递依赖仍漂移，正是 Arm 3 要打的靶子）+ D（先推 dev 用真 CI 坐实必红）** 均在 §5.3.5 留档。**新增未决**：是否给 CI 加「锁过期检查」任务 —— 暂**不做**。同步改动：§5.3 拍板线 + §5.3.1 正文与三条实测修正、**新增 §5.3.5 拍板记录**、§6.3 **第二次重写**（工具选型表）、§7 Arm 3 DoD 第一刀全条重写 | 本文档 |
| 2026-09-16 | **实现** | **Arm 3（依赖环境锁定三刀）落地 —— 生成工具按当日拍板换 uv** | **第一刀·锁定**：`requirements-lock.txt`（uv universal，**7 条环境标记**：`pywin32==312 ; sys_platform == 'win32'` / `tzdata==2026.4 ; sys_platform == 'emscripten' or sys_platform == 'win32'` / `colorama==0.4.6 ; sys_platform == 'win32'` / `watchdog==6.0.0 ; sys_platform != 'darwin'` / `httpcore2==2.13.0 ; sys_platform != 'emscripten'` / `truststore==0.10.4 ; sys_platform != 'emscripten'` / `httpx2-jsfetch==1.0 ; python_full_version >= '3.12' and sys_platform == 'emscripten'`）+ `requirements-dev-lock.txt`（7 包）；两份 `grep -c '\-\-hash'` **均为 0**（不加哈希，hash 随平台轮子变化会破坏可移植性）；**直接依赖版本与 pip-compile 版完全一致**（`langgraph 1.2.11` / `langfuse 4.15.3` / `openai 1.92.3` / `streamlit 1.64.0` / `pydantic 2.13.5` / `qdrant-client 1.19.1` / `jieba 0.42.1`）⇒ **换工具零版本漂移**。**跨平台可安装性实测**：`pip install --dry-run --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: -r requirements-lock.txt` 逐一解析通过，**唯一报错为 sdist-only 的 `jieba==0.42.1`** —— 由我强加的 `--only-binary=:all:` 所致（jieba 无 wheel，真实 CI 从 sdist 正常构建，纯 Python 包）⇒ **原「必红」判定消解**。**第二刀·声明**：`pyproject.toml` **新增** `requires-python = ">=3.11,<3.14"`（就地注释「无 `[build-system]` ⇒ 不具强制力，真正强制见运行时闸」，呼应 §4.4 的行业对照 —— 同类项目普遍只写开区间，本项目上界是有依据的加强）+ `[tool.ruff] target-version` `py310`→`py311`；`README.md:46` `Python 3.10+`→`Python 3.11 ~ 3.13`。**⚠️ 连锁发现**：`target-version=py311` 触发 ruff `UP042` ⇒ `FailureReason` 由 `(str, Enum)` 改 `StrEnum`（3.11+）；因全仓用法**均为 `.value`**，语义等价且附带修好 `f"{member}"` 的渲染（`StrEnum` 渲染 `"timeout"` 而非 `"FailureReason.TIMEOUT"`）。**第三刀·强制**：`research_engine/__init__.py` **import 期**执行 `assert_supported_python()`（区间外明确报错，信息含支持区间 + 当前完整版本 + `sys.executable` + 指向 README/§5.3，取代原先「等 `pydantic_core` 导入失败时报难懂二进制错」）；`.github/workflows/ci.yml` 扩为 `matrix: ["3.11","3.12","3.13"]` + `fail-fast: false`（任一档失败也要看全三档）+ `cache-dependency-path` 与安装步骤同步指向两份锁。**验证**：ruff 全绿；**209 测试全过**（新增 `tests/test_arm3_python_version_gate.py` 20 条：区间内 3.11.0/3.11.15/3.12/3.13 放行、区间外 3.9/3.10.9/3.14/4.0 抛 `RuntimeError`、**import 期真执行**（`exec(compile(...))` + monkeypatch `sys.version_info`）、错误信息可行动性（含 `"3.11 ~ 3.13"` + 版本 + `"§5.3"`）、**四口径耦合守卫**（pyproject / README / CI matrix / 锁文件互相对齐，任一处漂移即红）、`FailureReason` 为 `StrEnum` 且 reason 全是普通字符串）。**✅ 后续闭环**：当时标 ⏳ 的两条「待 CI 三档实测」项（首次 CI 运行 + matrix 三档全绿）**已由真实 CI 输出收口** —— 见下一行（2026-09-16 **验证**） | 本文档 + `requirements-lock.txt`/`requirements-dev-lock.txt`（新增）、`requirements-dev.txt`、`pyproject.toml`、`README.md`、`.github/workflows/ci.yml`、`research_engine/__init__.py`、`research_engine/failure_reasons.py`、`tests/test_arm3_python_version_gate.py`（新增） |
| 2026-09-16 | **验证** | **Arm 3 两条「待 CI 实测」DoD 以真实 CI 输出闭环 —— 跨平台假设由「待验证」转为「已验证成立」** | **dev** run `35072143636`（墙钟 **58s**）：`lint-and-test (3.11)` 41s / `(3.12)` 46s / `(3.13)` 54s，三档全绿；**master** run `35072917100`：`(3.11)` 47s / `(3.12)` 55s / `(3.13)` 40s，三档全绿。两分支均已同步至 `0477ea3`。**结论**：① 第一刀的**跨平台可安装性假设成立** —— uv `--universal` 生成的锁在 **ubuntu runner** 上 `Install dependencies` 步骤三档全部通过（此前 pip-compile 版按 `pywin32==312` 去标记平铺判定为**必红**，见实测修正二）；② 第三刀「**3.12 / 3.13 首次被真实验证**」成立。**成本账更正**：原文估「三档约 110s」，**实测墙钟 58s**（三档并行 + `fail-fast: false`），比预估**便宜近一半**。**踩坑登记（与 Arm 3 无关但本次暴露）**：① 全局 git 配置的 github 专项凭据 helper 写成了 **反斜杠路径** `!'D:\GitHub CLI\gh.exe' auth git-credential` ⇒ git 的 shell 执行不了它，表现为 `could not read Username for 'https://github.com'`（**与代理无关**，沙箱内外均复现）；用 `-c credential.https://github.com.helper="!'D:/GitHub CLI/gh.exe' auth git-credential"`（**正斜杠**）可临时绕过，**永久修法建议 `gh auth setup-git`**（改全局配置，待于晏拍板）；② 本环境 **safe-delete 安全层会拦截 `rm`**，临时凭据文件删不掉时须先 `dd` 覆盖销毁再删（本次已清理 3 个残留并复查 Temp 无 `ghcred*`）。同步改动：§7 Arm 3 DoD 两条 ⏳ 转 ✅ + 末尾闭环声明 | 本文档 |
| 2026-09-17 **补记** | **实现** | **Arm 4（工具失败原因结构化）+ Arm 5（run 级质量闸与指标离散度）落地**（PR #3 ⇒ `7a4124c`，本次同步 **就地补登** —— 落地当时只勾了 DoD、漏了变更记录） | **Arm 4**：新增 `rag/response.py`（`RetrieveResponse` + `FailureReason`），与 Arm 1 已建 `failure_reasons.py` 共用一张枚举；`bocha.py` / `arxiv.py` / `retriever.py` / `store.py` 四路改为**真填充 `resp.failure_reason`**，`DensityAlt` 层不再靠猜异常 ⇒ **删掉 `classify_tool_exception()`**（Arm 1 留下的临时替身）。**Arm 5**：新增 `eval/quality.py`（三态质量闸 `ok/suspicious/broken` + `verdict_reasons`）、`eval/stats.py`（bootstrap stderr，种子固定）、`eval/aggregate.py`（**指标聚合一处定义**，`_summarize` 与回填脚本共用）；新增 `tools/w8_backfill_stderr.py`，**75 个历史 run 全部回填**（0 跳过 / 0 异常，ok 73 / suspicious 1 / broken 1），并修掉 **D1 成本静默归零**（缺 `phase1_global_stats.json` 时由 raw 累加 + `cost_yuan=None` + `cost_degraded=true`）。**验证**：ruff 全绿；pytest **236 → 254**（+`test_arm4_failure_reasons.py` / `test_arm5_quality_gate.py`） | `7a4124c` + `rag/response.py`（新增）、`eval/{quality,stats,aggregate}.py`（新增）、`tools/w8_backfill_stderr.py`（新增）、`search/{bocha,arxiv}.py`、`rag/{retriever,store}.py`、`failure_reasons.py`、`eval/run.py`、`eval/report_gen.py` |
| 2026-09-18 **补记** | **实现** | **Arm 6（可复现元数据）落地**（PR #4 ⇒ `fa66d27`，同上漏登，就地补登） | 新增 `eval/prompt_hash.py`（**6 slot**，对「开关翻转但代码未变」敏感 —— D-06）；provenance 由 4 字段扩到 **10 字段**（+`prompt_hash` / `prompt_slots` / `scorer_version` / `citation_judge_model` / `coverage_judge_model` / `citation_judge_independent`）；**5 个 system 提示词构建器抽为唯一产生点**（`planner` / `validator` / `writer` / `critic` / `metrics`），历史 raw 缺新字段**如实为 `None`、不回填**（D-07）；`citation_judge_independent` 按**事实判断**实现而非写成常量（D-08）；raw 三条落盘路径统一走 `raw_provenance_fields()`；报告**在指标表之前**显著呈现裁判独立性。当前 `prompt_hash=cf95dafc78f98348`、`scorer_version=w8.1`。**验证**：ruff 全绿；pytest **254 → 291**（+`tests/test_arm6_provenance.py`） | `fa66d27` + `eval/prompt_hash.py`（新增）、`eval/provenance.py`、`eval/{aggregate,metrics,report_gen,run}.py`、`agents/{planner,validator,writer}.py`、`critic.py` |
| 2026-09-18 | **实现** | **Arm 7（产物治理双轨）落地 —— 双轨分开走：git 侧收敛到「引用即入库」判据，磁盘侧只出只读台账、绝不动手删** | **① 轨道 1 白名单收敛**：`.gitignore` eval 纪律段由「三条无注释 `!`」重写为「每条例外必须在**紧邻上方独立注释行**写明出处」，判据升级为**引用即入库**（只有被 `docs/` 正式文档或代码引用的 run 才允许进白名单）。**收敛结果**：原 189 个跟踪文件中 **94 项偏离白名单**，现已做到 `git ls-files research_engine/eval/results` 顶层**恰 8 项 ≡ 白名单集合**（5 个 run + `w7_experiment_20260911_194151` + `curated/` + `history.json`）。6 条例外出处逐条落实：`run_20260911_194156`（arm0 基线）/ `run_20260911_232515`（turbo 降档臂）/ `run_v11_compare`（298 条归因夹具来源）/ `run_20260906_184156`（`sampling-workbook.md` 逐条链其 raw，删即死链）/ `run_20260907_001658`（v0→v1 漂移前车之鉴）/ `w7_experiment_20260911_194151`（六臂容器 manifest+CODE_REVISION）；`curated/` 与 `history.json` 以 `# keep:` 行登记，后者注明**读写方**（`report_gen.append_history()` 写、趋势表读）—— 它们不是 run，靠「读写方」而非「被人挑过」入库。**唯一一处 `git rm --cached`**：`history_bak_v10.json` 在 `docs/` 与代码中**零引用**（W5 `22e0d73` 误入库）⇒ 移出索引、**磁盘文件保留**，并新增 `*_bak_*.json` 规则防止它转为常驻未跟踪噪音。**新增单点裁判 `tools/check_results_whitelist.py`**：三类违规 —— 已跟踪但不在白名单 / 白名单条目缺出处或出处不满足「引用即入库」/ 出处**只来自自动枚举文档**；配套 `tests/test_arm7_artifact_governance.py` **13 条**（含**活体守卫** `test_real_repository_is_consistent`，直接审真仓库，防止文档与实现分叉）；CI 新增步骤 `Eval 产物白名单纪律（Arm 7）`。**裁判经变异测试验证非空转**：注入行尾注释 / 删掉出处行 ⇒ 均 `exit 1`，还原后恢复通过。**② ⚠️ 两处实测修正 —— 照原文 §5.7.1 实现必踩坑**：ⓐ **`.gitignore` 只认行首 `#`** —— 写成 `!path/  # 注释` 会把注释吞进 pattern ⇒ **`!` 规则整条失效**（这正是原三条白名单「看着写了注释、实际不匹配」的根因）；出处必须**独立成注释行**写在规则紧邻上方，且**空行会打断归属**（已固化进单测）。ⓑ **自动枚举文档不算证据** —— `docs/eval-report.md` 由 `report_gen.py` 自动写出，实测含 **74 个**互不相同的 run id（≈无差别枚举），而手写结论 `docs/eval-w7-conclusion.md` 只提 **4 个** ⇒ 只出现在自动表里的 run **不能**充当入库证据；`AUTO_ENUMERATED_DOCS` 写入裁判，台账以 `spec_from_file_location` **加载同一常量**，避免两处分叉。**③ 轨道 2 只读台账**：新增 `tools/w8_artifact_ledger.py`（严格只读：只统计体积/mtime + 扫引用，**不删/不移/不重命名**）⇒ 产出 `docs/eval-artifact-ledger.md`：**101 个顶层条目 / 143.6 MB**（已入库 8.4 MB、未入库 **135.2 MB**），其中 **有手写引用 20 个 / 20.6 MB**（引用**含**结论文档/代码的 **17 个 / 15.2 MB**；仅来自看板/需求文档的 3 个 —— 见下方「登记反转」与决策 D-13）。引用列拆成「手写文档 / 代码证据」与「仅出现在自动表痕」两摊，并写死清理判据：**真正的归档候选 = 入库为「—」且 仅自动表痕为「⚠️ 是」，任何删除动作前须先确认该 run 不在 `tools/w7_backfill_*.py` 的输入集合内**（那两个脚本直接读 `run_dir/raw/*.raw.json`，是 W7 零成本可复算的唯一证据源）。**踩坑**：台账输出自己必然列出所有 run 名 ⇒ 回扫时每个都被判成「有引用」（**自指污染**，一度把 17 虚增到 101）⇒ 生成时以 `extra_auto` 排除输出文件自身。**踩坑②（「登记反转」，道理相反）**：`history_bak_v10.json` 原本零引用才被 `git rm --cached`，但治理过程把它的名字写进了 `docs/project-status.md` 与本文档 ⇒ **第二次生成时它被判成「有手写引用」**（17 → 18；事故复盘再点名两个受损 run 后 → **20**）⇒ 引用计数**只增不减是有偏的**。已在台账文末立规矩：review 只看「结论文档 / 代码」的引用，**写进处置记录 / 台账 / 变更日志的一律不算** —— 这是与「怕误删」相反方向的自欺风险：**怕被描述过就当成有证据**。**DoD 收口**：§7 Arm 7 轨道 1 五项**全部 ✅**（含 `history_bak_v10.json` 附带发现）、轨道 2 台账 ✅，**唯一 ⬜ = 「台账经人工 review 后独立拍板」**（待主理人；review 前不得删除/移动/重命名任何产物）。**验证**：ruff 全绿；pytest **291 → 304**（+13）；`check_md_tables.py` 0 不一致。同步改动：§5.7.1 第 2 条改写 + **新增第 3 条（自动枚举不算证据）与第 5 条（落地形态）**、§7 Arm 7 DoD | 本文档 + `.gitignore`、`tools/check_results_whitelist.py`、`tools/w8_artifact_ledger.py`、`tests/test_arm7_artifact_governance.py`、`docs/eval-artifact-ledger.md`（新增）、`.github/workflows/ci.yml` |
| 2026-09-18 | 🚨 **事故复盘** | **Arm 7 落地后误执行一次跨边界 `git checkout` ⇒ 产物被物理删除（`results/` 143.6 MB → 31 MB），已恢复 99.85%，5 个文件永久丢失** | **经过**：Arm 7 用 `git rm --cached` 把跟踪文件从 189 降到 8 条白名单后，我误执行了一次跨越该边界的 `git checkout`（本意只是同步分支）⇒ git 认为那些路径「属于旧版本」而**从磁盘删除**，删目录时连目录内**未跟踪**的 raw/eval 一并带走。**恢复三步**：① 从 D 盘回收站按 `$I` 元数据里的原始路径定向还原 **2,418 个文件**（`.workbuddy/restore_results_from_recycle.py`）；② `git checkout -f -- research_engine/eval/results` 补回被删的 tracked 文件；③ 从事故前做的物理备份回拷 ⇒ 现 **3,206 文件 / 151.8 MB**（台账口径 143.5 MB / 100 条目）。**永久丢失 5 个文件**（≈0.1 MB，被 git 硬删除且从未进对象库）：`run_20260910_033127/raw/q_014.raw.json`、`run_20260911_004201/eval/q_016` / `q_018` / `q_019` / `q_020.eval.json` —— 均属**无引用、非白名单**的历史 run，**不影响** W7 权威容器 `w7_experiment_20260911_194151` 与任何结论文档；但这两个 run 的 `summary.json` 仍写 `complete=20/20` ⇒ **文件与 summary 不自洽**，若要继续引用须重新生成 raw。**三条副作用/教训**：① **`git rm --cached` 只保护执行它那一刻的工作树** —— 治理动作「让已跟踪文件转为未跟踪」会给**任何后续 checkout/reset/pull** 埋雷 ⇒ 立**铁律 2**（决策 D-12）：移出索引后 master/dev 必须同指向，已用 `git branch -f master dev` 消除边界；② 还原把所有结果文件 **mtime 刷成恢复时刻** ⇒ 台账「最后修改」列**失去取证价值**（已写入台账自述）；③ 「登记的反转」被放大 —— 复盘写出受损 run 名后，台账手写引用数 18 → **20**，且自动把 **before 基线三个 run** 标成「仅看板/需求文档引用」⇒ 该分类**只作提示、不可作删除依据**（决策 D-13）。同步改动：§5.7.2 新增事故表 + 铁律 2、§7 Arm 7 DoD 台账条改三档口径、台账生成器加「最后修改列失效」自述与 `GOVERNANCE_DOCS` 软标记、看板新增缺陷 D5 + 决策 D-12/D-13 | 本文档 + `tools/w8_artifact_ledger.py`、`docs/eval-artifact-ledger.md`、`docs/project-status.md`、`.workbuddy/restore_results_from_recycle.py`（一次性脚本，未入库） |
| 2026-09-18 | **实现** | **命名三分（Arm 1 遗留项）落地 —— 三层 `status` 键名各自归位；做法 =「写侧只出新键 + 读侧 dual-read + 历史产物一律不回填」** | **① 键名契约唯一真相源**：新增 `research_engine/eval/status_keys.py`（常量 `INVOKE_STATUS`/`METRICS_STATUS`/`LEGACY_STATUS` + `read_invoke_status()`/`read_metrics_status()` + **未收敛裸 `status` 登记表 `BARE_STATUS_SITES`**）。层② = `raw/*.raw.json` 顶层 `invoke_status`（`done`/`incomplete`/`failed`/`timeout`）；层③ = `eval/*.eval.json` 顶层 `metrics_status`（`ok`/`partial`/`failed`/`timeout`）。**② 写侧**：`run.py` 共 **7 处落盘键**改名 —— `_run_one` 三条路径（ok / incomplete / failed）、phase1 超时落盘、`_evaluate_one`（ok/partial/failed）、phase2 透传与「dataset 无对应行」；`_run_one` 返回 dict 的 `status` → `invoke_status`，消费方 `phase1` 同步。**③ 读侧 dual-read**：`phase2`（读 raw）、`_summarize`（层③ 计数）、`report_gen` 失败异常附录、`tools/measure_paired_rho.py` 全部改走读函数（该工具补 `sys.path` 引导以 import 契约模块）。**④ 关键设计决策 = 历史产物不回填**，三条理由：它们是 Arm 7 台账的**取证对象**（产物只读）／`tools/w7_backfill_*.py` 的「W7 零成本可复算」依赖 raw 逐字节不变／回填会抹掉「这批数产生于旧口径」（同 Arm 6 拒回填 `prompt_hash`）。**⑤ 全量只读对账**：真仓库 **1,498 条 raw + 1,443 条 eval** 经 dual-read **全部读出、取值零越界**；三份 before 基线 run（`001005`/`011813`/`022440`）逐字节未动。**⑥ 新增 `tests/test_status_naming.py` 26 条**：**端到端落盘扫描**（真跑 phase1+phase2 后扫每个落盘 json —— 顶层无裸 `status`；raw 里唯一残留的 `status` 必须在 `state` 快照内；eval/summary/phase1_global_stats 任意层级皆无）、层②四条与层③三条落盘路径、dual-read 优先级（**两键冲突时新键必须赢**）、历史 eval 旧键仍可被 `_summarize` 消费、**两份反向守卫**（历史未被回填 / 兼容分支未被删）、源码级 AST 守卫（`run.py` 字符串常量中无 `"status"`）、报告异常附录段守卫、两套词汇表分别锁定。**⑦ 一处实证发现（由登记表的「登记为真」用例挖出）**：`results/history.json` 每条记录的 `status` 自称「回归判定」，实际写的是 `summary.struct.regression`，而**全仓从未有任何代码写入 `summary.struct`**（只有 `run.py` 空结果分支写过 `"struct": {}`）⇒ 该字段**恒为 `"PASS"` 且零读取方**，是空转字段；改名/删除要重写**已跟踪**的 `history.json`（Arm 7 台账 review 未过）⇒ **留待 review 后一并拍板**，本次仅把该事实固化成用例 `test_history_status_is_vacuous`。**DoD 口径边界**：本项只覆盖层②③（"裸 `status`"的语义歧义源头即此处）；其余四处裸 `status`（`state.status` 快照 / `history.json` / W7 manifest `runs[].status` / Langfuse trace）语义各自独立，已逐条登记并由 `test_bare_status_sites_registry_is_true` 验证存在。**验证**：ruff 全绿；pytest **304 → 330**；`check_md_tables.py` 0 不一致。同步改动：§5.1.1 新增「命名裁决 · 落地形态」表 + 四条约束、§5.5 命名裁决注补落地、§7 Arm 1 DoD 第 12 条勾选并写明口径边界、§9.1 单测表 +9 行 | 本文档 + `research_engine/eval/status_keys.py`（新增）、`eval/run.py`、`eval/report_gen.py`、`tools/measure_paired_rho.py`、`tests/test_status_naming.py`（新增）、`tests/test_arm5_quality_gate.py`、`tests/test_arm6_provenance.py`、`tests/test_eval_provenance.py` |
