# 需求 21：上线产品模块补全清单（任务列表 / 知识库 RAG / 平台化）

> 状态：**调研稿**（市面调研 + 全模块差距盘点 + 分批路线；评审后冻结，实施项另立子需求 22+）。
> 来源：2026-10-03 产品讨论（「开新需求：任务列表、知识库上传 RAG，并补全上线项目应有模块清单」）。
> 定位：产品化模块**总清单**；不替代需求 10（L3 路线）与需求 20（鉴权体系），以其为前置，
> 补齐「持续使用 / 可运营 / 可开放」三层。飞书镜像：待同步。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 21 |
| 标题 | 上线产品模块补全清单（任务列表 / 知识库 RAG / 平台化） |
| 优先级 | P1（A 批次为邀请内测可用性补全；B/C 批次按上线阶段递延） |
| 状态 | 调研稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | 待建 |
| 关联 PR | 本 PR |
| 创建 / 更新 | 2026-10-03 |

## 2. 问题背景

1. **工程底座已稳**：`docs/launch-readiness-review.md`（2026-09-30）结论「代码侧九成、卡非代码事实」；
   此后 L3-A staging 完成正式部署、鉴权体系重构（需求 20）与备份加密落地。核心链路（提交→SSE→报告→引用→导出→KB）可跑通。
2. **产品面缺三块**（现状能力集中在「跑通一次研究」）：
   - **持续使用**：任务多了没法管理——列表只有状态筛选/分页/预览，缺搜索、重命名、删除/归档、分组；
   - **知识库深度**：上传有了，但缺预览/分块检查/重新索引/格式覆盖/容量视图（RAGFlow、open-notebook 已把 KB 做成一等公民页面）；
   - **可运营**：零邮件通道（找回密码只能管理员 CLI）、零前端错误追踪、零产品分析、无分享、无开放 API。
3. **风险**：不一次性列全模块，会变成「想到哪补到哪」——本需求先把市面基线拉平，再按批次立项实施。

## 3. 市场基线（对标摘要）

### 3.1 开源同类

