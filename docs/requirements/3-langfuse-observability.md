# 需求-3-langfuse-observability

> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/U78LdzpeGoylU1xR4QHcOuyXnIg（DeepResearch 需求文档 / 第三周需求文档）
> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 重排说明：原计划（deepresearch-plan.md §4）W3 即 Langfuse 接入；W2 已先行（引用溯源可审计化，`2-provenance-auditable-reporting.md`）。本需求聚焦「开发者侧可观测」，与 W2「用户侧可审计」正交不互斥。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | #3 |
| 标题 | Langfuse 全链路可观测（LLM Observability） |
| 优先级 | P0 |
| 状态 | 草稿（2026-09-03 grill 定稿，Q1~Q7 全部拍板，待实现） |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #3（待建） |
| 关联 PR | |
| 创建 / 更新 | 2026-09-03 |

## 2. 问题背景
- 需求文档（v1.0）NFR1「可观测」缺失，`deepresearch-plan.md` 将其列为**简历四项缺口之一**（"主流可观测工具链"）。
- 对照项目 WeChatBot（Java/Spring AI）已通过 **OTel** 接入 Langfuse（Java 侧官方 SDK 已弃用，OTel 是唯一正路）；DeepResearch 为 **Python/LangGraph**，可观测路径不同，需在设计期定夺。
- 面试/博客叙事目标：「plan→research⇄critic（循环）→write→validate→render 全链路 trace 可回放」——**trace 必须能看出循环**，这是 agentic 叙事的一部分，不是单纯的日志。

## 3. 需求分析
- 目标：单次 research 生成**一条完整 trace**，覆盖 7 节点 × N 跳循环 + 全部 LLM 调用（含 token/成本），可在 Langfuse 控制台回放。
- 成功定义（可量化）：
  - ① Langfuse 控制台能看到一次研究的完整 span 树（plan → research「多跳」⇄ critic → … → render）；
  - ② 每次 LLM 调用有 generation 记录（model / prompt / completion / usage）；
  - ③ trace 附带研究元数据（topic / thread_id / depth / critic 决策次数 / token_used）；
  - ④ 主流程零感知：Langfuse 不可达不阻塞、不报错；离线单测可完全关闭埋点。

## 4. 当前设计（代码现状）
- `requirements.txt`：无 `langfuse`，全项目无可观测依赖。
- `research_engine/`：无 `observability.py`；全项目无 trace/span 埋点。
- `config.py:L19-35`：`LLMConfig` 仅 DASHSCOPE 配置，无 langfuse 段。
- `research_engine/llm/client.py:L37-60`：`LLMClient.chat()` 是**全项目唯一 LLM 出口**（planner/researcher/critic/writer/validator/compress 全走它），且 `:62-70 _accumulate_usage` 已把 token 累计进 `state.token_used`——单点埋 generation 的天然锚点。
- `research_engine/graph.py:L52-78`：7 节点（plan/research/critic/revise/write/validate/render）+ `:245-264 run()` 统一入口（已生成 `thread_id`，`recursion_limit` 已设）。
- `research_engine/state.py:L50-85`：`token_used` / `depth` / `reflection_log` / `progress` / `critic_signal` 齐备，可作 trace metadata 来源。
- **决策约束（ADR 级）**：LLMClient 保持自研薄封装、不迁 LangChain Runnable（W1 grill Q6-B 已锁定）→ 依赖 LangChain CallbackHandler 的自动追踪对 LLM 内部不可用，埋点需另行设计。

