# eval 报告（run_20260916_011813）

> ⚠️ **本文件由 `research_engine/eval/report_gen.py` 自动生成，每次运行 `run.py` 都会被整体覆盖。**
> 它是**单次 run 的原始快照，不是项目结论**；下文指标表的「达标 ✅」只按本 run 的 summary 数值机械判定，
> 未纳入跨裁判复判。**项目结论以 `docs/eval-w7-conclusion.md` 为准。**
> 若需长期保存某次 run 的判定，请另存为结论文档，不要依赖本文件。

> **可比性声明：**本报告是单 run 快照；不自动支持跨 run 因果比较。
> - 跨 revision：需逐 run 校验 `code_revision` / 环境指纹；不一致时禁止合并。
> - 缺 run / 缺格：必须显式登记原因；缺失结果不得静默当作 0。
> - 预算：steps、tokens、cost 不平衡时，结果标记为不可直接比较。
> - 闸门：被测组件不得兼任裁判；闸门失败只说明该格未过，不自动证明被测机制有害。
> - 因果：仅在同题/同证据池/同预算/固定独立裁判等条件满足时支持因果解释。


## 1. 数据集说明
- version: 1.1 / created_at: 2026-09-06
- anchor_samples: ['q_001']
- 标注方法学: ai_draft + human_calibration

## 2. 运行环境与双锚
- git commit: 13a6d3eea80c3b73d7e2377deec83f7a7b19a3e8 / dataset version: 1.1
- 墙钟/并发/统计: 20 条，并发 3，生成于 2026-09-16T02:24:30

## 3. 指标表（7 项，Q8 含检索命中率）
| 指标 | 目标 | 本轮 | 达标 |
|---|---|---|---|
| completion_rate | ≥90% | 100.0% | ✅ |
| citation_accuracy | ≥85%（**严格口径** verified） | 73.6% | ⚠️ |
| citation_accuracy_relaxed | 记录基线（**宽松口径**，仅解释性附注） | 75.8% | ✅ |
| coverage | ≥90% | 50.4% | ⚠️ |
| retrieval_hit_rate | 记录基线 | 54.6% | ✅ |
| avg_steps | 记录基线 | 9.2 轮 | ✅ |
| reflection_critic_stop_rate | ≥90% | 85.0% | ⚠️ |

💰 总 token（Phase1 研究）：967026，成本：¥0.8953（Phase2 judge 另计 59419 token）
  - 模型名桶：qwen-plus: 542133tok ¥0.7526, qwen-turbo: 424893tok ¥0.1428
  - 职责桶：planner: 24941tok, critic: 207166tok, smart: 132956tok, validator: 177070tok, compress: 424893tok

📏 **双口径与人工口径归属（W7 TBD-5 诚实披露，不得省略）：**
  - **严格口径**（`citation_accuracy`）= `verified = existence AND faithful`（引对编号 **且** 忠实）—— W2 契约口径，跨版本对比**一律以此为准**。
  - **宽松口径**（`citation_accuracy_relaxed`）= `existence AND (faithful OR supported)`——论断在系统内**能找到依据**即算通过（允许引错编号但内容真实）。
  - **⚠️ W5 人工抽检 83~92% 属「宽松口径」**：人工判的是「这论断有没有依据」，**不逐条核对编号** ⇒ 与机器严格口径**不是同一件事**；二者差异的**绝大部分是口径差**，**不是 validator 误拒**。
  - 人工抽检样本仅 **12 条**、置信区间极宽，**不作为真值**；宽松口径仅作**解释性附注**，不参与任何达标判定。

📐 次要指标（**只看不判**，无达标线）：**「信息不足」标注小节占比 72.4%**（89/123 小节）；引用位兜底标记 `[来源: 信息不足]` 0 处（统计覆盖 20 篇报告）。
  - 读法：比例**极低**可能意味着模型改为编造而非承认缺口；比例**极高**意味着检索没喂饱。两种极端都值得人工抽检，但**不作为任何达标判据**。


