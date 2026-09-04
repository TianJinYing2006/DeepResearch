# 需求-4-tools-and-strategic-model

> 飞书镜像：待创建（DeepResearch 需求文档 / 第四周需求文档）
> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 重排说明：原计划（deepresearch-plan.md §4）W4 即「工具集齐 + 强推理切换」；W2 引用溯源、W3 Langfuse 已先行。
> 本稿为 **grill 前初始版**：明确区域已写实，未定区域以 **TBD-N** 标注，待逐项拷问拍板。
> **✅ grill 定稿：2026-09-04 Q1~Q8 全部拍板，TBD 清零。§5 全部内容为本次 grill 新增/修订的定稿设计（下文的「## 5」即最终方案，历史否决项保留在括号内备查）。**

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | #4 |
| 标题 | 工具集齐（arXiv 学术检索 + 代码执行）+ 强推理切换 |
| 优先级 | P1 |
| 状态 | **定稿（2026-09-04 grill Q1~Q8 全部拍板，TBD 清零）** |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #4（待建） |
| 关联 PR | |
| 创建 / 更新 | 2026-09-04 |

## 2. 问题背景
- 需求文档（v1.0）FR3.3「学术检索」、FR3.4「代码执行」缺失，`deepresearch-plan.md` 列为缺口 3「工具只有网络搜索 + RAG」。
- D3「strategic 强推理」：需求要求"强推理模型"用于规划/裁决，当前 `qwen-plus` 一档到底。
- 面试叙事目标：「模型自主决定检索策略」（DoD）→ 工具面必须从 web/RAG **二元**扩到 web/RAG/arXiv/CodeExec **多元自决**，配合 W1 conditional_edge 讲"agentic 工具调用"，而非框架函数调用。
- 与 W3 正交：工具调用本身会成为 Langfuse trace 的 span 内容，观测基建已就位可直接复用。

## 3. 需求分析
- 目标：研究员在 **web / RAG / arXiv（学术）/ CodeExec（计算）** 四类工具间自决，planning/critic 用强推理模型做难问题决策。
- 成功定义（可量化）：
  - ① 一条"需计算的学术问题"（如"对比 Transformer 与 Mamba 的复杂度，并给出 8k 序列长度下的 FLOPs 数值"）能**自动**触发 arXiv 检索 + 代码执行，报告可溯源到两类工具产物；
  - ② 工具调用过程可见：CLI/报告能看出"这步用了哪个工具、产出什么"；
  - ③ strategic 层（planner/critic）切到强推理模型后成本可预估、不击穿 token 硬闸。

## 4. 当前设计（代码现状）
- `research_engine/search/base.py:L14-44`：`SearchProvider` 抽象仅"网络搜索"语义（`search(query, max_results)`）；工厂 `create_search_provider` 只认 `bocha`。
- `research_engine/agents/researcher.py:L84-96`：`search_once()` **硬编码** `_search_web() + _search_rag()` 全跑，无按查询性质选工具逻辑；`frontier` 元素 `{sq_id, query}`（`state.py:L64`）无工具类型字段。
- `research_engine/state.py`：`ResearchFinding.source_type` 仅 `web / rag`（L25）；`Citation.source_type` 同理（L43）；`visited_sources` 去重契约基于 `source` 字符串。
- `config.py:L24-27`：`strategic_model` 可切 `qwen-max`；pricing 表已预留 `deepseek-r1`（L35，注释"W4 强推理切换"）——**config 层半就位**。
- `research_engine/llm/router.py:L18-24`：`LLMRouter` 已按 `fast/smart/strategic` 三档建客户端、`strategic_*` 接口存在。
- **落点缺口**：`research_engine/critic.py:L110` `_verdict` 硬编码 `LLMClient(model=config.llm.smart_model)`——critic 是裁决层应属 strategic，强推理没接进裁决；planner 是否走 strategic 需核实。
- 代码执行：全项目无 sandbox/受限执行设施；根目录 `tools/` 是验证脚本目录（verify_w3_trace.py），与本研究工具无关。

