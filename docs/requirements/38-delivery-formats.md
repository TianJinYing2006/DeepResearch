# 需求-38-delivery-formats

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> **性质**：**已立项（批次 5）**；开工登记见 §12.7（主理人 2026-10-14 评审修订 v1 收窄版并按此开工；TBD-0~7 按 §5 修订口径执行）。
> **来源**：`docs/extension-directions-survey.md` §3 方向 C / §4 批次 5（调研建议 P2——最直观解决「功能单一」体感）。
> **竞品参照**（调研 §1.2~§1.3）：GPT Researcher 导出 MD/PDF/DOCX/JSON；ChatGPT Deep Research 导出 Markdown/Word/PDF；DeerFlow PPT（marp-cli）；Gemini 原生图表与信息图。
> **前置（已交付）**：`web/backend/export.py`（md/json 导出契约）；R4 流式协议；分享（需求 26）与输出 gate（flagged 阻断）口径。
> **纪律**：① 导出是**展示层**，不改研究图产出的 writer 原文；现有持久化报告可能是 `report_display`，不得声称能从历史产物还原 writer 原文；② 新格式全部复用输出 gate/flagged 阻断；③ 默认延续 D-19「不落盘」精神（按需生成、内存返回）；④ 分享页维持只读无格式下载（需求 26 边界，不因本批扩大）。
> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/CfGVdplPGoaUEzxxTYocYEJgnwc（`CfGVdplPGoaUEzxxTYocYEJgnwc`，位于 `DeepResearch 需求文档` 文件夹；2026-10-14 按开工版重建，立项版已删）。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 38 |
| 标题 | 批次 5「交付形态」：统一导出装配 + PDF/DOCX 导出（PPT / 图表后置） |
| 优先级 | P2 |
| 状态 | 进行中 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #225 |
| 关联 PR | 待定 |
| 创建 / 更新 | 2026-10-13 / 2026-10-14 |
| 前置评审 | **TBD-0~7 已拍板**（2026-10-14 主理人评审修订版；v1 = F1 + PDF + DOCX；PPT/F5 后置） |

## 2. 问题背景

- **方向判断（调研 §0/§2）**：交付形态单一（`md`/`json` 导出 + 只读分享 + 分段渲染 vs 竞品 PDF/DOCX/PPT/图表，缺口 🟡 中）——「功能单一」体感的一半来自这里；也是竞品共识（调研 §1.3⑤：输出不止 Markdown）。
- **代码现状（实测事实）**：
  - 导出只在 `web/backend/export.py`：`build_export_payload`（`:16-44`，结构即契约）+ `render_markdown`（`:64-93`，元数据头 + 正文原文照录 + 引用清单 + 访问来源）；**无 `research_engine/export.py`**；D-19 边界：不落盘、不写库（`:7-8`；测试 `test_web_guardrails.py:643-651`）。
  - **双路径不一致（既有缺陷）**：内存路径走 `render_markdown`（含元数据头 + `## 引用清单`）；store 路径直出 `report_md` 产物（`main.py:2201-2222`，无头无清单）；分享读链同样分叉（`:2272-2291`）；现有测试只覆盖内存路径（`test_web_guardrails.py:555-568`）。
  - 产物形态封闭：`run_artifacts.body text NOT NULL`（`migrations/0002:6-16`）；新 kind 需登记（`:16` 注释）；对象存储**仅 UTF-8 文本**（`objectstore.py:147-157`）。
  - 镜像与内存：`Dockerfile.api` = `python:3.13-slim`、**零 apt 系统包、零字体**（`:18-24`）；compose 内存上限 API **450M** / Worker **700M**（`docker-compose.staging.yml:298-301, 340-343`）。
  - 依赖：`python-docx`（`requirements.txt:11`，lock 1.2.0）与 `python-pptx`（`:57`，lock 1.0.2）**已安装但只用于读取**（RAG 解析）；无 matplotlib/weasyprint/pandoc/reportlab；`markdown` 声明未用（`:12`）。
  - 无任何图表/图像生成代码；无 `DR_EXPORT_*` 限额；报告展示面（`report_display`）已有 E4/E5 附录先例（`render.py:365-393`），**展示增强不回流 `report`**（`graph.py:491-514` 注释口径）。
  - 前端：`ReportView.tsx`（react-markdown + remark-gfm）渲染；导出按钮在 `ReportPanels.tsx:65-73`（`stage-export`）；flagged/under-review 时按钮禁用；分享页无导出按钮（需求 26 non-goal）。
  - 旧账：需求 21 缺口表已登记「PDF / Word 导出、BibTeX/RIS 引用导出」缺失（§C. 报告与引用）；FAQ 现文案仅承诺 Markdown/JSON（`docs/help/faq.md:46-52`）——交付后需同步。