## 4. 失败与异常附录
- 完整 20 / 部分 0 / 失败 0
- q_005 [ok] 68073tok ¥0.0953
- q_009 [ok] 64183tok ¥0.0899
- q_013 [ok] 93912tok ¥0.1315

## 5. 人工抽检记录（两级：报告级 4~5 份 + 引用级 10~15 条，Q6）
- 抽检人: 于晏（单人，判定以标注规范为准；reviewer 字段可补二审）
- ⚠️ **口径提醒**：人工抽检判的是**宽松口径**（「这论断有没有依据」，不逐条核对编号），
  与 §3 机器严格口径（`verified = existence AND faithful`）**不是同一件事**；
  W5 历史抽检 83~92% 即属此宽松口径，**不得与机器严格口径直接对比**。
- [ ] 报告级抽检 4~5 份（质量观感 + 反思质量面 + 每份 2~3 条引用精读）
- [ ] 引用级抽检 10~15 条（引用准确率人类口径）
- [ ] 锚点桶人工复核 6~8 条（确认"真回归 vs 数据漂移"）
- （跑完 v0 后在此填写逐条 verdict）

## 6. 可复现性（3 条重跑 Δ）
- 待完成：选 易/中/难 各 1 条重跑 1 次，记录单条 Δ（目标 ≤10%）与均值 Δ（≤5%）

## 7. 指标演进趋势（自 v0，pp 口径）
> ⚠️ 本表数值由各 run 的 summary 直读**主链路 validator**裁决，而各 run 的实验配置与裁判模型并不一致；
> 「vs 上轮(pp)」仅作记录，**不构成可比趋势**（W7 实测：同一引用集仅换裁判即产生 +7.53pp 差异）。
> 🧪 标记的行属于 **W7 权威对照实验的臂**（六臂 × 3 区块，配置各异、非全部为基线模型），**不得与主链路 run 横向比较**；结论见 `docs/eval-w7-conclusion.md`。

