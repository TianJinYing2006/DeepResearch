# 需求-2-provenance-auditable-reporting

> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/HziXd6yc8oVr5NxyFh9cdltbn8c（DeepResearch 需求文档 / 第二周需求文档）
> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 重排说明：原计划（deepresearch-plan.md §4）W2 为 Langfuse 接入；本需求将 **W2 重排为「引用溯源可审计化呈现」**，Langfuse 顺延至 W3。理由：W1 闭环后真实跑 `cli.py` 一次，拷打式 review 暴露"可信/可审计"主卖点只做了一半（来源类型不可见、校验失败静默保留、自指论断未隔离），这是面试最会被追问的软肋，优先级高于可观测。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | #2 |
| 标题 | 引用溯源可审计化呈现（Provenance-Auditable Reporting） |
| 优先级 | P0 |
| 状态 | 草稿（2026-09-03 设计评审 定稿，7 题全部拍板，待实现） |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #2（待建） |
| 关联 PR |  |
| 创建 / 更新 | 2026-09-01 / 2026-09-03 |

## 2. 问题背景
W1 把反思循环暴露为图中显式 Critic 节点 + conditional_edge 后，真实跑一次 `cli.py "大语言模型在金融风控中的应用现状与挑战"`（2026-09-01）得到 33 findings、引用校验 50/58 通过。对该报告做拷打式 review，暴露 5 个系统性缺口——它们共同指向一个事实：**检索与引用链路已通，但"面向用户的溯源呈现"是残的**，与一个以"防幻觉 / 引用溯源"为核心卖点的 Agent 自相矛盾：

1. 报告正文只用 `[来源: N]`，**不标注来源类型**（web / rag）；读者无法区分哪些是知识库 RAG 注入、哪些是 web 检索。RAG 注入的可证性（本次 `[来源: 9]` = `rag_sample.md`）对读者不可见。
2. 50/58 通过，但 **8 条未通过校验的论断仍原样留在报告正文**，且报告末尾写"全部结论严格基于所提供的 33 项研究发现，未引入外部信息"——与"8 条未通过"直接矛盾。失败论断未被隔离、未被解释。
3. 校验实为"**来源字符串存在**"级（`validator.py:97` `verified = real_source in known`），并非"论断忠实于该来源"级；LLM 返回的 `confidence`/`note`（失败原因）在 `validator.py:112-122` 被**直接丢弃**，无法向用户解释"为何这条不可信"。
4. 报告把**系统自身架构**当研究发现引用（`[来源: 10]` 写"DeepResearch Agent 采用 Planner→Researcher→Writer→Validator 四节点编排"），属自指/元描述，易让读者误以为这是外部权威结论。
5. 运行状态（`depth`/`token_used`/`reflection_log`/`visited_sources`，`state.py:51-62`）**未在任何产出里呈现**，读者不知道循环跑了几跳、critic 判了几次、web/rag 各命中多少。

## 3. 需求分析
- 目标：把"防幻觉 / 引用溯源"从**内部机制**提升为**用户可读、可核验、可追责**的呈现层；让报告对每一条论断都能回答：来自哪（web/rag+具体源）、是否通过校验（含原因）、是否是系统自述（隔离）。
- 成功定义（可量化）：
  - ① 每条引用在 CLI/Web 产出中**显式标注来源类型**（web/rag）与具体源（URL 或 collection/文件名）；
  - ② 任一条未通过校验的论断**必须被列出并附原因**，在正文中以 ⚠️ 标记警示，且不得作为"已证实"事实在正文中示人 —— **正文保留 + ⚠️ 标记 + 末尾"未通过校验附录"双轨（设计评审 Q1=A）**，报告末尾的可信声明随失败数动态生成并显式标注校验口径；
  - ③ `verified` 升级为"来源存在 **且** 论断忠实于该来源"；校验失败论断携带 `note`（原因）；`Citation` 与具体 finding 通过 `finding_id` 锚定（设计评审 Q2=A/Q3=A）；
  - ④ 自指/元描述类发现被打 `is_meta` 标签，**绝不**作为证据出现在正文，单独成"方法论说明"小节；检测为**双层**：researcher 关键词初标 + validator verdict 复核兜底（设计评审 Q5=A）；
  - ⑤ 报告/CLI/Web 末尾附"运行溯源"块：总跳数、critic 决策次数（continue/revise/stop）、web/rag 命中数、token 消耗。