## 3. 需求分析

目标（一句话）：**在不改变研究图和评测逻辑的前提下，统一 Markdown/JSON 与 PDF/DOCX 的导出数据装配，并复用既有审计元数据、引用清单和输出 gate；PPT 与图表按后续价值单独评估。**

- **F1 导出管线收口（前置）**：统一内存/store 双路径（私有 Markdown 导出产出 = `render_markdown` 语义：元数据头 + 正文 + 引用清单 + 访问来源），补 store 路径契约测试；md/json/pdf/docx（及若纳入的 pptx）共享同一「报告数据装配」入口。分享 endpoint 只统一其 Markdown 内容来源，不增加下载格式。
- **F2 PDF 导出**：服务端生成、中文正确（CJK 字体）、含元数据头/引用清单/附录；下载名沿用需求 17 口径；大小/页数上限。
- **F3 DOCX 导出**：结构等价（标题层级/段落/引用清单/附录）；用已装 `python-docx`（写入侧新用）。
- **F4 PPT 导出（范围待拍板，可裁）**：`python-pptx` 简版（标题页 + 章节要点 + 引用清单页），不追求版式引擎；若拍板裁剪则移出本批。
- **F5 图表（零模型，建议后置）**：先定义终局统计 spec 与分母/缺失语义；v1 如纳入，仅在导出文件生成统计图，不修改 `report_display` 或持久化报告正文。在线报告内展示另立阶段；**不做 LLM 生成图表**。
- **边界（本批不做）**：分享页导出（需求 26 边界）；BibTeX/RIS 引用导出（需求 21 另一缺口，另行立项）；服务端报告归档为二进制产物（按需生成，见 TBD-2）；版式精细排版（结构正确优先）。

## 4. 当前设计（代码现状）

| 层 | 位置 | 现状 | 本批次处置 |
|---|---|---|---|
| 导出契约 | `web/backend/export.py:16-93` | md/json；内存路径含头+引用清单 | **F1**：单一口径化（双路径一致） |
| 导出端点 | `main.py:2225-2255`、`2193-2222` | `format=md\|json`；store 路径直出产物 | **F2~F4**：扩展格式枚举；store 路径走同一渲染 |
| 产物 | `persistence.py:97-115`、`migrations/0002:6-16` | `report_md`/`export_json`/`evidence_manifest`（全文本） | **TBD-2**：不新增产物（按需生成） |
| 展示面 | `render.py:365-393`、`state.py:238` | E4/E5 附录先例；`report_display` 不回写 writer 原文 | **F5 后置**：若纳入，先定终局统计快照 |
| 前端 | `ReportPanels.tsx:60-84`、`ReportView.tsx` | md 导出按钮；markdown 渲染 | **F2~F4**：格式选择 + 下载；testid/e2e |
| 镜像 | `Dockerfile.api:18-24`、`docker-compose.staging.yml:298-343` | slim、无字体、450M/700M | **F2/F5**：apt 包 + Noto CJK 字体；容量记录 |
| 依赖 | `requirements.txt:11/57` | docx/pptx 已装（读侧） | **F3/F4**：写入侧启用；**F2/F5** 新增依赖（lock 外科式） |
| 输出 gate | `main.py:775-790`、`2479-2489` | flagged 导出 403 / 回放脱敏 | 新格式全量复用（测试锁定） |
| 分享 | `main.py:317, 2385-2417` | 只读无导出 | **不破**（维持） |

## 5. 优化方案（TBD 推荐 + 拍板记录）

- **TBD-0（前置，P0）before 基线 —— 推荐：不采，以「`report` 本体不动」断言护栏替代**。
  理由：导出/图表均属展示面；导出功能不得改研究图的 writer 输出与评分输入。护栏应在图内 writer 产物边界断言，不用生产历史 `result.report`/`report_md` 反推 writer 原文（当前终局优先保存 `report_display`）。