| 运行 | 完成率 | 引用准确率 | 覆盖度 | 检索命中率 | vs 上轮(pp) |
|---|---|---|---|---|---|
| run_20260906_184156 | 100.0% | 71.9% | 57.1% | 59.2% | — |
| run_v11_compare | 100.0% | 71.9% | 55.4% | 59.7% | completion_rate:+0.0, citation_accuracy:+0.0, coverage:-1.7, retrieval_hit_rate:+0.6 |
| run_20260907_001658 | 100.0% | 59.7% | 27.8% | 67.6% | completion_rate:+0.0, citation_accuracy:-12.3, coverage:-27.6, retrieval_hit_rate:+7.8 |
| run_20260909_155504 | 100.0% | 47.6% | 83.3% | 79.2% | completion_rate:+0.0, citation_accuracy:-12.0, coverage:+55.6, retrieval_hit_rate:+11.6 |
| run_20260909_160122 | 100.0% | 81.4% | 58.3% | 79.2% | completion_rate:+0.0, citation_accuracy:+33.8, coverage:-25.0, retrieval_hit_rate:+0.0 |
| run_20260909_160737 | 100.0% | 61.8% | 57.9% | 62.1% | completion_rate:+0.0, citation_accuracy:-19.6, coverage:-0.4, retrieval_hit_rate:-17.1 |
| run_20260909_164934 | 100.0% | 60.9% | 55.8% | 61.5% | completion_rate:+0.0, citation_accuracy:-0.9, coverage:-2.1, retrieval_hit_rate:-0.6 |
| run_20260909_173229 | 100.0% | 67.1% | 56.2% | 63.6% | completion_rate:+0.0, citation_accuracy:+6.2, coverage:+0.4, retrieval_hit_rate:+2.2 |
| run_20260909_185458 | 100.0% | 76.5% | 63.3% | 67.6% | completion_rate:+0.0, citation_accuracy:+9.4, coverage:+7.1, retrieval_hit_rate:+4.0 |
| run_20260909_194138 | 100.0% | 64.1% | 62.5% | 71.5% | completion_rate:+0.0, citation_accuracy:-12.4, coverage:-0.8, retrieval_hit_rate:+3.9 |
| run_20260909_202751 | 100.0% | 81.2% | 61.7% | 66.0% | completion_rate:+0.0, citation_accuracy:+17.1, coverage:-0.8, retrieval_hit_rate:-5.6 |
| run_20260909_211614 | 100.0% | 74.2% | 66.2% | 67.2% | completion_rate:+0.0, citation_accuracy:-7.0, coverage:+4.6, retrieval_hit_rate:+1.2 |
| run_20260909_220233 | 100.0% | 70.8% | 44.6% | 51.4% | completion_rate:+0.0, citation_accuracy:-3.4, coverage:-21.7, retrieval_hit_rate:-15.9 |
| run_20260909_224409 | 100.0% | 71.2% | 40.8% | 45.9% | completion_rate:+0.0, citation_accuracy:+0.4, coverage:-3.7, retrieval_hit_rate:-5.4 |
| run_20260909_232719 | 100.0% | 86.7% | 40.8% | 42.8% | completion_rate:+0.0, citation_accuracy:+15.6, coverage:+0.0, retrieval_hit_rate:-3.1 |
| run_20260910_003024 | 100.0% | 65.1% | 47.1% | 45.4% | completion_rate:+0.0, citation_accuracy:-21.6, coverage:+6.2, retrieval_hit_rate:+2.6 |
| run_20260910_010952 | 100.0% | 53.6% | 23.3% | 34.6% | completion_rate:+0.0, citation_accuracy:-11.5, coverage:-23.8, retrieval_hit_rate:-10.9 |
| run_20260910_014747 | 100.0% | 82.3% | 32.5% | 43.1% | completion_rate:+0.0, citation_accuracy:+28.7, coverage:+9.2, retrieval_hit_rate:+8.6 |
| run_20260910_022817 | 100.0% | 32.6% | 40.0% | 47.6% | completion_rate:+0.0, citation_accuracy:-49.7, coverage:+7.5, retrieval_hit_rate:+4.4 |
| run_20260910_025125 | 100.0% | 59.3% | 31.2% | 46.8% | completion_rate:+0.0, citation_accuracy:+26.8, coverage:-8.8, retrieval_hit_rate:-0.8 |
| run_20260910_033127 | 100.0% | 62.0% | 34.6% | 43.6% | completion_rate:+0.0, citation_accuracy:+2.6, coverage:+3.3, retrieval_hit_rate:-3.1 |
| run_20260910_040911 | 100.0% | 69.0% | 36.2% | 46.9% | completion_rate:+0.0, citation_accuracy:+7.0, coverage:+1.7, retrieval_hit_rate:+3.2 |
| run_20260910_044716 | 100.0% | 85.6% | 37.5% | 47.5% | completion_rate:+0.0, citation_accuracy:+16.6, coverage:+1.3, retrieval_hit_rate:+0.6 |
| run_20260910_052555 | 100.0% | 40.9% | 39.6% | 46.2% | completion_rate:+0.0, citation_accuracy:-44.7, coverage:+2.1, retrieval_hit_rate:-1.3 |
| run_20260910_054957 | 100.0% | 65.3% | 37.1% | 44.3% | completion_rate:+0.0, citation_accuracy:+24.4, coverage:-2.5, retrieval_hit_rate:-1.9 |
| run_20260910_062843 | 100.0% | 69.1% | 39.6% | 46.2% | completion_rate:+0.0, citation_accuracy:+3.8, coverage:+2.5, retrieval_hit_rate:+1.9 |
| run_20260910_071031 | 100.0% | 64.1% | 40.8% | 42.7% | completion_rate:+0.0, citation_accuracy:-5.0, coverage:+1.3, retrieval_hit_rate:-3.5 |
| run_20260910_075007 | 100.0% | 88.5% | 40.8% | 46.8% | completion_rate:+0.0, citation_accuracy:+24.4, coverage:+0.0, retrieval_hit_rate:+4.1 |
| run_20260910_083020 | 100.0% | 37.7% | 21.2% | 31.3% | completion_rate:+0.0, citation_accuracy:-50.8, coverage:-19.6, retrieval_hit_rate:-15.5 |
| run_20260910_085259 | 100.0% | 63.3% | 33.3% | 47.6% | completion_rate:+0.0, citation_accuracy:+25.7, coverage:+12.1, retrieval_hit_rate:+16.2 |
| run_20260910_100400 | 100.0% | 68.7% | 9.6% | 22.4% | completion_rate:+0.0, citation_accuracy:+5.4, coverage:-23.8, retrieval_hit_rate:-25.2 |
| run_20260910_105637 | 100.0% | 47.0% | 30.0% | 36.9% | completion_rate:+0.0, citation_accuracy:-21.7, coverage:+20.4, retrieval_hit_rate:+14.5 |
| run_20260910_114009 | 100.0% | 77.1% | 32.1% | 40.5% | completion_rate:+0.0, citation_accuracy:+30.1, coverage:+2.1, retrieval_hit_rate:+3.6 |
| run_20260910_121649 | 100.0% | 67.2% | 30.8% | 43.8% | completion_rate:+0.0, citation_accuracy:-9.9, coverage:-1.2, retrieval_hit_rate:+3.2 |
| run_20260910_125628 | 100.0% | 84.4% | 32.1% | 38.6% | completion_rate:+0.0, citation_accuracy:+17.2, coverage:+1.2, retrieval_hit_rate:-5.1 |
| run_20260910_133606 | 100.0% | 63.9% | 30.0% | 45.1% | completion_rate:+0.0, citation_accuracy:-20.5, coverage:-2.1, retrieval_hit_rate:+6.5 |
| run_20260910_141315 | 100.0% | 81.8% | 35.4% | 44.4% | completion_rate:+0.0, citation_accuracy:+17.9, coverage:+5.4, retrieval_hit_rate:-0.8 |
| run_20260910_154319 | 100.0% | 65.7% | 36.7% | 41.0% | completion_rate:+0.0, citation_accuracy:-16.1, coverage:+1.3, retrieval_hit_rate:-3.3 |
| run_20260910_162529 | 90.0% | 30.0% | 5.0% | 12.3% | completion_rate:-10.0, citation_accuracy:-35.7, coverage:-31.7, retrieval_hit_rate:-28.7 |
| run_20260910_165016 | 100.0% | 27.5% | 17.1% | 17.8% | completion_rate:+10.0, citation_accuracy:-2.5, coverage:+12.1, retrieval_hit_rate:+5.5 |
| run_20260910_171054 | 100.0% | 77.6% | 0.0% | 46.6% | completion_rate:+0.0, citation_accuracy:+50.0, coverage:-17.1, retrieval_hit_rate:+28.8 |
| run_20260910_173540 | 0.0% | 0.0% | 0.0% | 10.4% | completion_rate:-100.0, citation_accuracy:-77.6, coverage:+0.0, retrieval_hit_rate:-36.2 |
| run_20260910_175208 | 100.0% | 72.8% | 35.4% | 42.4% | completion_rate:+100.0, citation_accuracy:+72.8, coverage:+35.4, retrieval_hit_rate:+32.0 |
| run_20260910_183310 | 100.0% | 72.7% | 38.3% | 41.3% | completion_rate:+0.0, citation_accuracy:-0.1, coverage:+2.9, retrieval_hit_rate:-1.0 |
| run_20260910_191547 | 100.0% | 58.8% | 39.6% | 47.0% | completion_rate:+0.0, citation_accuracy:-13.9, coverage:+1.3, retrieval_hit_rate:+5.7 |
| run_20260910_194445 | 100.0% | 61.9% | 30.0% | 41.2% | completion_rate:+0.0, citation_accuracy:+3.1, coverage:-9.6, retrieval_hit_rate:-5.8 |
| run_20260910_202318 | 100.0% | 80.9% | 36.7% | 41.4% | completion_rate:+0.0, citation_accuracy:+19.0, coverage:+6.7, retrieval_hit_rate:+0.2 |
| run_20260910_210646 | 100.0% | 72.0% | 35.0% | 42.6% | completion_rate:+0.0, citation_accuracy:-8.9, coverage:-1.7, retrieval_hit_rate:+1.3 |
| run_20260910_214830 | 100.0% | 45.8% | 35.0% | 42.9% | completion_rate:+0.0, citation_accuracy:-26.2, coverage:+0.0, retrieval_hit_rate:+0.3 |
| run_20260910_221911 | 100.0% | 65.3% | 37.1% | 45.0% | completion_rate:+0.0, citation_accuracy:+19.5, coverage:+2.1, retrieval_hit_rate:+2.1 |
| run_20260910_225634 | 100.0% | 58.0% | 42.5% | 44.2% | completion_rate:+0.0, citation_accuracy:-7.3, coverage:+5.4, retrieval_hit_rate:-0.8 |
| run_20260910_233617 | 100.0% | 70.3% | 46.7% | 45.3% | completion_rate:+0.0, citation_accuracy:+12.3, coverage:+4.2, retrieval_hit_rate:+1.1 |
| run_20260911_001417 | 100.0% | 43.6% | 40.4% | 41.0% | completion_rate:+0.0, citation_accuracy:-26.8, coverage:-6.2, retrieval_hit_rate:-4.2 |
| run_20260911_004201 | 100.0% | 63.5% | 33.8% | 39.9% | completion_rate:+0.0, citation_accuracy:+20.0, coverage:-6.7, retrieval_hit_rate:-1.2 |
| run_20260911_150715 | 100.0% | 71.2% | 40.4% | 45.2% | completion_rate:+0.0, citation_accuracy:+7.6, coverage:+6.7, retrieval_hit_rate:+5.3 |
| 🧪 run_20260911_194156 | 100.0% | 76.0% | 34.2% | 43.8% | completion_rate:+0.0, citation_accuracy:+4.8, coverage:-6.2, retrieval_hit_rate:-1.4 |
| 🧪 run_20260911_202638 | 100.0% | 63.2% | 42.5% | 57.8% | completion_rate:+0.0, citation_accuracy:-12.8, coverage:+8.3, retrieval_hit_rate:+14.0 |
| 🧪 run_20260911_212259 | 100.0% | 74.6% | 37.9% | 40.1% | completion_rate:+0.0, citation_accuracy:+11.4, coverage:-4.6, retrieval_hit_rate:-17.7 |
| 🧪 run_20260911_220854 | 100.0% | 74.4% | 38.3% | 40.8% | completion_rate:+0.0, citation_accuracy:-0.2, coverage:+0.4, retrieval_hit_rate:+0.7 |
| 🧪 run_20260911_224011 | 100.0% | 72.3% | 35.4% | 44.9% | completion_rate:+0.0, citation_accuracy:-2.1, coverage:-2.9, retrieval_hit_rate:+4.1 |
| 🧪 run_20260911_232515 | 100.0% | 97.8% | 42.5% | 58.4% | completion_rate:+0.0, citation_accuracy:+25.5, coverage:+7.1, retrieval_hit_rate:+13.5 |
| 🧪 run_20260912_233902 | 100.0% | 79.0% | 17.9% | 24.6% | completion_rate:+0.0, citation_accuracy:-18.7, coverage:-24.6, retrieval_hit_rate:-33.8 |
| 🧪 run_20260913_002412 | 100.0% | 65.5% | 45.8% | 53.8% | completion_rate:+0.0, citation_accuracy:-13.5, coverage:+27.9, retrieval_hit_rate:+29.1 |
| 🧪 run_20260913_012018 | 100.0% | 73.4% | 13.6% | 21.2% | completion_rate:+0.0, citation_accuracy:+7.8, coverage:-32.2, retrieval_hit_rate:-32.5 |
| 🧪 run_20260913_021042 | 100.0% | 84.5% | 16.2% | 22.1% | completion_rate:+0.0, citation_accuracy:+11.1, coverage:+2.6, retrieval_hit_rate:+0.8 |
| 🧪 run_20260913_024745 | 100.0% | 77.9% | 11.2% | 19.4% | completion_rate:+0.0, citation_accuracy:-6.6, coverage:-5.0, retrieval_hit_rate:-2.7 |
| 🧪 run_20260913_033607 | 100.0% | 89.9% | 43.8% | 54.1% | completion_rate:+0.0, citation_accuracy:+12.0, coverage:+32.5, retrieval_hit_rate:+34.7 |
| 🧪 run_20260913_041629 | 100.0% | 71.0% | 38.3% | 42.1% | completion_rate:+0.0, citation_accuracy:-18.9, coverage:-5.4, retrieval_hit_rate:-12.0 |
| 🧪 run_20260913_045912 | 100.0% | 69.9% | 47.1% | 56.9% | completion_rate:+0.0, citation_accuracy:-1.1, coverage:+8.8, retrieval_hit_rate:+14.7 |
| 🧪 run_20260913_055850 | 100.0% | 70.1% | 19.6% | 24.4% | completion_rate:+0.0, citation_accuracy:+0.2, coverage:-27.5, retrieval_hit_rate:-32.5 |
| 🧪 run_20260913_064837 | 100.0% | 83.3% | 9.6% | 23.2% | completion_rate:+0.0, citation_accuracy:+13.2, coverage:-10.0, retrieval_hit_rate:-1.2 |
| 🧪 run_20260913_072332 | 100.0% | 69.8% | 12.9% | 23.2% | completion_rate:+0.0, citation_accuracy:-13.5, coverage:+3.3, retrieval_hit_rate:+0.0 |
| run_20260916_001005 | 100.0% | 71.8% | 39.5% | 53.0% | completion_rate:+0.0, citation_accuracy:+2.0, coverage:+26.5, retrieval_hit_rate:+29.8 |
| run_20260916_011813 | 100.0% | 73.6% | 50.4% | 54.6% | completion_rate:+0.0, citation_accuracy:+1.8, coverage:+10.9, retrieval_hit_rate:+1.6 |


