# 需求 23：知识库增强——可演进、可评测、可追溯的 RAG 知识库

> 状态：**已合**（PR #107 squash `d22a882`；CI 全绿：lint-and-test 3.11/3.12/3.13 + frontend + e2e + infra；
> 迁移 0019 已在 staging 幂等实测；部署按约定在 A 批次全部完成后统一执行）。
> 父需求：`docs/requirements/21-product-modules-completeness.md` §4-D / §5-A2（批次 A2，范围按本文件扩充）。
> 定位提升：从「上传通道增强」升级为**可演进、可评测、可追溯**的 RAG 知识库——
> **以结构化解析快照为基础，将索引作为可替换的派生产物，通过版本切换保障升级，
> 通过证据定位保障引用，通过消融评测决定检索策略。**
> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/J1EndzsL2o2B3Wx94EFchM8Nn5f（「需求设计文档：知识库增强」修订版）。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 23 |
| 标题 | 知识库增强——可演进、可评测、可追溯的 RAG 知识库 |
| 优先级 | P1 |
| 状态 | 已合 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #106（已关） |
| 关联 PR | #107（squash `d22a882`） |
| 创建 / 更新 | 2026-10-03 |

## 2. 问题背景：现状审计（现状事实 → 影响 → 本次决策 → 验收证据）

### 2.1 设计矛盾（本次修订要解决的核心）

原草稿写「只保留 `rag_chunks`，重索引不重解析」——这与「分块 v2 全量重建」冲突：
旧 chunks 已丢失段落、标题与格式信息，加入 overlap 后也无法拼回原文。**分块结果的存储
不能替代解析结构的存储**；否则「重索引」只能在错误的分块上重嵌入，分块器无法演进。

### 2.2 审计矩阵

| # | 现状事实（代码证据） | 影响 | 本次决策 | 验收证据 |
|---|---|---|---|---|
| F1 | 融合实为「向量优先、BM25 垫底截断」：向量取 `top_k*2` 去重后先占满 `top_k`，BM25 追加即被截掉（`research_engine/rag/retriever.py:136-152`） | **hybrid 名存实亡**，关键词精确匹配能力丢失 | 双路召回（各 top-20）+ **RRF（k=60 初值）**，身份级去重 | 消融矩阵 dense / BM25 / RRF |
| F2 | `chunk_overlap=100`、`use_rerank=False` 全仓零引用（`config.py:92/94`） | 分块无重叠、重排从未实施；「配置即真相」失真 | 重叠并入分块 v2；rerank 接线并纳入可配开关 | 消融 v1/v2、RRF / RRF+rerank |
| F3 | 分块=按 `\n\n` 段落贪心打包到 800 字符（`research_engine/rag/ingest.py:109-122`）：无结构感知、超长段落整段成块、无 overlap | 检索粒度不稳；标题与表格语境丢失 | 分块 v2：token 预算 + 结构保真（§4） | 跨块 / 表格类查询 Recall@20 |
| F4 | 融合按**文本**去重；BM25 结果只有 `source` 文件名（`retriever.py:139-151`） | 同文多出处丢来源；引用不可定位 | 统一结果契约（chunk_id 级身份与溯源，§5.1） | 引用支持率 + 定位抽查 |
| F5 | BM25 语料按作用域进程内缓存（`retriever.py:48-60`），无失效钩子 | 上传 / 删除 / 重建后可见性无保证；**删除后可能仍从缓存命中** | 缓存键含作用域 + 索引修订号；写路径主动失效（§5.3） | 「删除后不可检索」测试 |
| F6 | 原设计「只存 chunks、不存解析结构」与「先删后建」重建：重建中断存在不可检索窗口；旧 chunks 无法可靠重分块 | 升级不可恢复；**幂等≠可恢复** | 三层数据 + **版本化重建**（§3） | 中断恢复演练 + 版本切换测试 |
| F7 | 引用只有 `rag:<filename>`（`research_engine/agents/researcher.py:161`） | 无法回看证据；页码 / 表格级溯源缺失 | chunk 携带 locator（章节 / 页码 / sheet / 行范围），前端可回溯 | 引用回溯 E2E |
| F8 | 检索评测仅关键词命中率（`research_engine/eval/retrieval_eval.py`） | 无法支撑「默认开 rerank / 换分块」类决策 | 分层评测 + 消融矩阵 + 决策规则（§8） | 评测报告与开关决策记录互链 |

