# 需求-6-open-source

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 本稿为 **设计评审 定稿版（2026-09-07 Q1~Q10 全部拍板，TBD 清零）**：全部设计决策已定案，新增/修订细节以**加粗**标注。
> **设计评审 记录：Q1~Q10（TBD-1~10）全部定案（2026-09-07）；否决史保留在各条括号内备查。**
> 飞书镜像：（同步后回填链接）

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | #6 |
| 标题 | 开源 + 博客（M5 / DoD） |
| 优先级 | P2 |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #6（待建） |
| 关联 PR | |
| 创建 / 更新 | 2026-09-07 |

## 2. 问题背景
- `deepresearch-plan.md §8` 验收清单最后一项：**GitHub 开源 + README + 博客（M5）🔴 缺失**——W1~W5 全部收官后这是 DoD 收口项。
- 简历缺口（用户画像）：开源 / 社区佐证薄——DeepResearch 是唯一能"公开佐证 agentic 能力"的项目，不开源等于证据链断尾。
- `CONTRIBUTING.md §2` 发版流程（dev 合 master + 打 `v0.x` tag）**自仓库存活以来从未执行过**——开源 = 首次真实发版。
- `CONTRIBUTING.md §4` PR 门禁声明"CI（ruff lint + pytest + eval 冒烟）必须绿"，但**仓库无任何 CI 配置、无 lint 配置**——规范与事实脱节，开源前必须补齐。

## 3. 需求分析
- 目标（可量化成功定义）：
  - ① 仓库在 GitHub 公开可见，README 让陌生人 10 分钟内可复现跑通（安装 → 配置 → CLI/Web）；
  - ② README 含**面试话术段**：能"讲清楚 agentic vs 纯编排边界、conditional_edge 怎么用、eval 数字与成本账"（W1/W3~W5 叙事闭环）；
  - ③ CI 自动化：push/PR 自动跑 lint + 单测（离线轨），状态徽章可见；
  - ④ 发布 1~2 篇博客（agentic loop 设计踩坑 / Langfuse 接入实战）；
  - ⑤ **开源前硬门槛清零**：W4 DoD §7 挂账项（Job Object + Windows 单测）必须落地，不允许带着"留桩"代码开源。

