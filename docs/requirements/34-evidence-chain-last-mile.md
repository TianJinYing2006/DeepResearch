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
| 关联 PR | — |
| 创建 / 更新 | 2026-10-11 |
| 前置评审 | TBD-0 已拍板（不采，2026-10-11）；TBD-1~8 待设计评审逐项拍板 |

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
- 引用校验口径变化前后有 **eval 配对**（before/after，见 TBD-0）。

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

## 5. 优化方案（草案，TBD 待设计评审）

**目标链路**：`搜索候选 → safe_fetch 抓取 → 正文提取 → 快照落盘（内容寻址）→ finding 引用快照 →
claim 在快照中定位引文（offset）→ 校验器代码复核 → 交付自检 → 分级处置 → 渲染标注`。

- **TBD-0（前置，P0）before 基线 —— ✅ 已拍板：不采（2026-10-11）**：
  - 背景：E1~E5 改变输出证据面（web 内容从摘要变正文）；原建议采（20 题 × 3 轮 ≈ ¥4 / 3.6h，错过不可逆）。
  - 拍板记录：**不采**。理由（据现状记录）：W8 配对结论为四指标**不可判定**（分辨率不足，见
    `docs/eval-w8-after-baseline.md`）；本批次质量结论拟随批次 2 的 **12.11（新基线评测）**以新口径统一重建
    ⇒ 不追加前置采集。
  - 接受的后果：E1~E5 落地后**与历史基线不可配对**；如将来需要「改动前」口径，只能以代码回滚重建（成本更高）。
- **TBD-1 快照存储与生命周期**：engine CLI 与 web 两形态——
  - CLI：本地目录（如 `results/snapshots/` 或独立目录）？web：复用 MinIO `objectstore` / PG？
  - 内容寻址命名（`snap_<sha256[:16]>`，与 `evidence_id` 体系衔接）、去重、单快照大小上限；
  - **用户隔离与保留期**对齐 12.2 约束；CLI 无对象存储时的降级路径。
- **TBD-2 抓取策略**：抓哪些（web 候选 top-N / 置信度阈值）；并发与域名限速（呼应 robots/速率限制）；
  失败分类降级（超时/拒绝/非 HTML → snippet-only + `snapshot_status=missing`，不阻塞）；
  抓取时间/字节计入 run deadline 与预算的口径。
- **TBD-3 正文提取与清洗**：HTML→text（bs4 已有依赖；去 script/style/nav；正文启发式）；
  编码与上限；PDF 链接是否抓（倾向：本批次不抓，登记）。
- **TBD-4 引文产生方式（信任模型问题）**：
  - A. Writer 协议扩展输出逐字引文；
  - B. **代码从 claim 在快照中定位引文**（不信任模型输出；倾向）；
  - C. 双轨（模型给候选 + 代码校验定稿）。
- **TBD-5 offset 校验口径**：规范化（空白折叠 / 全半角 / 省略号）后匹配；匹配失败如何进 E5 分级
  （可修 vs 致命）；引用 span 的存储形态（快照内偏移 vs 定位符）。
- **TBD-6 自检项与输出**：三项固定（可定位 / 废弃段落 / 版本号来源）；输出字段
  （`state.delivery_self_check`）+ `report_display` 附录标注；**只标注不改交付状态**。
- **TBD-7 分级处置**：致命 vs 可修的判定清单与动作（致命：降格/阻断渲染？可修：走既有 repair 有界闭环）；
  与 `run_status` 三态、`verification_failed`/UNKNOWN 语义的衔接（不得把「校验未完成」当「事实错误」）。
- **TBD-8 与批次 2 的边界**：本批次不做 manifest 持久化与跨进程恢复；仅预留 `snapshot_id`/locator 字段
  与 12.2/12.10 的对接点。

## 6. 设计策略

- **不信任模型输出**：引文由代码在快照中定位（TBD-4 倾向 B），模型只负责 claim；自检层纯 Python 兜底；
- **复用既有资产**：`safe_fetch` 安全契约、`ev_` 内容寻址体系、`resolve_evidence_text` origin 状态机、
  repair 有界闭环；不另造并行体系；