### 2.3 范围判定

- **本期完成**：管理面（预览 / 整理 / 容量）+ 检索质量（融合修复 / 分块 v2 / rerank 接线 / 证据组装）+ 归因评测；
- **明确不做**：网页 URL 抓取、图片 OCR、GraphRAG、contextual retrieval（LLM 逐块上下文）——见 §9 演进路线；
- **迁移编号**：0019（0018 已被需求 22 占用）。

## 3. 数据模型：三层数据 + 版本化重建

### 3.1 三层数据（职责分离）

| 层 | 保存内容 | 作用 | 关键约束 |
|---|---|---|---|
| **解析快照**（`rag_parse_snapshots`） | 有序结构块：标题路径、页码 / sheet / 行范围、表格行列、原始文本 | **重新分块的唯一输入** | 原文件仍按现行策略删除；快照是「可留存的解析产物」 |
| **`rag_chunks`** | 分块结果：`chunk_id`、`generation`、展示正文、embedding 输入文本、locator | 预览、引用、重新嵌入 | 展示正文与 embedding 输入**分离**（后者可含标题路径） |
| **Qdrant 向量** | 向量 + 最小元数据（doc_id / generation / chunk_id / scope） | 可重建的检索索引 | 一切字段可从 PG 重建（派生品定位） |

### 3.2 两种操作（语义分离，不得混用）

| 操作 | 输入 | 产物 | 适用场景 |
|---|---|---|---|
| **重新嵌入** `re-embed` | 现有 `rag_chunks` | 同分块、新向量 | 换 embedding 模型 / 修复索引损坏 |
| **重新分块** `re-chunk` | 解析快照 | 新 generation 的 chunks + 向量 | 分块器升级（v1 → v2） |

- 旧文档（无解析快照、仅向量 payload）**只能**恢复块级预览与重新嵌入；
  需要结构重建时**明确提示重新上传**（不假装可恢复）。
- 旧文档的兼容：摄取管线先行「快照补齐」——新上传必存快照；历史文档在首次 re-embed 时提示无快照。

### 3.3 版本化重建（`index_generations`）

```sql
rag_index_generations (
  id bigserial PK, doc_id text, generation int,
  chunker_version text, embedding_model text, embedding_dim int,
  status text CHECK (status IN ('building','active','retired','failed')),
  chunk_count int, created_at, activated_at, error text,
  UNIQUE (doc_id, generation)
)
```

重建流程（**新版本写完并验证后才切换**）：

```text
① building：写入新 generation 的 chunks + 向量（payload 带 generation）
② 校验：块数/向量数一致 + 按 chunk_id 抽样回读
③ 切换：单事务把活动版本指向新 generation（旧 → retired）
④ 清理：异步删除 retired generation 的向量与 chunks
⑤ 失败：保留 building 现场供归因；检索继续用旧 active —— 无不可检索窗口
```

- **提交条件校验**（租约之外）：worker 完成时必须校验 generation 仍为 `building` 且所有权匹配，
  防过期 worker 覆盖新结果；同一 doc 同时仅一个 `building`。
- **维度变化**：`embedding_dim` 变化 ⇒ **新 collection**（`<collection>_d<dim>`）；禁止假定原 collection
  可直接复用。检索按 generation 记录路由到对应 collection。
- 状态自检与清理挂 Worker 周期任务（沿用 `SKIP LOCKED` + 租约模式）。

### 3.4 检索只认活动版本

- 查询过滤 `generation == active_generation`（doc 级 active 映射，检索后校验）；
- 缓存键包含索引修订号（§5.3），版本切换即失效。