## 5. 优化方案（✅ grill Q1~Q8 全部拍板定稿，2026-09-04；否决史保留在括号内备查）
1. **工具面扩展**：新增 `research_engine/search/arxiv.py`（学术检索）+ `research_engine/tools/code_exec.py`（代码执行）。
   - **工具调度契约（✅ grill Q1 终判=E，2026-09-04 拍板；历经 A→B→C→D→E 五轮）**：**并行全工具 + 结果池择优，零路由字段**。
     - research 节点对每个查询**并行调度全部可用工具**（web/rag/arxiv/code_exec），不挑报不优先；`ThreadPoolExecutor` 并行（**不引 asyncio**，保节点同步签名、零图改动）。
     - 合并去重 + 相关性预过滤：文本结果按 query 相似度（BM25/向量）排序取 Top-5/工具、总量封顶 10 条；**`source_type=code_exec` 的计算结果整条保留、豁免预过滤（P1）**。
     - Critic 只保留"充分度"单一职责（继续/停止/换角度查询），**从职责中删除"工具选型"**——路由误判类 bug 整体删除。
     - **code_exec 触发门槛（P2）**：确定性关键词启发式（复杂度/FLOPs/计算/数值对比/推导…），命中才执行，未命中返回空——零 LLM 成本、零 state 字段；不算路由，是 code 工具的入参门槛。
     - 已否决：A 全显式路由 / B 仅 Critic 动态标注 / C 顺序全工具（按 20 跳 × 4 工具 ≈ 80~160k token，会常态触发 200k 硬闸）/ D 双级字段（3 新字段 + 30 行回退链，为预算盈余内的"多余工具"问题写抽象）。
     - 成本实测口径：每跳去重后 4~6k token，实测 2~4 轮收敛 → 总 16~24k，占 `token_budget=200k` 的 ~12%。
   - **arXiv 实现（✅ grill Q3 终判=直连+统一抽象+B+ 内核修正，2026-09-04 拍板；历经 A→B+ 两轮）**：**官方 API 直连零依赖（方案 A 不倒退回 SDK）**，`ArxivSearchProvider(SearchProvider)`，`http://export.arxiv.org/api/query`（注意官方是 http 非 https）+ `sortBy=relevance` + `max_results=5`（abstract 信息密度高，比 web 少）+ 模块级 RateLimiter `min_interval=3s`（官方要求；每轮仅 1 次请求，实测多轮最坏 +12s，在线程池内不阻塞 web/rag）。
     - **统一证据抽象（采纳 B+ 内核、修正落地）**：**不新建 EvidenceChunk 类型**——给现有 `ResearchFinding` 增 `metadata: dict = {}` 字段；arXiv 的 `arxiv_id` / `primary_category` /（后填充）`citation_count` 全进 metadata；Critic/Writer 只消费 findings，**下游零改动**（尊重 W2 溯源协议与 ADR-0004 findings 契约）。abstract **不做 provider 层截断**（否决 B+ 的 `[:1000]`），整传由 Q1/Q2 压缩层统一处理。
     - **Semantic Scholar 后处理管道（采纳，话术修正）**：`SEMANTIC_SCHOLAR_API_KEY` 可选；research 节点返回后 batch 查 `citationCount`（`/graph/v1/paper/batch`，ids=`arXiv:{id}`）回填 `metadata.citation_count`；超时/无 key **静默跳过**。**如实记录墙钟**：单次 batch 请求同步执行 **+0.5~1.5s/轮**（非 B+ 声称的"零延迟"），演示场景可接受。
     - **citation_count 只采不决策**：Writer 不加权、不参与证据排序（与 Q1"不做工具预测"一致）；报告附录可展示引用数（呈现细节归 TBD-6/TBD-9）。
     - 失败兜底：请求异常/XML 解析失败 → 空列表（Q1 并行 `return_exceptions` 天然兼容）。
   - **sandbox 边界（✅ grill Q2 终判=B++，2026-09-04 拍板；历经 A→B→B++ 三轮）**：**三层纵深防御，subprocess 主路径 + 两层增强**。
     - **主路径（默认栈，可论证）**：`subprocess.run(["python","-I","-E","-S"])`（-I 隔离用户环境 / -E 忽略环境变量防密钥泄漏 / -S 不加载 site-packages=天然白名单）+ 独立临时 cwd + `timeout=15s` + stdout/stderr 各截断 **128KB**（超限标 `[TRUNCATED]`）+ 信号量**默认 2（可配 4）**——不引 asyncio，Windows 用 wall-clock timeout 作唯一可靠 kill 手段（`resource` 模块 Unix-only，直接出局）。
     - **第二层：AST import 白名单（静态压面）**：`Import/ImportFrom` 节点扫描，只放行 `{math, statistics, itertools, functools, decimal, fractions, collections, typing}`；非纯 stdlib 兜底由 `-S` 保证。此层可被动态 import 绕过 → 由第三层兜底。
     - **第三层：PEP 578 Audit Hook（运行时，默认启用，~10 行）**：子进程内 `sys.addaudithook` 拦截 `subprocess.Popen` / `socket.socket` / `os.system` 等事件**无条件拒绝**；**`open` 事件例外——白名单路径判断**（`abspath` 必须位于临时 cwd 内，cwd 外写权限拒绝），避免误伤包装脚本自身读写（B++ 原文坑，已修正）。
     - **增强层（可选开关）**：`CODE_EXEC_USE_JOB=1` 启用 **Windows Job Object**（ctypes 调 kernel32：`JOB_OBJECT_LIMIT_PROCESS_MEMORY` / `CPU_RATE`），进程树级内存/CPU 配额——**默认关**；配套 Windows 单测（skipif 非 win32）+ DoD 条款（开源前必须验证），定位"文档化的可选能力"而非死代码；Unix 对等分支可退 `resource`（本机不启用）。
     - 未启用 Job 时边界声明：AST + Audit + timeout 三层；Job 为 OS 级增强。
     - 失败回传：非零退出/超时/导入拦截 → finding `note=报错信息` 不吞，LLM 证据池可审计（Q1 延续）。
     - 已否决：A exec 同进程（面试自杀项，无法 kill 死循环）；C Docker（面试环境单点失败 + 每次 1~3s × 20 跳墙钟 +30~60s + 重依赖）；D 全双路径实现（两条都要维护，Job 层已作为可选开关覆盖其价值）。
