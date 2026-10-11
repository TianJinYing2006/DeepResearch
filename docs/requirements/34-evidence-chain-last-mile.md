# 需求-34-evidence-chain-last-mile

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> **来源**：`docs/roadmap.md` §2（批次 1）；需求 27 §12 挂账 **12.8 / 12.6**（关联 12.2 / 12.10 属批次 2）；
> EvidenceFlow 对照分析（可借鉴 5 条：强制快照 / 逐字引文 + offset / `find_in_source` / 零模型自检 / 分级处置）。
> **前置决策（P0，已拍板）**：E1~E5 会改 researcher 证据面（web 路径从「标题 + 搜索摘要」变为「实抓正文」）⇒
> 按 W8 教训（需求 8 §10.5）立项时先拍板 before 基线。**2026-10-11 拍板：不采**（记录与理由见 §5 TBD-0；
> 接受「与历史基线不可配对」的后果）。
> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/KmeVd0fsGo1WWkxz35ucMhy6nFe（`KmeVd0fsGo1WWkxz35ucMhy6nFe`，
> 位于 `DeepResearch 需求文档` 文件夹，lark-cli 直建）。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 34 |
| 标题 | 证据链「最后一公里」：网页快照 + 逐字引文 + 零模型自检（E1~E5） |
| 优先级 | P1 |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #188 |
| 关联 PR | #189 / #191 |
| 创建 / 更新 | 2026-10-11 |
| 前置评审 | TBD-0~8 **全部已拍板**（2026-10-11）：TBD-0 不采；TBD-1~8 五处「待确认」已逐项落档（见 §5）；**N1 ✅ 已完成（2026-10-11）** |

## 2. 问题背景

- **现状**：引用「不能凭空造」已做到——Writer 只写 `[来源: N]`，URL 由渲染层按编号回查持久化 finding 生成
  （`research_engine/render.py:43-89`）；但**「引用能回到原文那一段」未做到**：
  - web 证据只有标题 + 搜索摘要（`research_engine/agents/researcher.py:146-160`：`content = title + snippet`，
    截断后 confidence 0.6），**正文从未被读取**；
  - `research_engine/net/safe_fetch.py:1-14` 是**预置未接入**的 SSRF 安全抓取入口（scheme/全地址公网校验/
    禁重定向/pin IP/超时 10s/上限 2MB 契约齐备）；
  - `Citation` 无逐字引文与快照内偏移字段（`research_engine/state.py:121-152`）；
  - 长证据喂裁判用**头尾截断**（`research_engine/evidence.py:23-26`：>4000 字符保留头尾 + `…[证据截断]…`）
    ——静默问题已治，但「裁判看见的是否是 claim 的支持片段」无保证（挂账 12.6）。
- **对照物（EvidenceFlow）**：搜索只提供候选 URL，正文必须实抓并落盘快照；claim 绑定原文逐字引文 +
  offset 匹配 + 「原文匹配通过」比对；快照内二次定位；纯 Python 交付自检；证据链损坏与报告缺陷分级。
- **本批次不做**（批次 2）：12.4（来源身份分组）、E7（manifest 持久化，12.2）、E6（跨进程恢复，12.10）。

## 3. 需求分析

目标（一句话）：**每条引用都能回到已落盘快照的原文片段，且这条链由零模型检查兜底。**

- **E1 网页正文快照落盘**（12.8）：web 候选实际抓取正文并持久化快照（内容寻址）；抓取失败**降级不阻塞**
  （保留 snippet-only + 显式标记）。
- **E2 逐字引文 + offset 校验**（12.6 前半）：`Citation` 携带快照内逐字引文与偏移；校验器用**代码复核**
  「引文能在快照中定位」，不只信 LLM 裁决。
- **E3 `find_in_source` 快照内二次定位**（12.6 后半）：按 claim 在快照中检索支持窗口喂给裁判，
  替代 >4000 头尾截断；长文档不重复联网。
- **E4 零模型交付自检** `delivery_self_check`：纯 Python 三项——引文能否在快照中定位 / 是否落在被删除或
  废弃段落 / 版本号类断言是否有来源 URL；**只标注，不改交付状态**。
- **E5 证据链分级处置**：致命（如 `unknown_source_id`、快照中找不到引文）vs 可修（如正文含 URL、缺引用
  标注）——比现行「repair ≤1 次统一降格」更细。

成功定义（可量化，随 TBD 细化）：
- 冒烟：一次真实 research 的**每条引用**都带可定位的逐字引文（或显式 `missing` 标记）；
- 自检零模型、离线可跑，命中/误报有单测锁口径；
- 引用校验口径变化以**确定性断言**兜底（同 D-18：分辨率不足不得拿均值验收；TBD-0 已拍板不采 before 基线 ⇒ 不做配对）。

