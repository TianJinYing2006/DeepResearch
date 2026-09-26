# DeepResearch — 深度研究 Agent

[![CI](https://img.shields.io/github/actions/workflow/status/TianJinYing2006/DeepResearch/ci.yml?branch=dev&label=CI&logo=github)](https://github.com/TianJinYing2006/DeepResearch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **GitHub 为权威源**；Gitee 仅作国内访问镜像（可能滞后）：[gitee.com/tian-jinying/DeepResearch](https://gitee.com/tian-jinying/DeepResearch)

基于 **LangGraph 多 Agent 编排 + 多跳检索 + RAG 多源融合 + 交叉验证防幻觉** 的深度研究系统。

输入一个研究主题，系统自动完成 **规划 → 多跳检索（网络 + RAG 私有知识库）→ 生成带引用报告 → 引用校验与多源印证** 的完整流程。

## 核心能力

| 能力 | 说明 |
|------|------|
| **多 Agent 编排** | Planner（分解子问题）→ Researcher（多跳检索）→ Writer（生成报告）→ Validator（引用校验），LangGraph 状态机驱动 |
| **多跳检索** | 基于"信息充分度"动态判断是否继续检索；全局预算 `max_total_hops=20`，每子问题跳数上限按实际子问题数动态切分 `ceil(20 / n)`（`config.per_subq_hop_cap=5` 仅在子问题数不可得时作静态兜底） |
| **RAG 多源融合** | 网络搜索（博查）+ arXiv 学术检索 + 代码执行 + 私有知识库（Qdrant 混合检索）四路证据并行召回 |
| **交叉验证防幻觉** | 引用存在性校验 + 关键论断多源印证 + 置信度分级（W2：来源类型标注/失败隔离/运行溯源四桶） |
| **三层 LLM 分级** | fast（摘要）/ smart（写作）/ strategic（规划+裁决，W4 拆 planner/critic 分档可配强推理） |
| **全链路可观测** | Langfuse trace：7 节点 span（含 critic 循环逐跳）+ 每次 LLM 调用 generation（token/cost），CLI/Web 知情打印 + trace URL 回显 |
| **评测体系** | 检索命中率 + 引用准确率 + 报告质量（LLM-as-judge）三重评测 |
| **故障可归因（W8）** | 工具/provider/RAG/内部错误均有明确 `failure_reason`；`run_status`/`invoke_status`/`metrics_status` 三层状态分层，失败 run 不会伪装成成功 |
| **可复现与产物治理（W8）** | 每轮 run 落盘配置快照、五开关生效值、`prompt_hash`、`scorer_version` 与 git 修订；质量闸出 `verdict` + ±stderr；评测产物白名单 ≡ git 跟踪集合 |

## 架构

```
研究主题
  │
  ├─ [Planner]      分解为子问题（strategic LLM）
  │
  ├─ [Researcher]   对每个查询并行全工具检索（W4）
  │     ├─ 网络搜索（博查）+ arXiv 学术检索（官方 API 直连）
  │     ├─ RAG 知识库（Qdrant 向量 + BM25 混合检索）
  │     └─ 代码执行（受限沙箱，计算型查询自动触发）→ 结果池择优 Top-10
  │     └─ [Critic] 信息充分度裁决（hard_gate 硬闸 + LLM）→ continue/revise/stop 条件边
  │
  ├─ [Writer]       基于研究发现生成带引用报告（smart LLM）
  │
  └─ [Validator]    引用存在性校验 + 多源印证 + 置信度（smart LLM）
```

## 快速开始

### 1. 环境要求

- Python 3.11 ~ 3.13（W8 Arm 3：不在该区间的解释器会在 import 时直接报错，见 `research_engine/__init__.py`）
- Qdrant（本地 6333 端口，或配置远程地址）

### 2. 安装依赖

```bash
python -m pip install -r requirements-lock.txt -r requirements-dev-lock.txt
```

> 请在独立虚拟环境中安装；不要用系统 Python 3.14。两份 lock 分别固定运行依赖与 pytest/ruff 开发依赖。

### 3. 配置

复制 `.env.example` 为 `.env` 并填入密钥：

```bash
cp .env.example .env
```

必填项：
- `DASHSCOPE_API_KEY`：阿里云百炼（LLM + Embedding）
- `BOCHA_API_KEY`：博查搜索（网络检索）

可选：
- `QDRANT_URL`：Qdrant 地址（默认 `http://127.0.0.1:6333`）
- `FAST_MODEL` / `SMART_MODEL` / `STRATEGIC_MODEL`：三层模型（默认 qwen-turbo / qwen-plus / qwen-plus）
- `PLANNER_MODEL` / `CRITIC_MODEL`（W4 分档）：规划与裁决各自独立模型；不设则回落 `STRATEGIC_MODEL`。演示强推理时：`PLANNER_MODEL=qwen-max`（规划只跑 1 次，成本增量 ≈ +¥0.007/run）；`CRITIC_MODEL=deepseek-r1` 注意裁决每轮 +10~30s 延迟——演示建议 `qwen-max` 够用。
- **W7 主链路行为开关**：`CRITIC_GAP_ENABLED`、`VALIDATOR_FIXES_ENABLED`、`VALIDATOR_ASSERTIVE_FILTER_ENABLED`、`WRITER_SECTIONED_FEED_ENABLED`、`VALIDATOR_TRIM_ENABLED` 当前默认均为 `true`。它们是主链路开关，不是可忽略的实验残留；当前默认先保留；代码去留将在 W8 固定证据池、独立裁判、同预算重测后裁定，详见 `docs/w7-switch-disposition.md`。

### 3.2 工具：arXiv 学术检索 + 代码执行（W4）

**arXiv**：零配置可用（官方 API 直连，无需 key；自动 3s 间隔限流）。查询命中计算/复杂度/数值关键词时自动触发代码执行。

**代码执行沙箱**（三层纵深，默认安全栈）：
- subprocess `python -I -E -S` 隔离执行（不加载 site-packages = 天然依赖白名单）+ `timeout=15s` + 输出 128KB 截断 + 并发上限 2；
- AST import 白名单：仅 `{math, statistics, itertools, functools, decimal, fractions, collections, typing, random}`；
- PEP 578 Audit Hook 运行时拦截（网络/进程/破坏操作拒绝；文件读写仅允许沙箱临时目录内——cwd 外**读**也拒，防偷读 `.env`）。
- 可选增强（默认关）：`CODE_EXEC_USE_JOB=true` 启用 Windows Job Object 进程级内存/CPU 配额（开源前需验证，见 DoD）。

**Semantic Scholar 引用补全**（可选）：设 `SEMANTIC_SCHOLAR_API_KEY` 后，arXiv 论文的 `citationCount` 回填进报告溯源（"只采不决策"——不加权证据排序）；无 key 整条静默跳过。

### 3.1 可观测性（Langfuse，可选）

> **三态开关**：`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` **三件套齐备即自动启用**（trace 上报）；设 `LANGFUSE_ENABLED=false` 可强制禁用（CI/单测默认已禁用，零外发）；缺任一 key 自动降级，主流程零影响。

启用后：
- CLI/Web 启动打印**知情行**（数据去向 + 脱敏策略）；
- CLI 运行结束回显 **trace URL**（Langfuse 控制台可回放完整 span 树）；
- 控制台展示每次 LLM 调用的 usage 与成本（Qwen 未内置定价时 cost 列为空，成本以本地守恒口径为准）。

脱敏默认策略：节点 span 只记录截断输入（≤200 字符）；LLM 调用正文单字段 >4000 字符截断；敏感替换默认关闭（避免误伤论文/代码中的数字信息），需要时设 `LANGFUSE_MASK_SENSITIVE=true`。

开源逃生门：若观测事件超限，设 `LANGFUSE_SAMPLE_RATE=0.1` 采样（默认 1.0 全采）。

### 4. 启动 Qdrant

```bash
# Docker 方式
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
```

### 5. 运行

**CLI 方式：**
```bash
python cli.py "2026 年 RAG 技术的最新进展"
```

**Web UI 方式（W9 呈现层：FastAPI + React/Vite + SSE）：**
```bash
# 后端（SSE 端口 8000）
uvicorn web.backend.main:app --host 127.0.0.1 --port 8000

# 前端（另开终端，Vite dev server 5173）
cd web/frontend && npm ci && npm run dev
```

打开 http://localhost:5173 。生产模式下由 FastAPI 直接托管 `web/frontend/dist/`，只需启动后端。

**Docker 方式（staging 骨架，P1 部署底座）：**
```bash
# 一条命令：PostgreSQL + Redis + 迁移 + API（前端构建产物打进镜像，由 FastAPI 托管）
docker compose -f docker-compose.staging.yml up -d --build
curl http://127.0.0.1:8000/api/health/ready     # {"status":"ready","checks":{"postgres":"ok","redis":"ok"}}

# 端口冲突时用环境变量覆盖（本机 5432/6379/8000 常被其他项目占用）
# DR_POSTGRES_PORT=15432 DR_REDIS_PORT=16379 DR_API_PORT=18080 docker compose -f docker-compose.staging.yml up -d --build
docker compose -f docker-compose.staging.yml down       # 停服；加 -v 连数据卷一起删
```

- 迁移**只前向**执行（`migrations/NNNN_slug.sql` + `schema_migrations` 记录），重复执行全部 `skip`（CI 锁幂等：第二次必须 `applied=0`）；
- 探针：`/api/health/live`（进程活着）与 `/api/health/ready`（PG/Redis **未配置时不算失败**，配置了但探不通返回 503）；
- CORS：不设 `DR_CORS_ORIGINS` 时仅允许本地 Vite（5173）；staging/生产**必须显式设置**；
- Qdrant 不在 compose 内：默认指向宿主机 `host.docker.internal:6333`，staging 用 `QDRANT_URL` 指向真实实例；
- 境外服务按推荐基线默认关闭（`LANGFUSE_ENABLED=false` / `ENABLE_ARXIV=false`）；Worker 镜像与队列在 P3 增加。

Web UI 支持：提交研究主题与运行选项（多跳深度、子问题数上限、搜索引擎、学术检索）、实时查看阶段进度与降级事件、
查看 token/cost、**随时取消**（节点边界协作式取消，实测停止耗时中位 14.4s / 最大 31.4s）、查看带引用的报告与引用溯源、
**后端导出报告**（正文 + run_id / run_status / 降级条数等审计元数据 + 引用清单）、**刷新页面恢复当前运行**、
**历史任务列表**（`GET /api/runs`，需配置任务库）、查看**运行摘要**（总耗时 / 完成节点 / 检索跳数 / 降级条目 / 报告字数 / 成本估算）。

> **断线重连语义**：短暂断网由浏览器 `EventSource` 自动重连，并带 `Last-Event-ID` 续传；
> 刷新页面凭 `sessionStorage` 里的 run_id + `GET /api/research/{id}` 状态快照 + **从 0 回放全部帧**恢复
> （回放已发生事件，**不产生新的 LLM 调用**）。**终局后保留**最近一次 run 的 id ⇒ 刷新仍能回看最终报告或错误卡；
> **可补发** = 进程存活期间产生的全部事件帧（内存保留，事件 id = 帧下标）；配置任务库（`DR_DATABASE_URL`）后，
> **进程重启也能补发** —— `GET /stream` 从 `run_events` 按同一序号回放后收口，历史报告可从产物表导出。
> **仍不可补发** = 跨标签页恢复（`sessionStorage` 按标签页隔离）；未配置任务库时行为与 D-19 相同（重启即失）。
>
> **资源边界（诚实口径）**：单次 run 的事件帧、结果与报告**常驻内存**，随 run 数线性增长；
> 配置任务库后同步落库（事件 / 终局状态 / 报告产物），重启后仍可查询与导出，**但任务不会被续跑**
> —— 单实例内存态执行的残留非终局任务在服务启动时被标记为 `LOST`（租约接管属 P3）。未配置任务库时进程重启即消失。
> 客户端断开只停止**投递**，不会杀掉后端运行（取消必须显式调用 `POST /cancel`）；
> SSE 等待由心跳（15s）兜底超时，不会永久占住执行器线程；工作线程为 daemon，三条终局路径均释放，
> 强制收口后线程随节点自然收尾退出。

> 事件语义对齐 [AG-UI](https://docs.ag-ui.com/)；取消采用**协作式**而非抢占式 —— 取消请求立即生效，
> 执行停止在下一个节点安全边界，取消后不再启动新的研究节点与 LLM 调用。详见 `docs/requirements/9-web-ui-rewrite.md`。
>
> ⚠️ W9 之前的 Streamlit 旧入口 `web/app.py` 已于 2026-09-23 删除；依赖与 lock 已同步清理，不再维护。

#### 运行护栏（P1，2026-09-24）

| 护栏 | 行为 | 默认值 / 开关 |
|------|------|---------------|
| 运行超时闸 | 单次 run 有墙钟时限，到点后在**节点边界**停止；`stop_reason=timeout`，**不记为故障** | 3600s，`DR_RUN_TIMEOUT_SECONDS` |
| 单进程并发限制 | 同时活跃 run 数封顶，超出返回 429（`code=concurrency_limit`） | 1，`DR_MAX_CONCURRENT_RUNS` |
| 强制收口宽限 | 协作式停止失效（节点内部挂死）时，传输层最多再等这么久就补 `RUN_ERROR(stop_forced)` 收口 | 60s，`DR_FORCED_STOP_GRACE_SECONDS` |
| 状态查询 | `GET /api/research/{run_id}` 返回内存态画像（状态、已跑时长、剩余时间、事件数、stop_reason） | —— |
| 结构化错误 | 所有 HTTP 错误与 `RUN_ERROR` 共用 `{code, message, component, node, detail, retryable, hint}` | —— |
| 报告导出 | `GET /api/research/{run_id}/report?format=md\|json` | —— |

⚠️ **超时与强制收口都是协作式的**：Python 线程无法被 kill，若某个节点内部（如一次 HTTP 调用）挂死，
闸只能在下一个节点边界生效；硬截止只保证**传输层**收口、客户端不再干等，后台线程可能仍在收尾。
这是语言级限制，不是实现偷懒。

强制收口与终局写入已在同一把锁下原子完成（ADR-0008）：收口一旦发生，后台线程迟到的报告 / 状态 / 事件帧
会被**完全丢弃**，不会出现「收口后又出报告」「RUN_FINISHED 与 RUN_ERROR 双终局」。

**浏览器 E2E（已接 CI）**：用 `DR_DEMO=1` 的假图跑真实 SSE 管线，10 条用例（主流程 / **刷新恢复** / **完成后续看** /
降级可见 / 导出 / 取消语义 / 结构化错误 + 重试 + **详情折叠** / 窄屏无横向滚动）约 41s。

```bash
cd web/frontend
npm ci
npm run build                      # 后端托管 dist/，E2E 打的是 8000 端口
DR_PYTHON=<项目 venv 的 python> npm run e2e
# 换浏览器：E2E_CHANNEL=msedge npm run e2e（默认用本机 Chrome，不下载浏览器）
```

CI 里的 `e2e` job 用**官方 Chromium**（`E2E_CHANNEL=chromium` + `playwright install --with-deps chromium`），
不依赖 runner 预装浏览器。**首轮实测**（run `36006923861`）：job 总耗时 **109s**（含 Python 依赖 + npm ci +
build + Chromium 安装），用例 **10 passed / 45.0s** —— 成本远低于接入前的预估（原估 4~6 分钟）。

## 目录结构

```
DeepResearch/
├── research_engine/          # 核心引擎
│   ├── graph.py              # LangGraph 状态机编排
│   ├── state.py              # Pydantic 类型化状态
│   ├── agents/               # 多 Agent 节点
│   │   ├── planner.py        # 分解子问题
│   │   ├── researcher.py     # 多跳检索（网络 + RAG）
│   │   ├── writer.py         # 生成带引用报告
│   │   └── validator.py      # 引用校验 + 多源印证
│   ├── rag/                  # RAG 模块
│   │   ├── ingest.py         # 文档解析、分块、向量化
│   │   ├── retriever.py      # 混合检索（向量 + BM25）
│   │   └── store.py          # Qdrant 封装
│   ├── search/               # 网络搜索（可切换 Provider）
│   │   ├── base.py           # Provider 抽象
│   │   └── bocha.py          # 博查实现
│   ├── llm/                  # LLM 封装
│   │   ├── client.py         # 百炼 Qwen 客户端
│   │   └── router.py         # 三层 LLM 分级
│   ├── context/              # 上下文管理（隔离 + 压缩）
│   └── eval/                 # 评测体系
│       ├── retrieval_eval.py # 检索命中率
│       ├── citation_eval.py  # 引用准确率
│       └── report_eval.py    # 报告质量 LLM-as-judge
├── web/                      # W9 呈现层（FastAPI + React/Vite + SSE，对齐 AG-UI）
│   ├── backend/
│   │   ├── main.py           # FastAPI 应用装配与 HTTP/SSE 端点
│   │   ├── agui.py           # AG-UI 事件编码 + 心跳帧
│   │   ├── runner.py         # 前台运行管理与协作式取消
│   │   └── demo_graph.py     # DR_DEMO=1 离线演示图（零 LLM）
│   ├── frontend/             # React + TypeScript + Vite + Tailwind
│   │   └── src/lib/progress.ts  # 分层进度（不做假进度条）
│   └── app.py                # ⚠️ 已废弃：W9 之前的 Streamlit 旧入口
├── tools/
│   ├── check_frontend_boundary.py  # CI 边界守卫：前端目录不得 import research_engine
│   └── migrate.sh            # 迁移执行器（只前向 + schema_migrations 记录，CI 锁幂等）
├── migrations/               # 数据库迁移（NNNN_slug.sql；业务表从 P2 起）
├── Dockerfile.api            # API 镜像（node 构建前端 + python 运行时，多阶段）
├── docker-compose.staging.yml # staging 骨架：PostgreSQL + Redis + 迁移 + API
├── cli.py                    # CLI 入口
├── config.py                 # 配置
└── requirements.txt
```

## 评测

```python
from research_engine.eval.retrieval_eval import RetrievalEvaluator
from research_engine.eval.citation_eval import CitationEvaluator
from research_engine.eval.report_eval import ReportEvaluator

# A: 检索命中率
RetrievalEvaluator().evaluate([("查询", ["期望关键词"])])

# B: 引用准确率
CitationEvaluator().evaluate(report, findings)

# C: 报告质量
ReportEvaluator().evaluate(topic, report)
```

### 评测跑批与产物纪律（W8）

正式评测会调用外部模型/API 并产生费用；在做版本比较前，应先冻结代码提交、配置和数据集，再运行固定规模的 baseline。评测产物默认写入 `results/`，该目录默认被 Git 忽略；只有被正式结论文档引用、并在 `.gitignore` 白名单中登记出处的产物才允许入库。

跑完评测后先检查白名单纪律：

```powershell
python tools/check_results_whitelist.py
```

不要直接手动删除、移动或重命名 `results/` 中的历史产物。W7 回填和 W8 台账都可能依赖 `raw/*.raw.json` 作为零成本复算证据；需要清理或归档时，先查看 `docs/eval-artifact-ledger.md` 并完成人工 review。

### W7 主链路开关（默认行为）

W7 的五个开关属于主链路行为选择，不是独立插件；未设置环境变量时均默认为 `true`。正式跑批或版本比较时，应把它们的有效值随 provenance 一起记录：`CRITIC_GAP_ENABLED`、`VALIDATOR_FIXES_ENABLED`、`VALIDATOR_ASSERTIVE_FILTER_ENABLED`、`WRITER_SECTIONED_FEED_ENABLED`、`VALIDATOR_TRIM_ENABLED`。

其中 `CRITIC_GAP_ENABLED=true` 可保留 coverage，但实测约增加 118% 的 steps；`VALIDATOR_FIXES_ENABLED` 与 `VALIDATOR_ASSERTIVE_FILTER_ENABLED` 默认保留已知缺陷修复；`WRITER_SECTIONED_FEED_ENABLED` 与 `VALIDATOR_TRIM_ENABLED` 暂维持开启，待 W8 重新测量后再裁定。不要在未记录开关值的情况下横向比较评测结果。

## 能力与限制（W8 实测口径）

> 这一节是**对外口径**：能说什么、不能怎么说，全部有实测依据。
> 判定过程见 [`docs/eval-w8-after-baseline.md`](docs/eval-w8-after-baseline.md)，
> 逐项验收证据见 [`docs/eval-w8-dod.md`](docs/eval-w8-dod.md)。

### 能做到（A 类确定性断言，零噪声，不依赖统计功效）

| 能力 | 实测证据 |
| --- | --- |
| **故障可归因率 0% → 100%** | 工具 / provider / RAG / 内部错误均有明确 `failure_reason`，枚举一处定义（16 条测试） |
| **状态分层** | `run_status` / `invoke_status` / `metrics_status` 语义分离；历史产物 dual-read 且**未被回填**（26 条） |
| **异常不伪装** | 失败的 run 不会伪装成成功或空结果；递归超限、异常退出均有契约（24 条） |
| **成本守恒** | 成本源缺失时显式降级并标记，**不静默显示 ¥0**（21 条） |
| **质量闸** | 每轮 run 落 `verdict` + `verdict_reasons` + ±stderr，阈值外置、只告警不阻断（20 条） |
| **可复现元数据** | 配置、五开关生效值、`prompt_hash`、`scorer_version` 随每轮落盘；历史缺失字段如实为「未记录」（36 条） |
| **产物治理** | 白名单集合 ≡ git 跟踪集合，逐条写明引用出处（13 条 + CI 步骤） |
| **运行稳定性** | after 基线 3 轮：**60/60 题完成、零异常**、`git_dirty=false` 三轮全中 |

上述 8 项即 W8 的验收口径。**它们与「平均质量是否变好」是两件事，必须分开陈述。**

### 不能这么说（配对统计：四指标全部不可判定）

20 题 × 3 轮同题配对对照（before 09-16 vs after 09-19，冻结代码 `f723c2d`，配置/题集/模型与 before 同构）：

| 指标 | before | after | Δ | SE | MDE | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| coverage | 44.9pp | 39.8pp | −5.1pp | 3.81 | 10.66 | 不可判定 |
| citation_accuracy | 75.2pp | 78.3pp | +3.1pp | 3.29 | 9.21 | 不可判定 |
| retrieval_hit_rate | 55.2pp | 52.7pp | −2.5pp | 1.39 | 3.88 | 不可判定 |
| steps | 9.4 | 8.2 | −1.2 | 0.48 | 1.36 | 不可判定（且按 D2 归因于 Arm 2 功能修复，不算质量证据） |

- ❌ **禁止**：「W8 让覆盖率提升/下降 Xpp」「W8 让引用准确率提升 3.1pp」「W8 让系统少跑 1.2 步」。
- ✅ **可以说**：「after 基线 coverage 39.8%（20 题 × 3 轮）」「四项均未达判定门槛，本实验不宣称平均质量提升或下降」。

### 已知限制

| 限制 | 说明 |
| --- | --- |
| **仪器噪声 > 效应** | coverage 的 run 内噪声 σ≈24pp，MDE(3v3)=10.66pp，而预期效应只有 4~10pp |
| **before 基线不可重采** | 代码已改 ⇒ `R_b=3` 固定；单独加 after 轮次最多把 MDE 再降 14%（**D-18：故不续跑**） |
| **引用裁判非独立** | `citation_judge_independent=false`：评测层直读主链路 validator 产物，不重跑独立复判 |
| **检索是活的** | planner 每轮子问题不同 ⇒ 证据池本身会变；配对只消掉题目效应，消不掉检索漂移 |
| **规模与语言** | 20 题中文数据集、单模型族（qwen-plus / turbo），结论不外推到其它语言或模型 |
| **五开关未消融** | 主链路 5 开关默认全开，其单独贡献未经对照测量（见「W7 主链路开关」） |

### 对外推荐表述（可直接引用）

> W8 完成了故障状态分层、工具失败原因结构化、质量闸、成本守恒、可复现元数据和评测产物治理。
> after 基线在 20 个问题上运行 3 轮，60/60 个题目实例完成且无异常。配对统计中 coverage、
> citation accuracy、retrieval hit rate 和 steps 均未达到预设判定门槛，因此本实验不宣称 W8
> 带来了平均质量提升或下降。W8 的主要收益是故障可归因、结果可审计和运行稳定性；after 侧轮次间
> 波动出现下降迹象，但由于只有 3 轮，仅作为待验证观察。

## 设计要点

> 技术事实摆台面，不堆形容词。以下四件套是本项目的差异化设计。

### 1. 决策循环在图里，不在 prompt 里

核心区别：不是"在 prompt 里让 LLM 自己决定继续还是停止"，而是用 LangGraph 的 `conditional_edge` 把 continue/revise/stop 三态路由暴露为**图结构**。

```
plan → research → critic ──(conditional_edge)──┐
                  │           ├─ continue → research（多跳检索继续）
                  │           ├─ revise   → plan（方向跑偏，重新分解）
                  │           └─ stop     → write → validate → END
```

硬闸四维（depth/frontier/replan/token）优先于 LLM 裁决，路由纯函数——LLM 说"继续"但硬闸说"超 20 跳了"就停。防幻觉不是靠 prompt 祈祷，是靠图结构兜底。

### 2. 防幻觉三件套

| 手段 | 实现 | 位置 |
|------|------|------|
| **Validator 图内节点** | 引用存在性 + 忠实度双层校验，不通过论断进附录（不删除，读者需看见才能理解"为何不可信"） | `agents/validator.py` + `render.py` |
| **引用溯源四桶** | 每条引用标注来源类型（web/rag/arxiv/code），可审计 | `state.py:Citation.source_type` |
| **多源印证** | 关键论断需 ≥2 独立来源支撑，置信度分级呈现 | `validator.py` 交叉验证逻辑 |

### 3. eval 诚实数字

| 指标 | 数值 | 口径说明 |
|------|------|----------|
| 单元测试 | **461 项全绿** | 2026-09-24 本机 Python 3.13.14 完整复验（零 LLM、零 key）；CI 继续覆盖 3.11/3.12/3.13。其中 Web 层 65 条 = 流式 16 + HTTP 8 + **P1 运行护栏 35**（含强制收口交错回归、取消/超时时序、线程与断开语义、运行级统计、422 结构化）+ **P1 部署底座探针 6**（live/ready/CORS，`tests/test_web_health.py`）；eval 侧 25 条（含 planner 治理聚合 4 条）；另有 `frontend` job 跑 `tsc --noEmit` + `vite build`，**浏览器 E2E 10 条**已接 CI（见下节） |
| 完成率 | 100%（**60/60**） | after 基线：20 题 × 3 轮，**零异常**（before 基线三轮里两轮各有 1 题 failed） |
| 引用准确率 | **78.3%** | 机器口径（LLM-as-judge）；人工抽检修正区间见 W7 结论文档 |
| 覆盖度 | **39.8%** | after 基线 3 轮均值（同题配对 n=18）；before 为 44.9%，**差值不可判定**，见下节 |
| 单轮成本 | **¥0.79~0.92 / 48~51 分钟** | 20 题、并发 3、qwen-plus + qwen-turbo；真实 API 非确定性，需多轮取均值 |

不吹指标。覆盖度 39.8% 就写 39.8%；before/after 的差值**不可判定**就写不可判定 —— 追问时有据可查
（完整判定过程见 `docs/eval-w8-after-baseline.md`）。

### 4. 与主流项目差异

| 维度 | 本项目 | open_deep_research | dzhng/deep-research |
|------|--------|--------------------|---------------------|
| Validator | 图内节点（双层校验） | 无 | 无 |
| 代码沙箱 | 三层纵深（subprocess+AST+Audit Hook） | 无 | 无 |
| 可观测 | Langfuse 全链路 trace | LangSmith（可选） | 无 |
| 量化评测 | 20 条 + 7 指标 + 双轨成本 | Deep Research Bench 100 题 | 无 |
| LICENSE | MIT | MIT | MIT |

## 设计取舍

- **成本优先**：初始阶段三层 LLM 全用便宜的 qwen-turbo/plus，后续可单独升级 strategic 到 qwen-max
- **rerank 先评测再定**：默认关闭，先立评测再决定是否引入（借鉴 wechatbot 的 rerank 负收益经验）
- **Validator 是差异化亮点**：主流 DeepResearch 项目普遍缺失图内 Validator 节点，本项目将其作为防幻觉核心

## 相关文章

- **《agentic loop 设计与 Critic 节点化踩坑》**
  - 掘金：https://juejin.cn/post/7647054707223511059
  - 知乎：https://zhuanlan.zhihu.com/p/2080442055638127641

## 项目记忆（设计决策记录）

本项目建立**设计决策记录（ADR）机制**作为项目记忆，见 [docs/decisions/README.md](docs/decisions/README.md)。

**规范**：每次修复 bug 或优化设计，都必须新增一条 ADR，记录**背景（当时为什么这么设计）+ 设计策略（现在为什么改、怎么改、取舍）**，保持决策链完整。模板见 [docs/decisions/TEMPLATE.md](docs/decisions/TEMPLATE.md)。

初始设计决策见 [docs/decisions/0001-initial-design.md](docs/decisions/0001-initial-design.md)。