- **TBD-1（渲染选型）—— 推荐：全离线确定性栈**。
  - PDF：**WeasyPrint**（pip；apt 增 pango/harfbuzz 等运行库）+ **`fonts-noto-cjk`**；排除：pandoc/LaTeX（体积过大）、headless Chromium（超容器内存）、wkhtmltopdf（已废弃）；
  - DOCX：`python-docx`（已装，零新增）；
  - PPT：`python-pptx`（已装）；**不用 marp**（运行时容器无 node，引入 node 运行层成本更高）；
  - 图表：`matplotlib`（Agg 后台，Noto CJK，输出 PNG）。
  全栈无网络、无外部服务（延续确定性纪律）。
- **TBD-2（生成时机与存储）—— 推荐：按需导出、内存返回、不落盘**。
  请求时生成 → 内存响应（D-19 延续）；不预生成 `run_artifacts`（文本列 + 全文本对象存储不支持二进制；且每次 run 三格式预生成浪费）；可选进程内 LRU 缓存（同 run 重复导出加速）；**超限与并发策略**：单次导出大小/页数上限 + 并发上限（防大报告叠加打爆 450M 容器）。
- **TBD-3（本批范围）—— 推荐：F1 + PDF + DOCX；PPT 与 F5 后置**。
  首批验收先覆盖既有 md/json 和新 PDF/DOCX；PPT 需另证用户需求，F5 先做统计 spec 决策，图像导出再单独估时。排期不把后置项计入 v1。
- **TBD-4（图表数据源与呈现）—— 推荐：零模型统计 spec，离线生成图像**。
  数据 = 终局 `export_json` 可复算的确定性统计字段（零 LLM；不直接依赖运行中 `progress`）；缺字段历史 run 标记 `not_available`，不补造数据；在线 `report_display` 是否展示另行确认；导出时按需生成 PNG 并受图像/总响应大小上限约束，不把 base64 Markdown 当唯一承载协议；验收锁定统计 spec 和同环境下的图像结果，不承诺跨字体/绘图库版本逐字节一致；**不做 LLM 生成图表**。
- **TBD-5（双路径收口范围）—— 推荐：本批前置完成，行为变更显式登记**。
  私有 store 路径统一为 `render_markdown` 语义（历史报告导出的头部/引用清单从「缺」变「有」——属读取时渲染修复，不改写历史产物）；分享端仍只提供只读 Markdown，但其内存/store 内容来源保持一致，并明确分享文档是否包含审计元数据及引用清单，避免意外扩大敏感信息披露。
- **TBD-6（限额与并发）—— 推荐：显式上限 + 拒绝式保护**。
  对输入字节、渲染耗时、输出字节设硬上限；页数上限可作为成品约束，但不能替代生成前资源保护。超限 ⇒ 结构化错误（提示改用 md/json），不做静默截断；进程内信号量只保护单进程，生产多副本须按实测容量使用跨进程准入/专用导出 worker 或明确限制副本数。
- **TBD-7（合规与安全）—— 推荐：全量复用 + 字体许可确认**。
  输出 gate / flagged / under-review 阻断全量复用（与 md/json 同口径）；图表只含统计数字（无 PII）；Noto（OFL）许可入法务清单；导出文件名沿用需求 17（`download_names.py:24-47`）。
- **拍板记录（2026-10-14）**：TBD-0~7 按本 §5 **主理人评审修订版**口径拍板——v1 锁定 **F1 + PDF + DOCX**（PPT/F5 后置，不计入 v1 排期）；TBD-0 护栏改「图内 writer 产物边界断言」（不用历史 `report_md`/`result.report` 反推原文）；TBD-5 增分享披露边界（分享仅 Markdown，统一内容来源但不自动扩大审计头/引用清单）；TBD-6 增**生成前**输入字节/耗时/输出字节硬上限与跨副本准入要求；§12.4 六项处置与实施顺序见 §12.7。

## 6. 设计策略

