# DeepResearch — 深度研究 Agent

基于 **LangGraph 多 Agent 编排 + 多跳检索 + RAG 多源融合 + 交叉验证防幻觉** 的深度研究系统。

输入一个研究主题，系统自动完成 **规划 → 多跳检索（网络 + RAG 私有知识库）→ 生成带引用报告 → 引用校验与多源印证** 的完整流程。

## 核心能力

| 能力 | 说明 |
|------|------|
| **多 Agent 编排** | Planner（分解子问题）→ Researcher（多跳检索）→ Writer（生成报告）→ Validator（引用校验），LangGraph 状态机驱动 |
| **多跳检索** | 基于"信息充分度"动态判断是否继续检索，上限 5 跳防死循环 |
| **RAG 多源融合** | 网络搜索（博查）+ 私有知识库（Qdrant 混合检索：向量 + BM25）双路召回 |
| **交叉验证防幻觉** | 引用存在性校验 + 关键论断多源印证 + 置信度分级 |
| **三层 LLM 分级** | fast（摘要）/ smart（写作）/ strategic（规划），初始全用便宜模型，可配置升级 |
| **评测体系** | 检索命中率 + 引用准确率 + 报告质量（LLM-as-judge）三重评测 |

## 架构

```
研究主题
  │
  ├─ [Planner]      分解为子问题（strategic LLM）
  │
  ├─ [Researcher]   对每个子问题动态多跳检索
  │     ├─ 网络搜索（博查，可切换 Provider）
  │     └─ RAG 知识库（Qdrant 向量 + BM25 混合检索）
  │     └─ 信息充分度判断 → 决定是否继续下一跳
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

## 设计取舍

- **成本优先**：初始阶段三层 LLM 全用便宜的 qwen-turbo/plus，后续可单独升级 strategic 到 qwen-max
- **rerank 先评测再定**：默认关闭，先立评测再决定是否引入（借鉴 wechatbot 的 rerank 负收益经验）
- **Validator 是差异化亮点**：主流 DeepResearch 项目普遍缺失图内 Validator 节点，本项目将其作为防幻觉核心

## 项目记忆（设计决策记录）

本项目建立**设计决策记录（ADR）机制**作为项目记忆，见 [docs/decisions/README.md](docs/decisions/README.md)。

**规范**：每次修复 bug 或优化设计，都必须新增一条 ADR，记录**背景（当时为什么这么设计）+ 设计策略（现在为什么改、怎么改、取舍）**，保持决策链完整。模板见 [docs/decisions/TEMPLATE.md](docs/decisions/TEMPLATE.md)。

初始设计决策见 [docs/decisions/0001-initial-design.md](docs/decisions/0001-initial-design.md)。