## 4. 当前设计（代码现状）

| 层 | 位置 | 现状 | 本批次处置 |
|---|---|---|---|
| web 检索 | `agents/researcher.py:146-160` | 只取 title+snippet（`_truncate_head(WEB_SNIPPET_MAX)`，conf=0.6） | **E1**：候选 → 实抓正文 → 快照 |
| 安全抓取 | `net/safe_fetch.py` | SSRF 契约预置、未接入主链路 | **E1**：接入（唯一抓取入口） |
| 证据身份 | `state.py:103-118`、`evidence.py:29-60` | `ev_` 内容寻址 + `content_hash`/`retrieved_at`；原文层只增不改 | **E1**：finding 增 `snapshot_id`/`snapshot_locator`（字段预留，见 TBD-8） |
| 长证据 | `evidence.py:23-26` | >4000 头尾截断 | **E3**：改按 claim 检索窗口 |
| 引用模型 | `state.py:121-152` | 无逐字引文 / 快照偏移 | **E2**：增 `quote`/`quote_start`/`quote_end`/`snapshot_id` |
| 渲染 | `render.py:43-89` | 编号 → findings 回查、类型标注 | **E4**：`report_display` 附录标注自检结果 |
| 校验 | `agents/validator.py`、`evidence.resolve_evidence_text`（ADR-0011 R04） | claim 级裁决 + origin 状态（raw/truncated/missing/partial） | **E2/E5**：quote 定位复核 + 分级 |
| 返工 | `repair.py`（ADR-0011 R02/R03） | 确证失败可删、UNKNOWN 保留、有界闭环 | **E5**：分级入口对接 |

## 5. 优化方案（2026-10-11：问号 → 推荐 → 拍板落档）

> **本轮优化做了什么**：原稿 TBD-1~8 均为开放式提问。本轮结合**当前背景**（批次 0 收尾状态、
> **P0 数据库备份未部署**、**ICP 备案卡住**、需求 33 OTel 已迁移落地、L3-A 5 人封闭验证）与
> **代码逐项核实**，把每项收敛为「推荐方案 + 理由（优先给算术账/成本账）+ 拍板点」（2026-10-11 已由主理人逐项拍板，见各条「已拍板」）。
> ⚠️ **三处结构性缺陷已修正，见 §5.0**，优先于各 TBD 细节；原提问保留在各条「原提案」行便于追溯。

### 5.0 结构性缺陷（本轮核实发现，优先处置）

| 编号 | 缺陷 | 修正 |
|---|---|---|
| **A1** | **依赖顺序反了**：§2 / §5 / §10 三处均写「E3 依赖 E1/E2，排在 E2 之后」，但 §5 目标链路里「claim 在快照中定位引文（offset）」**在**「校验器代码复核」**之前** —— 即 E3 实质是 E2 的**前置** | 若 TBD-4 选 B（代码定位引文），**「定位」这件事本身就是 E3**，E2 只是把定位结果接到 `Citation` 与校验器上 ⇒ 重排为 **E1 → E3 → E2 →（E4/E5）**（见 §10）。**编号保持不变**，以免与 `roadmap.md` §2 / Issue #188 脱钩 |
| **A2** | **成功定义与已拍板的前置决策自相矛盾**：§3 成功定义第三条、§9 测试策略「eval」行、§7 DoD「测试」行 —— **三处都要求 before/after eval 配对**，但 **TBD-0 已于 2026-10-11 拍板不采 before 基线** ⇒ 配对无从成立 | 三处统一改为 **A 类确定性断言**（同 **D-18** 口径：分辨率不足时**不得**拿均值做验收），见 §7 修订 |
| **A3** | **TBD-8 的「预留 locator 字段」不成立**：`locator` 在 **RAG 路径早已存在并落地** | 链路：`rag/snapshot.py:43`（`SnapshotBlock.locator`，页码/幻灯片/章节）→ `rag/chunker_v2.py:103-136`（记 `row_range`）→ `agents/researcher.py:210`（写入 `metadata["locator"]`）→ `evidence.py:161`（轻量索引唯一读取方）。⇒ 本批次**不是「预留」，而是 web 路径填空 + 与 RAG 口径对齐**；真新增字段**只有 `snapshot_id` 一个**（原稿写 `snapshot_id`/`snapshot_locator` 两个 ⇒ 收敛为一个，locator 复用既有键） |

### 5.1 新增硬约束（来自当前背景，优先级高于各 TBD）