- **展示面原则**：评测使用的 writer 原文不因导出功能而改变；导出只消费显式的报告展示字段和结构化元数据。若历史产物未保留 writer 原文，不声称可从历史 `report_md` 逐字节还原；
- **单一装配入口**：md/json/pdf/docx（及若纳入的 pptx）共享同一「导出数据装配」（meta + report + citations + 附录 + 可选图表 spec），避免 N 套格式 N 套口径；
- **确定性生成**：全离线栈、无 LLM、无网络；结构化输入和统计 spec 确定，文件字节可能随生成时间/库版本变化；不承诺跨版本二进制完全一致；
- **不落盘**：按需生成 + 内存返回（D-19 延续）；存储问题留给未来「归档」独立议题；
- **容器可承受**：先本地实测内存/时间，再回填 `production-readiness.md`（含镜像体积变化）。

## 7. 验收标准（DoD）

- [x] TBD-0~7 拍板（记录进 §5 / §12.7）—— ✅（2026-10-14，主理人评审修订版）
- [ ] F1：私有导出内存/store 两路径一致；分享仍只读 Markdown，但内存/store 内容来源一致且披露字段明确；store 路径契约测试补齐
- [ ] F2：PDF 导出——中文正确（**文本层可提取中文断言**）、元数据头/引用清单/附录完整、页数/大小上限、下载名正确
- [ ] F3：DOCX 导出——结构等价（标题层级/引用清单/附录）
- [ ] F4（若纳入）：PPT 简版——标题页/章节要点/引用页；超限保护
- [ ] F5（后置/若纳入）：终局统计 spec 的字段、分母、缺失语义和 schema version 固定；图表仅进入导出附录，不改 `report_display`；测试 spec 稳定性及同构建环境渲染结果，不断言跨版本字节相同
- [ ] 前端：导出按钮支持新格式 + disabled 逻辑（under-review）+ 下载名 + e2e（桌面/窄屏）；分享页仍无导出
- [ ] 输出 gate：flagged/under-review 对新格式同样阻断（测试）；分享链路仍无格式下载；下载设置 `Cache-Control: private, no-store`，错误响应不泄漏报告内容
- [ ] 镜像：Dockerfile 增包 + 字体；**本地实测导出内存/时间并回填文档**（容量与部署口径）
- [ ] 全量套件 0 失败；`SCORER_VERSION` 不变；FAQ/落地页文案更新（当前仅承诺 MD/JSON）
- [ ] 文档：导出协议入 `release-runbook.md`；依赖 lock 变更单列；`pip check` 通过

## 8. 影响范围与风险

- **动**：`web/backend/export.py`（装配入口 + 新格式）、`main.py`（端点格式枚举 + 校验）、`runner.py`（导出访问器）、`download_names.py`（扩展名映射）；前端 `ReportPanels.tsx`/`ReportView.tsx`；`Dockerfile.api` + compose（字体/系统库）；`requirements*`（WeasyPrint 等，lock 外科式）。F5 后置时不动研究图和 `research_engine/render.py`。
- 风险①：**镜像变重与容器内存**（apt + CJK 字体 ≈100MB+；PDF 渲染峰值内存 vs 450M 上限）⇒ 本地实测 + 必要时调限或导出队列化（记为决策点）；
- 风险②：**CJK 渲染正确性**（字体缺失 => 方块）⇒ 镜像装 `fonts-noto-cjk` + 确定性测试（PDF 文本层提取中文断言）；
- 风险③：**双路径收口影响历史/分享行为**（导出内容由「缺头」变「带头」）⇒ 显式登记 + 测试锁定，属修复；
- 风险④：**版式长尾**（DOCX/PPT 版式在不同阅读器差异）⇒ 范围收敛为「结构正确 > 版式精美」；
- 风险⑤：**大报告资源**（长报告 + 图表 + 多格式并发）⇒ 上限 + 并发信号量 + 拒绝式保护；
- 兜底：任一格式失败 ⇒ 结构化错误提示，md/json 永远可用（既有路径不受影响）；F5 可独立关闭（如 `DR_REPORT_CHARTS_ENABLED`）。

## 9. 测试策略

- 单测：装配入口（现有 md/json + PDF/DOCX；PPT 若纳入）、扩展名/下载名、生成前输入/耗时/输出上限、超限错误、writer 原文路径不变断言、统计 spec 稳定性；
- 格式契约：PDF 文本层提取中文 + 引用清单出现；DOCX 段落结构断言；PPT 页数/标题断言（若纳入）；
- 输出 gate：flagged/under-review 各格式 403；分享链路仍无导出；
- 真 PG / 双路径：store 路径导出（历史 run）内容与内存路径一致；
- 前端：vitest + e2e（导出各格式下载名；under-review 禁用）；
- 全量套件 0 失败；镜像构建（CI infra lane 已含 compose/镜像构建）。

