# ADR-0012：可观测层迁移 —— Langfuse → OTel 原生（反转需求 3 的 W3 接入拍板）

> 飞书镜像：需求 33「需求设计文档：OTel 观测迁移」 https://wcnnpvbxd7li.feishu.cn/docx/MlvZdwL0SoaVYmxHjVrciGkcnad（2026-10-16 修复：原链接指向已删文档）

## 基本信息

- **编号**：0012
- **标题**：可观测层迁移（Langfuse SDK → OTel 原生；反转需求 3「OTel OTLP 出局」拍板）
- **日期**：2026-10-11
- **状态**：已采纳
- **涉及模块**：`research_engine/observability.py`、`research_engine/graph.py`、`research_engine/llm/client.py`、
  `config.py`、`cli.py`、`web/backend/egress.py`、`requirements.txt`、`tests/`、`tools/verify_otel_trace.py`
- **关联需求**：`docs/requirements/33-otel-observability-migration.md`（Issue #180）；
  反转对象：`docs/requirements/3-langfuse-observability.md:53`

## 背景

- W3（2026-09-03）上线 Langfuse 全链路可观测（7 节点 span + LLM generation usage/cost + trace URL 回显）。
  当时设计评审明确否决「OTel OTLP 替代 Langfuse」（需求 3:53 原文：「Python 侧样板过重；WeChatBot 走
  OTel 系 Java SDK 弃用所致，Python 无此限制」）。
- 此后 P1-8 已在 web 侧落地 OTel（`web/backend/otel.py`：OTLP + Prometheus、GenAI 指标、自动埋点），
  项目实际形成「引擎 Langfuse SDK + web OTel」双栈。
- 2026-10-11 主理人拍板：对齐国内技术栈趋势（企业 APM / 云厂商几乎全以 OTLP 为入口，GenAI 语义约定
  由 OTel 社区推动），将引擎埋点迁移为 OTel 原生。

## 问题 / 动机

- **单栈分裂**：同一个 trace 故事讲两套（引擎 Langfuse SDK vs web OTel），面试/开源叙事成本高；
- **境外依赖**：Langfuse Cloud 为境外服务（L3 默认关闭），国内生态认知度低；
- **monkey-patch 风险面**：`langfuse.openai` 为模块级全局 wrap，并倒逼 `openai` 锁 1.x 兼容窗口
  （W3.2 已因版本错配付出一次修复成本：generation 正文缺失 / usage 计量错乱）。

## 方案

- `research_engine/observability.py` 重写为 OTel 原生：
  - 根 trace：自持 trace_id（32-hex）并强制注入根 span（异常路径 CLI 也能回显 URL，W3 Q5 语义保留）；
  - 节点 span：中文展示名 + `dr.node` / `dr.round` / `dr.input` 等属性（对应 W3 `metadata.*`）；
  - generation：`LLMClient.chat` 单点显式 span（`gen_ai.*` 语义 + usage + 逐调用成本估算
    `dr.cost_estimate_cny`，无 monkey-patch）。
- 导出走 OTLP（`OTEL_EXPORTER_OTLP_ENDPOINT`，与 web 侧同 env）；后端可插拔
  （Jaeger / 自托管 Langfuse OTLP ingest / 任意 OTLP 后端）；URL 回显走模板
  `DR_OTEL_TRACE_URL_TEMPLATE`。
- 能力不减：三态开关（`DR_OTEL_ENABLED`）+ 知情打印 + 双层 fail-silent + 截断/脱敏（200/4000 常量）+
  token 对账（`tokens_total - state.token_used`）+ CLI 成本行（人民币，定价表复用）。
- Langfuse 从「SDK 依赖」降为「可选后端」（不再出现在依赖与代码中）。
- 进程内 provider 冲突（web Worker 先初始化）按「已有 SDK provider 即复用」处理，不重复 `set`。

## 理由与取舍

- **接受 W3 分析列出的代价**：W3 资产重做（verify 脚本 / 冒烟记录 / 截图 / README）；trace URL 需自建
  （后端模板）；Langfuse 控制台 cost 列消失（本地成本行本就自算，影响小）。
- **平移 W3 已验证的结构性资产**：单点埋点（唯一 LLM 出口）、`tokens_total` 对账、定价表、
  扁平轮次 span 设计、fail-silent 纪律、常量进代码——只换承载物，不重开已定设计。
- **控制新代价上限**：不引入 `opentelemetry-instrumentation-openai` 等隐式 wrap（避免重蹈
  monkey-patch 覆辙）；不追逐后端成本对账（成本以本地定价表为准）。
- **openai 锁暂留**：`<1.93` 的原始理由随包装器移除而消失，但解除需独立复评 + 回归（不阻塞迁移）。

## 影响

- 行为：默认关闭时零行为变化；开启后 span 树 / usage / 成本与 W3 语义对齐
  （`metadata.node` → `dr.node` 属性；generation → `chat <model>` span）。
- 依赖：`requirements.txt` 移除 `langfuse`（lock 文件待下次依赖更新统一重生成）；
  `openai` 锁暂留并在注释记录复评结论。
- 测试：`tests/test_observability.py` 重写（OTel 版）+ `test_arm1_run_status.py` /
  `test_egress_snapshot.py` / `test_status_naming.py` 适配；新增「引擎侧无 langfuse import」守卫。
- 文档：需求 3 追加反转注记（不删历史）；README / `.env.example` / 运维与法务文档口径切换；
  `tools/verify_otel_trace.py` 替代 `verify_w3_trace.py`。
- 未完成（随需求 33 收口）：真实冒烟 + 截图、自托管 Langfuse OTLP ingest spike、锁文件重生成。

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-10-11 | 初始记录：需求 33 建稿即拍板方向；实现随 `feature/180-otel-observability-migration` 落地 |
