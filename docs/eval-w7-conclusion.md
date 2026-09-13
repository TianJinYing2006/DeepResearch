# W7 对照实验结论（引用质量技术债 TBD）

> 本文件是 **W7 对照实验的项目级结论**，为长期有效文档。
> `docs/eval-report.md` 是 `report_gen.py` 每次运行自动覆盖的单 run 快照，**不能承载结论**——两者的分工不要混。
>
> 最后更新：2026-09-13 ｜ 状态：**已收口 —— 补跑 Block 1/2 完成，确认该实验设计不可判定**

---

## 1. 一句话结论

**不能宣布 arm6 优于基线 arm0。**

arm6（全部 W7 开关开启，并将 validator 由 qwen-plus 降档为 qwen-turbo）表面上的引用准确率提升 **+19.93pp**（按题均值）中，
约 **+7.5pp 来自「裁判换人」**而非引用质量改善；在同裁判口径下 arm 效应仅 **+6.68pp**，
且该效应**在换成 turbo 裁判后方向翻转为 −3.47pp**；按绝对通过条数衡量，arm6 反而**腰斩（−43.5%）**。

因此 W7 不能按「turbo 降档无损」收口，TBD-8 阈值（+4~10pp）**落在测量噪声之内，无法分辨**。

---

## 2. 实验设定（复现锚点）

| 项 | 值 |
|---|---|
| 数据集版本 | **v1.1**（20 题，created_at 2026-09-06，ai_draft + human_calibration） |
| 实验代码修订 | base commit `95adb77` **+ 未提交 W7 工作树 diff** |
| diff 指纹 | `aa4bb45276976b67772a95bd95b7bc97e13b6778`（20 files changed, +1958 / −116） |
| 补丁位置 | `research_engine/eval/results/curated/w7_revision_aa4bb452.patch` |
| 修订元数据 | `research_engine/eval/results/curated/CODE_REVISION.json` |
| 原始实验容器 | `w7_experiment_20260911_194151/`（6 arms × 3 blocks，18 runs：17 done + 1 skip_gate；补跑诊断见 §7） |
| 权威复判结果 | `research_engine/eval/results/curated/w7_rejudge_20260912_173125.json` |
| 复判时间 / 耗时 | 2026-09-12T17:34:28 / 183.2s |
| 复判模式 | `diagnostic`（**仅用于归因，不作生产判据**，见 §5） |
| 裁判模型 | `qwen-plus` 与 `qwen-turbo` 各判一遍（2×2 冻结设计） |
| 有效裁决 | 四格各 `valid_q=20`、`no_verdict=0`、`llm_failed_rate=0` |

### 两个 arm 的定义与来源 run

| arm | 定义 | 来源 run | Phase1 token / 成本 |
|---|---|---|---|
| `arm0_baseline` | 基线（validator = qwen-plus） | `run_20260911_194156` | 743,752 tok / ¥0.8637 |
| `arm6_validator_turbo` | **全部 5 个 W7 开关开启 + validator 降档为 qwen-turbo**（非单变量降档） | `run_20260911_232515` | 1,067,359 tok / ¥0.7274 |

> ⚠️ 两个 run 的 `summary.json` 均记录 `git_commit: 95adb77`，**这是 HEAD 假象**：
> 实际执行的是「HEAD + W7 未提交 diff」。复现必须应用上面的补丁，不能只 checkout `95adb77`。
> 这两个 run 目录**只能复制、不能移动或改名**——复判 JSON 的 `arms` 字段按 run 目录名解析路径（`w7_rejudge.py:301 load_frozen`）。

---

## 3. 指标口径（三量分离）

本实验把「引用准确率」拆成三个**不可互相替代**的量，这是本次分析能定性翻转的关键：

```text
existence_rate  = exist / total            # 被引来源是否真实存在（不判断内容是否支持）
rejudge_fidelity = passed / exist           # 在「真实存在」中，裁判判定内容确实支持断言的比例
end_to_end      = passed / total            # = existence_rate × rejudge_fidelity，端到端通过率
passed_refs_per_report = passed / valid_q   # 绝对产出：每篇报告最终通过多少条引用
```

**为什么必须拆开看**：arm6 的 `existence_rate` 与 arm0 几乎持平（97.05% vs 97.52%，Δ−0.47pp），
提升全部发生在 `fidelity` 一侧（+7.29pp）；且**分母从 58.4 条/篇缩到 30.55 条/篇**。
只看比率会被分母收缩误导，必须同时看绝对条数。

---

## 4. 核心结果（2×2 冻结复判）

### 4.1 端到端通过率与绝对产出