## 10. 排期

| 步骤 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| 0 | TBD-0~7 拍板（主理人评审修订版） | — | ✅（2026-10-14） |
| F1 | 双路径收口 + 装配入口 + store 测试 | 拍板 | 0.75~1 天 |
| F2 | PDF（WeasyPrint + CJK + 镜像包） | F1 | 1~1.5 天 |
| F3 | DOCX（python-docx 写作侧） | F1 | 0.5~1 天 |
| F4 | PPT（后置，另估） | 独立决策 | 不计入 v1 |
| F5 | 图表（先定统计 spec；导出图片后置） | 独立决策 | 不计入 v1 |
| 收口 | 前端/e2e/文档/镜像与峰值资源实测 | F1~F3 | 至少 1 天，按实测调整 |

v1（F1 + PDF + DOCX）粗估 **约 4~6 天的工程下界**，不含多副本全局限流改造、PDF 复杂长文档调优或线上观察；PPT/图表不纳入该估算。

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-13 | 文档 | 批次 5 立项草稿（调研 §4） | 建稿：F1 收口 / F2 PDF / F3 DOCX / F4 PPT（可裁）/ F5 图表五单元拆解（含代码现状盘点 + 行号）；TBD-0~7（推荐 + 待拍板）；DoD/风险/测试/排期 | 本文档 |
| 2026-10-13 | 评审补充 | 导出契约与部署评审 | 补充统一导出模型、HTML/URL 安全、图表终局数据契约及容器实测策略；推荐首批 PDF/DOCX，PPT 与在线图表另行确认 | 本文档 §12 |
| 2026-10-13 | 文档 | 飞书镜像建立 | 镜像至 `DeepResearch 需求文档` 文件夹（`WPKod8BjcoGBH0xUyhkcXJhgnDd`；自引用已回填） | — |
| 2026-10-14 | 评审修订 | 主理人 v1 收窄评审（§12.5 问题清单） | 统一口径：v1 = F1 + PDF + DOCX（PPT/F5 后置不计排期）；writer 原文改为「图内 writer 边界断言」并明确历史产物不还原原文；分享仅 Markdown + 披露边界明确；TBD-6 改「生成前输入/耗时/输出硬上限 + 跨副本准入推广前定」；排期收口按实测调整 | 本文档 |
| 2026-10-14 | 开工 | 主理人确认按修订版开工 | 状态草稿→进行中；Issue #225；TBD-0~7 按 §5 修订口径拍板（登记见 §12.7）；实施顺序 F1 → PDF spike → PDF → DOCX | #225 |
| 2026-10-14 | 文档 | 飞书镜像重建（开工版） | 按开工版重建至 `DeepResearch 需求文档` 文件夹（`CfGVdplPGoaUEzxxTYocYEJgnwc`；自引用回填；立项版已删） | — |

## 12. 评审结论与补充设计

### 12.1 合理性结论

PDF、DOCX 和统一导出契约有明确用户价值；PPT 与图表则分别增加版式治理和数据语义面，建议不要把「竞品有」直接作为首发必要条件。推荐首批交付 F1+PDF+DOCX，PPT 后置为单独决策；图表先定义可审计的统计数据契约和一种导出路径，再考虑进入在线 `report_display`。保留报告正文不变、输出 gate 一致、按需生成的方向。

### 12.2 导出数据模型与路径

- **背景**：当前内存/store 双路径已出现头部和引用清单不一致，新增格式前先统一是正确的；但「分享链路同语义」与「分享页不提供导出」容易混淆。F1 应统一的是私有导出数据装配；分享端仍只读、不暴露新格式，并验证其访问行为未改变。
- 建议定义版本化 `ExportDocument` 中间模型：报告正文（原文）、元数据、规范化引用清单、附录、统计图表 spec。md/json/PDF/DOCX/PPT 都从同一模型渲染；不得让某一种格式再去解析另一种格式的导出结果。历史 run 缺失字段时采用显式 `unknown/not_available`，不伪造数据。
- 先定义各格式的等价口径：正文、标题层级、引用 ID/链接、来源和生成时间；页眉页脚、分页、图表外观可因格式不同。`report` 字节不变应在生成前后断言，而不是要求所有格式字节相同。
- Markdown 转 HTML 必须使用受限 renderer，禁用原始 HTML 或先净化；WeasyPrint 的 URL fetcher 应拒绝 file/http/https 资源，只允许受限的内嵌资源。不能让报告正文触发本机文件读取或网络请求。DOCX/PPT 也要对外链、超长字符串和异常表格设置限额。

