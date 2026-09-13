# W7 实验开关去留决策备忘（C 项）

> 状态：**已拍板（2026-09-13）**。本文记录事实、成本账与本次 C 项处置决定。
> 生成时间：2026-09-13｜代码基线：`9292416`
> 相关文档：`docs/eval-w7-conclusion.md`（权威结论）、`docs/eval-w7-attribution.md`（失败归因）、
> `docs/requirements/7-technical-debt-and-content.md`（TBD-8）、`docs/requirements/8-fault-transparency-and-reproducibility.md` §10

---

## 1. C 项要决定什么

`docs/eval-w7-conclusion.md` §8.2 留了一句：

> 上述 4 个边界化的 arm（`arm3/4/5`）的代码改动**保留但标注「弱杠杆、未获实验证据」**，回滚与否另议。

「另议」就是 C 项。但取证后发现，把它理解成「删不删实验脚本」是错的。

---

## 2. 取证结论：这不是「实验代码」，这是「主链路默认配置」

### 2.1 机制

W7 的 6 个臂**没有**独立文件、**没有**分支、**没有** monkey-patch 桩。
它们的全部差异由 `config.py:120-142` 的 `ExperimentConfig` **5 个环境变量开关**表达，
实验脚本通过 `subprocess.run(cmd, env=env, ...)`（`w7_experiment.py:214`）注入子进程环境来实现分臂。

```python
# config.py:120-142（节选）
class ExperimentConfig:
    """W7 TBD-8 受控单变量实验开关。默认全开 = 保持当前行为；全关 = v1.1 基线。"""
    critic_gap_enabled: bool = field(default_factory=lambda: _env("CRITIC_GAP_ENABLED", "true")...)
    validator_fixes_enabled: bool = field(default_factory=lambda: _env("VALIDATOR_FIXES_ENABLED", "true")...)
    writer_sectioned_feed_enabled: bool = field(default_factory=lambda: _env("WRITER_SECTIONED_FEED_ENABLED", "true")...)
    validator_trim_enabled: bool = field(default_factory=lambda: _env("VALIDATOR_TRIM_ENABLED", "true")...)
    validator_assertive_filter_enabled: bool = field(default_factory=lambda: _env(
        "VALIDATOR_ASSERTIVE_FILTER_ENABLED", _env("VALIDATOR_FIXES_ENABLED", "true"))...)
```

### 2.2 关键事实（三条，均有实证；以下“零提及”均指本次文档化之前）

| # | 事实 | 证据 |
|---|---|---|
| **①** | 5 个开关**默认全为 true** | `config.py:120-142` 全部 `_env(..., "true")` |
| **②** | **`.env.example` 与 `README.md` 零提及**；`docs/` 仅 3 处顺带提到，**无一处说明「默认全开」** | 全仓 grep 这 5 个变量名：`.env.example` / `README.md` **0 命中**；`docs/` 3 命中且均为顺带 —— `eval-w7-conclusion.md:188`（讲归一化解耦）、`7-*.md:404`（讲 `avg_steps` 共线）、`7-*.md:605`（变更记录罗列开关名）。**注意 `7-*.md:605` 那份清单本身有误**：列了 `VALIDATOR_MODEL` 却漏掉真正的主开关 `VALIDATOR_ASSERTIVE_FILTER_ENABLED`，且**通篇未写默认值** |
| **③** | `cli.py` / `web/app.py` **不显式设置**任何开关 | 同上 grep 在 `cli.py`、`web/` 零命中 |

**推论：主链路（CLI 与 Web UI）当前静默运行在「W7 修复全开」配置上，且此事无任何面向用户的记录。**

也就是说 —— **W7 的实验配置事实上已成为生产默认值**；在本次 C 项文档化之前，7 周的需求文档、README、`.env.example` 里都没写。
这是本次取证最值得注意的发现，它把 C 项的性质从「清理实验残留」改成了「**裁定主链路默认配置**」。

### 2.3 影响面

W7 期间主链路文件的改动量（`95adb77..dev`，已排除 eval 产物）：

| 文件 | 改动 |
|---|---:|
| `research_engine/agents/validator.py` | +282（开关点 3 处：202 / 348 / 433） |
| `research_engine/agents/writer.py` | +150（开关点 1 处：69） |
| `research_engine/critic.py` | +111（开关点 2 处：122 / 167） |
| `research_engine/context/manager.py` | +44（开关点 1 处：93） |
| `config.py` | +29 |
| `research_engine/graph.py` | +16 |
| **合计** | **+569 / −63** |