## 4. 分块 v2（结构保真）

### 4.1 尺寸口径

- **分块尺寸用 token 预算**（初始 256~512 token/块，按 embedding 模型上限与 §8 评测调整）；
- **字符数仅用于资源限制**（沿用 `max_chars`/`max_chunks` 限额），不参与分块决策。

### 4.2 结构规则

- 标题栈（H1 > H2 > H3 …）随块存储；**不跨标题合并**；段落为基本单元；
- **overlap（10~15%）只用于长文本二次切分**，不机械跨标题、跨表格复制；
- 表格：**小表整表成块**；大表按行组拆分并**重复表头**，locator 保留 sheet、行范围、单位；
- PPT：保留页码与标题；HTML：保留标题路径、移除导航噪声（bs4）；
- **embedding 输入 = 标题路径 + 正文**（低成本上下文补充）；展示正文独立存储；
- 能力边界如实声明：PDF 结构识别受 pypdf 限制，仅文字版 PDF 可抽取标题/表格近似结构，
  **不承诺扫描件 / 复杂版式**；HTML/PPTX/XLSX 的结构保真度高于 PDF。

## 5. 检索与证据组装

### 5.1 统一结果契约

```text
{ scope, doc_id, generation, chunk_id, locator{section,page,sheet,row_range}, rank, score, text }
```

- 两路（dense / BM25）以 **chunk_id** 为身份做 RRF；
- **禁止按文本去重**：同文多出处不得因文本相同而丢失来源；
- `rank` = 各路内序；`score` = 融合 / 重排分。

### 5.2 链路（有预算的证据组装）

```text
双路召回（各 top-20）→ RRF（k=60 初值）→ 可选 rerank（gte-rerank：top-20 → top-5）
→ 去重与证据组装（重叠块合并、同 doc 上限、locator 保留）→ token 预算 → 生成
```

- top-20 / k=60 / 最终 5 均为**初始参数**，由 §8 评测调整，不写成「固定最优」；
- **命中 ≠ 适合直接入 prompt**：证据组装至少处理高度重叠块；相邻块扩展 / 父段落补充
  留后续（先评测再启用）；
- **RRF 分数只表示排序贡献，不得作为可信度或拒答阈值**（代码层加断言防误用）；拒答策略见 §9；
- rerank **fail-open**（失败/超时回落 RRF 排序并记指标），与限流 fail-open 同口径。

### 5.3 缓存一致性

- 任何派生缓存（BM25 语料等）键 = `(tenant/user scope, index_revision)`；
- `index_revision` 在**上传完成 / 删除 / 重建切换**时递增；
- 写路径主动失效；**删除后的内容不得从缓存命中**（专项测试）。

## 6. 设计策略：问题背景与取舍（为什么这样设计更好）

> 本章是 §3~§5 的「为什么」：先给问题，再列备选，再说明选择理由与代价；
> §8 的评测设计与之配套——每个决策都必须能被测量。

### 6.1 解析快照独立于分块产物（三层数据）

- **问题**：分块一旦丢失标题 / 表格 / 页序，分块器升级时无法重建（只能重嵌入错误的分块）；
  「只存 chunks、重索引不重解析」与「分块 v2 全量重建」自相矛盾。
- **备选**：a) 只存 chunks（原草稿）→ 不可演进；b) 永久保留原文件 → 与隐私最小化 / 保留期冲突；
  c) 只存全文 → 丢失结构与定位。
- **选择与理由**：快照存**结构块**而非原文件；chunks 与向量均为派生品。原文件仍可删除（合规不变），
  但结构信息「一次解析、多次利用」；分块器 / 嵌入模型演进不再要求用户重传；引用定位获得稳定锚点。
- **代价与缓解**：PG 多一份文本（数百 MB 量级）；旧文档无快照 → 结构化 409 引导重传，
  预览 / re-embed 仍可用（不假装可恢复）。

### 6.2 版本化重建（切换语义，而非先删后建）