- **降级优先**：抓取失败不阻塞研究（snippet-only + 标记）；自检失败只标注；
- **诚实边界**：快照仅覆盖 web 路径（RAG/arXiv 已有原文层）；PDF/动态页登记不做；
- **纪律**：先拍 TBD-0；改动输出口径必须走 eval 配对（需求 8 §10.5 纪律）。

## 7. 验收标准（DoD）

- [x] TBD-0 决策：**不采**（2026-10-11 拍板；理由与后果见 §5）
- [ ] E1：`safe_fetch` 接入 researcher；web 候选落盘快照（TBD-1 拍板后的存储形态）；失败降级不阻塞
- [ ] E2：`Citation` 增逐字引文 + 快照偏移；校验器代码复核「引文可定位」
- [ ] E3：`find_in_source` 落地；长证据按 claim 窗口喂裁判（替代头尾截断）
- [ ] E4：`delivery_self_check` 零模型三项；输出入 `report_display`（只标注）
- [ ] E5：分级处置落地（致命 vs 可修清单化）
- [ ] 测试：单测（抓取 mock / 引文定位 / 自检 / 分级）+ 集成 + eval 配对 + 真实冒烟（引用可回溯快照）
- [ ] 文档：README / 渲染口径同步（落地才写）；需求 27 §12.6/12.8 状态回填

## 8. 影响范围与风险

- 动：`agents/researcher.py`、`net/safe_fetch.py`（接入）、`evidence.py`、`state.py`、`agents/validator.py`、
  `repair.py`、`render.py`、`config.py`、`tests/`；web 侧快照存储（TBD-1）。
- 风险①：**输出口径变化**——引用校验/覆盖等指标会漂移 ⇒ before/after 配对（TBD-0）。
- 风险②：抓取网络依赖（被墙 / 反爬 / 超时）⇒ 分类降级 + 时间预算上限。
- 风险③：快照体积与隐私（公网页面；web 多用户隔离与保留期必须对齐 12.2 约束）。
- 风险④：误杀——自检/分级把「校验未完成」误判为「事实错误」（ADR-0011 红线）。
- 兜底：所有新层 fail 降级为现状行为（snippet-only / 头尾截断），不阻塞交付。

## 9. 测试策略

- 单测（离线）：抓取 mock（成功/超时/非 HTML/超限）、引文规范化匹配、自检三项、分级判定；
- 集成：fake fetch + 小快照端到端（finding→snapshot→quote→自检）；
- eval：before/after 配对（20 题 × 3 轮，口径见 TBD-0）；
- 冒烟：真实 topic → 抽查引用可回溯到快照片段 + 自检附录输出。

## 10. 排期（引用 roadmap §2）

| 批次 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| 0 | TBD-0 拍板 + 设计评审（TBD-1~8）+ before 基线（若采） | — | 0.5 天 + 基线 3.6h |
| E1 | 快照落盘（search → read → extract） | 评审 | 2~3 天 |
| E2 | 逐字引文 + offset 校验 | E1 | 1.5~2 天 |
| E3/E4/E5 | find_in_source / 自检 / 分级 | E1 / E2 | 2.5~4 天 |
| 收口 | eval 配对 + 冒烟 + 文档 | 全部 | 1 天 |

合计 ≈ 1~2 周；E1~E5 属同一交付单元。

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-11 | 文档 | 批次 1 立项（roadmap §2） | 建稿（草稿）：现状盘点（含代码行号）、E1~E5 方案草案、TBD-0~8、DoD、排期；Issue #188 | 本文档 |
| 2026-10-11 | 拍板 | TBD-0 before 基线 | **终判：不采**（主理人 2026-10-11）——W8 四指标不可判定、批次 2 以 12.11 新基线统一重建口径；接受与历史基线不可配对；飞书镜像已建（`KmeVd0fsGo1WWkxz35ucMhy6nFe`，lark-cli 直入文件夹） | 本文档 |