## 8. 已知局限
- 单人标注/单人抽检（标注者=评估者同源偏倚，如实声明）
- 真实 API 非确定性（博查/arXiv 结果随时间漂移）
- 成本为精确加权（input/output 拆分 × W3 pricing 表），价格有时效
- **引用准确率的裁判未与被测对象解耦**：`citation_accuracy` 直读主链路 validator 裁决，凡改动 validator 的 run
  其数值同时含「被测效应 + 裁判效应」，**不可与其他 run 直接比较**（W7 实测裁判效应 +7.53pp 与被测效应同量级）

## 9. W7 技术债对照实验（权威容器 `w7_experiment_20260911_194151`）

> 数据源：`research_engine/eval/results/w7_experiment_20260911_194151/manifest.json`
> （6 臂 × 3 区块，共 18 条 run 记录：17 done
> + 1 非 done）。
> 本章是**实验记录**而非趋势——目的就是让「六臂实验跑在哪、缺哪格、能不能比」在报告里可查。

### 9.1 轮次（区块）× 主指标

| arm | 区块 | 状态 | coverage | citation_accuracy | retrieval_hit_rate | avg_steps |
|---|---|---|---:|---:|---:|---:|
| `arm0_baseline` | B0 | done | 34.2% | 76.0% | 43.8% | 3.85 |
| `arm0_baseline` | B1 | done | 17.9% | 79.0% | 24.6% | 3.85 |
| `arm0_baseline` | B2 | done | 38.3% | 71.0% | 42.1% | 3.9 |
| `arm1_critic_gap` | B0 | done | 42.5% | 63.2% | 57.8% | 8.1 |
| `arm1_critic_gap` | B1 | done | 45.8% | 65.5% | 53.8% | 8.85 |
| `arm1_critic_gap` | B2 | done | 47.1% | 69.9% | 56.9% | 8.25 |
| `arm3_validator_fixes` | B0 | done | 37.9% | 74.6% | 40.1% | 3.85 |
| `arm3_validator_fixes` | B1 | done | 13.6% | 73.4% | 21.2% | 3.8421 |
| `arm3_validator_fixes` | B2 | done | 19.6% | 70.1% | 24.4% | 3.85 |
| `arm4_writer_sectioned` | B0 | done | 38.3% | 74.4% | 40.8% | 3.85 |
| `arm4_writer_sectioned` | B1 | done | 16.2% | 84.5% | 22.1% | 3.85 |
| `arm4_writer_sectioned` | B2 | done | 9.6% | 83.3% | 23.2% | 3.9 |
| `arm5_validator_trim` | B0 | done | 35.4% | 72.3% | 44.9% | 3.85 |
| `arm5_validator_trim` | B1 | done | 11.2% | 77.9% | 19.4% | 3.85 |
| `arm5_validator_trim` | B2 | done | 12.9% | 69.8% | 23.2% | 3.9 |
| `arm6_validator_turbo` | B0 | done | 42.5% | 97.8% | 58.4% | 8.1 |
| `arm6_validator_turbo` | B1 | done | 43.8% | 89.9% | 54.1% | 9.3 |
| `arm6_validator_turbo` | B2 | ⏭ `skipped_gate` | — | — | — | — |