| 编号 | 约束 | 依据（当前背景事实） |
|---|---|---|
| **N1** | ✅ **已完成（2026-10-11）**（原约束：E1 落盘前必须先完成「备份 cron 部署 + 一次真实恢复演练」） | 核实：cron 已在跑（2026-10-02 起，每日 03:30 加密 + COS，日志全 ok）+ **云上真实恢复演练通过**（离线私钥解密 → 临时容器恢复 → 断言 21/11/22 与生产一致）。记录：`release-runbook.md` §10.3。原缺口（只有代码包备份、无 `pg_dump`）已关闭 ⇒ E1 可开始写库 |
| **N2** | **抓取必须按「大陆单机出口」设计，境外站点快速失败 + 熔断** | 公网入口当前为 **IP 直访 + 自签证书**，域名 `ikaoju.cn` **未备案**，大陆云拦 80/443（`domain-cutover.md`）。同一台大陆云主机上，Bocha 返回的境外候选会大面积超时（safe_fetch 超时 **10s**）⇒ 不加处置会显著拉长单轮（当前 **48~51 分钟**，硬闸 **3600s**） |
| **N3** | **抓取是新的外部网络调用，必须进 OTel trace 与运行溯源** | 需求 33 已将引擎埋点由 Langfuse 迁到 OTel 原生（`research_engine/observability.py` 重写、`gen_ai.*` generation 单点、`dr.cost_estimate_cny`）。抓取不落 span ⇒ 「运行溯源」与**成本守恒**（W8 确定性 DoD 8 项之一）出现盲区 |
| **N4** | **自检/分级结果只走后端 `report_display` 附录，前端不重算** | CI 跑 `tools/check_frontend_boundary.py`（前端不得 import `research_engine`）。既有模式：`render.py:187` `build_planner_governance` —— E4 直接照抄该模式 |
| **N5** | **抓取失败不得写 `degradation_log`、不得推导 `run_status=degraded`** | 同 **D-03** 的算术理由：`resolve_run_status()` 规则是「`degradation_log` 非空 + 有报告 ⇒ `degraded`」；大陆云上公网抓取失败率高，若算故障 ⇒ **几乎每轮都是 `degraded`**，故障可归因率（W8 核心指标 0%→100%）被污染。既有先例：`render.py:232` `build_retrieval_warning` 明确「不额外写 degradation_log」 |

### 5.2 目标链路（重排后）

`搜索候选 → safe_fetch 抓取 → 正文提取 → 快照落盘（内容寻址 + extractor_version）→
finding 引用 snapshot_id → 按 claim 在快照中检索窗口（find_in_source）→ 引文逐字定位 + offset →
校验器代码复核 → 交付自检（零模型）→ 分级处置 → 渲染标注`

---

- **TBD-0（前置，P0）before 基线 —— ✅ 已拍板：不采（2026-10-11）**：
  - 背景：E1~E5 改变输出证据面（web 内容从摘要变正文）；原建议采（20 题 × 3 轮 ≈ ¥4 / 3.6h，错过不可逆）。
  - 拍板记录：**不采**。理由（据现状记录）：W8 配对结论为四指标**不可判定**（分辨率不足，见
    `docs/eval-w8-after-baseline.md`）；本批次质量结论拟随批次 2 的 **12.11（新基线评测）**以新口径统一重建
    ⇒ 不追加前置采集。
  - 接受的后果：E1~E5 落地后**与历史基线不可配对**；如将来需要「改动前」口径，只能以代码回滚重建（成本更高）。
- **TBD-1 快照存储与生命周期** —— 推荐：**PG 两表（全局内容寻址 + run 关联）**；CLI/web 共用同一 `SnapshotStore` 接口
  - 原提案：CLI 本地目录 vs web MinIO / PG？命名、去重、上限、保留期、降级路径均未定。
  - 推荐落点：
    - `snapshots(sha256 TEXT PK, url, content_type, source_bytes INT, text, text_truncated BOOL,
      extractor_version, created_at)` —— **全局内容寻址存一份**；
    - `run_snapshots(run_id FK→runs(id) ON DELETE CASCADE, sha256 FK, PRIMARY KEY(run_id, sha256))`
      —— run 内去重 + 删除 run 时级联解除关联；
    - 本体清理靠**保留期任务**（无引用 + 超期），不靠外键级联；
    - 迁移 `0022_snapshots.sql` + `migrations/checks/0022_schema_assert.sql`（接 CI `infra` job，
      照抄既有迁移的写法；现库最新为 `0021_report_shares.sql`）。
  - 理由（**成本账，不是选型偏好**）：
    - 引入 MinIO = **新增一个必须备份却没有备份的数据面** —— 当前 P0 备份都没装（N1），再开一个
      备份盲区不可接受；
    - PG 侧已有全套资产：持久化（PR #12）、备份脚本（`tools/backup.sh` 即 `pg_dump`）、
      CI 真实恢复演练（P8-A）、级联删除（0002/0003 已断言）；
    - **体积测算**：每 run 最多 15 次抓取 × 入库上限 **64 KB** ≈ **960 KB**；L3-A 5 人 × 1 次/日 ×
      30 天 = **150 run/月** ⇒ 最坏 **≈144 MB/月**，内容寻址去重后实测预计 30~60 MB/月；
      保留期 30 天 ⇒ **稳态约 30~60 MB**。PG 无压力，且这个增量正是 N1 要求先装备份的理由。
  - **已拍板（2026-10-11）：64 KB 采用** —— 按单页提取后的 **UTF-8 字节数**计算；超限**截断并标记**
    （`text_truncated=true`），**不得静默丢弃**。

