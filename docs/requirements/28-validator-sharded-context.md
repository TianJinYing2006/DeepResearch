# 需求 28：validator 按批分片上下文（A4）

> 状态：**草稿**（随 PR 合入后置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：mini 锚点（2026-10-07/08，Issue #154）实测——A3 校验地板落地后重型题仍结构性饥饿。
> 前置：需求 19（validator 分批并行）与本需求是**同一模块的前后两次修复**，19 优化了墙钟、
> 本需求修正 19 引入的 token 放大；两者不可互相替代。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 28 |
| 标题 | validator 按批分片上下文（喂料不再每批重复发送） |
| 优先级 | P0（正确性 / 预算） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #154 |
| 关联 PR | #156 |
| 创建 / 更新 | 2026-10-08 |

## 2. 问题背景（mini 锚点实测）

A3（`validation_floor=30k`，需求见 `config.py`）落地后跑 mini 锚点 5 题，raw 口径：

| 题 | depth | 终局 token | 停止原因 | cites | 未校验 | `budget_rejected` |
|---|---|---|---|---|---|---|
| q_001 | 10 | 85,621 | gap_reflection_cap | 4 | 0 | 0 |
| q_002 | 13 | 198,483 | replan_exhausted | 112 | **112（全部）** | **13** |
| q_004 | 10 | 118,265 | replan_exhausted | 21 | 0 | 0 |
| q_012 | 11 | 199,018 | replan_exhausted | 50 | 18 | 2 |
| q_020 | 6 | 193,864 | replan_exhausted | 37 | 32 | 2 |

合计 `budget_rejected = 17`；未校验引用 162/224 ≈ 72%。

**判读**：A3 地板**部分生效**——q_004 校验完整、q_001 的 4 条是判据拒绝（fidelity）而非饥饿；
但重型题仍结构性饥饿，且比推算更重：q_002 引用从 53 涨到 112（findings 97）。

## 3. 根因（三条，均有代码落点）

| 编号 | 事实 | 落点（修复前） |
|---|---|---|
| **F1 决定性** | `findings_text` 在分批循环**之外**一次性构造，`_judge_batch` **每一批**都把整段拼进 user prompt ⇒ 总输入 ≈ **批数 × findings_text**。需求 19 的分批只切了「待判定列表」，没切「上下文」 | `agents/validator.py`（`_build_findings_text` 调用点与 `_judge_batch`） |
| **F2** | `reserve_call` 拒绝条件是 `remaining <= want_in`（剩余装不下输入估计即拒）。q_002 终局 198,483 / 200,000 ⇒ remaining ≈ 1.5k << 14k ⇒ **每批必拒** ⇒ 112 条全部未校验。17 = 13+2+2 与实测完全对上 | `research_engine/budget.py:146` |
| **F3** | `validation_floor` **只在压缩阶段生效**，write 阶段没有地板 ⇒ write 吃掉 40k reserve 的大头，validate 只剩 1.5k | `research_engine/context/manager.py:62` |

F3 解释了「地板部分生效」：轻型题（4 条引用）跑得完，重型题（112 条）饿死。
**F3 不在本 PR 范围内**——先修 F1 把校验需求压下来，再按实测缺口决定是否上调
`token_budget_reserve`（见 §11）。

## 4. 实测 token 账（可直接复算）

复算方式：直接实例化真实 `Validator` 复现 prompt，与线上 degradation 的 `input_estimate`
逐批吻合到 ~1%。脚本 `validator_token_accounting.py`（路径见 §12）。

| 方案 | q_002 单轮校验需求 | 说明 |
|---|---|---|
| 当前实现（batch=16，7 批） | **111,020** | 每批重复整段 findings_text |
| A @ batch=16 | **40,439** | 分片喂料 |
| A @ batch=32 | **32,738** | 分片喂料 |
| A @ batch=0（单批） | ≈ 32k（理论最省） | **不可行**：112 条裁决输出 6~9k > `max_tokens=4096`，撞截断；且整单无批隔离 |

⚠️ **软肋（必须记录）**：`repair` 之后会跑**二次校验**，因此实际消耗是上表的**约两倍**
——A@16 两轮 ≈ 80k，A@32 两轮 ≈ 65k。这意味着**仅靠本 PR 不一定能把 q_002 压进 30k 地板**，
预算是否上调按落地后的实测缺口再定（② 已拍板：先 A 后按缺口定）。

### 4.1 实测复盘（2026-10-08，真实 API）

**端到端重跑作废**：`run_20261008_024805`（13m04s，commit `d2f8b73`）depth=2 / findings=19 /
cites=19 / token=66,913 / `budget_rejected=0` —— 看似达成，实为 arXiv 检索源 HTTP 429 + 超时
导致研究阶段没跑起来（基线 depth=13 / findings=97 / cites=112）。**不可比，不作证据。**
教训：重跑前先比对 `depth` / `findings` 是否与基线同量级。

