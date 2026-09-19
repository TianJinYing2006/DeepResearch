# W8 确定性 DoD 验收表（A 类断言）

> **为什么有这份文档**：after 基线的统计结论是「四指标全部不可判定」，但**不可判定 ≠ 没验收**。
> 自 §3.3.2 起，W8 的验收口径就已重定位为 **A 类确定性断言** —— 故障可归因、状态分层、成本守恒、
> 可复现元数据、产物治理。这类断言**不依赖统计功效**：要么成立要么不成立，零噪声，
> 因此不受「20 题 × 3 轮分辨率不足」的任何影响。
>
> **配套结论**：`docs/eval-w8-after-baseline.md`（统计侧：不可判定 + 不续跑论证）。

---

## 结论

| 维度 | 结论 |
| --- | --- |
| **W8 工程 DoD** | ✅ **已完成**（8/8 项均有测试证据，且由 CI 三档确认） |
| **平均质量指标改善** | ❌ **本次实验不能给出**（四指标全部不可判定，见配套结论文档） |

两件事必须严格分开说：**「W8 的确定性机制是否工作」可以验收**；**「W8 是否让 coverage / citation 的平均值变好」本次不成立**。

---

## 验收表（8 项）

| # | 验收项 | 必须证明的事实 | 证据（测试文件 · 关键用例） | 条数 |
| --- | --- | --- | --- | --- |
| 1 | **工具失败可归因** | provider / RAG / 内部错误都能落到明确的 `failure_reason`，且枚举**一处定义** | `tests/test_arm4_failure_reasons.py`：<br>`test_bocha_not_configured_does_not_raise`<br>`test_bocha_transport_failures` / `test_bocha_http_status` / `test_bocha_parse_error`<br>`test_arxiv_parse_error` / `test_arxiv_empty_result`<br>`test_retrieve_provider_error` / `test_retrieve_not_configured`<br>`test_retrieve_partial_backend_failure_keeps_failures`<br>`test_tool_reasons_have_no_unexpected_producers`（一处定义反向锁） | 16 |
| 2 | **状态分层** | `run_status` / `invoke_status` / `metrics_status` 语义不再混用；裸 `status` 无新落点 | `tests/test_status_naming.py`：<br>`test_end_to_end_written_records_have_no_bare_status`<br>`test_run_py_has_no_bare_status_literal`<br>`test_bare_status_sites_registry_is_true`（登记表收敛至 3 条）<br>`test_value_vocabularies_are_locked`<br>+ `tests/test_arm1_run_status.py::test_run_status_is_three_state_without_partial` | 26 + 1 |
| 3 | **异常退出契约** | 失败的 run **不会**伪装成成功或空结果；结构化 error 不被切片 | `tests/test_arm1_run_status.py`：<br>`test_failed_is_reachable_via_exception_contract`<br>`test_structured_error_shape_and_success_semantics`<br>`test_structured_error_is_not_a_str` / `test_report_gen_does_not_slice_error`<br>`test_researcher_unexpected_exception_is_internal_not_guessed`<br>`test_recover_survives_get_state_raising` / `test_recover_preserves_checkpoint_state`<br>`test_recursion_error_maps_to_recursion_limit` | 24 |
| 4 | **成本守恒（D1）** | 缺成本源时**不静默显示 ¥0**，而是显式降级并标记 | `tests/test_arm5_quality_gate.py`：<br>`test_cost_degraded_when_stats_missing`<br>`test_cost_normal_when_stats_present`<br>`test_backfill_repairs_d1_and_is_idempotent`（75 个历史 run 已修复）<br>`test_backfill_skips_run_without_eval`<br>+ `tests/test_eval_metrics.py::test_cost_precise_weighted` | 20 + 1 |
| 5 | **质量闸** | `ok / suspicious / broken` 三态与 `verdict_reasons` 能落盘；阈值外置、只告警不阻断 | `tests/test_arm5_quality_gate.py`：<br>`test_verdict_ok_for_healthy_run`<br>`test_verdict_suspicious_when_metrics_collapse`<br>`test_verdict_broken_when_pipeline_fails_mostly`<br>`test_broken_takes_precedence_over_suspicious` / `test_empty_run_is_broken`<br>`test_summarize_emits_stderr_and_verdict`<br>`test_missing_metric_does_not_trigger_suspicious`（缺指标 ≠ 可疑，D-05）<br>`test_thresholds_are_effective_not_hardcoded` | 20 |
| 6 | **可复现元数据** | 配置、五开关生效值、`prompt_hash`、`scorer_version` 可追溯；历史缺失字段**如实为未记录** | `tests/test_arm6_provenance.py`：<br>`test_prompt_hash_is_deterministic`<br>`test_critic_gap_switch_changes_hash`（指纹对开关敏感）<br>`test_scorer_version_recorded_and_nonempty`<br>`test_raw_records_all_provenance_fields` / `test_raw_failure_path_carries_provenance`<br>`test_historical_raw_reads_none_not_fabricated`<br>+ `tests/test_eval_provenance.py`：`test_config_snapshot_*`、`test_experiment_snapshot_*`、<br>`test_provenance_from_raw_reads_run_time_not_now` | 22 + 14 |
| 7 | **产物治理** | 白名单集合 ≡ git 跟踪集合，且每条都写明引用出处 | `tests/test_arm7_artifact_governance.py`：<br>`test_real_repository_is_consistent`<br>`test_detects_tracked_but_not_allowlisted` / `test_detects_allowlisted_but_not_tracked`<br>`test_auto_enumerated_doc_alone_is_not_proof`（D-10）<br>`test_real_gitignore_has_no_trailing_hash_on_bang_rules`（D-09）<br>+ CI 步骤 `Eval 产物白名单纪律（Arm 7）` + `tools/check_results_whitelist.py` | 13 |
| 8 | **历史兼容** | 旧产物 **dual-read**（新键优先、回落旧键）；**历史产物不被擅自回填** | `tests/test_status_naming.py`：<br>`test_read_invoke_status_falls_back_to_legacy`<br>`test_read_metrics_status_falls_back_to_legacy`<br>`test_legacy_raw_artifacts_are_not_backfilled`<br>`test_legacy_eval_artifacts_are_not_backfilled`<br>`test_before_baseline_runs_are_intact`<br>+ `test_arm6_provenance.py::test_historical_raw_reads_none_not_fabricated`<br>+ `test_eval_provenance.py::test_provenance_from_raw_missing_is_unknown_not_fabricated` | 26 |

