# 需求-7-technical-debt-and-content

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 本稿为 **设计定稿版（2026-09-09 建稿并定稿）**：**TBD-1 ~ TBD-10 全部定案**，无待拍板项，进入实现阶段。新增/修订细节以**加粗**标注。
> **设计评审记录**：Q1（TBD-1，轨迹 D′→C′→B′）、Q2（TBD-1b=E+）、Q3（TBD-1c①=⑤″）、Q3b（TBD-1c②=persona）已定案；技术债②③④ 与 TBD-8/9/10 由用户授权**自主推进定案**（行业调研先行 + 真实数据诊断）。否决史保留在各条括号内备查。
> **方法学注记**：技术债②③④ 均执行「**诊断先行（读 W5 落盘数据，零 API）→ 行业调研 → 定案**」，其中两个初始假设被数据证伪（多编号误拒假设、缓存/降采样候选），详见 §5.2/§5.4。
> 飞书镜像：[**第七周需求文档**](https://wcnnpvbxd7li.feishu.cn/docx/CbPvdhRXPoydTfxIYTtc2Rmpnww)（2026-09-09 定稿后同步，父文件夹 DeepResearch 需求文档）

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | #7 |
| 标题 | 开源后增强 + 技术债 + 内容扩散（W6 收尾延期项） |
| 优先级 | **P1**（技术债①②③④ 主体）、P2（内容扩散 TBD-9/10） |
| 状态 | **已收口**（Arm 1/3/4/5 代码已落地；TBD-8 对照实验已执行完毕，结果**不支持原假设**、按「如实收口」终止 —— 见 §5.5 复盘） |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #7（待建） |
| 关联 PR | |
| 创建 / 更新 | 2026-09-09（建稿 + 定稿）/ 2026-09-13（TBD-8 实验收口复盘） |
| 实现顺序 | **Arm 3（技术债②）→ Arm 4（技术债③）→ Arm 1（技术债① Step1）→ Arm 5（技术债④A′）→ Arm 2/6（条件触发）→ 链接回填** |

## 2. 问题背景
- W5 eval 收官（commit f1e9a21）留下 **4 项技术债**（plan.md W5 注记，优先级序）：
  1. **① critic 充分度判据接入覆盖度信号**——W5 归因：critic 早停 14/19 是覆盖度 55.4%（目标 ≥90%）的直接根因；critic 只知道"跳数/发现条数"，不知道"子问题是否真被回答"。
  2. **② validator 忠实度对查"note 错位"误拒修复**——机器口径引用准确率 72%，人工抽检修正后真区间 83~92%，差异主因之一是 validator 的 claim 提取窗口与 finding 对齐存在错位，导致"实际忠实但被判不忠实"。
  3. **③ writer "信息不足"标注纪律**——WRITER_SYSTEM 第 5 条要求对信息不足部分标注"信息不足"，但 W5 归因显示 writer 越界发挥（编不出就含糊带过）是引用失败主因之一，第 5 条实际未执行。
  4. **④ validator 成本占 47% 降本**——W5 职责桶实测：validator 48364 token / 91715 总 token ≈ 52.7%（成本占比约 47%），是单阶段研究里最大单一开销，需降采样/缓存。
- W6 收尾延期项（plan.md W7 任务 2~4）：**博客② Langfuse 全链路 trace 实战**（可缓篇）、**博客链接回填 README/需求文档**（可选）、**简历回填**（口径见 plan.md §6，是否并入 W7 待用户确认）。
- 面试叙事价值：这四项技术债构成"**用数据驱动修自己的系统**"的完整故事——发现软肋（eval 数字）→ 定位根因（归因）→ 修复（本需求）→ 再验证（回归受控实验）。这是简历上最能体现"工程闭环"的一周。

## 3. 需求分析
- 目标（量化成功定义，v1.1 基线为参照）：
  - ① 覆盖度 55.4% → **提升且可证明**：修复后与修复前在同一数据集上做受控对比，覆盖度显著上升（**目标 ≥ +5.0pp，Arm 1 达标线，TBD-8 已定**）；
  - ② 机器口径引用准确率 72% → **接近人类口径（83~92%）**：note 错位误拒修复后机器数字上移且与人工抽检的解释差异可归因（**目标 ≥ +4.0pp → ≥76%，Arm 3 达标线+TBD-5 双口径已定**）；
  - ③ writer 越界发挥 → 报告中对未覆盖/低信息子问题出现显式"信息不足"标注（比例可统计；**幻觉类占比 -10pp 为 Arm 4 达标线**）；
  - ④ validator 成本占比 52.7% → **≤45%（Arm 5 达标线，TBD-7 A′ 即可达成；降档验证通过则金额口径再降）**且引用准确率不下降；
  - ⑤ 四项修复各自有"改前 vs 改后"的 eval 对照记录（**受控单变量实验**：一次只改一项，隔离边际贡献）；
  - ⑥ 内容扩散：博客②发布或明确暂缓；链接回填完成或确认不需要。
- 成功定义约束（沿用 W5 血泪教训）：
  - **单轮 eval 数字是量级参考不是精确值**，跨版本对比必须多次重跑取均值（≥3 次）——每项技术债的对照实验都要算这个账（墙钟/成本，TBD-8）；
  - 不引入新的"虚构依赖"：任何新阈值/新字段必须对齐实际代码与真实数据结构。

## 4. 当前设计（代码现状，2026-09-09 盘点）

### 4.1 技术债①：critic 充分度判断（critic.py）
- `Critic._verdict`（critic.py:89-119）：LLM 裁决输入只有两个弱信号——`state.depth`（已检索跳数）+ `len(state.findings)`（发现条数），无任何"子问题是否被回答"的结构化信号；
- `hard_gate`（critic.py:35-53）：纯确定性四闸（frontier 空 / depth / token / replan），与覆盖度无关；
- **eval 侧已有成熟对查**：`metrics.py:106 compute_coverage`（LLM 逐子问题判 covered，smart 档 judge）——但只在 eval Phase 2 用，跑在主链路之外，critic 拿不到；
- `CriticVerdict`（critic.py:24-32）当前只有三字段：`sufficient`（必填 bool）/ `needs_replan` / `next_queries`——**无"还缺什么"的表达通道**，模型即使知道缺口也没有字段可写（D′ 定案要改的就是这里）。
- **关键矛盾**：eval 的覆盖度对查依赖 dataset 的 `expected_subquestions`（ground truth），**主链路运行期没有这个标注**——critic 的"覆盖度信号"数据从哪来是第一待定项（TBD-1，已于 Q1 定案为 D′，见 §5.1）。
- **findings↔子问题关联现状**：`ResearchFinding`（state.py:20-29）**无 sq_id 字段**；graph.py:115/123 的 `sq_id` 为局部变量，仅进 `per_subq_hop` 与 Langfuse span，**不落进 finding**——机械覆盖度统计（若有）需先补关联；**D′ 定案后该地基不再是必需项**（见 §5.1 末）。

### 4.2 技术债②：validator note 错位误拒（validator.py）
- `_claim_text`（validator.py:87-106）：取引用前 200 字符窗口、按句号等边界截断作 claim，清 markdown 残留；窗口错位时 claim 可能"张冠李戴"到前一句论断；
- `_extract_citations`（validator.py:108-123）：`[来源: N]` 正则提取 + 多编号拆分（ADR-0005），一条引用可能对应多条 claim 前置文本；
- LLM 对查（validator.py:190-257）：输入全量 findings + 待校验引用清单，按 `finding_id` 对齐；**错位场景**：claim 截取得不准 → LLM 判定"论断不受该 finding 支持"→ 误拒（机器口径被拉低 11~20pp）；
- W5 人工抽检已确认差异存在（机器 72% vs 人工 83~92%），但**逐条错位样例未落盘归档**（修复验证需要，TBD-4 要补）。

### 4.3 技术债③：writer 信息不足标注（writer.py）
- `WRITER_SYSTEM`（writer.py:13-24）第 5 条："对信息不足的部分明确标注'信息不足'"——**纯 prompt 软约束，无结构化输出、无后处理检查**；
- W5 归因：writer 越界发挥（对没查到的内容含糊带过/邻近发挥）是引用失败主因，第 5 条被执行率低；writer 出口（writer.py:39-48）直接返回 markdown 字符串，无自检环节。

### 4.4 技术债④：validator 成本（validator.py:197 + W5 职责桶）
- validator 单次 `chat_json`（validator.py:197-205）输入 = 全量 findings 文本（每条 content[:300]）+ 全部待校验引用——**输入随 findings 数量线性增长**，W5 实测 48364 token ≈ 总 token 52.7%、成本占比 ≈47%；
- 没有缓存：同一 finding 反复被多条引用校验时重复编码重复判定；
- 没有降采样：所有存在性通过的引用全部进 LLM（即使 claims 相似/重复）。

## 5. 优化方案（**2026-09-09 定稿，TBD-1 ~ TBD-10 全部定案**，按技术债分节）

### 5.1 技术债①：critic 接入覆盖度信号

#### ✅ TBD-1 定案（设计评审 Q1，2026-09-09；修正轨迹：**D′ → C′ → B′ 两步走**）——方案 B′：knowledge_gap 强制声明 + "gap 非空不许停"硬规则 + STORM 视角发现（澄清范围设计已定、实现挂 W8+）

> **回退理由（成本/时间账核算，2026-09-09）**：组件③澄清范围**在 eval 批量跑中必须强制关闭 = 无法被量化验证**，却需改动图结构（新增 clarify 节点 + 条件边 + CLI/Web 交互层，≈4~6h）且承担 W1 收敛单测回归风险——投入产出比最差。**D′ 内核一字未动；组件②保留并按两步走验证；组件③设计定案、实现挂 W8+**（不丢叙事，需求文档留痕）。

**实施节奏（两步走 = 受控单变量）**：
- **Step 1**：只落地组件①（≈1.5~2.5h 实现）→ v1.1 对照实验（**3 轮取均值**，2~3h 墙钟）→ 拿到"gap 机制对覆盖度的**净贡献**"；
- **Step 2**：①验证有效后再落地组件②（≈4~6h 实现）→ **独立**跑对照实验（3 轮，2~3h 墙钟）→ 拿到"视角发现的**边际贡献**"。
- 两步独立验证 = 每个数字可归因（呼应 W5"单轮不可信、需 ≥3 次重跑"教训）；若 Step 1 无效，Step 2 可直接取消，止损。

**机制（本期落地 2 条 + 挂起 1 条）**：
1. **`CriticVerdict` 新增 `knowledge_gap: str` 字段**（critic.py:24-32）——**零额外 LLM 调用**，与 `sufficient` 同一次输出吐出（抄 google-gemini/gemini-fullstack-langgraph-quickstart 的 `reflection_instructions`：`{is_sufficient, knowledge_gap, follow_up_queries}` 三件套同出）；
2. **硬规则（纯代码，确定性）**：`sufficient == True` 且 `knowledge_gap` 非空 → 判为**自相矛盾** → **强制 continue**（不许 stop）。落点在 `Critic.decide`（critic.py:76-87）或 `route_critic`（critic.py:56-66）的纯函数层，与 `hard_gate` 同层，不依赖 LLM 自觉。
3. **STORM 视角发现（plan 阶段前置，Step 2 落地）**：planner 拆子问题**之前**先跑一次"相似主题 survey 检索"，从真实检索结果中归纳 N 个 **perspectives（视角）**，用外部视角驱动子问题拆分——**治 D′ 治不了的"planner 漏拆"自证循环**（用真实检索到的外部视角，而非模型凭空自问）。参照 stanford-oval/STORM 的 perspective-guided question asking（`--max-perspective` 默认 5）。细节见 **TBD-1c**。
4. **研究前澄清范围（🟡 设计已定，本期不实现，挂 W8+）**：设计抄 open_deep_research `clarify_with_user`——研究启动前 LLM 判定"范围是否清晰"，不清晰则提一个澄清问题（图首节点，`goto=END` 吐问题而非 interrupt），澄清结果进 state 作为**用户确认过的参照标准**（比 planner 自拆子问题更接近 eval 的 `expected_subquestions`）；**必须可关闭**（eval/CI/CLI 管道强制 false）。**本期不实现的原因**：eval 中必须关闭 → 收益无法量化验证；且需改图结构 + CLI/Web 交互层（≈4~6h）与 W1 收敛单测回归面。设计细节以 **TBD-1d** 冻结备查，W8+ 启动。

**决策依据（正面打早停根因）**：W5 归因早停 14/19——模型的真实状态往往是"**它其实知道缺什么，只是没被逼着说出来**"。让它在声明 sufficient 的同一刻写下缺口：答得出来就不许收工。同时**每个 stop 都附带 gap 文本**，为 eval 提供可归因留痕（早停原因从"不可查"变成"可统计"）。

**行业调研依据（Q1 前置，源码级）**：

| 项目 | 停止判据 | 覆盖度信号 |
|---|---|---|
| dzhng/deep-research（19.6k★） | 纯硬闸递归（`newDepth=depth-1`、`newBreadth=ceil(breadth/2)`） | 零语义停止，靠"多问几圈"堆覆盖 |
| google-gemini/gemini-fullstack-langgraph | `{is_sufficient, knowledge_gap, follow_up_queries}` + `max_research_loops` | **knowledge_gap 字段，零额外调用** |
| langchain-ai/open_deep_research | 三层：`research_iterations > max_researcher_iterations` / `no_tool_calls` / `ResearchComplete` 工具调用；子 agent 另有 `tool_call_iterations >= max_react_tool_calls` | 无机械统计；靠 `think_tool`（反思文本回写消息历史） |
| stanford-oval/STORM | `max_conv_turn × max_perspective` | 大纲即覆盖参照 + perspective-guided question asking（外部视角破自证循环） |

**否决史（保留备查）**：
- ~~方案 A 机械计数（每子问题 findings 数 ≥N）~~ → **否决**：四家主流项目**零采用**（无行业先例）；且 W5 已实证覆盖度与忠实度负相关（"原料多"≠"答到了"）；另需先补 findings↔子问题关联地基，成本与收益不匹配。
- ~~方案 B 每轮语义对查（复刻 `compute_coverage`）~~ → **降级为加强针**而非否决：+¥0.12/轮（+25%）且行业无先例；**仅当 D′ 在 v1.1 对照实验中覆盖度无显著提升时才追加**（有数据再花钱）。
- ~~方案 C B + gap 检测（每轮多一次调用）~~ → **部分吸收**：gap 检测内核采纳，但实现改为 D′ 的"同一次输出带出"（成本从 +¥0.2/轮 降为 **¥0**）。
- ~~STORM 视角发现本期不做~~ → **撤销否决，C′ 采纳**（用户 2026-09-09"想做全面一点"）：治"planner 漏拆"的自证循环，代价 ≈1~2 次搜索/条 + 一次视角归纳 LLM（见 TBD-1c 成本账）；原"覆盖度天花板"已知局限随之解除。
- ~~机械信号常驻 prompt~~ → 随方案 A 一并否决。

**成本 / 时间账（B′，基数为 W5 实测 ¥0.79/轮·20 条 ≈ ¥0.04/条）**：

| 组件 | 实现工时 | 额外调用/条 | 运行成本/轮（20 条） | 验证墙钟（3 轮均值） | 可量化验证 |
|---|---|---|---|---|---|
| ① knowledge_gap + 硬规则（Step 1） | **1.5~2.5h** | 0 | **¥0** | 2~3h | ✅ |
| ② 视角发现（Step 2） | **4~6h** | 1~2 次检索 + 1 次视角归纳 LLM | ≈¥0.24（+30%） | 2~3h | ✅ |
| ~~③ 澄清范围~~（挂 W8+） | ~~4~6h~~ | ~~1 次 LLM~~ | ~~≈¥0.04~~ | — | ❌ eval 中须关闭 |
| **B′ 合计（本期落地）** | **≈6~8.5h** | | **≈+¥0.24/轮（+30%）** | **4~6h** | 两项均可独立归因 |

> 横向对照：原方案 D（停止前对查）+¥0.036/轮；方案 B（每轮对查）+¥0.12/轮；**C′ +¥0.28/轮（+35%）且含 1 项不可验证组件 + 图结构回归风险**。
> **B′ 的净收益**：省下澄清的 4~6h 实现工时与图结构回归风险，保留全部可归因数字；Step 1 若无效可立即止损（Step 2 取消）。

#### ✅ TBD-1c① 定案（设计评审 Q3，2026-09-09）——视角发现：**不计跳数 + 用途隔离（不进证据池）**

**决策背景（两次行业调研 + 一次软肋追打）**：
- 行业：**没有一家丢弃检索产出**——STORM `raw_search_results.json` → 筛选 → `url_to_info.json`（"最终文章使用的来源"）；open_deep_research `compress_research` 双轨（compressed + raw_notes）；gemini/dzhng 全进 → **方案①"用完即弃"撤回**；
- 软肋追打：压缩（10~20:1）**必然丢**数字/日期/实体/URL，且**压缩综述若作为可引用证据会断掉 W2 引用溯源链**（项目核心卖点）→ 改为用途隔离。

**定案两条**：
1. **跳数归属：不计入 `max_total_hops`** —— 视角发现是 plan 阶段前置动作，非研究跳；对齐 STORM"pre-writing 与 writing 两阶段各算各的预算"（`max_conv_turn × max_perspective`）；
2. **产出归属：用途隔离（三通道）**
   - **perspectives（角度清单）** → 供 planner 归纳子问题（无损，本就是归纳产物）；
   - **原始检索结果** → 存**独立背景通道**（不进 `state.findings`）；因不在 findings 中故**无编号 → ADR-0004 天然隔离：writer 不可引、validator 不校验、validator 成本零增加**；writer 可读取为背景（上下文 +2~5k token ≈ ¥0.006/条）；
   - **确有证据价值的视角内容** → **走 frontier 正式检索重新获取**（正常编号、正常校验），**不走后门**。

**否决史**：~~① 用完即弃~~（行业零先例 + 浪费一次检索）；~~② 原始全并 findings~~（validator 对查输入 +30~60%，与技术债④ 降本直接打架）；~~⑤′ 压缩进池且可引~~（有损 + **溯源断链**，牺牲 W2 核心卖点）。

#### ✅ TBD-1c② 定案（设计评审 Q3b，2026-09-09）——视角发现实现形态：轻量结构信号 + **persona 角色形态**

**流程五步**（抄 STORM `persona_generator.CreateWriterWithPersona` 精神并本地化）：
1. **1 次检索**：博查检索 `topic`（或 `topic` + survey 后缀）→ **只取返回结果的标题 + 摘要首句**（结构信号，**不取正文**——STORM 连搜索引擎都不用，只需"同类材料的标题/目录"）；
2. **1 次 LLM 归纳**：从标题/摘要中归纳 **4 个视角角色**（persona：角色标签 + 一句话关注点）；
3. **恒定兜底视角**：**"基础事实收集"**（抄 STORM 的 `Basic fact writer: 广泛覆盖基础事实`，恒定前置）——视角发现失败时天然退化为通用视角；
4. **planner 消费**：拿 5 个（4+1）视角 → 产出 ≤4 个子问题，**不动 `max_subquestions=4`**；视角以"角色标签 + 关注点"形式进 `SubQuestion.rationale`，**零新增结构**；
5. **降级**：检索失败/解析失败 → 只用兜底视角（等价于现状 planner，零新增代码路径）。

**形态选择依据**：**persona（角色）而非"方面列表"** —— STORM 论文实证"不同立场会问出不同问题"，这正是治"planner 拆偏"的药；方面列表易退化为泛泛四件套、差异化弱。

**成本**：1 次检索（≈¥0.005）+ 1 次 LLM 归纳（≈¥0.002）= **≈¥0.007/条 ≈ ¥0.14/轮（+18%）**；不计跳数、不进证据池（见 TBD-1c①）。

> **技术债① 设计闭环（Q1~Q3b 全部定案）**：**D′ 内核**（`knowledge_gap` + gap 非空不许停）→ **E+ 判定与封顶**（gap∧next_queries 绑定 + 反思轮次 N=6 + prompt 预算感知）→ **视角发现**（不计跳数 + 用途隔离 + persona 形态 + 兜底角色）。实施节奏仍为 **Step 1（组件①）→ Step 2（组件②）分步验证**，各 3 轮对照实验取均值。

**🟡 TBD-1d（冻结备查，W8+ 启动，本期不实现）**：澄清触发条件（每次都判？还是仅当 topic 模糊/短？）；**硬约束——eval 批量跑（`eval/run.py`）与 CI 必须可关**（配置 `allow_clarification`，默认开还是默认关？eval 轨强制 false）；澄清轮次上限（不能无限追问，建议 1 轮）；澄清结果落哪个字段（新增 `user_scope`？并入 `user_instructions`？）以及 **critic 是否消费它**（作为 gap 判定的参照标准）；CLI 非交互场景（管道/脚本）如何优雅跳过。

#### ✅ TBD-1b 定案（设计评审 Q2，2026-09-09）——方案 E+：gap ∧ next_queries 绑定判定 + 反思轮次专用封顶 N=6 + prompt 预算感知

**行业依据（源码级，Q2 前置调研）**：判定器一律为**布尔 / 工具调用**（gemini `Reflection.is_sufficient: bool`；open_deep_research `ResearchComplete`），**无一家用字符串判空**；gap 在 gemini 中只是生成 `follow_up_queries` 的**输入、不参与停止决策**；封顶一律为**独立循环计数器 + 小上限**（gemini `max_research_loops=2` / open_deep_research `max_researcher_iterations=6`、`max_react_tool_calls=10` / STORM `max_conv_turn=5`）；open_deep_research 把上限写进 `lead_researcher_prompt` 做**预算感知**。

**机制三条**：
1. **判定**：主判据仍是 `sufficient` 布尔（与 gemini 一致）；**强制 continue 条件 = `sufficient==true` ∧ `knowledge_gap` 非空 ∧ `next_queries` 非空**（gap 非空但给不出 next_queries → 判"无可行补充途径"，允许 stop 并留痕 `stop_reason="gap_unresolved"`）；
2. **封顶（关键修正）**：**不复用 `max_total_hops=20`**——那是**检索跳数**预算，不是**反思轮次**预算（行业封顶值 2/6/10 均远小于 20）。设**反思轮次专用上限 N=6**：`len(state.reflection_log) >= 6` 后不再因 gap 强制 continue，交还 LLM + hard_gate；**零新增 state 字段**（reflection_log 为 W1 既有 add reducer）；
3. **预算感知**：critic prompt 注入"当前第 k/6 轮反思"，让模型知晓剩余预算并主动收敛（抄 open_deep_research `lead_researcher_prompt.format(...)`）。

**否决史**：~~方案 A 纯字符串判空~~（行业零先例 + 两头脆弱：永不停 / 学会留空）；~~方案 B `gap_blocking` 自评布尔~~（多一字段多一调优点，且仍由模型自评，不如"能否给出 next_queries"硬）；~~方案 C 新增计数器字段~~（改用 `len(reflection_log)`，零字段）；~~方案 D 文本关键词/长度规则~~（中文否定表达多样，机械判据脆弱——重蹈 W5"虚构依赖"覆辙）。

**成本 / 风险账**：额外 LLM 调用 **0**；实现 ≈0.8h（prompt + 纯函数规则 + 3 单测）；**晚停风险由 ×2~5 压到 ≤+50%**（反思轮次 3.85 → 上限 6）；N=6 取 open_deep_research 默认值（W5 实测均值 3.85×1.5），为覆盖度留出手空间。

**留痕**：`reflection_log` 每条记录附 `gap` 文本 + `stop_reason`（`critic_stop` / `hard_stop` / **`gap_unresolved`** / `no_next_queries`），供 eval 统计"**带缺口停止**"这一新增终态。

- **TBD-2 信号接入方式**：**已定案收口**——gap 规则落纯函数层（与 hard_gate 同层，属"硬规则"而非 LLM 自觉），**不进 hard_gate 四闸**（不动 W1 硬闸结构）。**路由优先级（写死，防语义打架）**：`needs_replan`（hard_gate 侧，W1 既有）**优先于** gap 强制 continue（critic 侧）——needs_replan=true 时走 replan 路径；否则按 TBD-1b：`sufficient=false` → revise（走 W1 既有 next_queries 追回 frontier）；`sufficient=true ∧ gap 非空 ∧ next_queries 非空` → 强制 continue；`sufficient=true ∧ gap 非空 ∧ next_queries 空` → stop + `gap_unresolved` 留痕。两条路径正交：needs_replan 治"计划失效"，gap 治"证据不足"，不互相覆盖。
- **TBD-3 早停判据与阈值**：D′ 下"阈值"退化为 **gap 判空规则**（见 TBD-1b），不再设覆盖度百分比阈值。

### 5.2 技术债②：validator 引用校验修复

#### 🔬 诊断先行（2026-09-09，读 W5 落盘数据，零 API）

数据源 `research_engine/eval/results/run_v11_compare/raw/q_*.raw.json`（20 条，含 state 快照 + 完整 citations），诊断脚本 `.workbuddy/diag/diag_citations.py` / `diag_multi.py`，样例落盘 `.workbuddy/diag/failed_citations.jsonl`（298 条）。

| 指标 | 数值 |
|---|---|
| 总引用 | 1066（**编号级**），去重后 claim 580（**论断级**） |
| 机器口径准确率 | 768/1066 = **72.0%** |
| 存在性失败（越界编号/编造来源） | 仅 **17 条（1.6%）** |
| 未通过引用 | 298 条 |
| note 自报 claim 截断 | 37 条 |
| note 自报"本源未提、他源有"（编号错配） | 12 条 |
| note 判"虚构/无依据" | **123 条** |
| 多编号共享 claim | 315 组 / 801 条（**占 75%**） |
| 单编号 vs 多编号 通过率 | 70.9% vs **72.4%**（**无显著差异**） |
| 论断级口径推演 | 459/580 = **79.1%**（+7.1pp，属口径差异非质量提升） |

**三条诊断结论（推翻原假设）**：
1. **原假设"多编号逐条校验是误拒主因"被数据否证** —— 单/多编号通过率几乎相同（70.9% vs 72.4%），ADR-0005 **不做修订**；
2. **"机器 72% 是被低估"这个前提存疑** —— 未通过中 123 条被 LLM 明确判为虚构/无依据（真幻觉），真正的误拒（截断 37 + 编号错配 12）仅约 **16%**；W5 人工抽检仅 12 条样本（10 真实/1 存疑/1 不实），**置信区间极宽，不能当"真值"**；
3. **真正的瓶颈在 writer 幻觉（技术债③）** —— 与 W5 归因"writer 越界发挥是引用失败主因"互相印证。

> **⚠️ 2026-09-13 修正（结论 2 的「误拒仅约 16%」低估）**：按**现行 `_is_assertive`** 在 298 条夹具上重放，
> **技术性误拒实际为 123 条（41.3%）** = R4 结构残片 75（25.2%）+ R2 截断 29 + R3 有依据引错编号 19。
> 差异来源：当时**已识别 R4 为真 bug，却未把它的量计入「误拒」分子**。
> ⇒ **validator 修复的天花板由 16% 上调至 41.3%**；结论 3 方向不变（**须技术债③治理的仍有 110 条 = 36.9%**）。
> 归因细则、逐类修复对应（R4→F3 / R2→F1 / R3→TBD-5）与**算术上界**（F1+F3 合计 +8.4pp → 80.4%）
> 见 **`docs/eval-w7-attribution.md`**（由 `tools/w7_failure_attribution.py` 生成，可复算）。

**抽样验证发现的四个真 bug**（`failed_citations.jsonl` 逐条复核）：
- **R1 verdict/claim 错位**：note 描述内容与 claim 完全不匹配（例：claim 讲 GraphRAG，note 却在说"未提 CN-StockKG/预训练抽取器"）——批量对查中 LLM 判定串位；
- **R2 claim 截断**：`validator.py:185` 送 LLM 时 `claim[:100]`，而落盘 claim 最长 195、中位 51 → **LLM 基于残缺论断判"不忠实"**（note 自报 37 条）；
- **R3 `supported=true` 未计入 verified**：note 明写"但[9]明确描述…故 supported=true"，却因 `verified = faithful`（validator.py:243）被判未通过 —— **论断在系统内有依据，只是引错编号**；
- **R4 claim 抓到非论断内容**：markdown 表格残片（"实证研究概览 | 研究 | 数据集 |…"）被当作论断校验；
- **R5 claim 丢失主语**：`_claim_text`（validator.py:90-106）取"最近句子边界之后"= **只留最后一句**，长句主语被丢弃，论断不自包含。

#### ✅ TBD-4 定案（2026-09-09）——吸收 RAGAS 范式，五项修复（**全部零额外 LLM 调用**）

**行业依据（RAGAS `src/ragas/metrics/_faithfulness.py`，源码级）**：
- `StatementGeneratorPrompt`：把答案**语义拆分成自包含 statements**，明确要求 **"no pronouns are used in any statement"（去代词化）**；
- `NLIStatementPrompt`：批量判定但**强制回显 statement 原文**（`"the original statement, word-by-word"`）+ reason + verdict(0/1) —— **回显即对齐保障**，正是治愈我们 R1 的药；
- 判定标准：**"can be directly inferred based on the context"**；
- 计分为 **statement 级**（忠实 statements / 全部 statements），非"引用编号级"。
> 注：RAGAS 无引用编号，靠"拆分 + 全 context 比对"；我们**有编号、可精确定位到具体 finding**，设计本就更高级，**只吸收其低成本要点，不照搬 statement 拆分**（拆分会丢失编号关联）。

| 修复项 | 具体改动 | 治哪个 | 成本 |
|---|---|---|---|
| **F1 去截断** | `validator.py:185` `claim[:100]` → **不截断**（claim 最长 195，可整送） | R2 | ¥0 |
| **F2 回显对齐** | `CitationVerdictItem` 增 `claim_echo` 字段（RAGAS 式回显原文）；本地与 claim 做模糊比对（<0.6 判错位）→ 该条标 `verdict_unreliable`，**降级为"未获 LLM 反馈"保守通过并留痕**（不静默判 false） | **R1** | ¥0 |
| **F3 非论断过滤** | claim 提取过滤：表格行（含 `|`）、纯标题（无句号）、长度 <8 字符、无谓语片段 → 直接跳过不校验（不产生引用记录） | R4 | ¥0 |
| **F4 保留上下文** | `_claim_text` 改为"**最近句子边界 + 前一句主语兜底**"（当前只留末句致主语丢失），满足 RAGAS"自包含"要求 | R5 | ¥0（claim 略长，输入微增） |
| **F5 判定措辞** | 对查 prompt 吸收 RAGAS 标准："能否**直接由该来源推断**"（比"不夸大不曲解"更可判定） | 判定一致性 | ¥0 |

**样例集落地（TBD-4 后半）**：`.workbuddy/diag/failed_citations.jsonl` 的 298 条**直接作为回归测试夹具**——修复后重放，逐条断言"原误拒样例是否已转通过"，比只看总量数字涨跌可归因（呼应 W5 判定器分层与可归因原则）。

#### ✅ TBD-5 定案（2026-09-09）——双口径呈现，**不改 verified 语义**

- **严格口径（保持 W2 契约）**：`verified = existence AND faithful`（引对编号 **且** 忠实）—— 机器 72% 即此口径；
- **宽松口径（新增，仅呈现不改动状态）**：`verified_relaxed = existence AND (faithful OR supported)` —— 即"论断在系统内**能找到依据**"（允许引错编号但内容真实），对应 note 中"但[9]明确描述…supported=true"那批（R3）；
- **人工口径归位**：W5 人工抽检判的是**宽松口径**（人看"这论断有没有依据"，不逐条核对编号），故 83~92% 与机器 72% **不是同一件事**——**差异的绝大部分是口径差，不是 validator 误拒**；
- **诚实声明（写进 eval-report）**：人工抽检样本 12 条，置信区间极宽，**不作为真值**；跨版本对比一律以严格口径为准，宽松口径仅作解释性附注；
- **否决**：~~校准系数~~（人为放大数字 = 刷指标）、~~修改 verified 语义~~（破坏 W2 契约与基线可比性）。

**预期效果（诚实）**：F1~F5 主要清除**技术性误拒**（约 16% 的量），预期严格口径 72% → **约 76~80%**；**不可能靠修 validator 冲到 90%**，因为未通过的多数是真幻觉 —— **上限由技术债③（writer 纪律）决定**。故 **② 与 ③ 建议联合验证**（见 TBD-8）。

### 5.3 技术债③：writer 信息不足标注纪律（**诊断判定为引用准确率的第一瓶颈**）

#### 🔬 诊断依据（承接 §5.2）

未通过引用 298 条中 **123 条被 LLM 明确判为"虚构/无依据"**（真幻觉，占 41%），远超技术性误拒（约 16%）。**引用准确率的上限由 writer 纪律决定，而非 validator 精度** —— 与 W5 归因"writer 越界发挥"完全互证。

#### ✅ TBD-6 定案（2026-09-09）——STORM「分节喂料」范式 + 系统级兜底（G1~G5）

**行业依据（STORM `article_generation.py`，源码级）**：
- **逐节生成**：遍历 outline 一级章节，`ThreadPoolExecutor`（max 10）**并行**生成每个 section；跳过 introduction/conclusion（后处理补）；
- **每节独立检索喂料**：`information_table.retrieve_information(queries=section_query, search_top_k=5)` —— **每节只喂相关的 top-5 信息**，而非全量平铺；
- **信息量硬上限**：`limit_word_count_preserve_newline(info, 1500)`；
- 引用为行内 `[1][2]` 纯文本（无结构化输出）。
> 对照我们的现状：`writer.py:40` 把**全量 findings 一股脑拼给 writer**（`context.format_for_writer(findings)`，实测 40 条/条）——材料与"当前要写什么"无绑定，是张冠李戴与凭空发挥的温床。

| 修复项 | 具体改动 | 作用 |
|---|---|---|
| **G1 建关联** | `ResearchFinding` 增 `sq_id` 字段，`graph.py:142` 产出 findings 时打标（当前 sq_id 仅为局部变量，只进 `per_subq_hop` 与 span） | **地基**：喂料分组 / 覆盖检查 / 细粒度覆盖度 的公共前提 |
| **G2 分子问题喂料** | `ContextManager.format_for_writer` 改为**按子问题分组呈现**（每组 top-k，抄 STORM 的"每节 top-5"），取代全量平铺 | 材料与写作目标强绑定，压减张冠李戴 |
| **G3 显式标注无料** | 输入中对**无 findings 或不足 N 条的组**显式写"该子问题无可用材料，**必须在报告中标注'信息不足'**" | 从源头消除编造动机 |
| **G4 系统级兜底（硬）** | 报告产出后机械检查：每个子问题是否被覆盖（引用了该组 findings 或出现"信息不足"字样）→ **未覆盖则由系统追加"信息不足"段**，不依赖 writer 自觉 | **协议层强制**，比 prompt 纪律可靠（W5 已证软约束失效一次） |
| **G5 prompt 改写** | 第 5 条改为逐子问题的二选一指令："有料 → 写并引用编号 / 无料 → 写'信息不足：<子问题>未检索到足够证据'" | 与 G3/G4 同向 |

**否决**：~~A 结构化输出（writer 返回 JSON）~~ —— 触碰 ADR-0004 引用编号协议与"writer 输出纯字符串"现状，且 STORM 证明纯文本 + 行内引用即可；~~C 仅 prompt 强化~~ —— W5 已证软约束失效。

**成本账**：G1 1h + G2 1~2h + G4 1h + G3/G5 0.3h ≈ **3~4h 实现**；运行成本 ≈¥0（喂料重组不改变 token 总量，甚至因分组精简而略降）。

#### 🔁 地基反转说明（Q1 → Q3b/技术债③）

Q1 定案时因"机械覆盖度统计行业零先例"而**降级**了 findings↔子问题关联地基；**此处基于 STORM 分节喂料（源码级依据）重新启用该地基** —— 但用途完全不同：**不是为机械计数，而是为喂料组织与覆盖检查**。附带收益：eval 可产出**"每子问题覆盖度"**（比现有全局覆盖度更能定位短板），且为技术债① 的机械信号预留接口（若未来需要）。

> 与 §5.1 的关系：技术债① 的 D′ 内核**不依赖**此地基；G1 是技术债③ 的必要项、技术债① 的可选项 —— 实施时 G1 归入 **Step 3（技术债③）**。

### 5.4 技术债④：validator 降本

#### 🔬 诊断先行（2026-09-09，读 W5 `run_v11_compare` 落盘数据，零 API）

| 指标 | 实测值 | 出处 |
|---|---|---|
| validator 职责桶 | **48,364 tok（占全局 91,715 的 52.7%，最大单项）** | `docs/eval-report.md:18` |
| findings 总数 / 被引用去重数 | 551 / **325**（**41.0% 从未被引用**） | 诊断三轮 |
| `findings_text` 字符浪费 | 158,629 → 96,842，**浪费 39.0%** | 诊断四轮（严格复刻 `validator.py:181` 的 `content[:300]`） |
| 完全重复 `(claim, source)` 引用 | 9 / 1066 = **0.8%** | 诊断三轮 |
| 同 claim 内重复来源 | 9 / 1066 = **0.8%** | 诊断三轮 |
| `findings_text` 占 validator input 字符 | **49.0%**（另 `citations_json` 46%、system 常量 5%） | 诊断五轮 |

**候选方向数据裁决：**

- ~~A 缓存（(finding hash + claim 近似) → 复用判定）~~ → **证伪**：完全重复引用仅 **0.8%**，缓存命中率天花板 ≈0.8%，收益可忽略却引入"近似匹配阈值"新复杂度和误命中风险。**否决**。
- ~~B 降采样（多编号引用抽样校验）~~ → **证伪**：诊断二轮已确认单编号 vs 多编号通过率 70.9% vs 72.4%（无差异），多编号并非成本来源；且抽样直接触碰 ADR-0005「每条独立校验」定论。**否决**（ADR-0005 不修订）。
- **A′ 喂料裁剪**（新候选，诊断产物）：`findings_text` 只保留**被引用且存在性通过**的 findings。省 39.0% findings 部分 → validator token **-16.8%**，全局 **-8.9%**（金额口径 -5.7%）。
- **C 降档**（validator: qwen-plus → qwen-turbo）：按 `config.py:44-45` 单价（plus in ¥0.8/M out ¥2/M；turbo in ¥0.3/M out ¥0.6/M，差 **2.67 倍**）测算，validator 金额 ¥0.0457 → ¥0.0163，**全局金额 -26.0%**。
- **A′+C 合计**：全局金额 **-28.1%**。**降档杠杆 ≈ 裁剪的 4.5 倍**。

#### 🌐 同行做法（源码级，2026-09-09）

| 项目 | validator 喂料做法 | 对本项目启示 |
|---|---|---|
| **RAGAS `Faithfulness`** | `contexts_str = "\n".join(row["retrieved_contexts"])` —— **喂全量，不裁剪** | 不可直接照搬：RAGAS 是单轮 RAG，`retrieved_contexts` 天然只 3~5 条；本项目 findings 是**跨多轮累积 20~40 条**，规模差 10 倍。RAGAS 不裁是因为**不需要裁**，不是反对裁 |
| **RAGAS `FaithfulnesswithHHEM`** | 用 **vectara/hhem 本地小模型**做 NLI 分类替代 LLM 判定，`batch_size=10` | **降本终极路径**（零 API 成本/零延迟），但需本地 transformers ~1GB + 中文 NLI 模型选型未验证 → **记入 W8+ 探索项，本期不做** |
| **STORM** | `ArticlePolishingModule` 只对**被引用的 snippet** 做校验 | 支持 A′ 裁剪方向 |
| gemini-fullstack / open_deep_research / dzhng | **无独立 citation validator 环节** | 无参照价值 |

#### ✅ TBD-7 定案（2026-09-09）——**A′ 喂料裁剪（直接落地）+ C 降档（进受控实验验证后采纳）**

**A′ 喂料裁剪（零风险纯工程，本期落地）** —— 改 `validator.py:179-183`：

```python
# 裁剪前（现状）：全量 findings 进 prompt
findings_text = "\n".join(
    f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {f.content[:300]}"
    for i, f in enumerate(findings, 1)
)

# 裁剪后：只喂「存在性已通过」的引用所指向的 findings，且保留原编号
used_ids = {str(r["finding_id"]) for r in to_check}
findings_text = "\n".join(
    f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {f.content[:300]}"
    for i, f in enumerate(findings, 1) if str(i) in used_ids
)
```

**三条硬约束（缺一即引入回归，必须写进单测）：**

1. **存在性校验（阶段 1）必须继续用全量 `index`** —— `_build_index(findings)` 与 `existence = real_source in known` **不得**改为裁剪后集合。否则"引用了不存在的来源"在裁剪域内永远为真 → 存在性校验彻底失效（这是本方案唯一的高危陷阱）。存在性是纯 Python dict 查找，**零 token**，不受裁剪影响。
2. **编号必须保留原编号**（用 `enumerate(findings, 1)` 的原始 `i` 过滤，**不得**对子集重新编号）——否则与报告中的 `[来源: N]` 及 `to_check` 的 `finding_id` 错位，忠实度判定全部错配。
3. **安全阀**：若 `to_check` 非空但 `used_ids` 与 `to_check` 的 `finding_id` 存在不交集（理论不该发生），降级回全量喂料并 warn，不做静默裁剪。

**附带收益（与技术债② 同向，非成本收益）**：当 writer 引用了 finding A 的编号却写了 finding B 的内容时，全量喂料下 LLM 可能因"看到 B 也存在"而误判通过；裁剪后只喂 A → 必定判不支持 → **错位类误判下降**。

**C 降档（qwen-plus → qwen-turbo）—— 不直接落地，进 TBD-8 受控实验**：

- 理由一：**杠杆最大但风险也最大**。全局金额 -26%，但忠实度判定是引用准确率的守门人，而技术债② 的目标正是**提升**准确率 —— 方向相反，不能拍脑袋降。
- 理由二：**与技术债② 存在对冲机会**。TBD-4 五项修复（零额外调用）预期把机器口径准确率从 72% 上移；若修复后余量足够，降档才安全。**实验顺序固定：先验证 TBD-4 → 再验降档**。
- 判定门槛（写死，防事后找补）：降档臂的机器口径引用准确率 ≥ 基线 **-1.0pp**（即 ≥71.0%）且严格口径不塌，才允许采纳；否则维持 qwen-plus 并如实记录"降档不可行"。
- 配置项：新增 `VALIDATOR_MODEL` env（默认回落 `SMART_MODEL`=qwen-plus），**零改动主链路**，实验通过才改默认值。

**TBD-7 目标值**：validator 成本占比 **52.7% ≤ 45%**（A′ 单独即可达成约 48.8%；降档若验证通过则金额口径降至约 40%），且**引用准确率不低于基线**。

### 5.5 回归验证协议（贯穿四项技术债）

#### ✅ TBD-8 定案（2026-09-09）——**配对交替 + 主指标预注册 + 判定门槛写死**

**一、实验设计：配对交替跑（关键，直接针对 W5 教训）**

W5 已记录：*「引用忠实度/命中率波动大——真实 API 检索结果两次运行不同（博查结果集变化）→ findings 不同 → 报告 claim 不同 → validator 对查结果大幅漂移」*（`docs/eval-report.md:46`）。这是本实验最大的混淆变量。

- ❌ **禁止**"先跑完基线、再跑实验臂"的串行设计 —— 时间漂移会与改动效应混在一起，无法归因（W5 的 `run_20260906_184156` vs `run_20260907_001658` 覆盖度 -27.6pp 即为前车之鉴，且该落差**不含任何代码改动**）。
- ✅ **采用配对交替**：每一轮内，**基线臂与该轮实验臂在同一时间窗交替提交**（同一批 20 题，逐题交替或同题并行），使两者暴露在同一检索源分布下。判定用**配对比较**（逐题差值），而非两独立样本均值比较 —— 配对设计消除题间难度与检索漂移的共性方差，功效显著更高，**3 轮即够**（独立样本需 5+ 轮）。

**二、实验臂与轮次**

| 臂 | 内容 | 轮次 | 前置条件 |
|---|---|---|---|
| Arm 0 | 基线（当前 v1.1 代码） | 与每个实验臂**同期配对**跑，不单独计轮 | — |
| Arm 1 | TBD-1 Step 1（knowledge_gap 硬规则 + N=6 封顶） | 3 | — |
| Arm 2 | TBD-1 Step 2（persona 视角发现） | 3 | **仅当 Arm 1 覆盖度判定达标**（止损点，避免 8.5h 打水漂） |
| Arm 3 | TBD-4+TBD-5（validator 五项修复 + 双口径） | 3 | — |
| Arm 4 | TBD-6（writer 分节喂料 G1~G5） | 3 | — |
| Arm 5 | TBD-7 A′（validator 喂料裁剪） | 3 | 可与 Arm 3 合并跑（改动正交），但**分开报数** |
| Arm 6 | TBD-7 C（validator 降档 turbo） | 3 | **仅当 Arm 3 判定达标**（准确率有余量才允许降档） |

- **全量 20 题，不允许选子集跑主判定**（子集会放大检索漂移的抽样误差）。子集仅允许用于**预筛 pilot**（≤5 题、仅看是否崩，不用于结论）。
- 预算：7 臂 × 3 轮 ≈ 21 次全量跑；并发 3 → 墙钟约 **5~7h**，LLM 成本约 **¥10**（按 ¥0.48/轮含 judge）。超预算时**优先砍 Arm 2/6**（有前置条件的兜底臂），保 Arm 1/3/4/5。

**三、主指标预注册（每臂只认一个主指标，防多重比较假阳性）**

| 臂 | **主指标（唯一判定依据）** | 次要指标（只看不判） |
|---|---|---|
| Arm 1 | `coverage`（覆盖度） | avg_steps、gap 非空率、reflection_critic_stop_rate |
| Arm 2 | `coverage` | max_subquestions 遵守率、单条成本 |
| Arm 3 | `citation_accuracy`（**严格口径** verified） | verified_relaxed、失败分类占比（5 类归因） |
| Arm 4 | **失败分类中"幻觉类"占比**（诊断基数：123/298 = 41.3%） | citation_accuracy、"信息不足"标注率 |
| Arm 5 | validator 成本占比 | citation_accuracy（**守门指标，不降即过**） |
| Arm 6 | 全局金额成本 | citation_accuracy（**守门指标**） |

> 技术债② 与③ 的主指标**刻意错开**（准确率 vs 幻觉占比）：二者都影响引用准确率，若同用准确率则无法归因。用 §5.2 诊断已建立的 5 类失败分类做归因拆分，是本设计能分离二者贡献的关键。

**四、判定门槛（写死在文档，防事后找补 / p-hacking）**

- **正向判定**：主指标配对差值 ≥ 门槛，且 **3 轮中有 ≥2 轮同向**（不要求 3/3 —— 承认检索波动）。
- **守门判定**（Arm 5/6）：`citation_accuracy` **不低于基线 1.0pp**（阈值内视为"未下降"）。
- **Arm 6 降档专用门槛**：见 §5.4，机器口径准确率 ≥ 71.0%（基线 72.0% - 1.0pp）才采纳。
- **不达标处理**：如实记录 + 回滚该臂改动，**禁止**以"换个指标看变好了"作为采纳理由；若确需换指标，必须在变更记录中显式声明并说明理由（诚实披露铁律）。

**五、执行与留痕**

- 每次跑沿用 W5 两阶段管线（`run.py`），`reset_stats()` 隔离 Phase1/Phase2 成本。
- 结果入 `docs/eval-report.md` 新增「W7 技术债对照实验」章节，逐臂记录：轮次、配对差值、主指标均值、次要指标、判定结论。
- 所有数字遵守数据诚信铁律：**标注口径（严格/宽松）、轮次、单轮还是均值**。

#### 🔚 TBD-8 实验收口复盘（2026-09-13）——**协议无误，但分辨率不足；按「如实收口」终止**

实验实际执行 6 臂 × 3 区块（Block 0 于 09-11 19:41 ~ 09-12 00:31，Block 1/2 于 09-12 23:39 ~ 09-13 08:11 补跑完成），
共 18 runs（17 `done` + 1 `skip_gate`），Phase1 累计成本 **¥14.4303**。

**结论：TBD-8 的实验设计在本项目的数据尺度上不可判定，不再投入重跑。** 完整数据与判据见
`docs/eval-w7-conclusion.md` §7（补跑诊断）与 §8.2（各 arm 最终记账）。要点：

| 项 | 结果 |
|---|---|
| Arm 6（validator 降档） | ❌ **不成立**：表面 +19.93pp 中约 +7.5pp 为裁判效应；同裁判仅 +6.68pp 且换裁判后**方向翻转为 −3.47pp**；绝对通过条数 **−43.5%**（49.05 → 27.70 条/篇） |
| Arm 1（critic gap） | ⚠️ **弱证据，不予收款**：coverage 名义 +15.0pp，但 `avg_steps` **+117.3%（配对口径）**，超本文件 DoD 守门线（≤+50%）；机制为「以步数换覆盖度」。**其不可收口的原因是成本闸门失败，不是被噪声吞没**——arm1 三区块 coverage 极差仅 4.6pp（σ̂≈2.7pp），其配对效应在自身噪声下是同向的 |
| Arm 3 / 4 / 5 | ❌ **不可判定**：配对效应在区块间**符号翻转**（arm3 `+3.7/−4.3/−18.7`、arm4 `+4.2/−1.7/−28.7`、arm5 `+1.3/−6.7/−25.4`） |
| 噪声底（实测） | 同 arm 跨区块 `citation_accuracy` 波动 **4.5~10.1pp**；**低步数臂**的 coverage 为 **3 点区块极差 20~29pp（是极差，不是标准差；按 E[range]≈1.69σ 折算 σ̂≈12~17pp）** ⇒ 仍明显大于 TBD-8 门槛 +4~10pp |
| 元结论 | **`coverage` 受「证据池命中率（Block 级环境）× 检索轮数（arm 机制）」共同主导，二者在本实验中共线**：17 个 run 的 coverage 与 `retrieval_hit_rate` 相关系数 **r=0.9617**；Block 1 命中率塌陷与 coverage 同步下跌（低步数臂无法自愈，高步数臂靠多轮检索部分「自平均」） |

**协议本身没有执行瑕疵，但有四处需在后续实验中修正（已写入 `docs/eval-w7-conclusion.md` §8.2）：**

1. **被测对象不得兼任裁判** —— 原设计中 `metrics.py` 的引用准确率直读主链路 validator，而 Arm 6 改的正是 validator；
2. **`avg_steps` 是 critic_gap 的中介机制，不是独立混杂变量** —— 它只取 `{3.85, 8.1~9.3}` 两档且与 `CRITIC_GAP_ENABLED` 完全共线，**强行控制为常量等于关掉被测机制本身**；正确做法是改做**同预算成本-效果比较**（固定 max_steps / max_tokens / max_cost，比较同成本下的 coverage，或画成本-效果曲线）；
3. **必须固定检索快照/证据池** —— Block 级命中率漂移是 coverage 的主导环境因素（实测 r=0.9617），不能把它归因给 arm；配对设计只能消除共性漂移，消不掉「同题两次检索结果不同」的个体噪声；
4. **补跑前必须验证「当前工作树 ≡ 实验时状态」** —— 本次补跑跑在 `ca51886`（含 `60a4b50`），与 Block 0 的 `95adb77`+patch 口径不一致，该教训已写入长期记忆铁律。

**对 §5.5「五、执行与留痕」与 TBD-10 简历回填的影响：** 结果**未达标**，故按 TBD-10 既定口径，
简历**只回填定性描述**（"建立了 eval 驱动的 Agent 质量归因体系"），**不写任何提升幅度数字**；
博客③《用 eval 数据诊断 Agent 引用幻觉》按诚实披露铁律**如实写失败归因**，不粉饰。

### 5.6 内容扩散（W6 收尾延期）

#### ✅ TBD-9 定案（2026-09-09）——**博客② Langfuse 实战：明确暂缓至 W8+；改推博客③ 为 W7 完成后首选**

**前提核实**：W3 真实 trace 冒烟 **已 PASS**（`docs/requirements/3-langfuse-observability.md:134`，trace `093f7bda…`，17 观测点 = 10 span + 5 generation，CLI 回显 URL + 对账一致）→ 博客② **素材齐备，非"做不了"，而是"排不上"**。

**暂缓理由（优先级重排，而非砍掉）：**

| 维度 | 博客② Langfuse 接入实战 | **博客③ W7 幻觉归因（新增）** |
|---|---|---|
| 内容类型 | 教程型（如何接可观测） | **工程洞察型**（如何用 eval 数据归因 Agent 缺陷） |
| 可替代性 | 高——官方文档 + 人人可写 | **低**——依赖本项目真实数据与 5 类失败分类 |
| 面试区分度 | 中（"你接入了 Langfuse"） | **高**（"你怎么知道 Agent 真的有效 / 72% 是哪 41% 幻觉造成的"） |
| 素材就绪度 | ✅ 现已就绪 | ⏳ 依赖 W7 实验结果（顺带成为实验的交付压力） |

- **判断**：Agent 岗最被追问的是「你怎么证明你的 Agent 有效」，W5 eval + W7 归因正是该问题的答案，区分度**高于**可观测接入教程。
- **定案**：博客② 暂缓 W8+（素材已冷冻，随时可写）；**博客③ 列为 W7 实验产出后的首选篇**，题目方向《用 eval 数据诊断 Agent 引用幻觉：从 72% 到五项归因》。若 W7 实验数据不理想，按诚实披露铁律**如实写失败归因，不粉饰** —— 失败归因本身也是高质量内容。

#### ✅ TBD-10 定案（2026-09-09）——**链接回填本期做（还欠债）；简历回填锁在 W7 技术债落地之后**

- **链接回填（本期必做，≈15min）**：博客①《agentic loop 设计与 Critic 踩坑》已于 2026-09-07 发布**掘金主发 + 知乎镜像**，但发布后**未回填链接**（plan.md §6 W6 遗留任务 3 标注"可选"，实际属已发布内容的欠债，容易遗忘）。回填范围：
  1. `README.md` —— 新增/补全「相关文章」区（博客① 掘金 + 知乎双链）；
  2. `.workbuddy/deepresearch-plan.md` §6 W6 实现记录——把"草稿 docs/blog/"补成"已发布 + 链接"；
  3. `docs/requirements/6-open-source.md` —— 同步发布状态与链接。
  - **约束**：README 措辞延续 TBD-6 口径，**禁"面试/加分"字样**（公开仓库口径）。
- **简历回填（不提前，锁在 W7 技术债落地后）**：沿用 plan.md §6「落地才写」铁律（吸取 WeChatBot 误标教训）。**W7 技术债未经验证前不写任何数字**。若 Arm 1/3/4 判定达标，回填素材为「eval 驱动的 Agent 质量归因与优化（覆盖率 +Xpp / 引用准确率 +Xpp）」——**数字必须来自 TBD-8 的 3 轮配对均值，禁止用单轮数字**。若未达标，简历只回填定性描述（"建立了 eval 归因体系"），不写提升幅度。

## 6. 实现策略（落地执行计划，2026-09-09 补）

> **总纲**：按 §1 元信息实现顺序执行（**Arm 3 → Arm 4 → Arm 1 → Arm 5 → Arm 2/6 → 链接回填**）。每 Arm 独立提交 + 独立单测 + 独立对照实验，改动互不嵌套（受控单变量）。工时为**设计估算**（含单测，不含 eval 墙钟）。

### Arm 3 —— 技术债② validator 五项修复 + 双口径（TBD-4/5）

| 项 | 内容 |
|---|---|
| 改动文件 | `research_engine/agents/validator.py`（核心）、`research_engine/state.py`（Citation 字段）、`research_engine/render.py` + `cli.py` + `web/app.py`（呈现）、`research_engine/eval/metrics.py`（口径统计） |
| F1 去截断 | `validator.py:185` `claim: {r['claim'][:100]}` → **去掉 `[:100]`**（LLM input 侧；`citations_json` 行只截 source 字段不截 claim）；`claim` 最长 195、中位 51，可整送 |
| F2 回显对齐 | `CitationVerdictItem` 增 `claim_echo: str` 字段（RAGAS 式回显原文，字段名同 §5.2 表格）；LLM 输出必须逐字回显原 claim；`validator.py:207-212` 对齐时做**模糊比对**（如编辑距离比 <0.6）→ 不一致标 `verdict_unreliable`，**降级为"未获 LLM 反馈"保守通过并留痕**（不静默判 false） | 治 **R1** verdict/claim 错位 |
| F3 非论断过滤 | `validator.py:108-123` `_extract_citations` 后置过滤：拒绝**非论断句**（如"本节将…""综上所述""如图…"等元话语/过渡句、markdown 表格残片、纯标题、长度 <8 字符、无谓语片段）进 LLM 校验通道（纯规则/正则白名单，零 LLM） | 治 **R4** claim 抓到非论断内容 |
| F4 保留上下文 | `_claim_text`（validator.py:90-106）改为"**最近句子边界 + 前一句主语兜底**"：当截断后 claim 无主语/缺主语时，自动前补前一句主语，使论断自包含（满足 RAGAS "no pronouns / self-contained"） | 治 **R5** claim 丢失主语 |
| F5 判定措辞 | `VALIDATOR_SYSTEM`（validator.py:32-55）追加：明确"能否**直接由该来源推断**"作为 faithful 标准（比"不夸大不曲解"更可判定），含明确数值/日期/名称的算术推断视为忠实 | 治判定一致性 |
| TBD-5 双口径 | `state.py:47` `Citation` 加 `verified_relaxed: bool`（= `existence AND (faithful OR supported)` 推导，**默认 false**），用于呈现与解释；`verified` 语义保持 W2 契约 `existence AND faithful` 不变；`metrics.py` 统计键拆分 `citation_accuracy`（严格）与 `citation_accuracy_relaxed`；`render.py` 报告溯源块/CLI 汇总/R2 呈现同步标注两口径 | 治 **R3** supported=true 但未计入 strict 口径的口径差 |
| 配套单测 | `tests/test_validator_fixes.py`（新增）：① 长 claim 不截断 → LLM 收到的 claim==原文 ② `claim_echo` 错位 → 标 unreliable 并降级通过留痕 ③ 非论断句/表格残片被过滤 → 不产 LLM 调用 ④ `_claim_text` 无主语 → 自动补主语 ⑤ `verified_relaxed` 在 supported=true 时=true、strict verified=false ⑥ `failed_citations.jsonl` 抽 5 条回归 |
| 验证 | 单测全绿 + `failed_citations.jsonl` 298 条重跑分类对比（R1~R5 五类占比） |
| 工时 | ≈**2~3h**（主改 validator.py，双口径呈现层改动小） |

### Arm 4 —— 技术债③ writer 分节喂料 G1~G5（TBD-6）

| 项 | 内容 |
|---|---|
| 改动文件 | `research_engine/state.py`（`ResearchFinding.sq_id`）、`research_engine/graph.py`（检索后打标）、`research_engine/context/manager.py`（按子问题分节 + 压缩透传）、`research_engine/agents/writer.py`（prompt + 系统兜底） |
| G1 sq_id 字段 | `state.py` `ResearchFinding` 加 `sq_id: str = ""`；`graph.py:142` 在 `search_once` 返回后统一 `post-tag`（覆盖 web/rag/arxiv/code_exec 全路径），避免改造 4 个工具函数；`context/manager.py:compress` 压缩后透传 `sq_id` |
| G2 分节喂料 | `context/manager.py:format_for_writer(findings, subquestions)` **按 sq_id 切换分组**：遍历 findings 保持原始顺序（**编号 [N] 必须对齐 `state.findings`**），遇到新 sq_id 插入 `--- 子问题：<question> ---` 分隔；无 sq_id 的归入"未分类材料"节 |
| G3 无材料标记 | `format_for_writer` 在末尾显式列出"**以下子问题暂无研究发现**"并标注 `（暂无研究发现）`，把信息不足义务直接喂给 writer |
| G4 系统兜底 | `writer.py:_ensure_sections` 后置机械检查：① 越界引用 `[来源: N]`（`N<1` 或 `N>len(findings)`）强制替换为 `[来源: 信息不足]`；② 遍历 `subquestions`，报告未出现子问题文本 → 系统级 append 二级标题 + 信息不足正文（**不依赖 LLM 自觉**，零额外调用）。`smart_chat` 抛异常时走 `_fallback_report` 全信息不足兜底 |
| G5 prompt 改写 | `WRITER_SYSTEM` 新增核心约束："只能使用研究发现中材料，禁止使用外部知识"、"必须按子问题分节，无材料必须写'信息不足'"、"禁止编造编号" |
| 配套单测 | `tests/test_writer_sectioned_feed.py`（新增 8 条）：① 分节编号保序 ② 无材料子问题列出 ③ compress 透传 sq_id ④ 越界引用替换 ⑤ 缺失小节系统兜底 ⑥ LLM 失败系统兜底 ⑦ write 调用按子问题分节 |
| 验证 | 单测全绿（95 passed）+ ruff 全绿；失败分类"幻觉类"占比对比待 TBD-8 实验 |
| 工时 | **≈1.5h**（state 字段 + post-tag + 分节 + 兜底 + 单测） |

### Arm 1 —— 技术债① Step1：knowledge_gap 硬规则（TBD-1/B′ + TBD-1b/E+）

| 项 | 内容 |
|---|---|
| 改动文件 | `research_engine/critic.py`（`CriticVerdict`/`decide`/`_resolve_signal`/`_verdict`）、`research_engine/state.py`（新增 `critic_gap`/`critic_stop_reason`）、`research_engine/graph.py`（reflection_log 补字段） |
| gap 字段 | `critic.py` `CriticVerdict` 加 `knowledge_gap: str = ""`；`_verdict` system prompt 要求"若判充分但仍有缺口，写 knowledge_gap 并给出 next_queries；否则留空" |
| 硬规则 | `critic.py:decide` 在 hard_gate 之后调用 `_resolve_signal`：① `needs_replan=true` → revise；② `sufficient=false` → continue；③ `sufficient=true ∧ gap 非空 ∧ next_queries 非空` → **强制 continue**（覆盖 LLM 早停）；④ `sufficient=true ∧ gap 非空 ∧ next_queries 空` → stop + `gap_unresolved`；⑤ `sufficient=true ∧ (gap 空 ∨ next_queries 空)` → stop + `critic_stop`/`no_next_queries` |
| N=6 封顶 | 硬规则条件 `len(state.reflection_log) < MAX_GAP_REFLECTIONS`（`MAX_GAP_REFLECTIONS=6`）；≥6 后不再强制 continue，交还 LLM 决策 + hard_gate |
| 预算感知 | `_verdict` user prompt 注入"第 k/6 轮反思"（`k = len(reflection_log)+1`） |
| 留痕 | `state.critic_gap`/`critic_stop_reason` 写回；`graph.py` reflection_log 每条扩展 `knowledge_gap` + `stop_reason` |
| 配套单测 | `tests/test_critic_gap.py`（新增 8 条）：① gap+queries → continue ② gap 无 queries → gap_unresolved ③ 无 gap 无 queries → no_next_queries ④ 不充分 → continue ⑤ needs_replan → revise ⑥ 第 6 轮封顶 ⑦ 硬闸 stop_reason=hard_stop ⑧ prompt 含"第 k/6 轮" |
| 验证 | 单测全绿（103 passed）+ `tests/test_graph_loop.py` 收敛单测仍绿 |
| 工时 | **≈1h**（schema + 硬规则 + state 字段 + 单测） |

### Arm 5 —— 技术债④ A′ 喂料裁剪（TBD-7）

| 项 | 内容 |
|---|---|
| 改动文件 | `research_engine/agents/validator.py` 一处（新增 `_build_findings_text` + `validate` 调用点替换） |
| 改法 | `_build_findings_text(findings, to_check)`：只保留 `to_check` 中 `finding_id` 所指的 findings，用 `enumerate(findings, 1)` 原始编号过滤，**保留原编号**；阶段 1 存在性校验仍基于全量 `index`；当 finding_id 为空（URL 协议引用命中 source）时降级全量并 `warnings.warn` |
| 配套单测 | `tests/test_validator_fixes.py` 内加 3 条：① 存在性校验仍基于**全量** index（越界编号 → existence=False）② 裁剪后编号保留原编号，未引用 finding 不出现 ③ URL 引用 finding_id 为空 → 降级全量 |
| 验证 | 单测全绿（106 passed）+ ruff 全绿；成本占比待 TBD-8 实验 |
| 工时 | **≈0.3h**（纯工程） |

### Arm 2（条件触发）—— 技术债① Step2：persona 视角发现（TBD-1c②）

| 项 | 内容 |
|---|---|
| 改动文件 | `research_engine/agents/planner.py`（前置归纳）、`research_engine/search/*`（1 次检索）、`research_engine/state.py`（perspectives 字段）、`research_engine/graph.py`（plan 前挂载，倾向 planner 内嵌最小侵入） |
| 实现要点 | 1 次检索（**标题+摘要首句**）→ 1 次 LLM 归纳 **4 个 persona** → 恒定兜底"基础事实收集"视角 → planner 以 5 视角产 ≤4 子问题（视角进 `rationale`，**不动 `max_subquestions=4`**）→ 检索/归纳失败降级只用兜底视角 |
| 配套单测 | `tests/test_planner_persona.py`：① 视角归纳 prompt 命中 ② 失败降级兜底 ③ 输出子问题数 ≤4 |
| 工时 | ≈**4~6h**（TBD-1 定案 Step 2 成本账）；**前置条件：Arm 1 达标** |

### Arm 6（条件触发）—— 技术债④ C 降档（TBD-7）

| 项 | 内容 |
|---|---|
| 改动文件 | `config.py`（`VALIDATOR_MODEL` env，默认回落 `SMART_MODEL`）、`validator.py:197` 一行（`model=config.llm.validator_model`） |
| 实现要点 | **零主链路改动**：env 未设 → 行为与现状完全一致；实验通过才改默认值 |
| 配套单测 | `tests/test_validator_fixes.py`：config 读取回落逻辑 |
| 工时 | ≈**0.3h**；**前置条件：Arm 3 达标** |

### 链接回填（TBD-10）

| 项 | 内容 |
|---|---|
| 改动文件 | `README.md`（「相关文章」区）、`.workbuddy/deepresearch-plan.md` §6 W6 记录、`docs/requirements/6-open-source.md` |
| 内容 | 博客①《agentic loop 设计与 Critic 踩坑》掘金 + 知乎双链回填（**禁"面试/加分"字样**） |
| 工时 | ≈**15min** |

## 7. 设计策略
- **受控单变量**：一次只改一项技术债，各自独立对照实验（用户方法论偏好；隔离边际贡献，防止"合起来改好了但说不清谁起效"）。
- **数据驱动**：所有阈值/机制改动用 v1.1 数据集 + 多次重跑均值验证（W5 教训：单轮数字不可信）。
- **最小侵入**：技术债修复尽量不动 W1~W4 已定契约（state 字段/hard_gate 结构/ADR 协议）；触碰既有定论（如 ADR-0005 逐条校验、分档）必须显式记录"推翻/修订"及理由。
- **诚实披露延续**：修不完或修不好如实声明；博客/README 数字继续遵守数据诚信铁律。

## 8. 验收标准（DoD，2026-09-09 目标值已拍板）

> **口径通则**：所有数字均指 **TBD-8 配对交替实验的 3 轮配对均值**（≥2/3 轮同向），**禁止单轮数字作结论**。基线取 `run_v11_compare`：coverage 55.4% / citation_accuracy 59.7%（宽松口径）· 72.0%（机器口径）/ validator 占比 52.7% / 全局 ¥0.1133（Phase1）。

> ### 🔚 最终判定（2026-09-13，TBD-8 实验已收口）
>
> 六臂 × 3 区块已全部执行完毕（18 runs：17 `done` + 1 `skip_gate`，Phase1 累计 ¥14.4303）。
> **下列各达标线均未获通过**，原因分三类：① **分辨率不足**——低步数臂的 coverage 3 点区块极差 20~29pp
> （是极差不是标准差，σ̂≈12~17pp）明显大于 DoD 门槛 +4~10pp；② **方向不稳**——Arm 3/4/5 的配对效应在区块间**符号翻转**；
> ③ **成本闸门**——Arm 1 的 coverage 名义提升伴随 `avg_steps` +117.3%（配对口径），破守门线（≤+50%），**不是被噪声吞没**。
>
> | 达标线 | 判定 |
> |---|---|
> | Arm 1 coverage ≥ +5.0pp | 数值名义 +15.0pp，但 **`avg_steps` +117.3%（配对口径）破守门线（≤+50%）** ⇒ ⚠️ 不予收款 |
> | Arm 1 守门线（avg_steps 涨幅 ≤+50%） | ❌ **未通过**（+117.3%） |
> | Arm 3 citation_accuracy ≥ +4.0pp | ❌ **不可判定**（区块方向翻转） |
> | Arm 4 幻觉占比下降 ≥10pp | ❌ **不可判定**（区块方向翻转） |
> | Arm 5 成本占比 ≤45% + 守门线 | ❌ **不可判定**（区块方向翻转） |
> | Arm 6 机器口径准确率 ≥71.0% | ❌ **不成立**：裁判效应与被测效应混淆，绝对通过条数 −43.5%；且 Block 2 因守门未过缺失。⚠️ **另记协议违背**：该 arm 按 §8 风险⑧ 止损条款**本应取消**（Arm 3 未达标），实际仍执行，故其数据只作诊断、不作裁决依据 |
> | 六臂实验记录落 `docs/eval-report.md` | ✅ 已落：趋势表 🧪 标记 + 报告首页可比性声明 + **`## 9. W7 技术债对照实验` 专章**（9.1 轮次×主指标 / 9.2 配对差值与机械判定 / 9.3 不可比性声明，2026-09-13 补齐，由 `report_gen.py` 机械生成）。**权威判定仍以 `docs/eval-w7-conclusion.md` 为准**（报告是自动生成快照，每次 run 整体覆盖） |
>
> 完整数据、复判证据与元结论见 **`docs/eval-w7-conclusion.md`**；协议层面的复盘（四处需修正之处）见 §5.5 末尾。
> **未通过各类达标线的 checkbox 不再逐条勾选，以上表为总账。**

### 技术债①（critic 覆盖度信号）
- [x] **Arm 1 / TBD-1 Step 1**：`CriticVerdict.knowledge_gap` + "gap 非空不许停"硬规则 + N=6 反思封顶落地
- [ ] **Arm 1 达标线**：`coverage` 配对差值 **≥ +5.0pp**（55.4% → ≥60.4%；门槛高于配对设计下的噪声 ~2pp）
- [ ] **Arm 1 守门线**：`completion_rate` ≥95%（基线 100%）、`avg_steps` 涨幅 **≤+50%**（TBD-1b 封顶口径）、`gap 非空率`不塌至极低（防模型学会留空逃避）
- [ ] **Arm 2 / Step 2**：persona 视角发现跑通 —— **仅当 Arm 1 达标才执行**；达标线 `coverage` 在 Arm 1 基础上再 **≥+3.0pp**，否则回退（不影响 Arm 1 结论）
- [ ] ~~组件③澄清范围~~ → **本期不实现**（TBD-1d 冻结，挂 W8+）

### 技术债②（validator 误拒修复）
- [ ] **Arm 3 / TBD-4**：五项修复（F1~F5）全部落地且**零额外 LLM 调用**
- [ ] **Arm 3 达标线**：`citation_accuracy`（**严格口径 verified**）配对差值 **≥ +4.0pp**（72.0% → ≥76.0%）
- [ ] **Arm 3 / TBD-5**：双口径（`verified` 严格 / `verified_relaxed`）呈现落地，并在 eval 报告中**显式声明** W5 人工抽检 83~92% 属**宽松口径**（诚实披露）
- [x] 失败分类（5 类）归因表产出，可解释提升来源 → **`docs/eval-w7-attribution.md`**（2026-09-13 产出；互斥归类 R4/R2/R3/HALLU/MISSRC/OTHER 加总 298；技术性误拒 123 条 / 须技术债③ 110 条；F1+F3 上界 80.4%）

### 技术债③（writer 幻觉治理）
- [x] **Arm 4 / TBD-6**：G1~G5 落地（`ResearchFinding.sq_id` + 分节喂料 + 无材料标记 + 系统级兜底 + prompt 改写）
- [ ] **Arm 4 达标线**：失败分类中**「幻觉类」占比**从 **41.3%**（123/298）**下降 ≥10pp**
- [ ] 报告中"信息不足"标注比例可统计（次要指标，只看不判）

### 技术债④（validator 降本）
- [x] **Arm 5 / TBD-7 A′**：喂料裁剪落地，**三条硬约束**（① 存在性校验仍用全量 index ② 保留原编号 ③ 不交集降级全量）均有单测覆盖
- [ ] **Arm 5 达标线**：validator 成本占比 **52.7% → ≤45%**；**守门线**：`citation_accuracy` 不低于基线 1.0pp
- [ ] **Arm 6 / TBD-7 C**：`VALIDATOR_MODEL` env 落地（默认回落 qwen-plus）—— **仅当 Arm 3 达标才执行**；达标线全局金额 **-20%↑**，守门线机器口径准确率 **≥71.0%**

### 全局
- [ ] 六臂对照实验记录（轮次 / 配对差值 / 主指标均值 / 判定）落 `docs/eval-report.md` 新增「W7 技术债对照实验」章节
- [ ] 主链路契约零意外破坏：W1 硬闸 / ADR-0004 findings 无 reducer / ADR-0005 多编号协议 等既有单测全绿（**ADR-0005 本期不修订**）
- [ ] **链接回填完成**（README「相关文章」区 + plan.md §6 + 需求 6 文档，三处）
- [ ] 博客② **明确暂缓**结论已记录（TBD-9）；博客③ 排入 W8+ 待办
- [ ] 简历回填：**W7 技术债未达标则不写任何提升数字**（TBD-10 铁律）

## 9. 影响范围与风险
- 改动面：`critic.py`（knowledge_gap + 硬规则）、`validator.py`（**喂料裁剪** + 五项修复 + 可选降档）、`writer.py`（分节喂料 + 信息不足标注）、`state.py`（`ResearchFinding.sq_id`，G1）、`config.py`（`VALIDATOR_MODEL`）、`metrics.py`/`docs/eval-report.md`（对照记录）。
- 回归面：critic 改动触碰 W1 硬闸与路由（`tests/test_graph_loop.py` 收敛单测必须全绿）；validator 裁剪/修复/降档触碰 W2 引用口径（引用相关单测）；writer 结构输出触碰 ADR-0004 引用编号协议。
- 风险：① 对照实验墙钟/成本超预算 → **TBD-8 控制**（配对交替 3 轮、并发 3，预算 ¥10 / 5~7h；超预算优先砍 Arm 2/6）；② ~~覆盖度信号引入新 LLM 调用~~（D′ 定案后消除，零额外调用）；③ ~~gap 永非空 → 转晚停~~ → **已由 TBD-1b 控制**：反思轮次专用封顶 N=6 + next_queries 绑定判定，成本上限 **+50%**；eval 监控两项哨兵：**平均步数**（逼近 6 = 模型在"编查询"续命）与 **gap 非空率**（塌到极低 = 模型学会留空逃避）；④ ~~D′ 不治 planner 漏拆自证循环~~（C′ 视角发现后**解除**）；⑤ ~~澄清环节阻塞自动化~~（本期不实现，风险解除；W8+ 启动时仍需 eval/CI 强制关闭）；⑥ ~~视角发现吃预算~~ → **TBD-1c① 消除**（不计 `max_total_hops`、不进证据池）；**残余风险：背景通道内容被 writer "偷引"**（缓解：无编号 → 协议层强制不可引，比 prompt 纪律可靠）；⑦ 组件②单条成本 +18~30%，与技术债④ 形成对冲 → **TBD-8 全局算账**；⑧ **止损机制**：Step 1 无效 → 取消 Step 2；Arm 3 未达标 → 取消 Arm 6（降档）；
  **⑨【新增·技术债④ 高危陷阱】喂料裁剪若误改存在性校验域 → "引用不存在来源"永远检不出**（存在性校验在裁剪域内恒真）。**缓解（硬性，非建议）**：§5.4 三条硬约束——存在性阶段继续用**全量** `index`、编号**保留原编号**、不交集时降级全量并 warn；三条各配一条单测。
  **⑩【新增】降档（qwen-plus → turbo）致忠实度判定质量下滑** → 与"技术债② 要提准确率"方向相反。**缓解**：仅在 Arm 3 达标后执行；守门线 71.0%；不达标维持 plus 并如实记录"降档不可行"。
  **⑪【新增】检索源时间漂移污染实验结论**（W5 已实证：无代码改动下 coverage 跨 run 差 27.6pp）→ **缓解**：TBD-8 配对交替 + 逐题配对差值 + 3 轮 ≥2 轮同向；**残余风险**：配对只能消除共性漂移，无法消除"同一题两次检索结果不同"的个体噪声 → 故门槛取 +5pp（远高于配对噪声 ~2pp）。
  **⑫【新增】多重比较假阳性**（7 指标 × 6 臂）→ **缓解**：主指标预注册，每臂只认一个；次要指标"只看不判"；禁止事后换指标作为采纳理由（需显式变更记录 + 理由）。
- 降级：任何一项技术债修复验证不达标 → 如实记录 + 回滚该项改动，不强推。

## 10. 测试策略
- 单测：每项技术债新增/改造点配套单测 —— critic `knowledge_gap` 硬规则纯函数 / **validator 裁剪三条硬约束（全量 index 存在性、编号保序、降级回退）/ 同 `failed_citations.jsonl` 298 条作回归夹具** / writer 标注检查 / `sq_id` 关联正确性；
- 集成：六臂对照实验 = 真实 API eval（复用 W5 `run.py` 两阶段管线）；离线冒烟走既有注入模式；
- 回归：W1 收敛单测、W2 引用单测、W4 沙箱单测全绿为硬门槛；
- **防回归夹具**：`.workbuddy/diag/failed_citations.jsonl`（298 条失败引用）固化为引用修复的回归基准集，TBD-4 修复后重跑该集，按 5 类分类逐类对比。

## 11. 变更记录
| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-09 | 建稿 | W7 启动 | 初始版 V0：四项技术债现状盘点（critic.py/validator.py/writer.py/metrics.py 逐行定位）+ 内容扩散三项 + TBD-1~10 待设计评审 | |
| 2026-09-09 | 拍板 | 设计评审 Q1 | **TBD-1 定案 = 方案 D′（行业调研裁决）**：`CriticVerdict` 加 `knowledge_gap` 字段（零额外调用，抄 gemini-fullstack `reflection_instructions`）+ **硬规则「sufficient=true 且 gap 非空 → 强制 continue」**（纯函数层，不进 hard_gate 四闸）。成本 ¥0（原方案 D 需 ¥0.036/轮）。**否决方案 A 机械计数**（四家主流零采用 + W5 覆盖-忠实负相关实证 + 需补关联地基）；**方案 B 语义对查降级为加强针**（仅当 D′ 验证无提升才追加）；**gap 检测内核吸收但改零成本实现**（+¥0.2/轮 → ¥0）；**STORM 视角发现本期不做**（用户裁决，1~2 次搜索 ≈¥0.01/条，代价 = 自证循环不治，作已知局限记录）。**新开 TBD-1b**（gap 判空/晚停兜底）、TBD-2 收窄为优先级问题、TBD-3 退化为 gap 判空规则 | |
| 2026-09-09 | 调研 | Q1 前置行业调研 | 四项目源码级对比（dzhng 纯硬闸递归 / gemini knowledge_gap 三件套 / open_deep_research 三层停止+think_tool / STORM 大纲+视角发现）；**结论：机械覆盖度统计行业零先例，gap 检测可零成本实现**——直接推翻原推荐的部分内容 | |
| 2026-09-09 | 实现 | TBD-10 链接回填 | 博客①《agentic loop 设计与 Critic 节点化踩坑》双链回填：README「相关文章」区、plan.md §6 W6 记录、`docs/requirements/6-open-source.md` §7 | |
| 2026-09-09 | 实现 | TBD-8 实验准备 | 新增 5 个 env 开关（CRITIC_GAP_ENABLED/VALIDATOR_FIXES_ENABLED/WRITER_SECTIONED_FEED_ENABLED/VALIDATOR_TRIM_ENABLED/VALIDATOR_MODEL）；创建 `research_engine/eval/w7_experiment.py` + `w7_analysis.py`；pilot 2 题通过；完整 5 arms×3 runs 已后台启动 | |
| 2026-09-09 | 修正拍板 | 用户"还是想做全面一点" | **TBD-1 由 D′ 升级为 C′**（+视角发现 +澄清范围）：D′ 内核不动；成本重算 +35%/轮；新开 TBD-1c/1d | |
| 2026-09-09 | 拍板 | 设计评审 Q2 | **TBD-1b 定案 = 方案 E+（行业调研裁决）**：① 强制 continue = `sufficient=true ∧ knowledge_gap 非空 ∧ next_queries 非空`（gap 非空但无 next_queries → 允许 stop 并留痕 `gap_unresolved`）② **反思轮次专用封顶 N=6**（`len(reflection_log)>=6`，**不复用 max_total_hops=20**——那是跳数预算非反思预算）③ **prompt 预算感知**（注入"第 k/6 轮"）。**否决 A 纯字符串判空**（行业零先例）、**B gap_blocking 自评布尔**、**C 新增计数器字段**（改用 reflection_log 零字段）、**D 文本规则**。成本 ¥0、实现 ≈0.8h、晚停风险 ×2~5 → ≤+50%；留痕新增 `stop_reason` 四态含"带缺口停止" | |
| 2026-09-09 | 拍板 | 设计评审 Q3 | **TBD-1c① 定案（视角发现归属）**：① **不计入 `max_total_hops`**（plan 前置动作，对齐 STORM 两阶段独立预算）② **用途隔离三通道**：perspectives 给 planner / 原始结果存**独立背景通道（不进 `state.findings`，故无编号 → ADR-0004 天然隔离：writer 不可引、validator 不校验、成本零增）/ 确有证据价值的内容走 frontier 正式检索**。否决①用完即弃（行业零先例）、②全并（validator +30~60% 与技术债④ 打架）、⑤′压缩进池可引（有损 + **断 W2 溯源链**） | |
| 2026-09-09 | 拍板 | 设计评审 Q3b | **TBD-1c② 定案（视角发现实现形态）**：1 次检索只取**标题+摘要首句**（结构信号，不取正文）→ 1 次 LLM 归纳 **4 个 persona 视角角色** → **恒定兜底"基础事实收集"视角**（抄 STORM `Basic fact writer`）→ planner 拿 5 个视角产 ≤4 子问题（**不动 `max_subquestions=4`**，视角进 `rationale` 零新结构）→ 降级只用兜底视角。成本 ≈¥0.007/条（+18%/轮）。**技术债① Q1~Q3b 全部定案，设计闭环** | |
| 2026-09-09 | 调研 | Q3b 前置行业调研 | STORM 视角发现源码：**不用搜索引擎**（LLM 参数知识找相关主题 → 抓页面**只取 h1+h2~h6 目录**，不取正文）；**视角=persona 角色**（非"方面"）；**恒定兜底 `Basic fact writer`**；多 persona 对话 `ThreadPoolExecutor` 并行。→ 视角发现消费的是**结构信息**而非事实信息，与 ⑤″ 用途隔离天然咬合 | |
| 2026-09-09 | 调研 | Q3 前置行业调研 | **无一家丢弃检索产出**：STORM `raw_search_results.json`→筛选→`url_to_info.json`（文章实际引用来源）；open_deep_research `compress_research` 双轨（compressed+raw_notes，说明**压缩丢的是上下文位置不是数据本身**）；gemini/dzhng 全进 | |
| 2026-09-09 | 调研 | Q2 前置行业调研 | 判定器=布尔/工具调用（gemini `is_sufficient: bool` + open_deep_research `ResearchComplete`），**无一家字符串判空**；gap 只是生成 follow_up_queries 的输入、不参与停止决策；**封顶=独立循环计数器 + 小上限**（gemini 2 / open_deep_research 6、10 / STORM 5）；**预算感知**=上限写进 prompt；**无一家做无进展检测** | |
| 2026-09-09 | 诊断 | 技术债② 真实数据归因（零 API） | 读 `run_v11_compare` 落盘数据：**1066 条引用、机器口径准确率 72.0%**；**证伪原假设**——单编号 vs 多编号通过率 70.9% vs 72.4%（**无差异**），故**多编号逐条校验不是误拒主因，ADR-0005 不修订**。定位 5 个真实 bug：R1 verdict/claim 错位、R2 claim 截断 `[:100]`、R3 `supported=true` 未计入、R4 非论断句被捕获、R5 主体丢失。`298` 条失败引用固化为回归夹具 `.workbuddy/diag/failed_citations.jsonl` | |
| 2026-09-09 | 拍板 | 技术债② | **TBD-4 定案**：吸收 RAGAS 范式做五项修复 F1~F5（**全部零额外 LLM 调用**）——论断不截断、claim_echo 回显对齐（抄 RAGAS `statement: word-by-word`）、非论断句过滤、上下文保留、推断标准入 prompt。**TBD-5 定案**：双口径呈现（`verified` 严格 / `verified_relaxed`=supported OR faithful），并**诚实声明 W5 人工抽检 83~92% 属宽松口径**；预期严格口径 72%→**76~80%**（不做过度承诺） | |
| 2026-09-09 | 诊断+拍板 | 技术债③ | **诊断判定为引用准确率第一瓶颈**：298 条失败中 **123 条（41.3%）系 writer 真实幻觉**，非校验误拒。**TBD-6 定案**：STORM 分节喂料范式 G1~G5（G1 `ResearchFinding.sq_id` / G2 按子问题喂 top-k / G3 显式"无材料"标记 / G4 系统级兜底 / G5 prompt 改写）。**地基反转**：Q1 因"机械覆盖度统计行业零先例"降级 findings↔子问题关联地基，此处基于 STORM 源码级依据**重新启用**，但用途改为**喂料组织与覆盖检查**（非机械计数） | |
| 2026-09-09 | 诊断 | 技术债④ 真实数据归因 | **41.0% findings 从未被引用却全量喂给 validator**（551 条中 226 条）；`findings_text` 字符浪费 **39.0%**（严格复刻 `validator.py:181` 的 `content[:300]`）。**证伪两个候选**：A 缓存（完全重复引用仅 **0.8%**，命中率天花板可忽略）、B 降采样（多编号非成本来源，且触碰 ADR-0005）→ **均否决** | |
| 2026-09-09 | 调研 | 技术债④ 前置行业调研 | RAGAS `Faithfulness` **喂全量** `retrieved_contexts` 不裁剪——但系单轮 RAG 仅 3~5 条，**规模差 10 倍不可比**，不构成对裁剪的反证；RAGAS `FaithfulnesswithHHEM` 提供**本地小模型 NLI 替代 LLM** 的降本终极路径（记入 W8+ 探索，本期不做）；STORM 只对**被引用 snippet** 校验（支持裁剪方向） | |
| 2026-09-09 | 拍板 | 技术债④ | **TBD-7 定案 = A′ 喂料裁剪（直接落地）+ C 降档（进实验验证）**。A′：只喂"存在性已通过"引用所指 findings，**保留原编号**，validator token **-16.8%**、全局 **-8.9%**。C：validator qwen-plus→turbo，全局金额 **-26.0%**（**降档杠杆 ≈ 裁剪的 4.5 倍**）。**三条硬约束**（存在性仍用全量 index / 编号保序 / 不交集降级全量）防"存在性校验失效"高危陷阱。降档**不直接落地**：仅在 Arm 3 达标后实验，守门线 71.0% | |
| 2026-09-09 | 拍板 | TBD-8 实验协议 | **配对交替 + 主指标预注册 + 判定门槛写死**。针对 W5 实证的"检索源时间漂移"（无代码改动下 coverage 跨 run 差 27.6pp）：**禁止串行跑基线**，改为**同期配对交替 + 逐题配对差值**，3 轮 ≥2 轮同向。六臂（Arm 1/3/4/5 主跑，Arm 2/6 带前置条件止损）。**每臂只认一个主指标**防多重比较假阳性；技术债②③ 主指标**刻意错开**（准确率 vs 幻觉占比）以利归因 | |
| 2026-09-09 | 拍板 | TBD-9/10 内容扩散 | **博客② Langfuse 实战明确暂缓 W8+**（素材已就绪但属教程型、区分度低），**改推博客③《用 eval 数据诊断 Agent 引用幻觉》**为 W7 后首选（工程洞察型、区分度高、依赖本项目真实数据）。**链接回填本期必做**（补博客① 已发布未回填的欠债：README + plan §6 + 需求 6 三处）。**简历回填锁死**：W7 未达标不写任何提升数字，数字必须来自 3 轮配对均值 | |
| 2026-09-09 | 拍板 | 用户授权自主推进 | 用户指令："每次思考一个设计点时先参考其他项目怎么做，再按项目选择更合适的，然后自己更新需求文档" → 本文档 TBD-1~10 **全部定案**，进入实现阶段。新增 §5.4/5.5/5.6 定案章节，§7 填全部目标值，§8 新增风险 ⑨⑩⑪⑫，§9 加回归夹具 | |
| 2026-09-09 | **再修正拍板** | 成本/时间账核算后用户"改回去" | **C′ 回退为 B′ 两步走**：组件①（gap 机制）+ 组件②（视角发现）**分步实现、分步验证**（各 3 轮对照实验取均值，数字可归因，Step 1 无效即止损取消 Step 2）；**组件③澄清范围设计冻结为 TBD-1d、实现挂 W8+**——理由：eval 中必须关闭导致收益无法量化验证，且需改图结构（≈4~6h）+ 承担 W1 回归风险。本期落地 ≈6~8.5h + 验证墙钟 4~6h + 运行 +30%/轮；§7 验收改为 Step1/Step2 两条、§8 风险改为 ⑥视角吃预算 ⑦成本对冲 ⑧止损机制 | |
| 2026-09-09 | 补充 | 用户询问实现策略 | **新增 §6 实现策略**：按 Arm 给出落地执行计划（改动文件/精确落点/配套单测/验证/工时）——Arm 3（validator 五项修复 F1~F5 + 双口径，2~3h）、Arm 4（writer 分节喂料 G1~G5，2~3h）、Arm 1（gap 硬规则 + N=6，1.5~2.5h）、Arm 5（A′ 裁剪，0.5h）、Arm 2/6（条件触发）、链接回填（15min）；章节重编号（原 §7~§10 → §8~§11） | |
| 2026-09-09 | 实现 | Arm 3 落地 | **TBD-4/TBD-5 已实现**：validator.py 五项修复（F1~F5）+  双口径；改动文件 6 个（validator/state/metrics/render/cli/web）；新增 （8 单测）+ 更新 （1 处断言）；全量 pytest **87 passed**、ruff 全绿 | |
| 2026-09-09 | 实现 | Arm 4 落地 | **TBD-6 已实现**：`ResearchFinding.sq_id` + `graph.py` post-tag + `context/manager.py` 按子问题分节 + `writer.py` 系统级兜底（越界引用替换 / 缺失小节 append / LLM 失败 fallback）+ prompt 改写；新增 `tests/test_writer_sectioned_feed.py`（8 单测）；全量 pytest **95 passed**、ruff 全绿 | |
| 2026-09-09 | 实现 | Arm 1 落地 | **TBD-1 Step1 已实现**：`CriticVerdict.knowledge_gap` + `Critic._resolve_signal` gap 硬规则（sufficient+gap+queries → 强制 continue）+ `MAX_GAP_REFLECTIONS=6` 封顶 + prompt 预算感知；`state.py` 新增 `critic_gap`/`critic_stop_reason`；`graph.py` reflection_log 扩展；新增 `tests/test_critic_gap.py`（8 单测）；全量 pytest **103 passed**、ruff 全绿 | |
| 2026-09-09 | 实现 | Arm 5 落地 | **TBD-7 A′ 已实现**：`validator.py` 新增 `_build_findings_text` 喂料裁剪（只喂存在性通过的引用所指 findings、保留原编号、URL 引用降级全量）；`tests/test_validator_fixes.py` 加 3 条硬约束单测；全量 pytest **106 passed**、ruff 全绿 | |
| 2026-09-12 | 实验 | TBD-8 Block 0 执行 | 六臂 × Block 0 完成（09-11 19:41 ~ 09-12 00:31，4h19m，Phase1 ¥5.0976）；代码修订钉死 base `95adb77` + diff 指纹 `aa4bb452`（补丁 + `CODE_REVISION.json` 入 `results/curated/`） | |
| 2026-09-12 | 复判 | 固定裁判 2×2 复判 | **`w7_rejudge_20260912_173125.json`**（diagnostic，四格各 n=20，no_verdict=0）：arm0 83.99%(plus)/91.52%(turbo)、arm6 90.67%/88.05% ⇒ 纯裁判效应 **+7.53pp**、同裁判 arm 效应 **+6.68pp**、turbo 下 **−3.47pp 方向翻转**、绝对产出 **−43.5%** | |
| 2026-09-12 | 复判 | formal/diagnostic 等价性 | **不等价**：76.21% vs 79.93%（−3.72pp，formal 有 4.8% 漏裁决）。定规则：formal 作生产判据、diagnostic 只归因，**不得混用** | |
| 2026-09-13 | 实验 | TBD-8 Block 1/2 补跑 | 512.8min（≈8.5h），12 runs 收口，Phase1 **¥9.3327**，累计 **¥14.4303**；`arm6/Block 2` 因 `arm3 citation=0.7011 < 0.71` 守门未过 → `skip_gate`，3×6 配对缺一格。**实测区块噪声 20~29pp ≫ 目标效应 4~10pp** | 补跑跑在 `ca51886`，与 Block 0 的 `95adb77`+patch **口径不一致**（启动前未验证工作树 ≡ 实验时状态） |
| 2026-09-13 | 拍板 | **TBD-8 收口** | 用户决策**如实收口**：不重跑、不强行 `--force-conditional` 补缺格。新增 §5.5「TBD-8 实验收口复盘」、§8「最终判定」总账；状态行由「实现中」改为「已收口」 | 元结论（**2026-09-13 晚修正**）：`coverage` 受「证据池命中率 × 检索轮数」共同主导（r=0.9617） |
| 2026-09-13 | 交付 | DoD 技术债② 末条「失败分类（5 类）归因表产出，可解释提升来源」 | 新增 `tools/w7_failure_attribution.py` + **`docs/eval-w7-attribution.md`**（可复算）。对 298 条夹具做**互斥归类**：R4 结构残片 75（25.2%）/ R2 截断 29 / R3 有依据引错编号 19 / HALLU 明确判虚构 57 / MISSRC 来源未含或不存在 53 / OTHER 65，加总 298。**技术性误拒并集 123 条（41.3%）**，须技术债③治理 110 条（36.9%）。提升来源：**F3 是最大单项杠杆（+5.5pp），大于 F1（+2.7pp）**，F1+F3 算术上界 **80.4%（+8.4pp）**。**修正 §5.2 结论 2 的「误拒仅约 16%」低估**（R4 已识别但未计入误拒分子）⇒ 修复天花板 16% → 41.3%。R1/R5 因旧产物无 `claim_echo`/原报告文本**不可回溯度量**，如实登记 | 本文档 + `docs/eval-w7-attribution.md` |
| 2026-09-13 | 修正 | 与 `docs/eval-w7-conclusion.md` 对齐（该文档已先行修正，本文档漏改） | ① `avg_steps` 涨幅口径统一为**配对口径 +117.3%**（§5.5 表、§8 判定表）；② 元结论由「主导因素是检索轮数」改为「**证据池命中率 × 检索轮数共同主导**」（r=0.9617）；③ **删除已被推翻的建议「必须把 `avg_steps` 控制为常量或作协变量分层」**，改为「`avg_steps` 是 critic_gap 的**中介机制**而非混杂变量，强控=关掉被测机制，应改做**同预算成本-效果比较**」；④ 「20~29pp」明确标注为 **3 点极差非标准差**（σ̂≈12~17pp），并区分 Arm 1 不可收口的原因是**成本闸门**而非噪声；⑤ §5.5 修正清单由三处扩为**四处**（新增「必须固定检索快照/证据池」）；⑥ 补记 **Arm 6 违反 §8 风险⑧ 止损条款**（Arm 3 未达标却仍执行） | 本文档 |
| 2026-09-13 | 移交 | W7 挂账此前只在本文档自述，未进 W8 排期（核对确认 W8 文档搜不到任何一条） | **已移交 `docs/requirements/8-fault-transparency-and-reproducibility.md` §10「承接 W7 挂账」**：10.1 登记 5 项推迟项（博客③ 博客② / 组件③澄清范围 TBD-1d / persona 视角发现 TBD-1c② / RAGAS HHEM 降本探索）；10.2 把 W7 未完成项对齐到 W8 Arm 3/5/6（并指出 Arm 6 仍缺 `prompt_hash` 与 `seed`）；10.3 登记 4 项尚未立项的评测护栏候选（矩阵完整性 / 预算一致性 / 汇总自动排除 / `_config_snapshot` 每轮记录） | 本文档 + `8-fault-transparency-and-reproducibility.md` |
| 2026-09-13 | 修正 | 步数涨幅**估计量**选错（自查发现） | 上一版把 `+117.3%` 改成 `+118.2%`，只验了算术自洽（8.4/3.85=118.18%）**却没质疑估计量本身**。本实验是**区块配对设计**，正确主口径应为**逐区块配对比值均值**：三区块涨幅 +110.4% / +129.9% / +111.5% ⇒ **+117.3%**（原值是对的）；`+118.2%` 属「比值之均值(ratio of means)」口径。两者均远超守门线 ≤+50%，**判定不变**，但已把两份文档统一回配对口径，并注明替代口径。报告 §9.2 由 `report_gen.py` 机械计算该口径，避免人工口径漂移 | 本文档 + `docs/eval-w7-conclusion.md` + `report_gen.py` |