- **问题**：`delete → embed → upsert` 中断即文档暂时 / 长期不可检索；**幂等重试不等于可恢复**。
- **备选**：a) 先删后建（实现最简单，有故障窗口）；b) 原地更新（窗口小但不可回滚）；
  c) 双写后切换（最稳，复杂度高）。
- **选择与理由**：generation 状态机 + 校验 + **单事务切换** + 异步清理。检索永远有一个完整可用版本
  （要么旧、要么新）；升级失败自动留在旧版；审计可回答「当前是哪一次构建、由谁触发」。
- **代价与缓解**：状态机复杂度 → 提交条件校验（防过期 worker）+ 中断恢复演练进 DoD；
  **维度变化直接开新 collection**，避免原地换维的不可逆操作。

### 6.3 身份级融合（chunk_id + RRF），而非文本去重 / 分数加权

- **问题**：① 现状 BM25 实际被截断、不生效；② 文本去重把「同一段落多出处」错误合并；
  ③ 两路分数量纲不可比，加权需要持续标定且跨查询不稳。
- **备选**：a) 分数加权（需归一化与调参）；b) 只保留一路（丢能力）；c) RRF（只用排名）。
- **选择与理由**：双路各 top-20 → **RRF（k=60 初值）**，身份统一为 `chunk_id`。
  RRF 对分数量纲免疫、是工业默认基线；身份级去重保证溯源正确；参数由 §8 评测调整而非拍脑袋。
- **代价**：RRF 分数语义弱（不是相关性概率）→ 明文禁止作为可信度 / 拒答阈值，代码层加断言。

### 6.4 结构保真分块（token 预算 + 标题路径），而非机械 overlap 或重型解析栈

- **问题**：段落打包让标题与块分离、表格破碎、超长段整段成块；检索「块对了但语境没了」。
- **备选**：a) 固定字符 + 机械 overlap（结构破坏）；b) 引入 RAGFlow 式重型解析框架
  （能力强但运维 / 资源重，4C 机型不现实）；c) 结构感知轻量分块。
- **选择与理由**：标题栈 + 段落级结构分块；**尺寸用 token**（中英混排更稳）；
  overlap 仅用于长段二次切分；表格规则化拆分；**标题路径进 embedding 输入**（低成本上下文补充）。
  在不引入重解析栈的前提下，把块级语境还给检索；locator 因结构来自快照而稳定。
- **代价**：PDF 结构能力受 pypdf 限制 → 如实声明能力边界；html / pptx / xlsx 结构保真优先。

### 6.5 有预算的证据组装（检索 ≠ 直接入 prompt）

- **问题**：固定 top-5 直接入 prompt：高度重叠块浪费预算；关键块可能缺相邻语境。
- **备选**：a) 固定 top-k（现状）；b) 无限扩窗（token 爆炸）；c) 预算化组装。
- **选择与理由**：**召回求全（top-20）+ 入 prompt 求信息密度（组装后 top-5）**；
  合并重叠块、同 doc 上限、token 预算。参数由评测决定；相邻块扩展先评测后启用。
- **代价**：组装策略本身需要评测背书（§8 矩阵覆盖）。

### 6.6 云端 rerank（gte-rerank），而非本地 cross-encoder

- **问题**：4C / 4GB 机型跑不了 `sentence-transformers / torch`（内存、模型下载、维护成本）；
  无重排则融合质量封顶。
- **备选**：a) 本地 cross-encoder（质量好、资源不可行）；b) 不重排（质量差）；c) 云 rerank API。
- **选择与理由**：DashScope `gte-rerank`（复用现有 key 与账单）+ fail-open + 指标。
  零重力依赖、按量付费、与既有 provider 治理（限流 / 成本 / 归因）同构；默认开关由评测决定。
- **代价**：延迟与费用（+100~300ms 为待验证目标）→ 记录 p95 / 超时率 / 费用 / 回退率。

### 6.7 可归因评测与决策规则（不只证明「没下降」）