### 12.3 图表契约

- 图表数据应来自终局 `export_json` 可复算的统计字段，并为统计投影加 schema version；不依赖 worker 内存态 `progress`，否则历史 run/store 导出无法复现。字段不足时显式显示不可用。先确认引用校验率、来源构成、降级分布等字段的定义、分母、缺失值和隐私阈值，再绘图。
- `report_display` 是呈现面，不应成为导出的唯一数据存储。推荐把 chart spec 作为导出装配模型的派生字段；如要在在线报告中展示，走独立展示事件/附录并明确其不属于 `report` 正文和评测文本。
- matplotlib 输出的逐字节确定性只在固定 Python/Matplotlib/字体/元数据和排序下成立。验收应锁定同一构建环境下的数据 spec 确定性、图像像素或规范化输出，并去除 PNG 时间戳等可变元数据，不承诺跨库版本逐字节稳定。
- base64 会放大体积并复制到 Markdown、HTML 和响应缓冲区。在线报告宜优先用受限内嵌图或图表数据，而下载导出可临时嵌入；设定单图/总图/报告输入字节上限，避免 450MB 容器内多次复制。超限返回结构化错误，不静默省略。

### 12.4 部署与发布策略

1. F1 先统一装配模型与内存/store 契约，确保 md/json 输出 gate、下载名和历史兼容性通过；这是新格式的唯一前置。
2. 用实际最长报告在目标 Docker 镜像上测 PDF 的峰值 RSS、生成耗时、字体回退和并发；决定 WeasyPrint 与 CJK 字体方案后再锁依赖。镜像/内存验证不通过时，先限并发/报告尺寸或拆独立导出 worker，不应仅调高容器上限。
3. DOCX 与 PDF 各自独立灰度；PPT 和图表通过用户任务数据确认使用价值后再纳入。对下载设置 `Cache-Control: private, no-store`，错误响应不泄漏报告内容。
4. 发布核对 FAQ、可访问性（PDF 文本可选择、图表有文字替代）、字体许可和历史 run 导出行为；保持分享页无导出。

**立项前应补入 TBD 的决策**：首发格式范围；图表是否进入在线报告或仅下载导出；终局统计快照的 schema/持久化位置；PDF HTML 安全 renderer 与 URL fetcher 策略；导出最大输入/输出、页数、并发和超限错误契约。排期应拆为 F1、PDF、DOCX 的独立验收，不把字体/容器验证与浏览器端 E2E 当作轻量收尾项。

### 12.5 当前稿问题清单与评审结论

| 优先级 | 发现 | 依据 / 影响 | 建议处置 |
|---|---|---|---|
| P1 | writer 原文“不变”目前无法由 store 路径证明 | `worker._persist_result()` 先取 `state.report_display or state.report`，并把该值写入终局 result 和 `report_md`；历史导出可能只剩展示版，无法恢复 writer 原文 | 把“不变”限定为导出功能不改研究图/评测 writer 输出；若要求持久化可审计原文，需另增原文 artifact/schema，并单独评估存储和保留成本 |
| P1 | 图表的产品位置与验收口径冲突 | F5 曾要求进入 `report_display`、同输入同字节；TBD-4/§12 建议仅导出图表、稳定 spec，不保证跨版本二进制一致 | 本轮已统一为后置项：v1 不改 `report_display`；图表进入范围前先拍板 schema 与统计定义 |
| P1 | 导出并发上限只用进程内信号量不足以覆盖多副本 | 每个 API 进程独立计数，副本数增加会同比增加同时渲染数；PDF 属 CPU/内存密集工作 | 小流量内测可同步渲染并固定副本数；生产推广前选 DB/Redis 全局准入或专用导出 worker，并做峰值 RSS/超时测试 |
| P1 | 首批范围和工期过宽 | F1 双路径修正、CJK PDF、DOCX、前端、镜像改造、历史 run 契约一次上线；原 4~6 天还包含 PPT/图表估算 | v1 锁定 F1+PDF+DOCX；先完成 PDF 容量 spike，再承诺交期；PPT/F5 另立项或后续批次 |
| P2 | 页数上限不能防止渲染阶段资源耗尽 | 只有 PDF 生成后才能可靠知道页数，内存和 CPU 峰值可能已经发生 | 生成前限制 Markdown/HTML 字节、表格/图像数、渲染时限；在可终止子进程或隔离 worker 内渲染；生成后页数上限仅作交付约束 |
| P2 | 分享链路既被列入 F1，又被写作“不扩分享” | 当前内存分享返回导出 Markdown，store 分享返回 `report_md` 原产物，确有路径差异；但修正时可能额外暴露元数据或引用 | 明确分享继续只返回 Markdown；只统一其内存/store 正文来源，并单独审批是否包含审计头/引用清单，不复用私有下载权限 |

