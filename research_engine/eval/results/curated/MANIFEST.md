# W7 对照实验 — 复现清单（MANIFEST）

> 本目录（`research_engine/eval/results/curated/`）是 **W7 对照实验的忠实记录集合**。
> 结论见 [`docs/eval-w7-conclusion.md`](../../../../docs/eval-w7-conclusion.md)。
>
> 收录原则：**只收「解释结论所必需」的产物**。原始实验产物一律不改写内容（哈希可验）；
> 仅 `CODE_REVISION.json` 修正了一处失效路径引用，改动已在 §3 声明。

生成时间：2026-09-12

---

## 1. 实验标识

| 项 | 值 |
|---|---|
| 实验名 | W7 引用质量技术债对照实验（TBD） |
| 数据集版本 | **v1.1**（20 题，2026-09-06，ai_draft + human_calibration） |
| 代码修订锚点 | base `95adb77f31f535d07368f487ceb98fa4e32ea3bf` + 未提交工作树 diff |
| diff 指纹 | `aa4bb45276976b67772a95bd95b7bc97e13b6778` |
| 实验窗口 | 2026-09-11T19:41 ~ 2026-09-12T00:31 |
| 复判时间 | 2026-09-12T17:34:28（耗时 183.2s） |
| 设计 | 6 arms × Block 0 已跑；复判为 **2×2 冻结**（2 arm × 2 judge），各格 n=20 |
| 有效裁决 | `no_verdict = 0`（四格均干净） |

### ⚠️ 关于 `git_commit: 95adb77` 的假象

两个来源 run 的 `summary.json` 里都写 `git_commit: 95adb77`，**这只是当时的 HEAD，不是实际执行代码**。
实际执行的是「`95adb77` + 本目录补丁所描述的工作树改动」。详见 `CODE_REVISION.json` 的 `important_note`。

**复现时必须：** `git checkout 95adb77` → `git apply` 本目录补丁 → 再跑实验。
只 checkout `95adb77` 会得到**不同**的结果。

### 补丁代表「实验时」代码，不含其后的解耦修复

补丁于 2026-09-12 16:09 自工作树生成，冻结的是实验**当时**的代码状态。
此后提交 `60a4b50`（fix(W7): 解耦引用归一化、审计 validator 分母）**不在补丁内**：

- 应用补丁后的 `validator.py` 中**查不到** `filter_enabled` 相关逻辑；
- `config.py` 中也**查不到** `validator_assertive_filter_enabled` 配置项。

这是**预期行为，不是补丁缺漏**——复现实验理应复现当时的行为。
若需研究解耦后的行为，应基于当前 `dev` 另行设计对照实验（且仍须遵守「被测对象不得兼任裁判」）。

---

## 2. 数据来源 run

| arm | 来源 run 目录 | 备注 |
|---|---|---|
| `arm0_baseline` | `../run_20260911_194156/` | validator = qwen-plus |
| `arm6_validator_turbo` | `../run_20260911_232515/` | 仅 validator 降档为 qwen-turbo |

> 🔴 **这两个目录不可移动、不可改名。**
> 复判结果 JSON 的 `arms` 字段存的是 **run 目录名**，`w7_rejudge.py:301 load_frozen()` 按目录名解析路径。
> 一旦移动或改名，复判脚本即失效、`--resume-from` 也失去对应关系。

两个 run 的构成：`raw/`（20 个逐题原始产物）+ `eval/`（20 个逐题指标）+ `summary.json` +
`baseline.json` + `phase1_global_stats.json`，**全量随 Git 入库**（与仓库既有里程碑 run 惯例一致）。

---

## 3. 文件清单与哈希（sha256）

### 3.1 本目录文件

| 文件 | 大小 | sha256 | 说明 |
|---|---:|---|---|
| `w7_rejudge_20260912_173125.json` | 14.9 KB | `47da2601cbf58fe5c67abf4a62db3dadfba05199d74a9f940e3f6c0526a8f5d3` | **权威结果**（结论全部数值来源） |
| `w7_rejudge_formalcheck.json` | 2.2 KB | `a93acc08f09b76ced96265d3d218f0aa7763f9ac89316eb67f04a0d6d2869125` | formal/diagnostic 不等价证据 · **INCOMPLETE_DIAGNOSTIC** |
| `CODE_REVISION.json` | 1.8 KB | `8cec55d1c30aab5db130864644ef1c6cc91dd11295630c2cc47e1011b378e90d` | 代码修订元数据（**已修正路径，见 §3.3**） |
| `w7_revision_aa4bb452.patch` | 116 KB | `86033285baff8c6c42e2e2aced0ac09aa9664bf37289b6b6b5f7cabb4c5a6c9e` | 实际执行代码的补丁 |

### 3.2 来源 run 锚点文件

| 文件 | sha256 |
|---|---|
| `../run_20260911_194156/summary.json` | `840c4c4fc60c11f2b5e0368717d45f398116158b55f5fcd2715ad56d66049ba6` |
| `../run_20260911_232515/summary.json` | `65f0c6ca4509f85a39bc756a81d41dc0c2475bfea340c6a1913cf76a10cf3400` |
| `../run_20260911_194156/eval/*.json`（按文件名字典序拼接后哈希） | `1171446f5f6a9ef133f46d01b1b1ec0addd5ecbd234921cc8980229e6dfaf5a2` |
| `../run_20260911_232515/eval/*.json`（同上口径） | `d568dc9bddaf3673f1de03c5d3795c7358d8506b2590b5c2c750948d78ca5e95` |

### 3.3 唯一一处内容改动声明