- **问题**：10~20 条关键词命中率无法回答「哪一步带来增益或损失」，改分块 / 融合没有决策依据。
- **备选**：a) 只看端到端引用准确率（无法归因）；b) RAGAS 全套（依赖 LLM 裁判，成本与稳定性风险）；
  c) 分层最小集 + 消融。
- **选择与理由**：六类 query + 三组消融 + 指标分层（召回 / 排序 / 生成）+ **golden 锚原文位置**。
  每个开关的增益可单独测量；分块升级后评测不失效；决策规则前置（达标才默认开），避免玄学调参。
- **代价**：标注工作量 → 30~50 条起步，按误例补充。

### 6.8 演进克制（为什么本期不做 Agentic / GraphRAG）

- **问题**：多轮 Agentic 与 GraphRAG 收益显著，但复杂度同样显著，且**当前没有任何评测证据**证明
  本项目的查询分布需要它们。
- **选择与理由**：先做「一次性自适应」（有证据缺口才追加一轮拆解 / rewrite，记录增益与成本）；
  GraphRAG 明确不纳入，等待实体 / 多跳类评测需求出现。每一步演进都用同一套评测闭环背书，
  避免「为新潮堆复杂度」。

### 6.9 复用既有设施（不引入新中间件）

- **问题**：独立检索服务 / 消息队列会打破单机 4C 的运维预算，并让发布 / 备份 / 回滚体系复杂化。
- **选择与理由**：PostgreSQL 权威 + `SKIP LOCKED` / 租约 / 幂等 + Qdrant 派生索引 + 现有 Worker 周期任务。
  与全仓「PG 权威、派生可重建」的既成模式一致，发布 / 备份策略无需扩展。
- **代价**：客户端 BM25 有规模上限（1 万点滚动）→ 登记为规模化路线（原生稀疏向量），内测期够用。

## 7. 接口与前端（迁移 0019）

### 6.1 表变更

```sql
-- 解析快照（结构块）
CREATE TABLE rag_parse_snapshots (
  doc_id text, block_index int, kind text,            -- heading/paragraph/table/...
  title_path text[], locator jsonb, text text,
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (doc_id, block_index)
);
-- 分块产物（含版本与定位）
CREATE TABLE rag_chunks (
  doc_id text, generation int, chunk_index int, chunk_id text,
  text text, embed_text text, locator jsonb,
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (doc_id, generation, chunk_index)
);
-- 版本表见 §3.3；rag_ingestions 增列 display_name / tags
```

### 6.2 API

| 方法与路径 | 语义 |
|---|---|
| `GET /api/rag/docs` | **数据源改 PG 台账**：status / size / chunks / created_at / last_error / active_generation |
| `GET /api/rag/docs/{doc_id}/chunks` | 分块预览（分页；含 locator） |
| `PATCH /api/rag/docs` | `display_name` / `tags` |
| `POST /api/rag/docs/reembed` | 重新嵌入（同分块） |
| `POST /api/rag/docs/rechunk` | 重新分块（从解析快照；无快照 → 结构化 409 + 「请重新上传」提示） |
| `GET /api/rag/usage` | 本人用量（`DR_RAG_TOTAL_MB` 配额展示，默认 0=不限） |

- 所有写操作：归属校验 + 审计 + 幂等；重建类操作走 generation 状态机（§3.3）。

### 6.3 前端

- 列表：状态徽章 / 大小 / 块数 / 时间 / 失败原因 / 活动版本；
- 预览：分块列表 + locator 展示（章节/页码/sheet 行范围）；
- 操作：重命名 / 标签 / **重新嵌入** / **重新分块**（两步确认 + 无快照提示）/ 删除；
- 容量条（已用 / 配额）；
- 引用回溯（stretch）：报告引用携带 `doc_id + chunk_id + locator`，点击跳预览定位；
- 新增 testid：`kb-doc-status`、`kb-doc-size`、`kb-doc-preview`、`kb-chunk-list`、`kb-rename`、
  `kb-reembed`、`kb-rechunk`、`kb-usage`。