**可控对照实验**：固定基线那次的真实重型数据（97 findings / 91 working / 5134 字符 report
⇒ 112 cites），构造 `ResearchState(token_used=X)`，只切 `DR_VALIDATE_SHARD_CONTEXT` 与
`token_used`，跑真实 `Validator.validate(report, working, state, evidence=findings)`。
比端到端重跑快两个数量级且完全可比。

| 剩余预算 | shard | batch | 实际消耗 | budget_rejected | 未校验 |
|---|---|---|---|---|---|
| **1,517**（q_002 真实终局） | 1 | 16 | 0 | 7 | **112** |
| 1,517 | 0 | 16 | 0 | 7 | **112** |
| 30k | 1 | 16 | 29,556 | 6 | 96 |
| 40k | 1 | 16 | 38,689 | 7 | 112 |
| 50k | 1 | 16 | 47,532 | 5 | 80 |
| 50k | 1 | 32 | 49,091 | 4 | 112 |
| 50k | 1 | 48 | 42,557 | 2 | 99 |

⇒ **A4 必要但不充分**：分片把每批需求从 ~14k 降到 ~4k，但 `reserve_call` 的拒绝条件是
`remaining <= want_in`，**1.5k 连 4k 都装不下**，开不开分片都得拒。根子在 F3
（`validation_floor` 只在压缩阶段生效，write 阶段无地板）。

**修正 §4 的一处判断**：「分片后 batch 不再是 token 变量」只对**真实 token** 成立，对
**预算预占**不成立 —— 每批还固定预占 4096 的输出上限（`input + min(4096, remaining-input)`），
故总预占 ≈ 28k + **批数 × 4096**：batch=16(7 批)≈56k、32(4 批)≈44k、48(3 批)≈40k。
**batch=32 是较优点**（48 已贴近 `max_tokens=4096` 输出上限，有截断风险）。

脚本：`tools/exp_a4_threshold.py`（已随本 PR 入库；配套 `tools/compare_q002.py` 与单题集 `research_engine/eval/dataset_q002.jsonl`，见 §12）。

## 5. 优化方案（本 PR）

1. **`_build_findings_lines`**：把 `_build_findings_text` 的渲染逻辑抽出为「返回
   `{编号: 行文本}`」的版本；`_build_findings_text` 降级为它的拼接包装（既有签名/行为/用例不变）。
2. **`_source_to_ids` / `_used_finding_ids`**：来源→编号映射、引用→涉及编号的反查，
   全量与分批共用同一套逻辑（不再有两处实现漂移的风险）。
3. **`_judge_batch` 按批挑行**：只保留本批引用涉及的 finding 行拼进 prompt。
   `findings_lines` / `source_to_ids` 均为只读，工作线程无写冲突。
4. **开关 `DR_VALIDATE_SHARD_CONTEXT`**（默认 `1` 开启，`0` 回退旧行为），经
   `validator_shard_context()` 读取。
5. **指标拆分**：`last_validation_stats` 新增
   `unverified_starved_count`（饥饿：预算准入拒绝 + 批调用失败）与
   `verified_rejected_count`（判据拒绝：拿到裁决且判不忠实）。
6. **注释更正**：`config.py` 中 `validation_floor` 的注释（原按 53 条引用 / 4 批 ≈ 55-60k
   推算，已被实测证伪）。

## 6. 设计策略

- **单批场景逐字等价**：`DR_VALIDATE_BATCH_SIZE<=0` 或引用数 ≤ 批大小时，走原路径，
  拼装结果与关闭开关时完全一致（有单测锚定）。
- **多批场景 = 新 Arm**（2026-10-08 拍板 ①）：喂料口径变了，`prompt_hash` 会变——
  不是纯性能优化，必须按新 Arm 对待；`DR_VALIDATE_SHARD_CONTEXT=0` 提供对照与回退。
- **不改动判定口径**：system prompt / schema / claim 字段 / claim_echo 核对 / 宽松口径
  （`verified_relaxed`）与串行路径完全同构；**每个 finding 行的渲染文本逐字不变**。
- **`stats` / `statuses` 按全量 to_check 一次性统计**：喂料分片是 token 优化，
  不得让同一 finding 因被多批引用而重复计入 `evidence_truncated/missing/partial`。
- 不引入新依赖。

## 7. 验收标准（DoD）