---

## 3. 逐开关事实表

| 开关 | 对应 arm | 实验判定 | 独立于实验的正确性依据 | 关闭该开关的实测代价 |
|---|---|---|---|---|
| `VALIDATOR_ASSERTIVE_FILTER_ENABLED` | arm3 / F3 | ❌ 不可判定（符号翻转 +3.7 / −4.3 / −18.7） | ✅ **有**：结构残片 75 条 = 全部失败的 **25.2%**，是**识别为真 bug** 却未计入误拒分子的一项；F3 单项杠杆 **+5.5pp**（最大） | **2 个测试红**（`test_f3_meta_claim_filtered`、`test_f3_table_fragment_filtered`） |
| `VALIDATOR_FIXES_ENABLED` | arm3 / F1·F2·F5 | ❌ 不可判定（同上） | ✅ **有**：截断误拒 29 条（F1）、verdict/claim 错位（F2）均为实测缺陷；技术性误拒合计 **123 条 = 41.3%** | **4 个测试红**（`test_f2_claim_echo_mismatch_degraded`、`test_f3_meta_*`、`test_f3_table_*`、`test_f4_subject_backed`） |
| `CRITIC_GAP_ENABLED` | arm1 | ⚠️ 弱证据：coverage **+15.0pp** 全同向，但 `avg_steps` **+117.3%**，破 DoD 守门线（≤+50%） | ❌ 无。机制是「critic 几乎不停（stop_rate 90/95/80% vs 基线 5%），用 ≈2 倍步数换覆盖度」，是成本-覆盖度权衡而非检索质量改善 | **5 个测试红**（`test_critic_gap.py` 4 个 + `test_graph_loop.py::test_decide_with_mock_llm`） |
| `WRITER_SECTIONED_FEED_ENABLED` | arm4 | ❌ 不可判定（符号翻转 +4.2 / −1.7 / −28.7） | ❌ 无。且它**捆绑的不只是喂料格式**，还切换 system prompt（`WRITER_SYSTEM` ↔ `WRITER_SYSTEM_LEGACY`），改动面大于描述 | **4 个测试红**（`test_writer_sectioned_feed.py` 全部） |
| `VALIDATOR_TRIM_ENABLED` | arm5 | ❌ 不可判定（符号翻转 +1.3 / −6.7 / −25.4） | ❌ 无。纯降本优化（喂料裁剪 A′） | **2 个测试红**（`test_trim_preserves_original_numbering`、`test_trim_url_source_to_id_reverse_lookup`） |

> `VALIDATOR_MODEL` 不在本表：它是模型选择（arm6 用 `qwen-turbo`），默认 `""` 表示跟随 `config`，不构成去留问题。

### 3.1 「关闭某开关的实测代价」是怎么来的（可复算）

不需要改代码 —— `ExperimentConfig` 全部经 `_env()` 读环境变量，所以直接以环境变量模拟「改默认值」：

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 全关（模拟选项②）
CRITIC_GAP_ENABLED=false VALIDATOR_FIXES_ENABLED=false WRITER_SECTIONED_FEED_ENABLED=false \
VALIDATOR_TRIM_ENABLED=false VALIDATOR_ASSERTIVE_FILTER_ENABLED=false $PY -m pytest -q
# → 116 passed, 15 failed（基线为 131 passed）