## 8. 评测设计（可归因，不只证明「没下降」）

### 7.1 数据集

- **30~50 条 query**（10~20 条冒烟集不足以支撑默认开关决策）；
- 类别覆盖：精确术语 / 语义改写 / 跨块答案 / **表格问题** / **无答案问题** / **越权检索负例**；
- golden evidence **锚定原文证据位置**（doc + locator），**不绑定旧 chunk_id**（分块版本变更后仍可判定）；
- 脚手架：`python -m tools.rag_eval --dataset tools/eval_data/rag_golden_v1.json --dry-run`
  （起始 30 条六类；dry-run 零成本验证管线，真实指标需配置密钥后重跑）。

### 7.2 消融矩阵

| 对照 | 回答的问题 |
|---|---|
| dense / BM25 / RRF | 融合是否带来增益 |
| 分块 v1 / v2 | 结构与 overlap 是否有效 |
| RRF / RRF + rerank | 重排是否值得增加成本 |

### 7.3 指标

- 召回：**Recall@20**；最终排序：**MRR / nDCG@5**；
- 生成：引用支持率、无答案处理正确率；
- rerank 工程指标：**p50 / p95 延迟**（+100~300ms 为待验证目标）、超时率、费用、fail-open 回退率。

### 7.4 决策规则

- rerank 默认开：Recall@20 与引用支持率不降、且收益大于成本阈值；否则保持可配、默认关；
- 每次检索策略变更（分块 / 融合 / 重排）必须附本矩阵报告；开关状态与报告互链（变更记录）。

## 9. 演进路线（本期不做，登记不排期）

| 候选 | 说明 | 前置条件 |
|---|---|---|
| **自适应检索** | 证据缺口 → 最多一次问题拆解 / query rewrite，记录增益与成本 | 优先于多轮 Agentic RAG；先有 §8 评测基线 |
| 相邻块扩展 / 父段落补充 | 解决「小块准确但回答缺上下文」 | 评测证明增益后再启用 |
| contextual retrieval | LLM 逐块生成上下文（recall 提升显著） | 成本评估通过 |
| GraphRAG | 实体关系与多跳查询 | **当前无此类评测证据，明确不纳入** |
| 拒答阈值策略 | 基于评测校准，而非拿 RRF 分数当阈值 | §8 评测基线 |
| 网页 URL 抓取 / 图片 OCR / 连接器 | 复用 `research_engine/net/safe_fetch.py` | 后续独立需求 |

## 10. 验收标准（DoD）

- [x] 迁移 0019：`rag_parse_snapshots` / `rag_chunks`（含 generation/locator/title_path）/ `rag_index_generations` / `rag_ingestions` 扩列——staging 实测：migrate 两次（apply=1→0），4 表 + 5 列就位
- [x] 三层数据落地；**re-embed 与 re-chunk 语义分离**（`tests/test_rag_pipeline.py`：切换 / 失败保留旧版 / 清理）
- [x] 版本切换：**无不可检索窗口** + 提交条件校验（测试：构建失败旧 active 保留；重复激活拒绝）；维度变化走新 collection 策略（管道 `EMBEDDING_DIM` 约定，登记）
- [x] RRF + chunk_id 身份契约 + 缓存失效（`tests/test_rag_retriever_v2.py`：同文多出处保留 / revision 失效 / 删除后不可命中路径）
- [x] 分块 v2 落库 locator（`tests/test_rag_chunker_v2.py` 8 项：标题栈 / 长段 overlap / 表格拆分重复表头）
- [x] 证据组装（同 doc 上限 / 包含去重 / token 预算）；「RRF 分数 ≠ 可信度」写入代码与测试语义
- [ ] 评测：**起始 30 条六类数据集 + 消融脚手架 dry-run 已跑通**（`tools/rag_eval.py`）；真实指标报告与 rerank 工程指标（p50/p95/费用/回退）待部署后按 §8 重跑补录
- [x] 前端列表 / 预览 / 整理（重命名 / 重分块 / 重嵌入）/ 容量；构建绿、新 testid 齐备；引用回溯（stretch）未实施，登记后续
- [x] 飞书镜像同步 + 需求 21 §10 回填（随本文档「已合」收尾 PR 执行）