**结论**：核心方向合理，推荐约束为「统一私有导出装配 + PDF/DOCX 按需生成」。当前不建议将 PPT、图表在线展示和跨副本渲染治理塞进同一首发批次。PDF 方案只有在目标镜像实测通过后才算设计闭环；若实测显示同步渲染无法满足 450MB 容器预算，应改为隔离 worker/作业队列，而不是继续增加格式后再处理容量。

### 12.6 同类实现与来源

- [GPT Researcher README](https://github.com/assafelovic/gpt-researcher) 明确列出 PDF、Word 等报告导出，并描述平均报告以 PDF、DOCX、Markdown 等格式提供。这支持用户对可下载办公格式的预期，但它是项目能力说明，不是对其渲染质量、依赖体积或运行成本的独立评测。
- **可借鉴的优化**：以 PDF/DOCX/Markdown 三种交付覆盖阅读、编辑和机器处理；先实现同一导出模型，再独立测容器成本。该同类证据不足以支持将 PPT、统计图表和在线展示附录同时纳入首发，因此这些仍应依据用户任务数据分批决定。

### 12.7 开工登记（2026-10-14）

- **主理人口令**：按评审修订版（v1 收窄）开工（2026-10-14）；Issue **#225**；分支 `feat/38-export-pipeline`（F1 先行）。
- **TBD-0~7 执行口径（按 §5 修订版）**：TBD-0 护栏 = **图内 writer 产物边界断言**（不用历史 `report_md`/`result.report` 反推原文）；TBD-1 全离线确定性栈（PDF=WeasyPrint + Noto CJK；DOCX=python-docx；F5 后置）；TBD-2 按需生成/内存返回/不落盘（可选 LRU）；TBD-3 **v1 = F1+PDF+DOCX**（PPT/F5 后置）；TBD-4 图表后置（先定终局 `export_json` 可复算统计 spec）；TBD-5 私有 store 路径统一 `render_markdown` 语义 + 分享仅 Markdown（内容来源统一、披露边界明确）；TBD-6 **生成前**输入字节/耗时/输出字节硬上限（页数仅交付约束；跨副本准入推广前定）；TBD-7 gate 全量复用 + Noto 许可。
- **§12.4 六项决策处置**：① 首发范围 —— ✅ F1+PDF+DOCX（TBD-3）；② 图表在线 —— ✅ 后置（F5 不进 v1）；③ 统计快照 —— ✅ 后置项的前置定义（终局 `export_json` 可复算 + schema version，见 TBD-4）；④ PDF 安全 renderer —— ✅ §12.2（受限 renderer + `url_fetcher` 仅内嵌资源）；⑤ 限额契约 —— ✅ TBD-6 修订版；⑥ 验收拆分 —— ✅ F1/PDF/DOCX 独立验收（§10）。
- **实施顺序**：F1（装配入口 + 双路径收口 + 分享边界 + store 契约测试）→ **PDF 容量 spike**（目标镜像实测峰值 RSS/耗时/字体回退；通过才锁依赖）→ PDF（镜像 apt/字体 + lock 外科式）→ DOCX → 前端/e2e/文档回填。v1 ≈4~6 天（工程下界；不含多副本全局限流改造与线上观察）。
- **挂账（明确不在 v1）**：可审计 writer 原文 artifact（若为硬需求另行设计 + 保留成本评估）；PPT（另证用户价值）；图表在线展示（先定统计字段/分母/缺失语义）；跨进程导出准入 / 专用导出 worker（推广前决策）。