- **TBD-2 抓取策略** —— 推荐：**top-3 + 三预算硬闸 + 同域名串行限速 + 境外快速失败熔断**
  - 原提案：抓哪些（top-N / 置信度阈值）？并发与域名限速？失败分类？时间/字节计入哪个口径？
  - 推荐落点：

    | 项 | 取值 | 说明 |
    |---|---|---|
    | 抓取范围 | `resp.results[:3]` | `max_results=8` **不变**，其余 5 条仍转 snippet-only finding |
    | run 级次数闸 | ≤ **15** 次 | 触顶后其余候选直接 `snapshot_status=skipped_budget` |
    | run 级字节闸 | ≤ **5 MB** | 原始响应累计 |
    | run 级时间闸 | ≤ **90 s** | 计入 run deadline（硬闸 **3600s**） |
    | 并发 | 全局 **4**、同域名 **1** | 复用 researcher 的线程池语义 |
    | 同域名间隔 | ≥ **200 ms** | 限速兜底 |
    | 自动重试 | ≤ **2** 次/目标 | **计入 15 次抓取额度**（拍板 2026-10-11） |
    | robots.txt | **遵守**；不可检查时仅抓「明确批准来源」 | 拒绝 ⇒ `snapshot_status=skipped_robots`（不写 `degradation_log`；拍板 2026-10-11） |
    | 境外连接超时 | **3 s**（读取仍 10 s） | 快速失败，避免拉长单轮 |
    | 域名熔断 | 连续失败 **2** 次 ⇒ 本 run 不再抓 | 针对大陆云出口不可达 |

  - **失败分类 → 复用既有工具层 5 值，不新增枚举**（见 TBD-7 红线②）：

    | 失败形态 | `FailureReason` | 降级动作 |
    |---|---|---|
    | 连接 / 读取超时 | `timeout` | snippet-only + `snapshot_status=missing` |
    | 4xx / 5xx | `provider_error` | 同上 |
    | 3xx（禁重定向） | `provider_error` | 同上 |
    | 非 HTML / 非文本 | `parse_error` | 同上 |
    | 响应超 2 MB | `parse_error` | 同上 |
    | 未配置 / 存储不可用 | `not_configured` | `snapshot_status=disabled` |

    **以上全部不写 `degradation_log`**（N5 / D-03）。
  - 理由（**算术账**）：当前单轮 **48~51 分钟**，硬闸 **3600 s** ⇒ 抓取预算 90 s 占比 **≤2.5%**，
    不改变硬闸行为，也不破坏 W8 的耗时基线。抓 top-8 全量则最坏 15 跳 × 8 × 10 s = 1200 s，
    会逼近硬闸 ⇒ 不可取。
  - **已拍板（2026-10-11）**：① 抓取上限 **15** / 并发 **4** **采用**；自动重试 ≤2 次，**计入抓取额度**。
    ② **robots.txt 遵守**（原「倾向不做」被否决）—— 落地进本批次（按域名检查）；某目标无法完成检查
    （或实现暂不支持）时 ⇒ **仅抓「明确批准的来源」，不开放任意站点抓取**（默认拒绝）；
    robots 拒绝 ⇒ `snapshot_status=skipped_robots`（与 `skipped_budget` 并列；**不写 `degradation_log`**）。