## 4. 当前设计
- `research_engine/state.py:21-27`：`ResearchFinding` 已有 `source_type`（web/rag）、`source`、`confidence`——**类型信息在 finding 层完备**。
- `research_engine/state.py:29-34`：`Citation` 仅有 `claim/source/verified/supported`，**缺 `source_type`、缺 `confidence`、缺 `note`、缺 `finding_id`** → 缺口①③ 的根因：类型与失败原因在 citation 层被丢弃，且 citation↔finding 无锚点。
- `research_engine/agents/validator.py:49-51` `_build_index` 建编号→真实来源映射（**只映射 source，不带类型、不留编号**）；`:89` `known = set(index.values())`；`:97` `verified = real_source in known`——**仅来源存在性**，无论断忠实度、无类型。
- `research_engine/agents/validator.py:104` LLM 校验输入 `findings[:20]` 截断——**编号 >20 的引用看不到被引内容**，忠实度判定对其失效（设计评审 Q3 已定修复）。
- `research_engine/agents/validator.py:112-122`：LLM 返回含 `confidence`/`note`，但构造 `Citation` 时**只取 claim/source/verified/supported**，原因与置信度丢失；且**按输出顺序 zip**，与 local_results 不对齐，LLM 缺/多一条即全线错位；`:124-129` 降级分支同样丢失。
- `research_engine/context/manager.py:71`：`format_for_writer` 在 Writer 上下文里已带 `(类型: {f.source_type}, 置信度: ...)`——Writer **知道**类型，但报告文本与 Citation 层不传递。
- `research_engine/agents/writer.py:18`：Writer prompt 仅要求 `[来源: N]`，未要求类型标注/自指隔离。
- `research_engine/graph.py:219-226`：`validate` 节点算 `verified` 聚合数并推进度，但**报告正文保留全部论断**，失败论断不隔离；`graph.py:70-71` 拓扑 `write → validate → END`，**无渲染节点**。
- `cli.py:29-33`：仅打印报告 + 聚合 `verified/total` 计数，**失败条目不列出、类型不显示** → 缺口①② 的用户侧表现。
- `web/app.py:77-87`：列出 citations（claim + source），**无类型着色、无失败隔离**；**`web/app.py:45-60` 侧边栏 slider 仍设 `config.research.max_depth/breadth`——W1 重排后这两个配置已不被图消费，属 dead UI（设计评审 Q7 已定顺手修）**。
- `research_engine/state.py:51-62`：`depth`/`token_used`/`reflection_log`/`visited_sources`/`replan_count`/`critic_signal` 齐备，但**无"运行溯源"渲染入口** → 缺口⑤。

## 5. 优化方案
五个子需求，对应拷打 5 缺口。**2026-09-03 设计评审 7 题拍板已注入各子需求（加粗为本次定稿新增/修订）**：

### R2.1 来源类型显式化（web/rag 着色 + 具体源可见）
- `Citation` 增 `source_type: str`、**`finding_id: str`（设计评审 Q2=A：citation↔finding 锚点，URL 协议引用可为空）**；`validator._build_index` 改为返回 `{编号: (source, source_type)}` 并**保留编号本身**供 citation 携带。
- CLI 渲染：`[来源: N · 🔵web]` / `[来源: N · 🟢rag：<collection/文件名>]`；rag 类额外显示 collection 名（如 `deepresearch_docs`）或文件名。
- Web：`st.markdown` 渲染时用彩色徽章（🔵web / 🟢rag）替代纯文本 source。
- **渲染协议约束（设计评审 Q4=A，防打穿 ADR-0005）**：类型标注**必须由渲染层 post-process 完成，Writer 一律维持 `[来源: N]` 纯编号协议**。若 Writer 直接写 `[来源: 9 · 🟢rag]`，ref 含非数字 token 会使 `_split_ref`（ADR-0005，全数字才拆分）拒绝拆分 → `index.get` miss → 合法引用被误判 verified=False。render 节点复用 `_extract_citations` 同款 pattern 与拆分逻辑，对 `[来源: 5, 72, 77]` 每条分别标注类型。
- **render 节点载体（设计评审 Q1=A）**：新增 `render` 节点挂在 `validate → END` 之间（文档旧稿写"write 之前"系时序错误），负责 R2.1 标注、R2.2 附录、R2.5 溯源块的统一 post-process，展示层增强**不回流 `state.report`**。