## 5. 优化方案（草案，待 grill 逐项拍板）
- `requirements.txt` 加 `langfuse`。
- 新增 `research_engine/observability.py`：langfuse 实例（惰性单例）、节点 span 装饰器、LLM generation 埋点、trace metadata 注入、flush 辅助。
- `config.py` 加 `LangfuseConfig` 段；`.env.example` 补 `LANGFUSE_*`（`.env` 保持本地私有，不入库）。
- `graph.run()` 外层包 trace：一次研究 = 一条 trace（thread_id 作 trace id），节点 step 映射子 span。
- `cli.py` / `web/app.py`：结束前 flush，可选回显 trace URL。
- 以下为 **2026-09-03 grill 七题全部拍板后的定稿方案**（Q1~Q7，逐题记录见 `.workbuddy/design-grill.md` §W3）：

  - **TBD-1 接入方式（✅ grill Q1 终判=B'，2026-09-03 拍板，历经 A→B' 两轮）**：**混合路线 = `langfuse.openai` OpenAI 包装器 + `Langfuse.start_as_current_observation()` 显式 span 上下文**；**维持 LLMClient 自研薄封装（Q6-B 不推翻），LLMClient 代码零改动**。
  - **LLM generation**：`from langfuse.openai import openai`（模块级 drop-in，实测 langfuse 4.15.1）——模块导入时全局 wrap openai 的 `chat.completions.create`，自动记录 prompt/completion/latency/errors/**usage + cost(USD)**；LLMClient 内原版 `OpenAI(base_url=...)` 实例化照常命中，**observability.py 一处 import 覆盖所有 agent**。
  - **节点 span**：**弃用 `@observe` 装饰器**（3.x 装饰器自动把函数入参/返回值整包塞进 span，含 report 全文/网页正文 → 体积爆炸 + 脱敏困难）；改用 **4.x `client.start_as_current_observation(name=..., type="SPAN")`** 显式 with 块，只放必要字段（截断版 input + metadata），脱敏源头可控。
  - **注意事项（设计约束）**：① **enabled 才 import `langfuse.openai`**——机制是全局 monkey-patch，未启用/单测环境禁止导包，防 double-trace 泄漏；② **版本锁定 `langfuse>=4.15,<5`** + 冒烟脚本（trace 落库 + generation 挂树）进 DoD；③ **API 映射**：用户初稿 `langfuse_context.start_span` 系 3.x API（4.15.1 已移除 `langfuse.decorators`/`langfuse_context`），以 `start_as_current_observation` 为准；④ 生成自动挂载依赖 context 传播（`get_current_trace_id`/`get_current_observation_id` 实测存在），必须在 `start_as_current_observation` 的 with 块内调用 LLM。
  - CallbackHandler 出局（对自研 LLM 的 generation 缺位）；OTel OTLP 出局（Python 侧样板过重；WeChatBot 走 OTel 系 Java SDK 弃用所致，Python 无此限制）。
  - **TBD-2 trace 结构（✅ grill Q2 终判=A1'+B1+C1'，2026-09-03 拍板）**：
  - **粒度与标识（A1'）**：1 run = 1 trace；**trace id = `{thread_id}_{timestamp}_{uuid4[:8]}`（每次运行唯一）**——Langfuse 按 id upsert，同 `thread_id` 重跑（Web 追问/调试显式传 id/未来续跑）会**覆盖历史 trace**，唯一化后经 `metadata.thread_id` 过滤仍可查看同 thread 全部历史版本（个人项目迭代对比的生命线）；trace name = `deepresearch: <topic>`（截断 80 字符）。
  - **span 命名（B1）**：中文展示名 + `metadata.node` 英文键（如展示「检索 R1」/「裁决 R1」，`metadata.node="research"`）——人读中文、机器按英文过滤。
  - **循环呈现（C1'）**：**扁平兄弟 span + 轮次前缀命名 `R1-检索`/`R1-裁决`/`R2-检索`…**，**不用容器嵌套**——LangGraph 循环系节点函数交替调用，容器 span 需跨节点生命周期（只能包整个 `run()` 或 hack state 存 span id，均不干净）；轮次编号放前缀使控制台按名称排序即可圈出单轮全貌；实测 2~4 轮平铺 4~8 个 span 视觉可接受。
  - **metadata 约定**：trace 带 `topic/user_instructions(截断)/model/thread_id`；span 带 `node/depth/sq_id/query/signal`。
  - **TBD-3 LLM generation 埋点与 usage 对账（✅ grill Q3 终判=D'，2026-09-03 拍板）**：
  - **埋点位置**：已由 Q1 解决——`langfuse.openai` 包装器在方法层全局 wrap，任一 agent 的 LLM 调用自动成 generation（含 usage/cost），**无需任何节点级/单点埋点代码**。
  - **对账机制（D' 运行时无条件计数）**：`LLMClient` 增加实例级计数器 `tokens_total`——`_accumulate_usage` 内**无条件** `self.tokens_total += total`（不管 `state` 是否传入）；`state.token_used` 只累加传 state 路径（Q6-B 原逻辑不变）。跑完/CLI/冒烟脚本输出差值 `tokens_total - state.token_used`：**>0 即命中"某条调用路径漏传 state"**，报告提示并定位修复（不 fail）。
  - **否决项**：① 方案 A 事后软对账（可作冒烟补充，但无"必抓漏"保障：漏传路径不对比发现不了）；② 方案 D 运行时双写审计（**死代码**：usage 系 `client.chat` 单点原子累加，`expected_inc` 与 `actual_usage` 同源恒等，`audit_buffer` 恒为 0；且 Langfuse 异步批量上传，运行中读不到实时合计，无法做本地 vs Langfuse 的实时比对）。
  - **TBD-4 开关与脱敏（✅ grill Q4 终判，2026-09-03 拍板）**：
  - **开关：三态自动判定**——① `LANGFUSE_ENABLED=false` 强制禁用（逃生门，不 import 包装器、零外发）；② 三件套（PUBLIC_KEY/SECRET_KEY/HOST）齐备且非 false → **自动启用**（配 key 即用，个人项目/开源模板开箱即用；CI 不配 key 天然降级）；③ 缺任一 → 自动降级（等同禁用，零开销零外发）。
  - **知情打印（自动启用的补偿）**：CLI/Web 启动时打印：`Langfuse 可观测性：已启用/已禁用 (host=...)` + 开关状态 + 脱敏策略（长度裁剪 4000 / 敏感替换 关）——使用者（含面试投屏）一眼看到数据去向与处理方式。
  - **脱敏：两级防线**——① 节点 span 由 Q1 B' 显式控制（只放截断 input + metadata，固化）；② **generation（prompt/completion）统一过 `Langfuse(mask=回调)`**：单字段 **>4000 字符截断**（网页正文进 prompt 前已被 `ContextManager.compress` 压缩，超长兜底极少触发）；**敏感正则替换默认关闭**（`MASK_SENSITIVE` 配置位默认 false——个人项目无涉密数据，开替换反而误伤论文/代码里的数字信息；留位给开源用户）。
  - **实现支座（实测 langfuse 4.15.1）**：`Langfuse.__init__` 原生参数含 `mask` / `mask_otel_spans` / `tracing_enabled` / `sample_rate`——官方支座齐备，不造轮子。
  - **TBD-5 失败降级与 flush（✅ grill Q5 终判，2026-09-03 拍板）**：
  - **双层 fail-silent**：① Langfuse SDK 自带异步批量 + backoff 重试，不阻塞主线程；观测失败不吞业务异常（包装器先记 error generation 再 re-raise 原异常，实测 `openai.py:1420` 附近）；② 业务层 `get_langfuse()` 惰性单例——未启用/构造失败返回 `None`，所有调用点 `if lf is not None:` 防御。观测是旁路，断网照常出报告。
  - **flush 时机**：**CLI 退出前显式 `lf.flush()`（尽力而为，SDK 内部超时兜底，不阻塞退出）**；**Web（Streamlit 常驻）不 flush**（后台线程自然上传，避免每次 rerun 挂死 30s）。
  - **URL 回显（执行细节修正版）**：observability 提供 `create_trace_id(thread_id) = {thread_id}_{ts}_{uuid4[:8]}`；**CLI 在调用方自持 thread_id/trace_id，`try:/finally:` 中 finally 里 flush + 打印 trace URL**——无论 `graph.run()` 成功或异常（含硬闸 `BudgetExceededError`）均可回显；trace id 自生成天然在手，**不依赖 Langfuse 上下文查询；弃用外部补丁中的 `langfuse_context` 3.x API（4.15.1 实测不可导入）**；异常时已创建 span 由 with 自动 end/标 error，trace 含失败步骤可投屏复盘。
  - **单测三重隔离**：① `tests/conftest.py` 强制 `LANGFUSE_ENABLED=false`；② 禁用态不 import `langfuse.openai`（防全局 patch 泄漏）；③ 可断言 `observability.get_langfuse() is None`。
  - **TBD-6 成本与体积（✅ grill Q6 终判，2026-09-03 拍板）**：
  - **采样率**：**默认 `sample_rate=1.0` 全采**；`Langfuse(sample_rate=env("LANGFUSE_SAMPLE_RATE", 1.0))` 为开源预留逃生门（README 说明，届时用户自行调 env，代码不改）。个人阶段全量数据是调试/面试命脉，免费层（单次 ~70 观测 × 每日数次）远未触顶。
  - **成本口径（双轨）**：CLI 以 `state.token_used` 为精确基准 + **config.py 分层定价表**（fast/smart/strategic 各配 input/output 单价，**默认值以阿里云官网为准、注释标明"价格有时效，失效即更新"**）+ 保守上界公式（假设全为 output token，**永不低估**，呼应硬闸保守精神）；**展示以人民币为主**（如 `≈ ¥0.40`，中国面试官视角）；Langfuse 控制台 cost **有则显、无则忽略，不花时间配 Qwen 定价表**。归属：定价表与换算落 W3，**W5 eval 复用同一张表算"平均 token 成本"指标**，不重复造轮子。
  - **体积闭环**：节点 span input = query/topic **≤200 字符**、output 一句话摘要（Q2 补齐的明确规格）；generation 由 Q4 mask 单字段 4000 字符兜底——**观测 payload 恒定，不随网页正文长度膨胀**。
  - **TBD-7 配置与演示资产（✅ grill Q7 终判，2026-09-03 拍板）**：
    - **`LangfuseConfig` 7 字段**：`public_key` / `secret_key` / `host`（三件套，缺任一自动降级）/ `enabled`（`Literal["auto","true","false"]`，`false` 强制禁用、`auto` 由三件套判定）/ `sample_rate`（默认 1.0）/ `mask_sensitive`（默认 false）/ `truncate_len`（4000）。**截断长度（4000 / span 200）作为常量放 `observability.py` 不开放成配置**——开关类进 config、策略常量进代码，字段不膨胀。
    - **`.env.example` 6 变量**：`LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST / LANGFUSE_ENABLED / LANGFUSE_SAMPLE_RATE / LANGFUSE_MASK_SENSITIVE`，各带一行注释（如"三件套齐备即自动启用观测"）。
    - **对外口径（W6 衔接）**：README 新增「可观测性」小节（三态开关 + 知情打印 + 脱敏默认 + 逃生门）；面试演示资产 = CLI 跑一次 + 控制台 span 树/usage 列截图 + 失败案例（硬闸/校验失败 trace）；简历回填（**落地才写**，吸取 WeChatBot 误标教训）：`Langfuse 全链路 trace（7 节点 span + LLM generation + usage/cost 对账）`。

## 6. 设计策略
- 复用 W1 已锁定的 `token_used`（Q6-B）：Langfuse usage/cost 与 state 计数互为对账，支撑"硬闸靠可观测数据"的叙事。
- 保持 LLMClient 薄封装（ADR：不迁 Runnable，Q6-B）：**埋点以 OpenAI 包装器（`langfuse.openai` 全局 wrap）+ 显式 span 上下文注入，LLMClient 代码零改动**——仅 observability.py 一处 import 即可接住全部 agent 的 LLM 调用。
- **enabled 才 import**：`langfuse.openai` 为全局 monkey-patch，未启用/测试环境不导包（隔离在 observability.py 内）。
- 与 W2 正交：observability 不触碰 report/citation 数据流，零回归面。

## 7. 验收标准（DoD）
- [ ] **Q1**：`observability.py` 落地 `langfuse.openai` 包装器 + `start_as_current_observation` 显式 span；`enabled` 才 import 包装器（防全局 patch 泄漏）；版本锁定 `langfuse>=4.15,<5`；**LLMClient 代码零改动**（含 generation 自动记录 usage/cost）
- [ ] **Q2**：单次 research = 1 trace：`create_trace_id(thread_id)` 唯一化 + `metadata.thread_id` 关联；span 中文展示名 + `metadata.node` 英文键；循环 span `R1-检索`/`R1-裁决`… 扁平兄弟前缀分组
- [ ] **Q3**：`LLMClient.tokens_total` 无条件计数；跑完`差值 = tokens_total - state.token_used` 报告命中漏传 state 路径（>0 提示，不 fail）
- [ ] **Q4**：三态开关（ENABLED=false 强制关 / 三件套齐自动启用 / 缺 key 自动降级）+ CLI/Web 启动知情打印；`mask` 回调：generation 单字段 >4000 截断、敏感替换默认关（`MASK_SENSITIVE=false`）
- [ ] **Q5**：fail-silent 双层（`get_langfuse()` 返回 None 时调用点防御）；CLI 退出前 flush + finally 兜底回显 trace URL（成功/异常均可用）；conftest 三重隔离（pytest 全绿且零外发）
- [ ] **Q6**：`sample_rate = env(LANGFUSE_SAMPLE_RATE, 1.0)`；CLI 成本展示 = config 分层定价表（官网价、标注时效）+ 保守上界换算，**人民币为主**
- [ ] **Q7**：`LangfuseConfig` 7 字段 + `.env.example` 6 变量 + README「可观测性」小节
- [ ] **冒烟（实测）**：一次 research 后 Langfuse 控制台可见完整 span 树（含多轮 critic 循环）；generation 挂树；usage 列有值；trace URL 可回显
- [ ] **离线**：不配 key 跑 pytest 全绿（测试环境零外发零报错）；Langfuse 不可达时主流程零影响（断网出报告）

## 8. 影响范围与风险
- 动：`requirements.txt`、`config.py`、`research_engine/llm/client.py`（埋点）、`graph.py`（trace 包裹）、`cli.py`、`web/app.py`、`.env.example`；新增 `research_engine/observability.py`、`tests/test_observability.py`。
- 风险①：埋点若阻塞主流程 → 必须 fail-silent（try/except 全覆盖 + 可整体禁用）。
- 风险②：测试误外发 → enabled 默认关 + 单测显式禁用。
- 风险③：请求体含敏感内容（用户 query）→ 脱敏策略（TBD-4）未定时不开启完整 body 记录。

## 9. 测试策略
- 单测（新增 `tests/test_observability.py`，全离线、不配 key）：
  - 三态开关判定（ENABLED=false 强制禁用 / 三件套缺一自动降级 / 齐备自动启用）与 `get_langfuse()` 惰性 None 语义（Q4/Q5）
  - `create_trace_id(thread_id)` 唯一性（同 thread 两次调用不同 id）+ `metadata.thread_id` 关联（Q2）
  - `mask` 回调：长字段截断至 4000、敏感替换默认不触发（Q4）
  - `LLMClient.tokens_total` 无条件累计（state=None 也记）与差值报告（Q3）
  - conftest 强制禁用：断言可写 `observability.get_langfuse() is None`（零外发，Q5）
- 集成/冒烟（配真实 key，本地 .env）：
  - `python cli.py "<topic>"` 跑通后：CLI 打印 trace URL（flush 成功、finally 兜底含异常路径）+ 成本行（本地定价表换算）
  - Langfuse 控制台人工核对：span 树含 `R1-检索/R1-裁决…` 循环、generation 挂树、usage 列有值、metadata.thread_id 可过滤出同 thread 全部历史
  - 断开网络跑一次：主流程零影响、出报告、无 trace（或 error generation）——fail-silent 验收
- 回归：现有 pytest 23 项全绿（埋点仅增量、LLMClient 零改动）

## 10. 变更记录
| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-03 | 优化 | 补齐 NFR1 可观测缺口（简历四项缺口之一） | 建 W3 需求：Langfuse 全链路可观测（草稿，TBD-1~7 待 grill 拍板） | 本文档 |
| 2026-09-03 | 设计定稿 | grill Q1 首判 A；用户提出替代方案 B'（langfuse.openai 包装器 + 显式 span），实测验证后改判 | **TBD-1 终判=B'**：`langfuse.openai` 包装器（自动 usage+cost）+ `start_as_current_observation` 显式 span；`@observe` 弃用（入参/返回值全塞 span 难脱敏）；LLMClient 零改动；enabled 才 import + 版本锁定 + 3.x API 映射三条约束入库 | 本文档 |
| 2026-09-03 | 设计定稿 | grill Q2 拍板（采纳用户升级建议 A1'+C1'） | **TBD-2 终判=A1'+B1+C1'**：trace id 唯一化 `{thread_id}_{ts}_{uuid4[:8]}` + `metadata.thread_id` 关联防同 thread 覆盖；span 中文名+英文键；循环用扁平兄弟 + 轮次前缀命名（`R1-检索`/`R1-裁决`）取代容器嵌套（LangGraph 节点函数模型下容器需跨节点生命周期，不可干净实现） | 本文档 |
| 2026-09-03 | 设计定稿 | grill Q3 拍板（评估用户方案 D 后改判 D'） | **TBD-3 终判=D'**：`LLMClient.tokens_total` 无条件计数 + 跑完差值报告（>0 即命中漏传 state 路径）；方案 D 运行时双写审计在单点累加架构下恒等=死代码，且 Langfuse 异步上传无法运行中实时比对，否决 | 本文档 |
| 2026-09-03 | 设计定稿 | grill Q4 拍板 | **TBD-4 终判**：三态开关（有 key 自动启用 / `ENABLED=false` 强制关 / 缺 key 自动降级）+ 启动知情打印；两级脱敏（节点 span 显式控制 + generation `mask` 回调 4000 字符裁剪、敏感替换默认关、留 `MASK_SENSITIVE` 位）；`mask/mask_otel_spans/tracing_enabled/sample_rate` 官方支座实测可用 | 本文档 |
| 2026-09-03 | 设计定稿 | grill Q5 拍板（含外部补丁审视修正） | **TBD-5 终判**：双层 fail-silent（SDK 异步 backoff + `get_langfuse()` 惰性 None 防御）；CLI flush/Web 不 flush；CLI 自持 `create_trace_id(thread_id)` + finally 兜底 URL 回显（弃 3.x `langfuse_context`）；conftest 三重隔离 | 本文档 |
| 2026-09-03 | 设计定稿 | grill Q6 拍板（含外部建议 B1' 审视修正） | **TBD-6 终判**：默认全采 + `LANGFUSE_SAMPLE_RATE` 逃生门；双轨成本口径（`state.token_used` + config 分层定价表按官网价维护标注时效 + 保守上界/人民币为主；W5 复用同表）；体积闭环（span input ≤200 字符 + mask 4000 兜底，payload 恒定） | 本文档 |
| 2026-09-03 | 设计定稿 | grill Q7 拍板，7 项待定项全部清零 | **TBD-7 终判**：`LangfuseConfig` 7 字段（截断常量进代码不进配置）+ `.env.example` 6 变量 + README「可观测性」/面试演示资产/简历回填口径（落地才写）；DoD §7 与测试策略 §9 按 Q1~Q7 完整填充 | 本文档 |
| 2026-09-03 | 实现完成 | grill 定稿后落地（并行会话完成，本会话验证） | 实现 commit `7b61ab3`(dev)：observability.py(214 行) + config `LangfuseConfig`/分层 `pricing` + `LLMClient.tokens_total`(Q3 D') + graph 7 节点 span 包裹(C1') + cli 收尾(Q5/Q6) + conftest 三重隔离 + test_observability 15 项；**pytest 38 passed**；两处 4.x 落地修正：trace id 用官方 `create_trace_id(seed=...)` 生成 32-hex（文档原拼串含下划线不合规）、mask 回调签名 `(*, data)` | `7b61ab3` |

## 11. 实现与冒烟记录（2026-09-04）
- 实现：commit `7b61ab3`（dev，pytest 38 全绿）；README「可观测性」小节见 `e9722e3`；缺 key 降级冒烟通过（知情打印已禁用 + fail-silent）。
- **真实 trace 冒烟（LANGFUSE_* 三件套 → jp.cloud.langfuse.com）**：✅ PASS——trace `093f7bda598843025160a16161802c28`（32-hex，name=`deepresearch: 2026年RAG技术进展`）；**span 序列完整**：规划 → R1-检索 → R1-裁决 → R1-修订 → R2-检索 → R2-裁决 → 写作 → 校验 → 渲染（17 观测点 = 10 span + 5 generation）；CLI 回显 trace URL + 成本行 ≤¥0.32（26,972 tokens）+ 对账行一致；引用校验 43/43 双口径通过；运行溯源 2 跳/revise 1/replan 1/web 8/rag 6。验证脚本 `tools/verify_w3_trace.py`（解析日志 → Langfuse API 核对 → 断言）。
- **⚠️ 待办 W3.2（真实冒烟暴露的两个实现偏差）**：
  1. **generation 正文未记录**：Langfuse API 侧 5 个 generation 的 `input/output` 均为 2 字符（疑似 `{}`）——`langfuse.openai` 包装器未抓到 prompt/completion（Q1 承诺"自动记录 prompts/completions"未兑现）；面试若展示"每次调用的输入输出回放"会缺料。应对：查包装器参数解析（openai v1 request payload 结构）或降级为 client.py 单点手动 `create_generation` 兜底。
  2. **usage 拆分/计量异常**：每个 generation `input:0 output:0 total>0`，且 5 个 total 合计 54,480 = 本地 27,240 的 **2 倍**——包装器对 openai v1 `usage`（prompt_tokens/completion_tokens/total_tokens）解析不兼容。影响面：**成本展示走本地口径（Q6 已定，不受影响）**、硬闸走本地（不受影响）；仅 Langfuse 控制台 usage/cost 列不准（面试演示以本地 + trace 结构为主即可）。应对：升级/降级 langfuse 复测，或 client 层补充修正。