# 逐个关（上表第 5 列的数字来源）
env CRITIC_GAP_ENABLED=false $PY -m pytest -q       # → 5 failed
env VALIDATOR_FIXES_ENABLED=false $PY -m pytest -q  # → 4 failed
env VALIDATOR_ASSERTIVE_FILTER_ENABLED=false $PY -m pytest -q  # → 2 failed
env WRITER_SECTIONED_FEED_ENABLED=false $PY -m pytest -q       # → 4 failed
env VALIDATOR_TRIM_ENABLED=false $PY -m pytest -q              # → 2 failed
```

**读数**：单开关失败数相加为 17 > 全关的 15，差 2 来自重叠（`test_f3_meta_*` / `test_f3_table_*` 同时依赖 `VALIDATOR_FIXES_ENABLED` 与 `VALIDATOR_ASSERTIVE_FILTER_ENABLED`）。
**含义**：这 15 个用例**不设开关、直接依赖默认值** —— 它们把「当前默认」当成了隐含契约。这在改默认值时是**必须补夹具的成本**，但同时也是**这批行为从未被显式声明过**的又一证据。

### 3.2 一处必须记住的不可逆性

`writer.py:95-98` 的**引用归一化已从 `sectioned` 开关中解耦，改为无条件执行**（注释原文：
"Citation normalization is a protocol guarantee, not a sectioned-feed experiment"）。

⇒ **把 `WRITER_SECTIONED_FEED_ENABLED` 改为 false，并不能复现 arm0 在 Block 0 时的行为**，因为当时归一化还捆在开关里。
任何「回滚后与 arm0 等价」的说法都不成立。

---

## 4. 三个选项与成本账

### 选项 ①：保持现状（5 个全 true，不改）

- 行为变更：**0**
- 成本：主链路 `avg_steps` 约为 v1.1 基线的 **2 倍**（`critic_gap` 贡献）；4 项修复中 3 项（arm4/arm5 + arm3 的效果幅度）无证据支持却当默认
- 风险：把「实验配置」当「生产默认」且**零文档**，后人无法判断这些行为从何而来
- 评价：**不推荐**，但不是因为"效果差"，而是因为**它把未证实的配置固化成了隐式契约**

### 选项 ②：全回滚（5 个改 false，代码与测试一并删）

- 行为变更：大
- 成本：丢 **+569 行**主链路改动 + 需重写 **15 个**依赖默认值的测试（实测，见 §3.1）—— `test_critic_gap.py` 4、`test_graph_loop.py` 1、`test_validator_fixes.py` 6、`test_writer_sectioned_feed.py` 4
- 风险：**过度反应**。它把「已实测的缺陷修复」（41.3% 技术性误拒）和「未证实的优化」一起扔了。
  `docs/eval-w7-attribution.md` §4 已明确收回了「修 validator 没用」这个读法
- 评价：**不推荐**

### 选项 ③：分类处置（按「缺陷已证实 vs 效果未证实」切开）

| 处置 | 开关 | 依据 |
|---|---|---|
| **保持 true** | `VALIDATOR_ASSERTIVE_FILTER_ENABLED`、`VALIDATOR_FIXES_ENABLED` | 针对的是**实测缺陷**（123 条 / 41.3% 技术性误拒），证据独立于 arm 实验；且 F3 是最大单项杠杆 |
| **改 true → false**（代码保留） | `CRITIC_GAP_ENABLED` | 成本账**明确为负**：+117.3% 步数破守门线。（注意：arm1 是全实验里**唯一效果可分辨**的臂，σ̂ 仅 2.7pp —— 它不是被噪声吞没，而是**贵**。所以这一刀是产品取舍，不是实验结论） |
| **改 true → false**（代码保留） | `WRITER_SECTIONED_FEED_ENABLED`、`VALIDATOR_TRIM_ENABLED` | 符号翻转 ❌ + 无独立依据 + `sectioned` 还捆绑提示词模板 |

**改默认值的真实成本**（比看起来大，以下为实测数字）：

1. 代码改动极小 —— 每个开关 1 行 `_env(..., "true")` → `"false"`
2. 但**会连带红掉测试**，数量已实测（§3.1）：全关 **15 个**；按开关 ~ 单关分别 5 / 4 / 2 / 4 / 2 个（有重叠）。
   这 15 个用例**没有一处显式设置开关**，全部隐式依赖默认 ON ⇒ 需逐个补 `monkeypatch` 夹具
3. 且这是**行为变更**，严格说需要重跑基线验证 —— 又是一笔跑量成本

> 第 2 条的 15 个红测本身就是一条证据：**这批主链路行为从未被显式声明过，只被"当前默认"隐式承载**。
> 一旦有人改了默认值或某天重构 `ExperimentConfig`，会以"测试挂了"的形式暴露，而不是以"文档过时"的形式。

### 选项 ④：先文档化，默认值裁定推迟（最低成本第一步）

- 把 5 个开关写进 `.env.example`，注明默认值与含义
- 在 `7-*.md` 记一条事实：「主链路默认 = W7 全开」
- 行为变更：**0**；重跑成本：**0**

---

## 5. 我的建议

**先做 ④（现在就做，零成本），③ 的默认值裁定并入 W8 设计时一起定。**

理由是成本账，不是技术判断：

1. **实验已判明「不可裁决」**，即目前**没有任何正面证据**支持改默认值 —— 改了也只是换一种无证据状态
2. **W8 需求文档 §10.2 已把 Arm 3/5/6 列为要重新对齐的对象**。如果 W8 要用这些开关做新实验，现在改默认值只会让新旧实验的基线口径再乱一层
3. ③ 的成本不在改代码（5 行），而在**补测试夹具 + 重跑基线**，属于实质工作量，应当放进 W8 预算里排期，而不是作为 W7 的收尾顺手做
4. ④ 能立刻消除唯一真正的隐患 —— **「生产默认值没人知道是什么」**

**唯一可以考虑现在就动的是 `CRITIC_GAP_ENABLED`**，且只在你的目标是「控成本」时才动：
它是全实验里唯一效果可分辨的臂，**用 +117.3% 步数买 +15.0pp coverage**，价格明确。
愿不愿意付这个价是产品决策，不是实验能替你答的。若当前阶段更看重报告质量而非成本，**保持 true 反而是合理的** —— 但**必须把它写进文档**，别再让它隐形。

---

## 6. C 项拍板（2026-09-13）

1. **先做 ④**：文档化 5 个开关，并记录「主链路默认 = 全开」；本轮不改运行行为。
2. **`CRITIC_GAP_ENABLED` 保持 `true`**：承认它是用约 +118% steps 购买 coverage 的产品取舍，先保质量。
3. **其余开关默认值裁定并入 W8**：当前保留 `true`，不在 W7 收尾阶段做回滚或重跑基线。
4. **加一条护栏单测**：断言 5 个开关在环境变量未设置时默认启用，防止未来静默漂移。
5. **不把 `WRITER_SECTIONED_FEED_ENABLED=false` 当作 arm0 回滚等价物**：引用归一化已无条件执行，历史 arm0 行为不可仅靠该开关复现。
6. **不回滚 569 行主链路代码**：`arm3/4/5` 的改动留在仓库（开关默认保持 `true`），默认值与代码去留并入 W8 重测后再裁定。
7. **arm6 不得解释为单变量 validator 降档实验**：其定义为「5 个 W7 开关**全部开启** + `VALIDATOR_MODEL=qwen-turbo`」——两个变量同时变动，故其结果**不能归因于降档本身**。
   （同一表述已在 `docs/eval-w7-conclusion.md` §2 表格与 §8.2 记账中记录；此处并列以免备忘与结论文档口径不一致。）

---

## 附：可复算命令

```bash
# 确认开关默认值
grep -n -A25 "class ExperimentConfig" config.py