### R2.2 校验失败论断强制隔离
- **双轨呈现（设计评审 Q1=A）**：`render` 节点（validate 之后）把 `verified=False` 的论断抽进报告末尾"⚠️ 未通过引用校验的论断"附录（含 **claim + note 原因 + finding_id**）；正文保留原论断但引用标注加 ⚠️ 警示。可审计 ≠ 删除：读者需看到论断本身才能理解"为何不可信"；纯 post-process 不重写 Writer 输出，零二次生成/幻觉风险。
- **报告末尾"可信声明"动态生成并显式标注口径（设计评审 Q6=A）**：有失败则写"X/Y 条通过存在性校验、M/N 条通过忠实度校验，未通过者见附录"，**不得写"全部基于"**；口径（存在性/忠实度）必须显式区分，避免与 DoD 双口径对不上。
- CLI/Web 同步：先打印报告，再打印"未通过校验附录（claim + 原因）"。

### R2.3 校验升格为"来源真实 + 论断忠实"
- 保留 `validator.py:97` 来源存在性；新增**论断忠实度**判定：LLM 判断 claim 是否被其所引用的 finding 内容支持。
- **合并规则（设计评审 Q3=A）**：`verified = 本地存在性 AND LLM忠实度`；**本地存在性 False 直接短路判 False，不进 LLM**（省调用）；仅存在性 True 的进忠实度判定。
- **LLM 输入修复（设计评审 Q3=A）**：废除 `findings[:20]` 截断，**被引用涉及的全部 findings 进 LLM 输入**（压缩后 ≤30 条，每条裁剪，成本可接受），保证编号 21-30 的引用也能判忠实度。
- **输出对齐修复（设计评审 Q3=A，顺带修 zip 错位）**：LLM 输出按 `finding_id` 对齐构造成 Citation，不再按序 zip——LLM 缺/多一条不会全线错位。
- `Citation` 增 `confidence: float`、`note: str`；`validator.py:112-122` 不再丢弃，落入模型字段，供 R2.2 附录与 R2.1 着色使用。
- 越界编号（`[来源: 999]` 超出 findings 数）已由 `index.get(ref, ref)` + `known` 判为未通过（存在性 False → 短路不进 LLM），保留并计入失败附录。
- **口径拆分（设计评审 Q6=A）**：存在性通过率与忠实度通过率分开统计、分开展示，二者不混为一个 `verified` 数字。

### R2.4 自指/元描述发现隔离
- `ResearchFinding` 增 `is_meta: bool = False`。
- **双层检测（设计评审 Q5=A）**：
  - 第一层：`researcher`/`ingest` 产出 finding 时关键词启发式初标（命中 "DeepResearch"/"本系统"/"Planner→Researcher→Writer→Validator"/"本 Agent" 等）；
  - 第二层（兜底）：`validator` LLM verdict 增加 `is_meta` 字段复核——validator 本就逐条审视 citation，顺带判自指边际成本≈0，弥补启发式漏标（自指论断可不含关键词，如"本研究所采用的方法…"）。
  - **任一层命中即隔离**；`ContextManager.compress` 重建 ResearchFinding 时**透传 `is_meta`**（与 `source_type`/`confidence` 同等处理，防压缩后标签丢失）。
- Writer prompt 增加约束：元描述类发现**只能**用于生成独立的"方法论说明"小节，不得作为事实证据在正文引用；正文引用编号自动排除 `is_meta` 发现。

### R2.5 运行溯源 footer
- 新增渲染入口（CLI/Web/报告末尾共用，**统一落在 render 节点**）：从 `state` 取 `depth`（总跳数）、`reflection_log`（critic 决策计数：continue/revise/stop）、`visited_sources` 中 `web:`/`rag:` 前缀计数（web/rag 命中数）、`token_used`、`replan_count`。
- 报告末尾固定"运行溯源"块，CLI 同步打印，Web 以 `st.metric` 展示。