- **TBD-3 正文提取与清洗** —— 推荐：**bs4 结构化优先 + 空白折叠 + 64 KB 上限 + `extractor_version` 强制落盘**
  - 原提案：HTML→text 规则？编码与上限？PDF 是否抓（倾向不抓）？
  - 推荐落点：
    - 移除 `script / style / noscript / nav / header / footer / aside`；
    - 优先取 `<main>` → `<article>` → 兜 `<body>`；
    - 空白折叠（连续空白 → 单空格），保留段落换行；
    - 入库上限 **64 KB**，超出 `text_truncated=true`；
    - 编码：优先 HTTP `Content-Type` charset → 其次 `<meta charset>` → 兜 utf-8 `errors="replace"`
      （与 `safe_fetch.py:43` 的 `FetchResult.text` 现口径一致）；
    - PDF：按 **content-type 或扩展名双重判定** ⇒ 不抓，登记 `skipped_pdf` 计数（进运行溯源）；
    - ⚠️ **必须落 `extractor_version`**（初值 `webtext.v1`）：同 **D-06** 机理 —— 清洗规则变了历史 offset
      就失效，没有版本号就无法判断旧 offset 该不该信。
  - 理由：复用既有依赖（bs4 已在依赖里）、降级明确（提取失败 ⇒ snippet-only）。
  - **已拍板（2026-10-11）：本批次不做** —— 页面无直接可读正文时**标记「暂不支持」**，不启动 JS 浏览器渲染。

- **TBD-4 引文产生方式** —— 推荐：**B（代码定位）**，但必须落地为**两段式**，否则不可实现
  - 原提案：A（Writer 输出引文）/ B（代码定位，倾向）/ C（双轨）。
  - 推荐落点（两段式）：
    1. **候选窗口**：claim → 关键词抽取 → 快照滑窗（窗长 800 / 步长 100 字符）→ 字面重叠打分
       （中文按字 2-gram，与既有 BM25 语料口径同源）→ 取 Top-3 窗口；
    2. **精定位**：在候选窗口内按 L0→L1→L2 逐级规范化匹配；命中 ⇒ 落 `quote` + `char_start/char_end`；
       未命中 ⇒ `quote_status=not_found`（进 E5 分级）。
  - 为什么**不用 A**：Writer 给出的"引文"无法保证逐字 —— 这恰恰是本批次要防的问题；
    且 A 会扩大 Writer 协议面（与需求 28 的编号协议收口方向相反）。
  - 为什么**不用 C**：C = A + B，多一次模型输出只换定位速度，本批次规模（≤15 次抓取/run）不需要。
  - ⚠️ **由此推出 A1 的重排**：第 1 段（候选窗口检索）**就是 E3 的内核**，E2 只是消费它的输出
    ⇒ E3 必须先于 E2 落地。
  - **已拍板（2026-10-11）：新增轻量关键词抽取** —— `extract_entities` 继续用于**实体抽取**，
    **避免把实体识别结果当关键词**。

- **TBD-5 offset 校验口径** —— 推荐：**三级匹配 + 记录 `match_level`；偏移量而非定位符**

    | 级别 | 规则 | 结果 |
    |---|---|---|
    | **L0** | 快照文本精确子串包含 | `located` |
    | **L1** | 连续空白折叠为单空格后匹配 | `located` |
    | **L2** | 全半角统一 + 省略号 / 引号 / 破折号归一 + L1 | `located` |
    | — | 三级皆未命中 | `quote_status=not_found` → 进 E5 |

  - 存储形态：**快照内字符偏移（`char_start` / `char_end`）**，不用定位符（XPath / CSS）。
    理由：快照由 `sha256` 锁定为**不可变**，偏移可复算；定位符会随页面结构失效。
  - 🚨 **红线**：offset 只对「同一 `extractor_version` + 同一 `snapshot_sha256`」成立；两者任一变化
    ⇒ `quote_status=stale`，**不得**据此判「引用错误」（同 ADR-0011「UNKNOWN 不得当事实错误」）。

- **TBD-6 自检项与输出** —— 推荐：**三项固定 + 判据可执行 + 只标注**

    | 检查项 | 判据（零模型、离线） |
    |---|---|
    | `quote_locatable` | 每条引用的 `quote` 能在其 `snapshot_id` 对应文本中按 L0~L2 定位 |
    | `quote_in_dropped_span` | 引文的偏移区间与 repair 删除 / 降格区间**有交集** |
    | `version_claim_has_source` | 命中版本号断言（正则 `v?\d+(\.\d+){1,3}` / 「第 X 版」/「20XX 年 X 月版」）的**句**必须带引用标记 |

  - 输出：`state.delivery_self_check = {"checks": [...], "counts": {...}}`；经 `render.py` 追加为
    `report_display` 附录 —— **照抄 `build_planner_governance`（`render.py:187`）的既有模式**（N4）。
  - ⚠️ **新依赖（原稿 §5 / §8 均未登记）**：第 2 项需要 repair 回写「被删 / 降格区间明细」。
    当前 `repair_report`（`repair.py:61-123`）只返回 `removed_claims` **计数**，区间仅存在于局部变量
    `spans` ⇒ **必须先扩充 `stats` 或新增返回值**，否则该项无法计算。
  - **只标注，不改交付状态**；**不写 `degradation_log`**（N5）。