| 引用集 | 裁判 qwen-plus | 裁判 qwen-turbo | 通过条数/篇（plus / turbo） | 总引用条/篇 |
|---|---:|---:|---:|---:|
| **arm0**（1168 条） | 83.99% | 91.52% | 49.05 / 53.45 | 58.40 |
| **arm6**（611 条） | 90.67% | 88.05% | 27.70 / 26.90 | 30.55 |

### 4.2 四格明细

| 格子 | total | existence | passed | existence_rate | fidelity | end_to_end |
|---|---:|---:|---:|---:|---:|---:|
| arm0 \| plus | 1168 | 1139 | 981 | 97.52% | 86.13% | **83.99%** |
| arm0 \| turbo | 1168 | 1139 | 1069 | 97.52% | 93.85% | **91.52%** |
| arm6 \| plus | 611 | 593 | 554 | 97.05% | 93.42% | **90.67%** |
| arm6 \| turbo | 611 | 593 | 538 | 97.05% | 90.73% | **88.05%** |

### 4.3 效应分解

| # | 对比 | 含义 | 差值 |
|---|---|---|---|
| A | arm0 + plus | 基准格 | 83.99% |
| B | arm0 + turbo | **纯裁判效应**（引用集不变，只换裁判） | **+7.53pp** |
| C | arm6 + plus | **同裁判下的 arm 效应** | **+6.68pp** |
| D | arm6 + turbo | turbo 裁判下的 arm 效应 | **−3.47pp（方向翻转）** |

- `B − A = +7.53pp`：**裁判效应与被测效应同量级**，这正是原实验设计不成立的原因。
- `C − A = +6.68pp`：扣除裁判混淆后，arm6 的同裁判差异只有这个量级，且与实测跨区块 citation 波动（4.5~10.1pp）同量级。
- `D − B = −3.47pp`：换 turbo 裁判后 arm 效应**符号反转**，说明该效应不是稳健的引用质量改善。
- 绝对产出：`49.05 → 27.70 条/篇`（**−43.5%**），即 arm6 让每篇报告最终通过的引用条数腰斩。

> 附注（成本亦受同一混淆影响）：arm6 表面成本更低（¥0.7274 vs ¥0.8637），但 token 消耗反而**高 43.5%**
> （1,067,359 vs 743,752）。降本同样来自「换成低价模型」，**不是效率提升**。

---

## 5. formal 与 diagnostic 不等价（两套判据不得混用）

在 arm0 + qwen-plus、同样 **6 道题（q_001~q_006）**、同样 269 条引用上，两种模式的对照：

| 模式 | end_to_end | 说明 |
|---|---:|---|
| `formal`（生产判据） | **76.21%** | 使用生产对齐机制，有 **13/269 条未获裁决（no_verdict_rate 5.0%）** |
| `diagnostic`（分析口径） | **79.93%** | 复判口径，no_verdict 全部补问到 0 |
| 差值 | **+3.72pp**（diagnostic − formal） | 聚合成系统偏差 |

单题波幅远大于聚合差：

| 题号 | diagnostic | formal | Δ |
|---|---:|---:|---:|
| q_001 | 79.3% | 86.2% | +6.9pp |
| q_002 | 78.2% | 65.5% | **−12.7pp** |
| q_003 | 81.4% | 78.6% | −2.9pp |
| q_004 | 72.2% | 75.9% | +3.7pp |
| q_005 | 90.0% | 90.0% | 0.0pp |
| q_006 | 86.3% | 76.5% | −9.8pp |

**规则：**
- **`formal` 结果用于生产闸门与最终判定；**
- **`diagnostic` 结果只用于分析原因（归因）；**
- **两者不得互相替代、不得混在同一张表里比较。**

该差值同时含「对齐机制差异」与「prompt 变体差异」两个因素，**未做单变量隔离**，故不宣称已定位到单一成因。

---

## 6. 元结论（方法论层面，本项目最重要的产出）

> **引用准确率在「判据/裁判」变化下，单次观测可出现约 ±10pp 的仪器/区块波动；而被测 arm 效应本身只有 4~10pp。**
> **在当前设计下，指标分辨率不足以稳定分辨目标效应。**

配套的两条硬约束：

1. **被测对象不得兼任裁判。** 原设计中 `metrics.py` 的引用准确率**直读主链路 validator 的裁决**，
   而 arm6 改的正是 validator（qwen-plus → qwen-turbo）——被判定的对象和被读取的裁判是同一个组件，
   这使 arm6 的数字同时含「被测效应 + 裁判效应」，不可与其他 run 横向比较。
   → 已随 `60a4b50` 解耦评价口径，并在 `report_gen.py` 的「已知局限」中显式声明。