2. **触发与调度**：TBD-4（计算型子问题识别：**已由 Q1 的 P2 启发式吸收**；识别错/漏的兜底走"本轮空产出 → Critic/硬闸收敛"，Q4 已实证兜住）。
   - **溯源呈现（✅ grill Q6 终判=Q6+E+ 修正，2026-09-04 拍板）**：
     - **source 取值协议**：web→URL / rag→`rag:<filename>` / arxiv→`https://arxiv.org/abs/{id}` / **code→`code:{sha256(脚本+参数+触发查询)[:10]}`**（完整执行上下文串接哈希——覆盖"同脚本不同参数"不误去重且防重复计算；E+ 参数指纹主张采纳，收敛为单 hash）。
     - **图标映射**（render.annotate_types else 分支显式化）：`arxiv → 🔬arxiv`、`code_exec → 💻code`；CLI/Web 同一套。
     - **运行溯源块口径修正（实锤 bug）**：`build_run_provenance` 两桶（rag/其余=web）会把 arxiv/code 误算进 web → 改**四桶分前缀**（web 无前缀 / `rag:` / `arxiv:` / `code:`），此即 TBD-9 工具命中数主线。
     - **validator 双口径对 arxiv/code**：arxiv existence=**格式校验**（`/abs/{id}` 正则，不发 HTTP——来源出自 API 响应，伪造面≈0）；code existence=**执行成功**（失败→`existence=False`+`note=error`→进 W2 附录）；faithful 两者均 **LLM 对查**（claim vs abstract / claim vs stdout）。
     - **E+ 修正（结构化比对该判的教训）**：code stdout 数值提取与 claim 数值比对**只记不改判**——结果存 `metadata.structured_match`（呈现层，报告附录可显示"数值与执行结果一致 ✅"）；**verdict 仍以 LLM 对查为准**（字符串层匹配存在语义无关假阳性：裸数字对撞可绕过 LLM；成本账倒挂：省 1~6 次调用≈几分钱 vs 60~80 行解析器+误判面）。