## 6. 设计策略
- **尊重既有 ADR**：沿用 ADR-0002 的 `[来源: N]` 编号协议与 `format_for_writer` 编号映射，只在其上叠加类型/失败原因，**不改编号体系**（避免 W1 已锁的引用契约失效）。
- **渲染与校验解耦（设计评审 Q4=A）**：校验层管协议（纯编号）、展示层管排版（类型标注后处理）。Writer 与 Validator 间契约（ADR-0002/0005）保持字节级不变。
- **尊重 ADR-0004**：findings 压缩/无 reducer 不变；`is_meta`、`source_type`、`finding_id` 作为既有字段透传/新增，不引入新 reducer。
- **复用 W1 状态字段**：`depth`/`reflection_log`/`visited_sources`/`token_used` 已就位，R2.5 只是渲染，零新增状态。
- **最小改动面**：R2.1/2.3 改 `state.Citation` + `validator`；R2.2/2.5 归新增 `render` 节点（validate→END）+ CLI/Web 展示；R2.4 改 `researcher`/`validator`/`manager.compress`/`state`。校验逻辑仍"本地规则 + LLM"两段式。
- **单变量与口径分离（设计评审 Q6=A，呼应 W1 设计评审 Q4）**：R2.3 新增"忠实度"判定维度，不能拿旧口径（存在性）当新功能验收标准——旧链路用存在性口径锁回归，新能力用忠实度口径立新基线。
- **W2 范围收口（设计评审 Q7=A）**：顺手修 web dead UI（slider 改绑 `max_total_hops`，删失效的 `max_depth/breadth` 控件）；eval 路径 errata 改正（实际在 `research_engine/eval/`，非根目录 `eval/`）；不引入其他新能力。
- **Langfuse 顺延 W3**：本需求聚焦"用户侧可审计"，Langfuse 是"开发者侧可观测"，二者正交，不互斥；W3 再接。

## 7. 验收标准（DoD）
- [ ] `Citation` 含 `finding_id`/`source_type`/`confidence`/`note`；`validator._build_index` 返回 `{编号: (source, source_type)}` 并带出编号（设计评审 Q2=A）
- [ ] CLI 跑通：每条引用标注类型（🔵web / 🟢rag+collection），rag 显示具体源；**标注由 render 节点 post-process 完成，writer 输出仍为纯编号**（设计评审 Q4=A）
- [ ] 对 2026-09-01 金融风控样例复跑：原 8 条未通过论断**全部**出现在"未通过校验附录"并附 `note` 原因；正文对应引用带 ⚠️ 警示；报告可信声明随失败数动态生成且**显式标注校验口径**（设计评审 Q1=A/Q6=A）
- [ ] `verified` = "来源存在 **且** 论断忠实"；存在性 False 短路不进 LLM（省调用）；被引用 findings 全量进 LLM 输入（编号 21-30 亦可判忠实度）；LLM 输出按 `finding_id` 对齐（设计评审 Q3=A）
- [ ] `is_meta` 发现不进入正文证据，单独成"方法论说明"小节（复跑样例 `[来源: 10]` 自指论断被隔离）；**双层检测生效：validator verdict 对启发式漏标有复核**；`compress` 后 `is_meta` 不丢失（设计评审 Q5=A）
- [ ] 报告/CLI/Web 均含"运行溯源"块：总跳数、critic 决策次数、web/rag 命中数、token 消耗，且数值与 `state` 一致（设计评审 R2.5）
- [ ] **双口径验收（设计评审 Q6=A）**：存在性通过率 ≥ 基线（2026-09-01 样例 50/58 不下降，锁回归）+ 忠实度通过率单独记录为新基线；CLI/报告计数显式区分两口径
- [ ] Web UI：侧边栏 slider 不再设失效的 `max_depth/breadth`（改绑 `max_total_hops` 或改为展示性说明），citations 带类型徽章与失败隔离（设计评审 Q7=A）

## 8. 影响范围与风险
- 动：`state.py`（`Citation` 增 finding_id/source_type/confidence/note、`ResearchFinding` 增 is_meta）、`agents/validator.py`（index/合并规则/全量输入/按 finding_id 对齐/verdict 增 is_meta）、`agents/researcher.py`（is_meta 初标）、`agents/writer.py`（prompt 自指约束，**编号协议不变**）、`context/manager.py`（compress 透传 is_meta）、`graph.py`（新增 `render` 节点挂 validate→END）、`cli.py`（render 后打印 + 附录 + 溯源 + 双口径）、`web/app.py`（类型徽章/失败隔离/slider 修正/`st.metric` 溯源）、`research_engine/eval/citation_eval.py`（**路径 errata：文档旧稿误写 `eval/citation_eval.py`，实际在 `research_engine/eval/`**；按 source_type 拆分输出）。
- 新增：`tests/test_citation_audit.py`。
- 风险①：改 `Citation` 模型会破坏现有调用方（`graph.py`/`web/app.py`/`eval/citation_eval.py`）→ 需同步迁移字段访问（新字段均有默认值，向后兼容）。
- 风险②：R2.2 双轨（正文 ⚠️ + 附录）可能让正文略长 → 用"⚠️ 单字符标记 + 附录承载原因"控制篇幅，不丢信息。
- 风险③：R2.3 加忠实度判定增加一次 LLM 调用 + 输入全量（≤30 条 findings）token 略升 → 存在性 False 短路不送 LLM（省调用）；降级时回退到存在性（沿用 `:124-129` 兜底）。
- 风险④：render post-process 的正则替换需与 `_extract_citations`/`_split_ref` 保持同源（复用同一实现），否则展示与校验口径漂移 → 提取逻辑抽为共享函数，单测覆盖。
- 降级/兜底：validator LLM 失败仍返回存在性结果（保 W1 行为）；`is_meta` 启发式漏标由 validator 复核兜底；render 节点异常不阻断主流程（降级为不标注，报告原样输出）。