## 4. 当前设计（代码现状，2026-09-07 盘点）
- **版本控制**：单仓 `dev` 分支独走（本项目环境 feature/* 分支即时清除，铁律为 dev 直改）；`git remote` 为空（`git remote -v` 无输出）→ 尚无远程。
- **密钥/敏感**：`.env` 被 `.gitignore` 忽略且**从未进入任何提交**（`git log --all -- .env` 为空）；git 历史无 `.key/secret/credential` 文件；库内无 `sk-[0-9a-zA-Z]{20,}` 长密钥串；eval results（`research_engine/eval/results/`）无 `*_API_KEY=` 字样——**泄漏面为零，可放心公开**。
- **发布物现状**：
  - `README.md`（8.6KB，W4 已补架构图/工具配置/分档段）：缺面试话术段、CI 徽章、eval 数字段；
  - `CONTRIBUTING.md`（5.7KB）：规范齐全（分支模型/PR 门禁/命名/合规 §7）；
  - `.env.example`（38 行）：占位符干净（无真值）；**缺 `SEARCH_PROVIDER` 条目**（config.py:55 可配）；占位符形态 `sk-your-dashscope-key` 或触发 GitHub secret scan 误报，需改 `<your-...>` 形态；
  - `LICENSE`：**不存在**；
  - `.github/workflows/`：**不存在**；
  - `pyproject.toml` / `ruff.toml` / `.ruff.toml`：**不存在**（CONTRIBUTING §4 声明的 ruff 门禁无配置支撑；requirements.txt 亦无 ruff/pytest 依赖）。
- **测试基线**：default venv（pytest 9.1.1）`tests/` 收集 **72 项**全绿候选；`tests/conftest.py` 强制 `LANGFUSE_ENABLED=false` + 不依赖 .env → 单测轨离线可跑（CI 友好）。
- **硬闸/成本（eval 轨的 CI 代价）**：W5 实测 20 条约 ¥0.48/轮（qwen-plus 均价）+ 并发 3 墙钟 40-60min——**把 eval 轨塞进每次 push 的成本不可接受**，CI 轨设计须绕开（TBD-4）。
- **遗留挂账**：`code_exec.py:174-177` Job Object 留桩（`CODE_EXEC_USE_JOB=1`，W4 DoD §7 第 5 条未达）；`config.py:71` `use_job_object` 字段已就位。
- **开源泄漏点（已修复）**：原 `docs/deepresearch-plan.md`、`docs/workflows/` 下过程文档含本地绝对路径——已移出至 `.workbuddy/`（gitignored），公开仓库零本机路径。
- **面试资产缺口**：W3 遗留"真实 key 冒烟 + Langfuse 控制台截图"未做（README/博客要用）。

## 5. 优化方案（评审前初始版，TBD-N 待拍板）
1. **✅ TBD-1 仓库平台与可见性（设计评审 Q2 定案，2026-09-07）——A+B+C 全做（用户修正，接受并补纪律）**：
   - **A 主仓 = GitHub public**：`TianJinYing2006/DeepResearch`（2026-09-07 确认账号 login=TianJinYing2006，ID 242596525，2025-11 创建、现 4 公开仓）；权威源，一切更新以 GitHub 为准。
   - **B 镜像 = Gitee**（用户追加，接受）：国内访问兜底（面试/国内社区双场景）；**同步纪律 = 允许滞后但发布必同步**——每次里程碑/发布动作后 `git push gitee` 一条命令，README 双平台链接并标注"GitHub 为权威源，Gitee 为镜像可能滞后"；Gitee 账号执行时确认。
   - **C 内容包装（免费零成本）**：仓库 **Pinned 置顶** + **topics 6 个**（`langgraph` `agent` `deep-research` `rag` `llm` `langfuse`）+ 描述一行话 + README 首屏一图流（架构图已有，补 CI 徽章 + eval 数字表入口）。
   - **描述一行话（定稿）**：`Multi-agent deep research with LangGraph: planner→research⇄critic→writer→validate, RAG + arXiv + code sandbox, Langfuse trace, 20-sample eval.`
   - 否决了"单平台聚焦"的首荐——用户场景下双平台兜底价值 > 双平台维护心智，接受。
2. **✅ TBD-2 发版形态（设计评审 Q4 定案，2026-09-07）——A**：`v0.1.0` tag on master（里程碑合流执行 CONTRIBUTING §2）；GitHub Release 带 notes（W1~W6 里程碑 + **诚实指标口径**：引用准确率 83~92% 修正区间/覆盖度 55% 如实/token 成本 ¥0.48 规划价，不吹）；**master 保护规则不开**（单人仓库 PR-only 自锁，对外贡献者流程留给 v0.2+ 再评估）。
3. **✅ TBD-3 LICENSE 选型（设计评审 Q3 定案，2026-09-07）——MIT**：与两个主流同类项目一致（dzhng/deep-research、langchain-ai/open_deep_research 均为 MIT）；企业 fork 零顾虑（GPL 传染性会吓退企业法务）；单人项目无专利诉求（Apache-2.0 的专利授权条款空转）；面试话术："行业一致 + 企业零顾虑 + 单人无专利诉求"。
4. **✅ TBD-4 CI 双轨设计（设计评审 Q1 定案，2026-09-07）——A+B 组合（行业实证裁决）**：
   - **自动轨（每次 push/PR，免费零 API）**：ruff lint + pytest 72 项离线单测——**不含任何 LLM 调用**（conftest 强制 LANGFUSE=false 且不依赖 .env，已验证离线可跑）；预期 <3min。
   - **eval 真实 API 轨 = 不进 CI**：成本（20 条 ~¥0.5/轮）+ 墙钟（40~60min）+ Qdrant 容器三大复杂度全免——**行业共识实证**：langchain-ai/open_deep_research 的 Deep Research Bench 100 题跑一次 ~$20-100，README 公开成本警告，评测走 `tests/run_evaluate.py` 手动命令 + LangSmith 记录，**不进自动 CI**；dzhng/deep-research（19.6k★）根本无 CI。我们照抄"本地/手动跑 + README 公开结果表 + 成本警告声明"范式（我们数字：~¥0.48/轮 20 条）。
   - **手动复现入口（半套 B）**：`.github/workflows/eval-manual.yml` 提供 `workflow_dispatch` 手动触发轨（DASHSCOPE/BOCHA key 存 GitHub Secrets + services 起 Qdrant 容器），README 写"想复现指标：点这里"——fork 拿不到 secrets 天然防滥用，原仓手动跑也不自动烧钱。
   - **防线边界（比 open_deep_research 更诚实）**：自动轨零 LLM 调用，不依赖任何线上状态；真实能力验证 = 本地 e2e 冒烟 + Langfuse 截图 + eval 手动轨，三件套分工明确。
5. **✅ TBD-5 lint 配置落地（设计评审 Q5 定案，2026-09-07）——照抄行业范本 + 两处本地豁免**：`pyproject.toml` ruff 规则集 = `E,F,I,D` + `UP`，**忽略 `D401`**（我们 docstring 为中文，祈使句规则不适用）+ **忽略 `UP006/UP007/UP035`**（新旧 typing 风格都允许）+ **排除 `T201`**（sandbox 靠 print 输出给 LLM，属契约非调试残留）+ **E501 行长不设限**（与 open_deep_research 一致）；依赖补 `ruff`；存量修面用 `ruff check --fix` + 手工复查后 **pytest 72 复核零回归**（预期 30~60min 工作量）。
6. **✅ TBD-6 README 设计要点段（设计评审 Q8 定案，2026-09-07）——A**：独立「**设计要点**」节（**严禁使用"面试/加分/亮点"字样**，只摆技术事实）；四件套 = ① 图级 conditional_edge（plan→research⇄critic→write→validate；**决策循环在图里、不在 prompt 里**）② 防幻觉三件套（validator 图内节点 / 引用溯源四桶 / 多源印证）③ eval 诚实数字（72 单测/完成率 100%/引用准确率 83~92% 修正区间/覆盖度 55% 如实/¥0.48 轮）④ 与主流差异小表（vs open_deep_research/dzhng：图内 validator + 三层沙箱 + 全链路可观测 + 量化评测）；段落 500~800 字。
7. **✅ TBD-7 博客发布策略（设计评审 Q9 定案，2026-09-07）**：**掘金主发 2 篇**（① agentic loop 设计与 Critic 节点化踩坑——**必发**，仓库发布后 1 周内；② Langfuse 全链路 trace 实战——**可缓**，隔 1~2 周）+ **知乎镜像第 1 篇**（SEO 搜索流量）；CSDN/公众号本期不做；正文禁"面试/简历/求职"字样（纯技术分享）；每篇链接仓库 + README。
8. **✅ TBD-8 脱敏与内容边界（设计评审 Q7 定案，2026-09-07）——A'（防"全靠 AI"观感，用户拷问修正）**：
   - **核心原则：公开「工程证据」，移出「过程方法论」**——面试官从公开仓库应读到"人类做过的技术决策与闭环验证"，而非"AI 驱动的过程痕迹"。
   - **公开**：README + CONTRIBUTING + `docs/decisions/`（ADR 全量，含否决史）+ `docs/requirements/`（措辞中性化后）。
   - **移出公开范围 → `.workbuddy/`（gitignore 已盖）**：`docs/workflows/grill-me-需求梳理工作流.md`（AI 驱动过程指令，公开=自曝）+ `docs/deepresearch-plan.md` 面试话术段（"面试一问就露/简历回填"等自指措辞）。
   - **requirements 措辞中性化清单（机械替换，技术决策链一字不动）**：`grill Q1 定案`→`设计评审 Q1 定案`；`grill 前初始版`→`评审前初始版`；`AI 草案 + 人工校准`→`草案 + 人工校准`；删除"主理人"称谓。
   - **路径脱敏**：`docs/deepresearch-plan.md` + 工作流文档的 `D:\项目\...` 绝对路径改相对/删除；终审 = 全库 grep `grill|主理人|AI 草案|D:\项目|C:\Users`（已实测 git 历史 0 命中，无需历史改写；工作树仅上述文档往返）。
   - **`.env.example` 占位符**：`sk-your-dashscope-key` → `<your-dashscope-key>` 形态（防 GitHub secret scan 误报）。
   - **飞书 token 保留**（无权限者读不了内容，属无害链接）。
9. **✅ TBD-9 Job Object 开源硬门槛（设计评审 Q10 定案，2026-09-07）——收口为 W6 DoD 前置第 0 条**：
   - **实现方案**：`code_exec.py` Job 路径 = `Popen` 正常启动 + 立即 `AssignProcessToJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`（`Popen._thread` 不存在 → CREATE_SUSPENDED 方案不可行，放弃；竞态窗口 = 解释器启动前微秒级，注释声明，且 audit hook 已拦 Python 层 fork）；超时 `proc.kill()` + `CloseHandle(job)` 补杀进程树（防 ctypes 逃逸进程）；`config.code_exec.use_job_object` 驱动（env `CODE_EXEC_USE_JOB=true` 已就位）；Job 创建失败安全降级 plain（三层基线不受影响）。
   - **Windows 单测**（`tests/test_code_exec.py`，skipif 非 win32 + env 开关）：① ctypes 造逃逸进程 → 断言超时后被 job 杀净；② Job 路径正常执行；③ 创建失败降级 plain。
   - **失败策略**：单测不绿 → **开源延期**（不强推）；文档口径：无 Job 时 AST+Audit+timeout 三层是 W4 DoD 基线，功能不受影响。
10. **✅ TBD-10 依赖可复现性（设计评审 Q6 定案，2026-09-07）——b 轻量版**：`requirements.txt` 保持区间 + 新增 `requirements-dev.txt`（ruff + pytest）；CI 固定 **Python 3.11**（LangGraph 生态验证最充分；README 声明"本地 3.13 开发实测 / CI 3.11 验证"，双版本兼容性验证面）；uv.lock 全量锁定列入 v0.2+ 演进（fork 变多/外部贡献时再切，避免迁移心智 > 单人收益）。

## 6. 设计策略
- **开源 = 真实发版**：首次公开不是"传个 zip"，而是对齐 CONTRIBUTING 发版流程（TBD-2）——这是面试可讲的工程纪律故事。
- **CI 成本纪律**：真实 API 轨的成本/墙钟是公开仓库的软肋（任何人 fork 跑 push 都烧钱）——自动轨只跑离线，真实能力用截图/数字/手动轨呈现（TBD-4 决策主线）。
- **叙事一致性**：README 面试话术段与 W3~W5 文档口径一致（不夸大：覆盖度 55% 如实声明、eval 单轮数字需 ≥3 次重跑取均值、成本 ¥0.48/轮为 qwen-plus 规划价）。
- **数据诚信铁律延伸**：博客与 README 中的数据必须来自 eval-report 真实记录，不编演示效果。

## 7. 验收标准（DoD）
- [x] **Job Object + Windows 单测跑通（开源前置硬门槛，TBD-9 定案）**：`tests/test_code_exec.py` 三用例绿（逃逸杀净/正常执行/降级），否则开源延期 ✓ commit 58fcf39
- [ ] 仓库公开（GitHub 主仓 + Gitee 镜像），README 可复现（陌生人 10 分钟跑通 CLI + Web）
- [x] README「设计要点」段四件套齐（conditional_edge / 防幻觉 / eval 数字诚实口径 / 差异小表），全文无"面试/加分"字样 ✓ commit 6a31e5e
- [x] CI 自动轨绿（ruff + 79 单测），徽章可见；eval 手动轨 workflow_dispatch 就位 ✓ commit bff0ba8
- [x] LICENSE（MIT）生效 ✓ commit 6a31e5e
- [x] 密钥/路径脱敏终审零泄漏（TBD-8 A' 全清：路径相对化/措辞中性化/占位符 `<your-...>` 形态/历史 0 命中已实测）✓ commit 6a31e5e
- [ ] v0.1.0 tag + Release notes（W1~W6 里程碑 + 诚实指标口径）
- [ ] 博客 ≥1 篇发布（掘金①必发；知乎镜像①）

## 8. 影响范围与风险
- 新增：`.github/workflows/ci.yml`、`pyproject.toml(rust?)`、`LICENSE`、可能 `requirements-dev.txt`；改 `README.md`、`.env.example`、`docs/` 2 处路径、`code_exec.py` + `tests/test_code_exec.py`（Job Object）。
- 回归面：Job Object 改动 `code_exec.py` 主路径（默认关，回归风险低，但 W4 沙箱单测必须全绿）；`.env.example` 改动影响本地复制参考。
- 风险：① 公开后他人运行需要真实 API key（成本免责声明不入 README 会误导）；② eval 轨若进 CI 自动轨 → fork 恶意滥用消耗用户额度（TBD-4 必须决策）；③ 博客质量差反噬（宁缺毋滥——W1/W3 主题已储备足够素材）。
- 降级：任何硬门槛未达 → 开源延期到下周，不强推。

## 9. 测试策略
- 单测：72 项存量全绿 + 新增 Job Object Windows 单测（skipif 非 win32）。
- lint：ruff 全量过 `ruff check .`（TBD-5 规则集定后修存量）。
- CI：本地模拟 workflow（act 不可用则手动跑等价命令：`ruff check . && pytest tests/ -q`）。
- 不依赖真实 API 的路径：自动轨全离线；真实能力验证走本地 e2e 冒烟（`run_e2e_smoke.py` 模式）+ Langfuse 截图（面试资产）。

## 10. 变更记录
| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-07 | 建稿 | W6 启动 | 初始版：plan.md §8 收口 + 现状盘点（remote/LICENSE/CI/密钥/路径泄漏/Job Object 挂账）+ 优化方案留 TBD-1~10 待设计评审 | |
| 2026-09-07 | 拍板 | 设计评审 Q1 | **TBD-4 定案（行业实证裁决）**：CI 双轨 = **A+B 组合**——自动轨仅 ruff + 72 离线单测（零 LLM 调用）；eval 真实 API 轨不进 CI（~¥0.5/轮 + 40~60min + Qdrant 容器复杂度；实证 open_deep_research 100 题 $20-100 亦手动跑 + README 成本警告惯例），改 `workflow_dispatch` 手动复现入口 + README 公开结果表/成本警告；fork 不共享 secrets 天然防滥用 | |
| 2026-09-07 | 拍板 | 设计评审 Q2 | **TBD-1 定案（用户修正）**：A+B+C 全做——GitHub public 主仓（TianJinYing2006/DeepResearch，权威源）+ Gitee 镜像兜底（同步纪律：发布必同步、README 标注滞后可能）+ 内容包装（Pinned/topics 6/描述一行话已定稿）；否决单平台聚焦首荐，接受双平台兜底价值 | |
| 2026-09-07 | 拍板 | 设计评审 Q3 | **TBD-3 定案**：LICENSE = **MIT**——行业一致（两个主流同类均 MIT）+ 企业 fork 零顾虑 + 单人无专利诉求；被追问时一句话讲清 | |
| 2026-09-07 | 拍板 | 设计评审 Q4 | **TBD-2 定案**：发版 = **A**——v0.1.0 tag on master + GitHub Release（W1~W6 里程碑 + 诚实指标口径）；master 无保护规则（单人 PR-only 自锁，外部贡献流程 v0.2+ 再评估） | |
| 2026-09-07 | 拍板 | 设计评审 Q5 | **TBD-5 定案**：ruff 规则集照抄 open_deep_research（E/F/I/D/UP）+ 本地豁免 D401（中文 docstring）/UP006-007-035/T201（sandbox print 契约）/E501；存量 `--fix` 修面 + pytest 72 零回归复核（30~60min） | |
| 2026-09-07 | 拍板 | 设计评审 Q6 | **TBD-10 定案**：依赖 = **b 轻量版**——requirements-dev.txt（ruff+pytest）+ CI 固定 3.11（本地 3.13 开发实测，README 声明双版本）+ uv.lock 演进 v0.2+ | |
| 2026-09-07 | 拍板 | grill Q7 | **TBD-8 定案（用户拷问修正 → A'）**：内容边界 = 公开工程证据（README/CONTRIBUTING/ADR/requirements 中性化措辞）、移出过程方法论（workflows + plan 面试话术段 → .workbuddy/）；措辞中性化清单定稿（grill→设计评审、AI 草案→草案）；git 历史零泄漏已实测（无需改写）；.env.example 占位符改 `<your-...>` 形态 | |
| 2026-09-07 | 拍板 | 设计评审 Q8 | **TBD-6 定案**：README 独立「设计要点」节（四件套 + 差异小表，500~800 字，全文禁"面试/加分"字样，只摆技术事实） | |
| 2026-09-07 | 拍板 | 设计评审 Q9 | **TBD-7 定案**：掘金 2 篇（①必缓可缓）+ 知乎镜像①；节奏 = 仓库发布后 1 周内发①、隔 1~2 周发②；正文禁求职字样 | |
| 2026-09-07 | 拍板 | 设计评审 Q10 | **TBD-9 定案（无争议收口）**：Job Object = DoD 前置第 0 条——Popen+AssignProcessToJobObject+KILL_ON_JOB_CLOSE（CREATE_SUSPENDED 因 Popen._thread 缺失否决）+ ctypes 逃逸杀净等三 Windows 单测；不绿则开源延期。**TBD 全部清零，W6 需求定稿** | |