- **TBD-7 分级处置** —— 推荐：**清单化 + 明确动作 + 两条红线**

    | 类别 | 判定 | 动作 |
    |---|---|---|
    | **致命** | `unknown_source_id`（引用编号在 findings 中不存在） | **阻断**：该引用不得出现在报告 ⇒ 走 repair 删除该句（复用 R02） |
    | **致命** | `quote_not_found` **且** `snapshot_status=ok`（快照在、引文找不到） | **降格标注**，**不删除** —— 可能是 claim 措辞问题，**不等于事实错误** |
    | 可修 | 正文含裸 URL 但缺引用标记 | repair 降格（复用 R03） |
    | 可修 | `snapshot_status=missing`（抓取失败） | 标注「来源正文未取得」，**不删除** |
    | 可修 | `quote_status=stale`（提取器版本变化） | 标注「引文定位待重新提取」，**不删除、不算失败** |

  - 🚨 **红线①**：**不得**把「校验未完成 / 快照缺失 / offset 过期」判成「事实错误」
    （ADR-0011 红线；与 `repair.py:35-39` `_definitively_failed`「UNKNOWN 不删除」同源）。
  - 🚨 **红线②**：若评审认为**必须新增 `FailureReason` 枚举值**，须**同步改两处硬断言** ——
    `tests/test_arm1_run_status.py:149-151`（`len(ALL_REASONS) == 9` / `len(TOOL_REASONS) == 5` /
    `len(NON_TOOL_REASONS) == 4`）与 `tests/test_arm3_python_version_gate.py:164`。
    ⇒ **本批次推荐不新增枚举**：抓取失败全部映射到既有工具层 5 值，用 `component="web_fetch"`
    区分来源（component 为自由文本，不受枚举表约束）。
  - 与 `run_status` 的衔接：抓取失败**不推导** `degraded`（N5 算术理由），三态断言保持不变。

- **TBD-8 与批次 2 的边界** —— 修正：**不是「预留 locator」，而是「web 填空 + 与 RAG 对齐」**
  - 原提案：仅预留 `snapshot_id` / locator 字段与 12.2 / 12.10 对接。
  - 修正落点（依据 §5.0 A3 的核实）：
    - `locator` **RAG 路径已落地**：`rag/snapshot.py:43` → `rag/chunker_v2.py:103-136`（`row_range`）
      → `agents/researcher.py:210`（写入 metadata）→ `evidence.py:161`（索引读取方）。
      故本批次只需**定义 web locator schema 并与 RAG 并列**：
      - RAG：`{page | slide | row_range}`
      - web：`{snapshot_id, char_start, char_end, block_path?}`
      - 由 `source_type` 区分，不新增并列体系；
    - **真新增字段只有 `ResearchFinding.metadata["snapshot_id"]` 一个**（原稿写两个 ⇒ 收敛）；
    - 批次 2 对接点：12.4（来源身份分组，按 `source_type` + locator 归组）、
      E7（manifest 持久化引用 `snapshot_id`）、E6（跨进程恢复：快照**不进 checkpoint**，按 `snapshot_id` 回查）；
    - 🚨 **历史产物一律不回填**（D-14 纪律：写侧只出新键 + 读侧 dual-read）。

## 6. 设计策略

- **不信任模型输出**：引文由代码在快照中定位（TBD-4 倾向 B），模型只负责 claim；自检层纯 Python 兜底；
- **复用既有资产**：`safe_fetch` 安全契约、`ev_` 内容寻址体系、`resolve_evidence_text` origin 状态机、
  repair 有界闭环；不另造并行体系；
- **降级优先**：抓取失败不阻塞研究（snippet-only + 标记）；自检失败只标注；
- **诚实边界**：快照仅覆盖 web 路径（RAG/arXiv 已有原文层）；PDF/动态页登记不做；
- **纪律**：先拍 TBD-0（已拍板：不采）；改动输出口径改以**确定性断言**验收 + 「尺子变了」口径断点登记（D-18 / Arm 6 机制）。

## 7. 验收标准（DoD）

- [x] TBD-0 决策：**不采**（2026-10-11 拍板；理由与后果见 §5）
- [x] **N1（第 0 项，硬前置）**：✅ **已完成（2026-10-11）** —— cron 已在跑（每日 03:30 加密 + COS）
      + 真实恢复演练通过（21/11/22 与生产逐项一致；记录见 `release-runbook.md` §10.3）⇒ E1 可开始写库