3. **结果进上下文的体积闭环（✅ grill Q5 终判=方案 A+A+ 修正，2026-09-04 拍板；历经 A→A+ 两轮）**：**三层安检，顺序钉死：入池截断 → 才可能触发 compress → format 兜底**。
   - **入池层**（research 节点 merge 后、写回 state 前，一个 `pool_and_trim()` 纯函数可单测）：确定性截断 + 择优（Q1 落位）。数值：web 300 字符 / arXiv abstract **1000 字符** / code stdout **头 8KB + 尾 4KB（共 12KB，尾部放宽保 Python Traceback 完整）**；并行工具各自 Top-5、总量封顶 10 条；**code 豁免的是"相似度过滤"（不被 BM25 滤掉），"总量 Top-10 封顶"对 code 同样生效**（防 Monte Carlo 多条结果失控）。现实口径：5~8 文本（×~1KB）+ 1~2 code（×12KB）≈ 5~7k token/轮。
   - **压缩层**（ContextManager.compress 存量：仅 >30 条触发、fast LLM 按来源分组）：**补"构造时透传 metadata"**——压缩构造新 finding 时带 `metadata`（合并语义：`citation_count` 取组内 max、`retry_history` 拼接、其余键并集；source_type/confidence/is_meta 沿用存量逻辑）。**否决 A+ 的"注入式压缩"**（把元数据烧进文本塞 LLM）：①Q3/Q4 已拍"citation_count 只采不决策、retry_history 呈现层"，消费侧本期不读；②结构化（int/list）转文本 = 呈现层想精确展示就得正则提取。**压缩顺序**：入池先确定性截断 → 才送 compress（fast LLM 输入 token 省 ~80%，A+ 采纳点）。
   - **出池兜底层**（`format_for_writer`）：单条 content **2000 字符硬截断** + `[…]截断` 标记（正常不触发，防漏网巨块）。
   - **已顺便实证**：`compress` 的 fast 调用已传 `state=state`（manager.py:50）→ token 已进硬闸计数，无新增联动工作。
4. **强推理切换（✅ grill Q7 终判=分档+E+ 修正，2026-09-04 拍板；历经单档→E+ 两轮）**：**按职责分层、无动态难度判定**。
   - **难易即职责划分**：规划（strategic）与裁决（strategic）= 难；写作/提取/压缩（smart/fast）= 易——不改每题判难度（与 Q1"零字段、不做预测"一致）。
   - **拆分 `planner_model` / `critic_model`**：planner 1 次/run 可上强档；critic 2~4 轮/run 默认中等。**默认两档均 qwen-plus（保 W3 实测基线，避免污染 W5 eval 对比样本）**；env `PLANNER_MODEL` / `CRITIC_MODEL` 独立可配、未设回落 `STRATEGIC_MODEL`（向后兼容）；`strategic_model` 保留为 **planner_model 的兼容别名**（router 的 `strategic_*` 继续服务 planner，接口零改）；critic 改 `LLMClient(model=critic_model)`——修复 **critic.py:110 硬编码 smart_model** 的现状 bug。
   - **成本表（W3 pricing，strategic ≈ 11k in + 1.4k out/run）**：qwen-plus ≈¥0.012 / qwen-max ≈¥0.04（+3.3×）/ deepseek-r1 ≈¥0.05（+4×）；分档实测增量（planner 切 max）≈ **+¥0.007/run** 而非 E+ 估的 +¥0.011，量级一致。
   - **硬闸零联动（显式记录防死代码）**：`token_budget` 数 token 不数钱，切档不改 token 消耗 → 硬闸行为零变化；W3 成本报告按 pricing 自动反映。
   - **启动知情打印（采纳 E+）**：与 W3 Langfuse 三态打印同构：`🧠 模型档位: 规划=… / 裁决=…`。
   - **成本报告切档模拟值（采纳 E+，附依赖）**：⚠️ 前提 = **client 层补 per-model token 计数（~10 行）**，否则汇总值会把 fast/smart 的 token 按 r1 价算错；列为演示增强（DoD 可选），不阻塞主功能。
   - **延迟警示**：deepseek-r1 推理模型，critic 单轮 +10~30s、2~4 轮 +30~120s 墙钟；演示建议 critic 切 `qwen-max`（快且够用），r1 作配置选项。
5. **失败兜底（✅ grill Q4 终判=R2+ 修正版，2026-09-04 拍板；历经 R1→R2→R2+ 三轮）**：
   - **读型工具（web/arXiv）**：`retries=1` + **固定退避 1s**（429 带 Retry-After 则遵之）；不做指数退避（单机单用户 1 次重试，1→2→4 的差别无意义，成本+0~4s/研究）；不做 429/5xx/超时多维区分（R3 否决：20 跳预算内不值得区分）。
   - **重试留痕（采纳 R2+ 内核，定位修正）**：每次尝试写入 `retry_history`（attempt/status/error/elapsed）进 `finding.metadata`（复用 Q3 的 dict）；**Critic/Writer 本期不消费**（prompt 不加重试概念、token 不涨），定位为**呈现层数据**，报告附录可展示"该来源首次被限流、重试成功"（细节归 TBD-6/TBD-9）。实现诚实账：wrapper + 4 调用点签名适配 + 单测 ≈ 40~60 行，非"5 行"。
   - **sandbox**：**零重试**（确定性失败重跑同错，错误经 `finding.note` 回传，Q2 已定）；**否决 R2+ 的 random 检测重试**——`random` 原不在白名单（前提矛盾：检测逻辑服务不存在的代码），且随机模拟两次结果均有效，"多次采样"由用户代码自身循环实现。
   - **`random` 进白名单（功能决策，独立于重试）**：Q2 白名单 8→**9 个**（+random），支持蒙特卡洛/数值模拟类计算型子问题；random 无 I/O/系统/网络能力，扩界风险≈0。
   - **全域失败**：不设显式降级分支——Q1 并行 + `return_exceptions` 天然覆盖（web 挂 rag/arxiv 照跑），四源全空走 Critic 自然收敛。
   - **空产出防饿死（实证已兜住，不新增机制）**：`graph.py:151` depth 每查询**无条件 +1**（无论产出是否为空）+ `graph.py:119-127/154` frontier **一次性 pop 消费** → 空产出轮最坏撞 `max_total_hops=20` 强制停，不发生无限空转。