2. **未获裁决必须补问到 0，否则不得进入结论。** 权威复判一律 `no_verdict=0`；
   存在漏裁决的产物（如 `w7_rejudge_formalcheck.json`，13/269 未裁决）**只能定性、不能定量**。

### 噪声底实测（三区块）

2026-09-12~13 补跑 Block 1/2 后，6 臂 × 3 区块 = 18 个 run 到手（唯一缺格见 §7.2），区块波动由推算变为实测：

| 量 | 幅度 |
|---|---|
| 同 arm 跨区块 `citation_accuracy` 波动 | **4.5~10.1pp** |
| 同 arm 跨区块 `coverage` 波动（3.85 步的 arm） | **20.4~28.7pp** |
| 同 arm 跨区块 `coverage` 波动（8.1 步的 arm） | **1.3~4.6pp** |
| 配对效应跨区块极差（coverage） | **19.6~37.0pp** |

⇒ TBD-8 设定的阈值 **+4~10pp 落在噪声里**，按 1 区块设计**不可能**得出结论；补足到 3 区块后依然不能
（配对效应在区块间**符号翻转**，见 §7.4）。

---

## 7. 补跑 Block 1/2 诊断（2026-09-12 23:39 → 09-13 08:11）

**执行**：512.8 min（≈8.5h），12 runs 收口，Phase1 成本 **¥9.3327**，实验累计 **¥14.4303**。
**结论：补跑未能使 W7 收敛，本实验按「如实收口」终止。**

### 7.1 缺陷①　补跑代码修订与 Block 0 不一致

| 批次 | 运行时间 | 实际代码 | 每题 raw 的 `git_commit` |
|---|---|---|---|
| Block 0 | 09-11 19:41 ~ 09-12 00:31 | `95adb77` + patch `aa4bb452` | `95adb77f` |
| Block 1/2 | 09-12 23:39 ~ 09-13 08:11 | **`ca51886`**（HEAD，含 `60a4b50`） | `ca518866` |

其间已提交 `60a4b50`（解耦引用归一化 + metrics 口径），而补跑启动前**只验证了「补丁可干净应用到 `95adb77`」，
未验证「当前工作树 ≡ Block 0 实验时状态」**。实证证据（报告正文引用格式）：

| run | `[来源:N]` | 裸 `[N]` |
|---|---:|---:|
| arm0 / Block 0（`95adb77`+patch） | 614 | **18** |
| arm0 / Block 1（`ca51886`） | 484 | **0** |
| arm0 / Block 2（`ca51886`） | 535 | 2 |
| arm4 / Block 0 | 289 | 0 |
| arm4 / Block 1 | 290 | 0 |

`writer.py:95-98` 的引用归一化现为「协议保证、无条件执行」，Block 0 时它捆在 `WRITER_SECTIONED_FEED_ENABLED` 内
—— arm0/3/5 在 Block 0 **完全不归一化**；arm4（sectioned=true）两批一致，恰印证差异只落在 sectioned=false 的 arm 上。

⇒ **Block 1/2 与 Block 0 在 `citation` 口径上不可合并**，跨区块的引用准确率配对全部作废。
（`coverage` 由 findings 决定、不经引用归一化，故其读数可作线索——但下列 §7.3 的噪声结论不依赖该读数。）

**已落护栏（防止重演）**：`w7_experiment.py` 现在①每个区块开始时记录 `code rev`，
②manifest 落 `code_revision` / `resumed_revisions`，③续跑时若当前修订与首轮不一致则**显式告警**
（纯函数 `_revision_mismatch_warning`，由 `tests/test_w7_experiment_guard.py` 锁定）。
本次实验的 manifest 已回填 `code_revision=95adb77+patch:aa4bb452…`、`resumed_revisions=[ca518866]`
以及本口径不一致的说明条目。

### 7.2 缺陷②　arm6 缺 Block 2（守门未过）

```text
⏭ 跳过 arm6_validator_turbo：守门条件未满足
   — arm3_validator_fixes citation_accuracy=0.7011（同区块）< 守门线 0.71
```

⇒ arm6 只有 2 个区块，其余 arm 有 3 个，3×6 配对缺一格。**未使用 `--force-conditional` 强行补齐**
（强行跳过守门会让该格失去设计含义）。

### 7.3 缺陷③与核心元结论　证据池命中率 × 检索轮数共同主导