**缺格 / 未执行登记（原因不得省略）：**
> - `arm6_validator_turbo` / B2：`skipped_gate` —— arm3_validator_fixes citation_accuracy=0.7011（同区块）< 守门线 0.71

### 9.2 配对差值（coverage，相对 `arm0_baseline` 同区块，pp）与机械判定

| arm | B0 | B1 | B2 | 均值 | 区块方向 | 机械判定 |
|---|---:|---:|---:|---:|---|---|
| `arm1_critic_gap` | +8.3 | +27.9 | +8.8 | +15.0 | 全同向(+) | ⚠️ 全同向(+)，但**预算不平衡、破守门线（≤+50%）**（avg_steps 8.40 vs 基线 3.87（配对涨幅均值 **+117.3%**；比值之均值口径 +117.2%））⇒ 属成本-覆盖度权衡，不能作为机制更优的证据 |
| `arm3_validator_fixes` | +3.7 | -4.3 | -18.7 | -6.4 | **符号翻转** | ❌ **不可判定**（区块间符号翻转） |
| `arm4_writer_sectioned` | +4.2 | -1.7 | -28.7 | -8.8 | **符号翻转** | ❌ **不可判定**（区块间符号翻转） |
| `arm5_validator_trim` | +1.3 | -6.7 | -25.4 | -10.3 | **符号翻转** | ❌ **不可判定**（区块间符号翻转） |
| `arm6_validator_turbo` | +8.3 | +25.8 | — | +17.1 | 全同向(+) | ⚠️ 全同向(+)，但**缺 1 格** ⇒ 不可裁决 |

### 9.3 不可比性声明（本实验的硬约束）

> - **跨 revision**：首轮 `code_revision=95adb77+patch:aa4bb45276976b67772a95bd95b7bc97e13b6778`，续跑修订集合 = `ca518866`。跨区块的 citation 类指标**不得合并比较**（引用归一化开关差异会让报告正文的裸 `[N]` 引用消失）。
> - **缺格**：凡缺失格一律显式登记原因（见 9.1 与下方备忘），**不得静默当作 0**。
> - **被测兼裁判**：`citation_accuracy` 直读主链路 validator；凡改动 validator 的 arm，其数值同时含「被测效应 + 裁判效应」⇒ 不可与其他 arm 直接比较（实测纯裁判效应 +7.53pp）。
> - **预算不平衡**：`avg_steps` 分档且与实验开关共线 ⇒ 跨 arm 的 coverage 差**不可直接解释为机制优劣**；正确做法是同预算成本-效果比较。
> - **结论归属**：本报告只做机械判定，**不主张任何因果结论**；权威判定见 `docs/eval-w7-conclusion.md`。