- [x] 行索引版本与旧整段拼装严格一致（`test_build_findings_lines_matches_legacy_text`）
- [x] 多批时每批只含本批引用涉及的 finding 行（`test_sharded_feed_only_includes_batch_findings`）
- [x] `DR_VALIDATE_SHARD_CONTEXT=0` 回到每批全量（对照/回退开关）
- [x] 单批场景喂料仍为全量（逐字等价边界）
- [x] 未校验按「饥饿」/「判据拒绝」拆分统计
- [x] 全量 ruff + 全量 pytest 零回归
- [x] **mini 锚点重跑（部分达成）**：端到端 run 因检索源故障作废（§4.1）；改用「固定基线
      数据 + 只切开关」的对照实验，结论为**必要但不充分**——真实输入 111k → 28k，
      但 q_002 真实终局预算（剩余 1,517）下仍 7 批全拒、112 条全部未校验
- [ ] `cit_fid` 从 0 变为可测 —— **未达成**：依赖裁判侧分片（§11.1），百条级题型仍因
      单调用超时（`no_verdict=112`、eval partial）拿不到裁判分
- [ ] **PR 描述改口**：不得宣称「解决了 #154」，须写明「必要但不充分」并列出 §11 后续
- [ ] 裁判侧分片单独立项（见 §11.1）

## 8. 影响范围与风险

- 模块：`research_engine/agents/validator.py`、`config.py`（注释）、`tests/test_validator_batching.py`；
- 风险：**中**——判定口径未变，但多批喂料变窄。已知软肋：
  1. 若某条 claim 需要跨 finding 佐证（综合陈述），只喂本批涉及的 finding 可能丢掉旁证。
     当前 `_build_findings_text` 已是「只喂被引用 findings」，本改动是同一逻辑的延伸，
     风险可控但需 mini 锚点实测确认 `cit_fid` 不降；
  2. 多批场景 `prompt_hash` 变化 ⇒ 与历史 run 的 fid 分数**不直接可比**，须按新 Arm 记录。

## 9. 测试策略

- 新增 5 条零 API 单测（分片生效 / 开关回退 / 单批等价 / 行索引一致性 / 指标拆分）；
- 回归：`tests/test_validator_fixes.py`（`_build_findings_text` 既有 3 处调用）+ 全量 pytest；ruff。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-08 | 修复 | 需求 19 分批每批重复整段 findings_text ⇒ 重型题校验结构性饥饿（#154） | 行索引喂料 + 按批挑行 + 回退开关 + 未校验归因拆分 + 注释更正 | 本 PR |

## 11. 未含在本 PR：三件事（按优先级）

**0. F3：write 阶段地板 / 预算分配（建议需求 29，优先级最高）**

§4.1 实测已证明 A4 单独救不了 q_002：校验的**预占需求 44~56k**，而实际只剩 **1,517**。
正确方向是让「研究 + 写作」在 **~150k** 处停（现在 ~198k）——给 write 阶段也加
`validation_floor` 式保护，或把 `token_budget_reserve` 从 40k 提到 ~90k
（write 40k + validate 50k）。
**不建议**单纯把 `token_budget` 提到 250k：那是花钱掩盖 F3，重型题会继续吃掉新增额度。
**优先级高于裁判侧**——A4 的收益要等 F3 才能真正兑现。

1. **裁判侧分片（F15 / `eval/citation_judge.py`）** —— **单独立项（建议需求 30）**。
   同款问题：q_002 的 `citation_judge` 单次调用带 112 条引用超时，`no_verdict=112`、
   eval 标记为 partial ⇒ **百条级题型拿不到独立裁判分**。
   **为何不并入本 PR**：裁判属**评测侧**，validator 属**生产侧**；两者混在一个 PR 会让
   「生产行为变更」与「评测口径变更」耦合，一旦 fid 分数变化将无法归因。
   但它与 A4 是**同一个模式**（`_build_findings_text` 在 `citation_judge.py:90` 有独立副本），
   且是**阻塞项**（不做就永远拿不到 q_002 的裁判分）⇒ 排在 A4 之后**紧邻**做，不往后拖。
2. **batch 16 → 32**（随上面第 0 项一起定）—— 每批固定预占 4096 输出上限，
   批数减半可降预占开销约 12k（§4.1 实测：16 → 5 批拒，48 → 2 批拒）。
   48 更省但贴近 `max_tokens=4096` 输出上限，有截断风险，不作默认。

## 12. 复算资产（给 reviewer）

- mini 锚点 raw + eval（可直接复算）：
  `C:\Users\ADMINI~1\AppData\Local\Temp\opencode\dr-mini\research_engine\eval\results\run_20261008_003828\`
- 缺陷期基线三轮 `...\dr-w9-baseline\...`；A/B `...\dr-ab\...`
- token 账脚本 `...\opencode\validator_token_accounting.py`
- 阈值实验（复算 §4.1 表）：`tools/exp_a4_threshold.py --raw <上面的 q_002.raw.json>`
  （真实 LLM，逐组跑一次校验；默认 7 组用例）
- 新旧 run 对照：`tools/compare_q002.py --base <基线 raw> --new <新 raw>`
- 单题 mini 集（q_002，重跑锚点用）：`research_engine/eval/dataset_q002.jsonl`
