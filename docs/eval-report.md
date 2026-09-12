# eval 报告（run_20260911_232515）

## 1. 数据集说明
- version: 1.1 / created_at: 2026-09-06
- anchor_samples: ['q_001']
- 标注方法学: ai_draft + human_calibration

## 2. 运行环境与双锚
- git commit: 95adb77f31f535d07368f487ceb98fa4e32ea3bf / dataset version: 1.1
- 墙钟/并发/统计: 20 条，并发 3，生成于 2026-09-12T00:00:20

## 3. 指标表（7 项，Q8 含检索命中率）
| 指标 | 目标 | 本轮 | 达标 |
|---|---|---|---|
| completion_rate | ≥90% | 100.0% | ✅ || citation_accuracy | ≥85%（忠实度口径） | 97.8% | ✅ || coverage | ≥90% | 42.5% | ⚠️ || retrieval_hit_rate | 记录基线 | 58.4% | ✅ || avg_steps | 记录基线 | 8.1 轮 | ✅ || reflection_critic_stop_rate | ≥90% | 95.0% | ✅ |
💰 总 token（Phase1 研究）：1067359，成本：¥0.7274（Phase2 judge 另计 82407 token）
  - 模型名桶：qwen-plus: 370847tok ¥0.4684, qwen-turbo: 696512tok ¥0.2590
  - 职责桶：planner: 20168tok, critic: 174069tok, smart: 176610tok, compress: 506501tok, validator: 190011tok


## 4. 失败与异常附录
- 完整 20 / 部分 0 / 失败 0
- q_009 [ok] 60339tok ¥0.0845
- q_010 [ok] 112386tok ¥0.1573
- q_012 [ok] 87921tok ¥0.1231

## 5. 人工抽检记录（两级：报告级 4~5 份 + 引用级 10~15 条，Q6）
- 抽检人: 于晏（单人，判定以标注规范为准；reviewer 字段可补二审）
- [ ] 报告级抽检 4~5 份（质量观感 + 反思质量面 + 每份 2~3 条引用精读）
- [ ] 引用级抽检 10~15 条（引用准确率人类口径）
- [ ] 锚点桶人工复核 6~8 条（确认"真回归 vs 数据漂移"）
- （跑完 v0 后在此填写逐条 verdict）

## 6. 可复现性（3 条重跑 Δ）
- 待完成：选 易/中/难 各 1 条重跑 1 次，记录单条 Δ（目标 ≤10%）与均值 Δ（≤5%）

## 7. 指标演进趋势（自 v0，pp 口径）
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
| run_20260911_194156 | 100.0% | 76.0% | 34.2% | 43.8% | completion_rate:+0.0, citation_accuracy:+4.8, coverage:-6.2, retrieval_hit_rate:-1.4 |
| run_20260911_202638 | 100.0% | 63.2% | 42.5% | 57.8% | completion_rate:+0.0, citation_accuracy:-12.8, coverage:+8.3, retrieval_hit_rate:+14.0 |
| run_20260911_212259 | 100.0% | 74.6% | 37.9% | 40.1% | completion_rate:+0.0, citation_accuracy:+11.4, coverage:-4.6, retrieval_hit_rate:-17.7 |
| run_20260911_220854 | 100.0% | 74.4% | 38.3% | 40.8% | completion_rate:+0.0, citation_accuracy:-0.2, coverage:+0.4, retrieval_hit_rate:+0.7 |
| run_20260911_224011 | 100.0% | 72.3% | 35.4% | 44.9% | completion_rate:+0.0, citation_accuracy:-2.1, coverage:-2.9, retrieval_hit_rate:+4.1 |
| run_20260911_232515 | 100.0% | 97.8% | 42.5% | 58.4% | completion_rate:+0.0, citation_accuracy:+25.5, coverage:+7.1, retrieval_hit_rate:+13.5 |


## 8. 已知局限
- 单人标注/单人抽检（标注者=评估者同源偏倚，如实声明）
- 真实 API 非确定性（博查/arXiv 结果随时间漂移）
- 成本为精确加权（input/output 拆分 × W3 pricing 表），价格有时效