| arm | coverage B0 / B1 / B2 | 极差 | `avg_steps` |
|---|---|---:|---:|
| arm0_baseline | 34.2 / 17.9 / 38.3 | **20.4pp** | 3.85 |
| arm3_validator_fixes | 37.9 / 13.6 / 19.6 | **24.3pp** | 3.85 |
| arm4_writer_sectioned | 38.3 / 16.2 / 9.6 | **28.7pp** | 3.85 |
| arm5_validator_trim | 35.4 / 11.2 / 12.9 | **24.2pp** | 3.85 |
| arm1_critic_gap | 42.5 / 45.8 / 47.1 | 4.6pp | **8.10** |
| arm6_validator_turbo | 42.5 / 43.8 / — | 1.3pp | **8.10** |

> **元结论：`coverage` 同时受证据池命中率（Block 级环境）与检索轮数（arm 机制）主导，二者在本实验中共线。**
> 17 个 run 的 coverage 与 `retrieval_hit_rate` 相关系数为 **r=0.9617**；Block 1 的命中率塌陷与 coverage 同步下跌。低步数臂无法自愈，高步数臂通过更多检索产生部分「自平均」。

两条推论，均可迁移到后续实验设计：

1. **环境因素的修法是固定检索快照/证据池**，不能把 Block 级命中率漂移归因给 arm；
2. **步数是 critic_gap 的中介机制而非独立混杂变量**：不能把 `avg_steps` 强行控制为常量，否则会关掉被测机制；应改做同预算成本-效果比较。

补充限制：每 arm 每 block 仅运行 1 次（n=1），区块配对只能消除部分共同漂移，不能消除区块内约 3.5 小时的时间漂移。

### 7.4 逐 arm 判定（coverage 相对 arm0 同区块）

| arm | B0 | B1 | B2 | 均值 | 区块方向 | 判定 |
|---|---:|---:|---:|---:|---|---|
| `arm1_critic_gap` | +8.3 | +27.9 | +8.8 | +15.0 | 全同向 | ⚠️ **看着赢，实则不可收**（见下） |
| `arm3_validator_fixes` | +3.7 | −4.3 | −18.7 | −6.4 | **符号翻转** | ❌ 不可判定 |
| `arm4_writer_sectioned` | +4.2 | −1.7 | −28.7 | −8.8 | **符号翻转** | ❌ 不可判定 |
| `arm5_validator_trim` | +1.3 | −6.7 | −25.4 | −10.3 | **符号翻转** | ❌ 不可判定 |
| `arm6_validator_turbo` | +8.3 | +25.8 | — | +17.1 | 全同向（仅 2 轮） | ❌ 缺格；结果不能归因于单独降档（§4） |

**arm1 为何也不可收**：其 `avg_steps` 三区块由 3.85 / 3.85 / 3.90 升至 8.10 / 8.85 / 8.25，
逐区块配对比值均值 = **+117.3%**（+110.4 / +129.9 / +111.5%；若按「比值之均值」口径为 +118.2%），而 DoD 守门线是 **≤+50%**；
且 `critic_stop_rate` 为 90 / 95 / 80%（arm0 仅 5%）。即 arm1 的机制是「critic 几乎从不停，用约 2 倍步数换覆盖度」，
是**成本-覆盖度权衡**，而非"更聪明的检索"。其"全同向"说明成本-质量 frontier 可能移动，但不等于同预算下机制优于基线；它不能作为通过 DoD 的证据。

### 7.5 为什么不重跑而选择收口

即使把代码一致性（§7.1）修好、把 arm6 缺格（§7.2）补齐，低步数臂的区块极差（20~29pp）仍明显大于目标效应（4~10pp），
且配对效应在区块间**符号翻转**（§7.4）。这里的 20~29pp 是 3 点极差而非标准差；按 E[range]≈1.69σ 折算，arm0/3/5/4 的估计 σ 约为 12.1/14.4/14.3/17.0pp。arm1 极差仅 4.6pp（σ̂≈2.7pp），其不可收口原因是成本闸门失败，而不是噪声吞没。
按当前设计重跑只会得到一个"更干净的不可判定"，故不再投入（重跑成本约 8.6h / ¥9.3）。

---

## 8. 最终判定

### 8.1 原假设

| 原假设 | 判定 | 依据 |
|---|---|---|
| arm6 使引用准确率显著提升 | ❌ **不成立** | +19.93pp 中约 +7.5pp 为裁判效应；同裁判仅 +6.68pp 且方向随裁判翻转 |
| turbo 降档无损（可省钱） | ❌ **不成立** | 绝对通过条数 −43.5%；token 反增 43.5%；成本降低来自单价而非效率 |
| 该实验能分辨 TBD-8 阈值（4~10pp） | ❌ **不成立** | 低步数臂的 3 点区块极差约 20~29pp（不是标准差），目标阈值难以稳定分辨；arm1 另因成本闸门失败 |