`CODE_REVISION.json` 的原版（`../w7_experiment_20260911_194151/CODE_REVISION.json`）中
`worktree_diff_patch` 指向 `.workbuddy/w7_revision_aa4bb452.patch`，而 `.workbuddy/` 被 `.gitignore` 忽略，
**该引用在提交后必然失效**。本目录副本做了两项最小修正：

- `worktree_diff_patch` → `research_engine/eval/results/curated/w7_revision_aa4bb452.patch`
- 新增 `worktree_diff_patch_sha256`、`curated_relocated_at`、`curated_note` 三个字段

仅改引用，未改任何实验事实字段。其余三份产物**零改动**。

---

## 4. 补足的自解释字段（原产物缺失）

复判 JSON 的顶层只有 `generated_at / mode / judges / arms / original / cells / per_question / elapsed_s`，
**不含数据集版本、判据版本、成本**。为免"只留数字、无法解释数字来自哪"，此处显式补齐：

| 缺失字段 | 补足值 | 来源 |
|---|---|---|
| `dataset_version` | **1.1** | 两个 run 的 `summary.json` |
| 判据（prompt）版本 | **由代码修订指纹唯一确定** | 见下 |
| Phase1 成本 | arm0 ¥0.8637 / 743,752 tok；arm6 ¥0.7274 / 1,067,359 tok | 两个 run 的 `summary.json` |
| Phase2 judge token | arm0 70,358 / arm6 82,407 | 同上 |
| 实验时间窗口 | 2026-09-11T19:41 ~ 2026-09-12T00:31 | `CODE_REVISION.json.verified` |

### 判据版本如何锚定

`w7_rejudge.py` 未定义 `prompt_version` 常量，因为**判据本身就是源码常量**：

- `formal` 模式 → `research_engine/agents/validator.py` 的 `VALIDATOR_SYSTEM`（生产判据全等）
- `diagnostic` 模式 → `w7_rejudge.py` 的 `JUDGE_SYSTEM_DIAGNOSTIC`（判定规则与 `VALIDATOR_SYSTEM` 逐字一致，
  差异仅在于去掉了 `claim_echo` 逐字回显）

因此 **diff 指纹 `aa4bb452…` 即判据指纹**——两者都是该修订下的源码字面量，无法在指纹不变的情况下漂移。
如需逐字校验，应用补丁后对上述两个常量取哈希即可。

---

## 5. 可用 / 不可用产物对照

| 产物 | 状态 | 可否引用 |
|---|---|---|
| `curated/w7_rejudge_20260912_173125.json` | ✅ 权威 | **可**——结论唯一依据 |
| `curated/w7_rejudge_formalcheck.json` | ⚠️ INCOMPLETE_DIAGNOSTIC（6 题 / 269 条 / **13 条未裁决**） | 仅可作"两模式不等价"的**定性**证据，**不可定量引用** |
| `../w7_rejudge_20260912_160857_SUPERSEDED.json` | ❌ 旧 schema（`grid` 键、无 `mode`） | 不可 |
| `../w7_rejudge_20260912_164110_SUPERSEDED.json` | ❌ 旧 schema | 不可 |
| `../_smoke_rejudge_DISCARDED.json` | ❌ 冒烟产物 | 不可 |
| `../_recompute_check_DISCARDED.json` | ❌ 校验产物 | 不可 |
| `../run_20260912_000027_DISCARDED/` | ❌ Block 1 首臂被 kill，无 `summary.json` | 不可 |

> 被弃产物一律以 `_SUPERSEDED` / `_DISCARDED` 后缀命名，并已由 `.gitignore` 排除在提交之外；
> 文件保留在本地仅供追溯，**不进入正式记录**。

---

## 6. 复现步骤

```bash
# 1) 回到实验时的基修订
git checkout 95adb77f31f535d07368f487ceb98fa4e32ea3bf

# 2) 应用工作树改动（等价于实验当天的 dirty worktree）
git apply research_engine/eval/results/curated/w7_revision_aa4bb452.patch
#    校验：应得到 20 files changed, 1958 insertions(+), 116 deletions(-)

# 3) 复判（用两个来源 run 的冻结产物，不重跑研究阶段）
python research_engine/eval/w7_rejudge.py --mode diagnostic \
  --arms arm0_baseline=run_20260911_194156,arm6_validator_turbo=run_20260911_232515

# 4) 对照：formal 模式（生产判据）——注意须单独跑，不可与 diagnostic 混用
python research_engine/eval/w7_rejudge.py --mode formal ...

# 5) 校验产物未被篡改
sha256sum research_engine/eval/results/curated/*.json
```

---

## 7. 字段语义（`w7_rejudge.py:summarize_cell`）

```text
total             本格全部被引条目数
existence         被引来源确实存在的条数
passed            严格口径：裁判判定"内容确实支持该断言"的条数
no_verdict        未获裁决的条数（本次一律为 0；>0 时该产物只能定性）
existence_rate    = existence / total
rejudge_fidelity  = passed / existence        （仅看"存在"之中有多少被判定支持）
end_to_end        = passed / total            （= existence_rate × rejudge_fidelity）
passed_refs_per_report = passed / valid_q     （绝对产出：每篇报告最终通过几条）
no_verdict_rate   = no_verdict / existence
llm_failed_rate   = 失败题数 / 总题数（失败题从分子分母中剔除）
```

**读法提醒：** `end_to_end` 单独看会被分母收缩误导（arm6 引用条数腰斩反而推高比率），
必须与 `passed_refs_per_report` 并看。详见结论文档 §3。