> 8 项合计覆盖 **187 条**用例；全量测试 **330 条全绿**。

---

## 不在本表范围内（重要）

| 命题 | 状态 | 原因 |
| --- | --- | --- |
| 「W8 让 coverage 提升/下降 Xpp」 | ❌ 不成立 | Δ = −5.1pp，SE 3.81、MDE 10.66 ⇒ 不可判定 |
| 「W8 让 citation_accuracy 提升 Xpp」 | ❌ 不成立 | Δ = +3.1pp，MDE 9.21 ⇒ 不可判定 |
| 「W8 让 retrieval_hit_rate 下降 Xpp」 | ❌ 不成立 | Δ = −2.5pp，MDE 3.88 ⇒ 不可判定 |
| 「W8 让 steps 减少 1.2 步」 | ⚠️ 有信号但**不得作为质量证据** | 按 **D2**：Arm 2 是功能性修复（旧模板无条件 `SyntaxError` ⇒ code_exec 144 条成功 0），须单独归因 |
| 「after 侧轮次间波动从 12.50pp 降到 2.92pp」 | 🔍 **观察，非结论** | σ 只由 3 个轮次对估出、自由度极小（F ≈ 2.0 边缘），需更多轮次才坐实 —— 而加轮次的性价比已被 D-18 否掉 |

---

## 复现方式

```bash
# 全量测试（330 条）
<venv>/Scripts/pytest.exe tests/ -q --basetemp=<干净的新目录>

# 静态检查
<venv>/Scripts/ruff.exe check .
python tools/check_md_tables.py
python tools/check_results_whitelist.py
```

⚠️ `--basetemp` 每次都要换成**干净的新目录**（复用非空目录会因清理被拦而报假 ERROR）；
不加 `--basetemp` 会被沙箱的批量删除守卫卡在临时目录 GC 上。

## CI 证据

提交 `53ad3b1`：Py3.11 / 3.12 / 3.13 **三档全 success**（零 LLM、零 key）。