| 项目 | 模块亮点 | 启示 |
|---|---|---|
| [gpt-researcher](https://github.com/assafelovic/gpt-researcher) | 报告类型（research/outline/detailed）、PDF/Word/MD/JSON 导出、文件上传、GUI 设置页、多部署形态；**无任务历史/分享** | 导出与设置是报告类标配 |
| [open_deep_research](https://github.com/langchain-ai/open_deep_research) | 研究前反问澄清、参数面板（模型/搜索源/并发/迭代上限）、本地 API + Swagger + Studio | 研究参数可视化是差异化亮点 |
| [morphic](https://github.com/miurla/morphic) | 聊天历史落库、**分享只读 URL**、模型选择、多搜索源、游客模式、Supabase Auth | 任务历史 + 分享是「产品化」分水岭 |
| [storm](https://github.com/stanford-oval/storm) | 多视角提问、Co-STORM 人机协同、VectorRM 自有语料检索 | 知识库检索我们已具备 |
| [open-notebook](https://github.com/lfnovo/open-notebook) | Sources/Notes/Chat 三栏、多格式来源、笔记、多聊天会话、18+ provider、REST API | 知识库应是**独立页面**而非附属功能 |
| [ragflow](https://github.com/infiniflow/ragflow) | DeepDoc 解析可视化 + 人工干预、模板化分块、检索测试、引用定位原文块、连接器 | KB 管理深度的天花板 |
| 跨项目共性 | 任务发起+进度流、报告+引用、导出/分享、任务历史、知识库上传、搜索源管理、模型选择、API、设置页 | 见 §4 差距表 |

### 3.2 商业产品

| 产品 | 模块亮点 | 启示 |
|---|---|---|
| Perplexity | Projects/Spaces（工作区，view/edit 权限）、会话分享链接、Pro Search、API key + 分层限流 | 分享 + API 是开放层两块 |
| NotebookLM | Sources 多格式（PDF/网页/YouTube/音频）、公开分享、Plus 团队共享 + 用量分析 | 多格式来源 + 限额视图 |
| Elicit / Consensus | 系统综述流程（检索→筛选→抽取→报告）、句级引用、CSV/RIS/BibTeX 导出、Library 集合 | 引用导出格式要全 |
| Kimi / 秘塔 | 「项目」工作区（文件+指令+会话归总）、知识库专题 + 分享协同、定时任务管理 | 任务/知识库都需要「组织容器」 |

来源：见各产品官方文档与仓库（[Perplexity Projects](https://www.perplexity.ai/help-center/en/articles/10352961-what-are-projects)、[NotebookLM Sources](https://support.google.com/notebooklm/answer/16213268)、[Elicit SR](https://elicit.com/solutions/systematic-review)、[Consensus](https://help.consensus.app/en/articles/9922673-how-consensus-works)、[Kimi 项目](https://www.kimi.com/zh-cn/help/features/project)、[秘塔专题](https://www.qbitai.com/2024/11/217566.html)）。

### 3.3 RAG 知识库设计基线

上传 → 解析（保留标题/页码/元数据）→ 分块（512 token、10~15% overlap 为常见起点）→ 嵌入 →
**dense+BM25 混合检索（RRF）→ cross-encoder 重排**（先取 20~50 再留 3~5 入 prompt）→ 带引用生成 → 评估；
文档管理需：列表、预览、重命名、删除、标签/元数据、**增量索引/重新索引**；检索侧需：top-k、rerank、按文档过滤；
删除用户数据必须覆盖向量与缓存。来源：[AWS RAG 最佳实践](https://docs.aws.amazon.com/pdfs/prescriptive-guidance/latest/writing-best-practices-rag/writing-best-practices-rag.pdf)、[Production RAG](https://www.stripesys.com/blog/production-rag-pipelines)。

## 4. 模块清单与差距（核心）

> 现状引用代码位置；✅=已具备，🟡=部分，❌=缺失。批次：A=邀请内测补全，B=公开前，C=商业化/暂缓。

### A. 入口与账号

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 落地页 / 登录 / 邀请制 | ✅ `web/frontend/src/features/landing`、`features/auth` | 全对标有 | — | 已完成 |
| 口令与会话安全 | ✅ 需求 20（`__Host-` Cookie / NIST / 会话页） | 行业基线 | — | 已完成 |
| 邮箱验证 | ❌ | 标配 | 邀请制下可降级为「补填即验证」 | A（可选） |
| 自助找回密码 | ❌（管理员 CLI 兜底，`web/backend/admin.py`） | 标配 | **邮件通道 + 重置 token 全流程**（接口 `/api/auth/reset` 已有，缺投递） | A |
| OAuth / Passkey / MFA | ⏳ 需求 20 已设计 | Passkey 主流 | 视排期 | C |

### B. 研究任务（用户点名「任务列表」）

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 提交 / SSE 进度 / 取消 / 新话题 | ✅ | 全对标 | — | 已完成 |
| 历史任务列表 | 🟡 `HistoryPanel.tsx`：状态筛选 + 分页 + 报告预览 | Morphic 历史、Kimi「项目」 | **关键词搜索、重命名、删除/归档、置顶、分组/标签**、失败一键重试、进行中任务与历史统一入口 | A |
| 任务详情页 | 🟡 侧栏/预览 | Perplexity Thread | 独立 URL（可分享到具体任务） | A/B |

### C. 报告与引用

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 报告版式 / 证据栏 / 引用校验 | ✅ 需求 2/6/16 | 全对标 | — | 已完成 |
| 导出 | 🟡 .md / .json（需求 17） | GPTR：PDF/Word；Elicit：CSV/RIS/BibTeX | **PDF / Word 导出、BibTeX/RIS 引用导出** | A/B |
| 分享 | ❌ | Morphic/Perplexity/NotebookLM 只读链接 | **只读分享链接**（带过期/撤销） | A |

### D. 知识库 RAG（用户点名「知识库上传 RAG」）

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 上传→异步摄取→状态 | ✅ `POST /api/rag/ingest`、Worker 隔离区 | RAGFlow/OpenNotebook | — | 已完成 |
| 格式与限额 | 🟡 pdf/docx/md/txt ≤10MB | NotebookLM 多格式（音视频/网页）、RAGFlow 图片/表格 | **pptx/xlsx/html/网页 URL**、上限评估 | A |
| 文档列表 / 删除 | ✅ `GET/DELETE /api/rag/docs` + UI | RAGFlow | **重命名、标签/集合（文件夹）、容量/配额视图** | A |
| 预览与分块检查 | ❌ | RAGFlow 解析可视化 | **原文预览、分块查看、解析结果人工干预** | A |
| 索引管理 | 🟡 内容寻址去重 | RAGFlow/生产实践 | **重新索引、增量更新（文件变更）、索引版本** | A |
| 检索质量 | 🟡 混合检索已实现；rerank 开关默认关、`use_rerank=False` | 生产基线=混合+重排 | **开启 rerank 并用既有 eval 体系标定**、按文档过滤检索 | A |
| 引用回溯 | ✅ 报告引用到 chunk | RAGFlow 定位原文块 | 点击引用跳到 KB 原文位置 | A |

### E. 搜索与模型

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 搜索源 | 🟡 仅博查（README 已登记单点风险） | GPTR/Morphic/STORM 多源 | **国内第二源**（采购或登记风险） | B |
| 学术 / 代码执行 | ✅ arXiv、受限沙箱 | 差异化 | — | 已完成 |
| 模型分档 | ✅ fast/smart/strategic + planner/critic | 全对标 | 设置页可视化（仅管理员 env） | B |

### F. 运营与增长

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 审计 / 审核 / 申诉 / 配额预算 | ✅ 需求 10/13/20 | — | — | 已完成 |
| 事务邮件 | ❌ 全仓零 SMTP/SMS | 标配 | **SMTP/API + SPF/DKIM**、注册欢迎、重置、告警邮件 | A |
| 站内通知 / Webhook | 🟡 仅告警 webhook | Kimi 定时任务通知 | 任务完成/失败通知、开发者 webhook | B |
| 帮助中心 / FAQ / 反馈 | ❌ | 标配 | 帮助页 + 反馈入口（收集到工单/表格） | A |
| 产品分析 | ❌ | PostHog 类 | 关键漏斗（注册→首跑→导出），隐私最小化 | B |
| Feature flag | ❌（靠 env） | 标配 | 轻量配置表即可，不引第三方 | B |
| 邀请看板 | 🟡 CLI `list-invites` | — | 管理端 UI（用量/转化） | B |

### G. 平台与集成

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 开放 API | ❌ | ODR/OpenNotebook/Perplexity | **API key 管理 + 限流 + OpenAPI 文档** | B |
| MCP | ❌ | GPTR/RAGFlow 已支持 | 研究能力 MCP server（可复用引擎接口） | C |

### H. 可观测性

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 指标/追踪 | 🟡 OTel/Prometheus/Langfuse（默认关） | Sentry 类标配 | **前端+后端错误追踪（Sentry）** | A |
| 拨测 / 成本看板 | ❌ | 标配 | uptime 拨测、月成本趋势（已有 usage ledger 可出数） | B |

### I. 合规与文档

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| ToS / 隐私政策 | 🟡 `docs/legal/` 已写 | 页面接入 | **页脚链接 + 注册同意勾选 + 版本留档** | A |
| 数据导出（个人信息副本） | ❌ | GDPR/PIPL 实践 | 一键导出本人数据 | B |
| ICP / 生成式 AI 备案 | ❌ 业务动作 | 法定前置 | 与域名并行（不在本文范围，登记） | 并行 |

### J. 协作（公开前）

| 模块 | 现状 | 市场对标 | 缺口 | 批次 |
|---|---|---|---|---|
| 分享 | ❌ | 见 §C | 只读分享（A）；协同编辑不做 | A |
| 团队 / 工作区 / 权限 | ❌ | Spaces/NotebookLM | 多租户组织容器 | C（先不做） |

## 5. 分批路线与 DoD

### 批次 A：邀请内测可用性补全（建议 2 周，本次优先）

- [x] **A1 任务列表增强**（子需求 22）：关键词搜索、重命名、归档、置顶、失败重试；E2E 覆盖（实现完成，待合并/部署）
- [ ] **A2 知识库增强**（子需求 23）：原文预览 + 分块查看、重新索引、重命名/标签、格式扩展（pptx/xlsx/html）、容量视图；rerank 标定报告
- [ ] **A3 邮件通道 + 自助找回密码**：SMTP/API 接入、SPF/DKIM、重置全流程（复用 `/api/auth/reset`）、限流与审计
- [ ] **A4 错误追踪 + 反馈 + 帮助**：Sentry（前后端）、页脚反馈入口、FAQ 页、ToS/隐私页接入与注册同意
- [ ] **A5 报告只读分享**：唯一 URL + 过期/撤销 + 审计

### 批次 B：公开前（需求 10 L3-B 判据联动）

- [ ] 国内第二搜索源；[ ] 有资质审核服务；[ ] 开放 API（key/限流/文档）；[ ] 产品分析；[ ] uptime 拨测 + 成本看板；[ ] 数据导出；[ ] 站内通知/webhook

### 批次 C：商业化 / 规模化（暂缓，登记不排期）

- [ ] 计费订阅（当前邀请制无商业闭环，不做）；[ ] 团队/多租户；[ ] Passkey/MFA（需求 20 已设计）；[ ] i18n；[ ] MCP

## 6. 设计策略

1. **复用现有设施**：新模块一律沿用「PG 权威 + 增量迁移 + 幂等 + 审计 + 开关灰度」的既有模式；不引入 K8s / 消息队列 / 新数据库。
2. **邀请制阶段取舍**：能用管理员 CLI 兜底的（找回密码、邀请管理）可先做界面后做自动化；但**邮件通道**是账号闭环的必要件，优先级最高。
3. **分享与权限**：只读分享采用不可猜测 token + 过期时间 + 可撤销；不暴露内部 run_id / 对象存储直链（延续 BFF 口径）。
4. **KB 深度对齐 RAGFlow，但不抄复杂度**：先做「预览 + 分块 + 重索引 + rerank」，不做多路解析器编排与连接器。
5. **对外开放延后**：API/Webhook 等公开前再做，避免邀请期攻击面扩大。
6. **每项独立可开关、可回滚**；用户可见行为变更先小范围内测。

## 7. 验收标准（DoD）

本文档级：

- [ ] 模块清单经评审，批次与「暂缓项」明确（本文件定稿）
- [ ] 子需求 22（任务列表增强）、23（知识库增强）立项，各自携带独立 DoD 与测试策略
- [ ] 每批次实施完成后回填 §10 变更记录与需求 10 对应 Gate

## 8. 影响范围与风险

| 风险 | 说明 | 对策 |
|---|---|---|
| 邮件送达 | 无自有域名邮箱易进垃圾箱 | 用云邮件服务 + SPF/DKIM/DMARC；先内测白名单 |
| 分享泄露 | 只读链接被转发 | 随机 token + 过期 + 撤销 + 审计；默认关闭 |
| KB 解析成本 | 大文件/表格解析耗时与内存 | 沿用隔离区异步管线 + 限额；预览走 worker 产物 |
| rerank 引入延迟 | 检索链路变长 | 离线 eval 标定后再开，按档位（quick/standard）开关 |
| API 滥用 | 开放后刷额度 | 登录限流同款滑窗 + key 级配额 + kill switch |
| 分析埋点隐私 | 违反最小化口径 | 只采事件不采内容；隐私政策同步更新 |

## 9. 测试策略

- **A1/A2**：Playwright E2E（搜索/重命名/删除/预览/重索引）+ 后端单测（迁移、权限、幂等）；真实 PG 集成沿用 CI `infra` job 模式
- **A3**：邮件 sandbox（MailHog/云服务测试模式）+ 重置 token 单测（过期/一次性/全设备吊销）
- **A4**：Sentry 错误注入演练（前端抛错、后端 500 各一次，确认可达）
- **KB rerank**：用既有 eval 体系（`docs/eval-report.md` 口径）对比开启前后检索命中率/引用准确率，达标再默认开

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-03 | 新建 | 产品讨论：开新需求，补全上线模块清单 | 初稿：市场对标（开源 6 + 商业 4）+ 10 层模块差距表 + 三批次路线 + DoD | 本文档 |
| 2026-10-03 | 实施 | 批次 A1 落地 | 需求 22 实施完成（迁移 0018 已于 staging 验证；API/Store/前端/E2E 全绿），待合并与部署 | 需求 22 |