- [ ] E1：`safe_fetch` 接入 researcher（唯一抓取入口，**含 robots 检查**）；web 候选 top-3 落盘快照
      （`snapshots` + `run_snapshots` 两表，含 `extractor_version`）；失败降级为 snippet-only 不阻塞；
      robots 拒绝 ⇒ `snapshot_status=skipped_robots`（不写 `degradation_log`）
- [ ] **E3（提前，先于 E2）**：`find_in_source` 落地 —— 快照滑窗 + 字面重叠打分产出候选窗口；
      长证据改按 claim 窗口喂裁判（替代 `evidence.py:139-144` 的 >4000 头尾截断）
- [ ] **E2（依赖 E3）**：`Citation` 增 `quote` / `char_start` / `char_end` / `snapshot_id`；
      校验器代码复核「引文可定位」（L0~L2 + `match_level`）
- [ ] E4：`delivery_self_check` 零模型三项；输出入 `report_display`（只标注、不写 `degradation_log`）
- [ ] E5：分级处置落地（致命 vs 可修清单化；`unknown_source_id` 阻断、其余降格不删除）
- [ ] 测试（**口径已按 §5.0 A2 改为确定性断言，不再要求 eval 配对**）：
      - [ ] 单测（离线）：抓取 mock（成功 / 超时 / 非 HTML / 超限 / 3xx / robots 拒绝 / 重试）、三级规范化匹配、
            自检三项（含误报锁）、分级判定
      - [ ] 集成：fake fetch + 小快照端到端（finding → snapshot → quote → 自检 → 渲染附录）
      - [ ] **确定性断言（替代 eval 配对）**：一次真实冒烟中
            **每条引用要么 `quote_status ∈ {located, not_found}`，要么显式 `snapshot_status=missing`；
            零条「无 quote 且无 missing 标记」的引用**；located 者可按 L0~L2 离线复算定位
      - [ ] **故障可归因率不退化**：抓取失败不入 `degradation_log`，
            `tests/test_arm1_run_status.py` 全绿（三态断言未松动）
      - [ ] **预算闸可观测**：单次 run 的抓取次数 / 字节 / 时间三项均有值且未越上限
      - [ ] 自检离线可跑（无网络、无 key）
      - [ ] 真实冒烟（引用可回溯快照）
- [ ] 文档：README / 渲染口径同步（落地才写）；需求 27 §12.6 / §12.8 状态回填；
      **eval 报告「尺子变了」检查登记本批次口径断点**（Arm 6 已有该机制）

## 8. 影响范围与风险

- 动：`agents/researcher.py`、`net/safe_fetch.py`（接入）、`evidence.py`、`state.py`、`agents/validator.py`、
  `repair.py`、`render.py`、`config.py`、`tests/`；web 侧快照存储（TBD-1）。
- 风险①：**输出口径变化**——引用校验/覆盖等指标会漂移 ⇒ 以确定性断言验收 + 口径断点登记（TBD-0 不采 before 基线，见 §5/§9）。
- 风险②：抓取网络依赖（被墙 / 反爬 / 超时）⇒ 分类降级 + 时间预算上限。
- 风险③：快照体积与隐私（公网页面；web 多用户隔离与保留期必须对齐 12.2 约束）。
- 风险④：误杀——自检/分级把「校验未完成」误判为「事实错误」（ADR-0011 红线）。
- 兜底：所有新层 fail 降级为现状行为（snippet-only / 头尾截断），不阻塞交付。

## 9. 测试策略

- 单测（离线）：抓取 mock（成功 / 超时 / 非 HTML / 超限 / 3xx / robots 拒绝 / 重试）、三级规范化匹配（L0~L2）、
  自检三项（含**误报**锁）、分级判定；
- 集成：fake fetch + 小快照端到端（finding → snapshot → quote → 自检 → 渲染附录）；
- ⛔ **eval 配对已移除**（口径冲突，见 §5.0 A2）：TBD-0 已拍板不采 before 基线 ⇒ before/after
  配对无从成立；本批次质量结论随批次 2 的 **12.11（新基线评测）**以新口径统一重建。
- ✅ **替代：确定性断言**（同 D-18 口径）—— 引用可回溯率 + 零无标记引用 + 自检离线可跑 +
  故障可归因率不退化 + 预算闸可观测（判据见 §7）。
- 冒烟：真实 topic → 抽查引用可回溯到快照片段 + 自检附录输出 + 抓取预算三项计数。

## 10. 排期（引用 roadmap §2）