6. **工具调用可见性（✅ grill Q8 终判=B+ 修正，2026-09-04 拍板；TBD 全部清零）**：
   - **跳级消息升级为状态快照**（每跳 progress 消息 + 约 5 行）：`第 N/20 跳 [sq_id]：+12 条新发现（web 5 / rag 2 / arxiv 3 / code 1 失败），累计 47 条`——本跳新增（`len(new_findings)`）+ 工具产出明细（web/rag/arxiv/code 计数，失败带 `(失败)` 标记）+ 累计（`len(state.findings)`）+ **hop 进度 `depth/max_total_hops`**，全部已有数据、零新增字段。
   - **否决"已解决子问题数"（B+ 假字段）**：`solved_sq_ids` 不存在——Q1 方案 E 无初标、findings 无 `sq_id`、Critic 是整体充分性裁决无逐子问题完成度概念；补字段撞零字段精神。进度语义用 hop 预算（诚实且已有）。
   - **溯源块四桶（Q6 已定）作汇总视图**；消息 = 明细视图，闭环完整。
   - **不做完整工具调用表**：明细归 Langfuse trace（W3 产物，按 trace_id 可查），报告不重复——与 Q5 体积闭环一致。

## 6. 设计策略
- 沿用 W1 硬闸优先原则：工具调度是纯函数路由的延续，不新增 LLM 内联 while。（Q1=E 后此条修订为：**无工具路由层，并行调度 + 硬闸兜底依旧成立，Critic 充分度裁决与停止条件不变**。）
- 复用 W2 溯源协议：新工具 finding 走 `source` / `source_type` 既有字段（扩展枚举 `arxiv` / `code_exec`），不另造 state 结构（Q1"零字段"仅指路由字段，来源标注扩展为必要改动）。
- 复用 W3 观测：工具调用即 span（`metadata.tool=arxiv|code`），不新增埋点框架。
- 约束：不推倒自研 LLMClient 薄封装（W1 Q6-B）；代码执行不得引入重依赖（docker 等需论证）。

## 7. 验收标准（DoD）
- [ ] 需计算的学术问题端到端：自动并行调度（arxiv + code_exec 命中触发）+ 报告溯源（plan.md §4 W4 DoD）
- [ ] 四类工具并行可用：web/RAG 已有；arXiv、CodeExec 新增；单工具失败不影响其余工具产出
- [ ] strategic 切 `deepseek-r1` 后 planner/critic 全链路跑通，成本 ≤ token_budget 内可预估
- [ ] CLI 能看出每次工具调用（工具名 + 产物摘要）
- [ ] **sandbox DoD（Q2 拍板）**：默认栈（AST+Audit+timeout）单测覆盖：导入拦截、cwd 外文件写拒绝、超时 kill、输出截断标记；`CODE_EXEC_USE_JOB=1` 路径配 Windows 单测（skipif 非 win32），开源前必须跑通
- [ ] pytest 全绿（新增 `tests/test_arxiv.py` / `tests/test_code_exec.py` / 并行调度单测：单工具挂、其余照跑）

## 8. 影响范围与风险
- `research_engine/`：search/（新增 arxiv）、新 tools/ 包、agents/researcher.py（调度）、critic.py（模型档位）、state.py（枚举扩展）、graph.py（如需新路由）、render.py（新来源类型展示）。
- `config.py` / `.env.example`：学术检索 key（如需）、sandbox 开关、strategic 默认档。
- 回归面：W1 硬闸（新增 token 来源）、W2 溯源渲染（新 source_type 分支）、W3 trace（新 span 类型）。
- 风险：代码执行安全是最大雷区（面试演示可能跑任意代码）；arXiv 限流/稳定性；强推理成本上涨 vs `token_budget` 默认 200k 的联动。