## 9. 测试策略
- 单测（新增 `tests/test_citation_audit.py`，全部离线、mock LLM）：
  - `_build_index` 返回 `(source, source_type)` 且保留编号（Q2）
  - `validate` 输出 citation 含 `finding_id`/`source_type`/`confidence`/`note`；存在性 False 短路不进 LLM（Q3）
  - 构造"来源存在但论断不忠实"用例，断言 `verified=False`（忠实度生效，Q3）
  - render 节点：构造 `verified=False` 用例，断言正文 ⚠️ + 附录含 claim/note/finding_id；断言 writer 输出纯编号经 render 后类型标注正确（Q1/Q4）
  - `is_meta=True` finding：断言 Writer 不将其作为正文证据；断言 validator verdict 复核可将"漏标"发现兜底隔离（Q5）
  - 断言运行溯源块数值等于注入的 `state`（R2.5）
  - 断言 `_split_ref` 对带类型标注的 ref `[来源: 9 · 🟢rag]` 拒绝拆分（守住 ADR-0005 防线，Q4）
- 集成：复跑 `cli.py "大语言模型在金融风控中的应用现状与挑战" --json`，断言 citations 携带 `finding_id`/`source_type`/`verified` 原因；**双口径对比：存在性通过率 vs 50/58 基线，忠实度通过率建档**（Q6）。
- eval：扩展 `research_engine/eval/citation_eval.py` 输出按 `source_type` 拆分的通过率 + 失败原因分布（含 is_meta 命中数），记入 `docs/eval-report.md`。

## 10. 变更记录
| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-01 | 优化 | W1 闭环后拷打式 review 暴露"可信/可审计"呈现层残缺 | 建 W2 需求：引用溯源可审计化（R2.1~R2.5，对应拷打 5 缺口）；W2 重排先于 Langfuse | 本文档 |
| 2026-09-03 | 设计定稿 | 设计评审 7 题（Q1~Q7）全部拍板，注入各子需求 | Q1 正文保留+⚠️+附录、render 挂 validate→END（修时序倒置）；Q2 `Citation`+finding_id；Q3 忠实度 AND 合并+全量输入+按 id 对齐；Q4 渲染 post-process 协议约束（防打穿 ADR-0005）；Q5 is_meta 双层检测+compress 透传；Q6 DoD 拆双口径（存在性回归+忠实度基线）；Q7 顺手修 web dead UI+eval 路径 errata | 本文档；设计评审 四段式记录详见 `.workbuddy/design-review.md` §W2 |

## 11. 遗留待办（2026-09-03 真实冒烟验证后）
> 冒烟：`2026年RAG技术进展`（真实 LLM，max_total_hops=4 实测 2 跳收敛，token 29342）。**W2 六处呈现全部通过**：双口径 51/56 存在性 + 50/51 忠实度；忠实度判定实战抓获"来源真实但论断编造"（编号 15 内部文档被虚构"双模态输入"→ faithful=false）；is_meta 自指隔离生效（[7][14][15] 入方法论小节）；运行溯源块数值与 state 一致。

- [ ] **待办 W2.1 附录 claim 文本清理**：`_extract_citations` 取"引用前 80 字符"导致附录 claim 带 markdown 残留（如"ph-to-Text"、表格截断），展示观感脏。方向：claim 提取规则升级（按段落边界/去除行内 markdown 符号），不影响校验逻辑，纯展示层。
- [ ] **待办 W2.2 writer 方法论措辞收敛**：writer 自产「方法论说明」小节出现"严格遵循DeepResearch系统架构"等自称，虽已限定在小节内（is_meta 隔离成功），但措辞偏绝对。方向：writer prompt 补充措辞约束（方法论小节用陈述句、不写"严格遵守/唯一正确"类绝对表述）。
- [ ] **待办 W2.3 正式双口径回归（DoD 收口）**：以金融风控样例（2026-09-01 基线）完整复跑：存在性通过率 vs 50/58 基线 + 忠实度通过率建档，结果记入 `docs/eval-report.md`。冒烟已证链路通，此步为 DoD 证据留档。