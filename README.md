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
| **多跳检索** | 基于"信息充分度"动态判断是否继续检索，上限 5 跳防死循环 |
| **RAG 多源融合** | 网络搜索（博查）+ arXiv 学术检索 + 代码执行 + 私有知识库（Qdrant 混合检索）四路证据并行召回 |
| **交叉验证防幻觉** | 引用存在性校验 + 关键论断多源印证 + 置信度分级（W2：来源类型标注/失败隔离/运行溯源四桶） |
| **三层 LLM 分级** | fast（摘要）/ smart（写作）/ strategic（规划+裁决，W4 拆 planner/critic 分档可配强推理） |
| **全链路可观测** | Langfuse trace：7 节点 span（含 critic 循环逐跳）+ 每次 LLM 调用 generation（token/cost），CLI/Web 知情打印 + trace URL 回显 |
| **评测体系** | 检索命中率 + 引用准确率 + 报告质量（LLM-as-judge）三重评测 |

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

- Python 3.10+
- Qdrant（本地 6333 端口，或配置远程地址）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

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

**Web UI 方式：**
```bash
python -m streamlit run web/app.py
```

Web UI 支持：上传文档到 RAG 知识库、设置多跳深度、实时查看研究进度、输出带引用的报告。

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
├── web/app.py                # Streamlit Web UI
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
| 单元测试 | 79 项全绿 | 离线可跑，CI 自动轨验证 |
| 完成率 | 100%（20/20） | 20 条 dataset 全部产出可用报告 |
| 引用准确率 | 72%（机器口径）→ 83~92%（人工抽检修正区间） | 机器口径低估，真实区间需人工两级抽检修正 |
| 覆盖度 | 55.4% | 如实声明——"查得不够"是当前主要瓶颈（critic 早停 14/19 条） |
| 单轮成本 | ~¥0.48（qwen-plus 规划价） | 真实 API 非确定性，单轮数字需 ≥3 次重跑取均值 |

不吹指标。覆盖度 55% 就写 55%，引用准确率分机器/人工双口径——追问时有据可查。

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

## 项目记忆（设计决策记录）

本项目建立**设计决策记录（ADR）机制**作为项目记忆，见 [docs/decisions/README.md](docs/decisions/README.md)。

**规范**：每次修复 bug 或优化设计，都必须新增一条 ADR，记录**背景（当时为什么这么设计）+ 设计策略（现在为什么改、怎么改、取舍）**，保持决策链完整。模板见 [docs/decisions/TEMPLATE.md](docs/decisions/TEMPLATE.md)。

初始设计决策见 [docs/decisions/0001-initial-design.md](docs/decisions/0001-initial-design.md)。