| 步骤 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| **0** | ✅ **N1：备份 cron 部署 + 一次真实恢复演练**（**2026-10-11 完成**；记录见 `release-runbook.md` §10.3） | — | 0（已完成） |
| 0' | ✅ 设计评审拍板（**2026-10-11 完成**，5 项落档见 §5） | — | 0（已完成） |
| E1 | 快照落盘（`safe_fetch` 接入 → 提取 → 两表落盘） | N1 + 评审 | 2~3 天 |
| **E3** | **`find_in_source` 定位内核**（滑窗 + 打分 + 窗口喂裁判） | E1 | 1~1.5 天 |
| **E2** | **逐字引文 + offset 落 `Citation` + 校验器代码复核** | **E3** | 1~1.5 天 |
| E4 | `delivery_self_check` 零模型三项 + 渲染附录 | E2（第 3 项可先行） | 1 天 |
| E5 | 分级处置清单化落地 | E2 | 1~1.5 天 |
| 收口 | 确定性断言 + 真实冒烟 + 口径断点登记 + 文档 | 全部 | 1 天 |

合计 ≈ **1.5~2 周**（较原稿 +0.5 天：N1 备份前置；robots 检查落地含在 E1 内）；E1~E5 属同一交付单元。

> ⚠️ **口径提示**：TBD-0 不采 before 基线 ⇒ 本批次与历史基线**不可配对**。
> 若将来需要「改动前」口径，只能以代码回滚重建（成本更高）—— 此后果已于 2026-10-11 被主理人接受。

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-11 | 文档 | 批次 1 立项（roadmap §2） | 建稿（草稿）：现状盘点（含代码行号）、E1~E5 方案草案、TBD-0~8、DoD、排期；Issue #188 | 本文档 |
| 2026-10-11 | 拍板 | TBD-0 before 基线 | **终判：不采**（主理人 2026-10-11）——W8 四指标不可判定、批次 2 以 12.11 新基线统一重建口径；接受与历史基线不可配对；飞书镜像已建（`KmeVd0fsGo1WWkxz35ucMhy6nFe`，lark-cli 直入文件夹） | 本文档 |
| 2026-10-11 | 优化 | 结合当前背景优化 TBD-0~8 设计方案 | §5 重写为「推荐 + 理由 + 待确认」；新增 **§5.0 三处结构性缺陷**（A1 依赖顺序反了 / A2 成功定义与 TBD-0 拍板矛盾 / A3 locator 早已落地非预留）与 **§5.1 五条新增硬约束**（N1 备份前置 / N2 大陆出口快速失败 / N3 抓取进 OTel / N4 自检只走后端附录 / N5 抓取失败不写 degradation_log）；§7 DoD 移除 eval 配对改确定性断言；§9 同步；§10 排期重排为 E1→E3→E2 并前置 N1；均基于代码核实（`safe_fetch.py:25-26`/`agent/researcher.py:47,128-160,210`/`evidence.py:139-144,161`/`rag/snapshot.py:43`） | 本文档 |
| 2026-10-11 | 复核 | 优化版入库前复核修正 | 清理残留矛盾（§3/§6/§8 三处 eval 配对 → 确定性断言，对齐 §5.0 A2）；TBD-1 迁移编号勘误（`0005` → `0022`，现库最新 `0021`）；代码引用抽查通过（`researcher.py:210` / `render.py:187,232` / `repair.py:70-121` / `test_arm1_run_status.py:149-151` / `test_arm3_python_version_gate.py:164`） | 本文档 |
| 2026-10-11 | 拍板 | TBD-1~4 五处「待确认」逐项拍板（主理人） | ①TBD-1：**64 KB 采用**（按 UTF-8 字节数、超限截断必标记不静默）②TBD-2①：15 次 / 并发 4 **采用** + 重试 ≤2 次计入额度 ③TBD-2②：**robots.txt 遵守**（原「倾向不做」被否决；不可检查时仅抓明确批准来源、默认拒绝）④TBD-3：动态渲染不做、无正文标「暂不支持」⑤TBD-4：新增轻量关键词抽取（`extract_entities` 仍用于实体）；N1 硬前置重申 | 本文档 |
| 2026-10-11 | 执行 | N1 硬前置完成（第 0 项） | 核实服务器 cron 已在跑（2026-10-02 起每日 03:30 加密 + COS；日志全 ok）+ **云上真实恢复演练通过**（离线私钥解密 208 条目 → 临时容器流式恢复 → 断言 21/11/22 与生产逐项一致 → 清理）；md5 三方一致；顺手修复服务器 `tools/backup-verify.sh` 事故空目录；记录见 `release-runbook.md` §10.3 | 本文档 |