## 11. 影响范围与风险

| 风险 | 对策 |
|---|---|
| PG 文本体积（快照 + chunks 双份） | `max_chars=2e6` 上限；100 文档量级 ≈ 数百 MB，4C 机型可承受；超量评估对象存储 |
| 新依赖体积 / 安全 | `python-pptx` / `openpyxl` 纯 Python 小型库；进 lock 并跑 CI |
| 版本切换复杂度 | 状态机 + 提交条件校验 + 中断演练；building 失败不影响 active |
| rerank 延迟 / 费用 | fail-open + metrics；评测不达标不默认开 |
| BM25 规模化（客户端全语料、1 万点上限） | 内测期够用；上量前换 Qdrant 原生稀疏向量（登记） |
| 旧文档无快照体验 | 明确 409 + 引导重新上传；预览 / re-embed 仍可用 |
| 评测标注工作量 | 先用 samples 提炼 30~50 条；golden 锚原文位置降低维护成本 |

## 12. 测试策略

- 单测：分块器（结构规则 / 表格 / 长段 overlap / locator）、RRF 身份融合、缓存失效、generation 状态机；
- 集成（真实 PG + Qdrant，CI `infra` job 模式）：上传 → 快照 → chunks → re-chunk 切换 → 检索 → 删除全链路；
- **恢复演练**：构建中断（kill worker）→ 旧版本继续可检索 → 重试成功；
- E2E：预览 / 重命名 / re-embed / re-chunk（含无快照 409）/ 容量；
- 评测：按 §8 矩阵执行并留存报告。

## 13. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-03 | 新建 | 需求 21 批次 A2 立项 | 初稿：现状核实（解析/管线/检索/删除/前端）+ `rag_chunks` + 重索引 + 3 格式 + rerank 接线 + DoD | 本文档 |
| 2026-10-03 | 重大修订 | 2026-10-03 设计讨论（对照实现审计） | 定位提升为「可演进/可评测/可追溯」；三层数据（解析快照/chunks/向量）+ 版本化重建（generation 切换、提交条件校验、维度→新 collection）；统一结果契约与 RRF（chunk_id 身份、缓存随索引修订失效）；分块 v2 结构保真（token 预算/表格/PPT/HTML/locator）；证据组装与「RRF 分数≠可信度」；分层评测（30~50 条 + 消融矩阵 + 决策规则）；演进路线登记（自适应检索优先，GraphRAG 不纳入） | 本文档 |
| 2026-10-03 | 修订 | 评审反馈：补充设计策略的问题背景 | 新增 **§6 设计策略：问题背景与取舍**（9 项决策的「问题 → 备选 → 为什么更好 → 代价」）；章节重排（§7~§13）并同步全部交叉引用 | 本文档 |
| 2026-10-03 | 实施 | 需求 23 落地 | 迁移 0019（三层 + 版本状态机 + 修订号，staging 幂等实测）；`snapshot`/`chunker_v2`/`rerank`/RRF 身份融合/证据组装；`rag_pipeline`（file/rechunk/reembed + retired 清理）与摄取任务分流；管理 API（PG 台账/预览/重命名/rechunk/reembed/用量）；前端 KB 面板升级（预览/整理/容量）；依赖 python-pptx/openpyxl 进 lock；评测脚手架 + 30 条起始集；测试：管道 6 / 管理 API 9 / 分块 8 / 检索 9 / 摄取管线更新 | #106 |
| 2026-10-03 | 已合 | PR #107 合并 | squash `d22a882`；CI 全绿（lint-and-test 3.11/3.12/3.13 + frontend + e2e + infra）；Issue #106 关闭；部署待 A 批次完成后统一执行 | #107 |