### 8.2 各 arm 的最终记账

| arm | 最终记账 |
|---|---|
| `arm1_critic_gap` | ⚠️ **弱证据，不予收款**：coverage 名义 +15.0pp，但 `avg_steps` **+117.3%**（配对口径）超 DoD 守门线（≤+50%），机制为「以步数换覆盖度」，非检索质量改善 |
| `arm3_validator_fixes` | ❌ **不可判定**：区块方向翻转（+3.7 / −4.3 / −18.7） |
| `arm4_writer_sectioned` | ❌ **不可判定**：区块方向翻转（+4.2 / −1.7 / −28.7） |
| `arm5_validator_trim` | ❌ **不可判定**：区块方向翻转（+1.3 / −6.7 / −25.4） |
| `arm6_validator_turbo` | ❌ **不成立 + 缺格**：该 arm 不是单变量降档；其结果不能归因于降档，Block 2 因守门未过缺失 |

> 注：arm1/3/4/5 的 coverage 同样直读主链路结果，相关开关未与检索环境、裁判口径完全解耦，不能视为单变量因果证据。

**对技术债清单的影响：** validator 降档**不能**作为已收款的技术债修复项。上述 4 个边界化的 arm
（`arm3/4/5`）的代码改动**保留但标注「弱杠杆、未获实验证据」**，回滚与否另议。

若仍要评估这类改动，必须重新设计为：
**固定裁判（独立于被测组件）+ 固定检索快照 + 同预算成本-效果比较 + 多区块重复 + 报告绝对条数**。

---

## 9. 归档位置与产物清单

### 已入库：结论与元数据（小而权威）

| 文件 | 说明 |
|---|---|
| `research_engine/eval/results/curated/w7_rejudge_20260912_173125.json` | 权威复判结果（本文件全部数值来源） |
| `research_engine/eval/results/curated/w7_rejudge_formalcheck.json` | formal/diagnostic 不等价证据（**标记 INCOMPLETE_DIAGNOSTIC，不可当最终结果**） |
| `research_engine/eval/results/curated/CODE_REVISION.json` | 代码修订元数据（已修正失效的 patch 引用路径） |
| `research_engine/eval/results/curated/w7_revision_aa4bb452.patch` | 实验实际执行代码的补丁（116KB） |
| `research_engine/eval/results/curated/MANIFEST.md` | 复现清单：全部 sha256、自解释字段补足、可用/不可用对照、复现步骤 |
| `research_engine/eval/results/w7_experiment_20260911_194151/` | 六臂实验容器（manifest + CODE_REVISION） |
| `docs/eval-w7-conclusion.md` | 本文件 |

### 已入库：两个来源 run 的原始产物（含 `raw/`）

| run | 体积 | 内容 |
|---|---:|---|
| `run_20260911_194156/`（arm0） | 2.4 MB | `raw/`×20 + `eval/`×20 + 3 个汇总 JSON |
| `run_20260911_232515/`（arm6） | 2.1 MB | 同上 |

**为什么连 `raw/` 一起入库**：`raw/` 是逐题原始产物，是回答「某条引用为何被判错」
「q_018 补问前后发生了什么」的唯一依据，而这两个 run 是 W7 结论的**唯一原始来源**。
仓库既有惯例也是里程碑 run 全量入库（`run_20260906_184156`、`run_v11_compare` 均含 `raw/`），
故保持一致。代价：`results/` 已跟踪体积由 6.4MB 增至约 11MB。

### 不入 Git（本地保留，由 `.gitignore` 兜住）

- 其余 60 个迭代 `run_*` 目录、9 个非权威 `w7_experiment_*` 目录；
- `*_SUPERSEDED.json` / `*_DISCARDED.json` / `_tmp_*` 等被取代、废弃、临时产物。

> **行尾保护**：`.gitattributes` 对 `curated/` 与两个权威 run 标记 `-text`。
> 本仓库 `core.autocrlf=true`，若不锁行尾，`.patch` 检出时会变 CRLF 而使 `git apply`
> 上下文失配，`MANIFEST.md` 记录的 sha256 也会失效。

### 特别标注

- `w7_rejudge_20260912_160857_SUPERSEDED.json` / `..._164110_SUPERSEDED.json`：
  旧 schema（用 `grid` 键、无 `mode` 字段），**已被 173125 取代，不得引用**。
- `_smoke_rejudge_DISCARDED.json` / `_recompute_check_DISCARDED.json`：冒烟与校验产物，无解释效力。
- `run_20260912_000027_DISCARDED/`：Block 1 首臂被 kill，无 `summary.json`，不可作为数据。

