# W7 对照实验结论（引用质量技术债 TBD）

> 本文件是 **W7 对照实验的项目级结论**，为长期有效文档。
> `docs/eval-report.md` 是 `report_gen.py` 每次运行自动覆盖的单 run 快照，**不能承载结论**——两者的分工不要混。
>
> 最后更新：2026-09-12 ｜ 状态：**已收敛，但不支持原假设**

---

## 1. 一句话结论

**不能宣布 arm6 优于基线 arm0。**

arm6（将 validator 由 qwen-plus 降档为 qwen-turbo）表面上的引用准确率提升 **+19.93pp**（按题均值）中，
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
| 原始实验容器 | `w7_experiment_20260911_194151/`（6 arms × Block 0） |
| 权威复判结果 | `research_engine/eval/results/curated/w7_rejudge_20260912_173125.json` |
| 复判时间 / 耗时 | 2026-09-12T17:34:28 / 183.2s |
| 复判模式 | `diagnostic`（**仅用于归因，不作生产判据**，见 §5） |
| 裁判模型 | `qwen-plus` 与 `qwen-turbo` 各判一遍（2×2 冻结设计） |
| 有效裁决 | 四格各 `valid_q=20`、`no_verdict=0`、`llm_failed_rate=0` |

### 两个 arm 的定义与来源 run

| arm | 定义 | 来源 run | Phase1 token / 成本 |
|---|---|---|---|
| `arm0_baseline` | 基线（validator = qwen-plus） | `run_20260911_194156` | 743,752 tok / ¥0.8637 |
| `arm6_validator_turbo` | **仅**将 validator 降档为 qwen-turbo | `run_20260911_232515` | 1,067,359 tok / ¥0.7274 |

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
- `C − A = +6.68pp`：扣除裁判混淆后，arm6 的真实增益只剩这个量级，且 **< 噪声底**。
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

> **引用准确率在「判据/裁判」变化下存在约 ±10pp 的仪器噪声，而被测 arm 效应本身只有 4~10pp。**
> **指标分辨率低于被测效应，实验从根上无法分辨。**

配套的两条硬约束：

1. **被测对象不得兼任裁判。** 原设计中 `metrics.py` 的引用准确率**直读主链路 validator 的裁决**，
   而 arm6 改的正是 validator（qwen-plus → qwen-turbo）——被判定的对象和被读取的裁判是同一个组件，
   这使 arm6 的数字同时含「被测效应 + 裁判效应」，不可与其他 run 横向比较。
   → 已随 `60a4b50` 解耦评价口径，并在 `report_gen.py` 的「已知局限」中显式声明。
2. **未获裁决必须补问到 0，否则不得进入结论。** 权威复判一律 `no_verdict=0`；
   存在漏裁决的产物（如 `w7_rejudge_formalcheck.json`，13/269 未裁决）**只能定性、不能定量**。

### 噪声底实测（旧实验 3 区块）

| 量 | 幅度 |
|---|---|
| 同 arm 跨区块 citation 波动 | ±8~12pp |
| 配对效应区块间 std | ±9.5~14.7pp |
| 单区块可分辨的最小效应 | **≳19pp** |

⇒ TBD-8 设定的阈值 **+4~10pp 完全落在噪声里**，按 1 区块设计**不可能**得出结论。

---

## 7. 最终判定

| 原假设 | 判定 | 依据 |
|---|---|---|
| arm6 使引用准确率显著提升 | ❌ **不成立** | +19.93pp 中约 +7.5pp 为裁判效应；同裁判仅 +6.68pp 且方向随裁判翻转 |
| turbo 降档无损（可省钱） | ❌ **不成立** | 绝对通过条数 −43.5%；token 反增 43.5%；成本降低来自单价而非效率 |
| 该实验能分辨 TBD-8 阈值（4~10pp） | ❌ **不成立** | 噪声底 ≳19pp（单区块），阈值在噪声内 |

**对技术债清单的影响：** validator 降档**不能**作为已收款的技术债修复项。
若仍要评估 validator 改动，必须重新设计为：**固定裁判（独立于被测组件）+ 多区块重复 + 报告绝对条数**。

---

## 8. 归档位置与产物清单

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