# 确认主链路不设置这些开关（应为空；不含配置文件和实验 runner）
grep -rn "CRITIC_GAP_ENABLED\|VALIDATOR_FIXES_ENABLED\|WRITER_SECTIONED_FEED_ENABLED\|VALIDATOR_TRIM_ENABLED\|VALIDATOR_ASSERTIVE_FILTER_ENABLED" cli.py web/

# 确认 .env.example / README 已记录 W7 主链路默认开关
grep -rn "CRITIC_GAP_ENABLED\|VALIDATOR_FIXES_ENABLED\|WRITER_SECTIONED_FEED_ENABLED\|VALIDATOR_TRIM_ENABLED\|VALIDATOR_ASSERTIVE_FILTER_ENABLED" .env.example README.md

# 确认 docs/ 中除本备忘外的历史提及仍可追踪
grep -rn "CRITIC_GAP_ENABLED\|VALIDATOR_FIXES_ENABLED\|WRITER_SECTIONED_FEED_ENABLED\|VALIDATOR_TRIM_ENABLED\|VALIDATOR_ASSERTIVE_FILTER_ENABLED" docs/ | grep -v w7-switch-disposition

# 量化「改默认值」的代价（见 §3.1）
CRITIC_GAP_ENABLED=false VALIDATOR_FIXES_ENABLED=false WRITER_SECTIONED_FEED_ENABLED=false \
VALIDATOR_TRIM_ENABLED=false VALIDATOR_ASSERTIVE_FILTER_ENABLED=false \
  "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pytest -q

# 逐 arm 判定与开关组合
sed -n '231,284p' docs/eval-w7-conclusion.md
sed -n '103,175p' research_engine/eval/w7_experiment.py
```