## 9. 测试策略
- 单测：arXiv provider 响应解析（含限流/空结果）；sandbox 资源限制与异常回传；工具路由纯函数（注入调用）；strategic 切换后 critic verdict 结构不变（复用 `tests/test_graph_loop.py` 风格）。
- 集成：复用 `run_e2e_smoke.py` 跑一条计算型学术样例，断言 finding 含 `source_type in {arxiv, code_exec}`。
- 不依赖真实 API：exercised provider 可注入（沿用 `llm_fn` 注入模式）。

## 10. 变更记录
| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-04 | 建稿 | W4 启动 | 初始版（模板 §5 方案留 TBD-1~9 待 grill） | |
| 2026-09-04 | 拍板 | grill Q1 | TBD-1 定案：并行全工具 + 结果池择优（方案 E），零路由字段；补丁 P1 计算豁免预过滤、P2 关键词触发门槛、P3 线程池并行；TBD-4 触发识别由 P2 吸收 | |
| 2026-09-04 | 拍板 | grill Q2 | TBD-3 定案：三层纵深 sandbox（AST 白名单 + PEP 578 Audit Hook 默认启 + Job Object 可选开关），数值 timeout 15s/输出 128KB/并发默认 2；修正 B++ 的 open 全禁误伤坑为 cwd 白名单路径判断 | |
| 2026-09-04 | 拍板 | grill Q3 | TBD-2 定案：arXiv 官方 API 直连（零依赖）+ relevance + max_results=5 + 3s RateLimiter；采纳 B+ 统一抽象内核但落地为扩展现有 ResearchFinding 加 metadata 字段（不新建类型）；S2 后处理管道可选（citation_count 只采不决策，顺序墙钟 +0.5~1.5s/轮如实记录） | |
| 2026-09-04 | 拍板 | grill Q4 | TBD-8 定案（R2+ 修正版）：读型 retries=1 + 固定退避 1s/遵 Retry-After；重试留痕进 metadata 但定位呈现层（LLM 本期不消费）；sandbox 零重试（否决 random 检测——前提矛盾）；random 进白名单（8→9，支持蒙特卡洛）；全域失败无显式降级（并行天然覆盖）；空产出防饿死实证已由 graph.py:151 depth 无条件 +1 兜住 | |
| 2026-09-04 | 拍板 | grill Q5 | TBD-5 定案（A+A+ 修正）：三层安检（入池截断→compress→format 兜底）顺序钉死；数值 web 300/abstract 1000/stdout 头 8 尾 4KB（12KB）/format 2000；code 豁免相似度过滤但计入 Top-10 封顶；compress 补构造时 metadata 透传（否决注入式：结构化→文本破坏呈现，消费侧本期不读）；compress 已传 state 实证 token 进硬闸 | |
| 2026-09-04 | 拍板 | grill Q6 | TBD-6 定案（Q6+E+ 修正）：source 协议 code=`code:{sha256(脚本+参数+查询)[:10]}`（单 hash 收敛）；图标 🔬arxiv/💻code；溯源块两桶→四桶（实锤 arxiv/code 误算 web 的 bug）；validator 双口径（arxiv 格式校验/code 执行成功即存在；faithful 均 LLM 对查）；E+ 结构化比对**只记不改判**（存 metadata.structured_match 呈现层，verdict 仍 LLM 为准——字符串层假阳性实锤 + 成本账倒挂） | |
| 2026-09-04 | 拍板 | grill Q7 | TBD-7 定案（分档+E+ 修正）：按职责分层无动态判定；拆 planner_model/critic_model（默认均 qwen-plus 保 W3 基线——动默认会污染 W5 eval）；strategic_model 降为 planner 兼容别名；修复 critic.py:110 硬编码 smart；硬闸零联动显式记录；启动知情打印；成本模拟值附 per-model token 计数依赖（演示增强） | |
| 2026-09-04 | 拍板 | grill Q8 | TBD-9 定案（B+ 修正）：跳级消息升级状态快照（新增/工具明细/累计/hop 进度，零新增字段）；否决 solved_sq_ids 假字段（数据源不存在，进度用 hop 预算）；溯源块四桶作汇总；不做完整调用表（归 Langfuse trace）。**TBD 全部清零，grill 收官** | |