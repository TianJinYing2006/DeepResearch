# 项目状态看板（唯一真相源）

> **为什么有这份文档**：2026-09-16 盘点发现「文档状态漂移」已是项目最现实的管理风险 ——
> `docs/requirements/8-*.md` §1 元信息仍写「状态 = 草稿」、§1.1 全景表里 **before 基线与 Arm 1 仍标 ⬜**（实际已完成），
> 而同一份文档后半部分的 DoD 明细却标了完成。**两张口径打架 ⇒ 「已完成的重复做、未完成的被当成完成」**。
>
> **规则**：本文件是**当前状态**的唯一真相源。其余设计与需求文档（含 `8-*.md`、`.workbuddy/deepresearch-plan.md`）
> 只保留**历史决策与设计依据**，不再承担看板职责。

| 项 | 值 |
| --- | --- |
| 更新 | **2026-09-24**（**P1 第一批部署底座本地骨架已交付**：`Dockerfile.api` 多阶段镜像 / `docker-compose.staging.yml`（PG+Redis+迁移+API）/ 迁移执行器（只前向 + 幂等，CI 锁） / `/api/health/live`+`ready` 探针 / CORS 环境变量化 / CI 新增 `infra` job；pytest **461 全绿**；本机 Docker 实跑通过（迁移两次 `applied=0`、ready=postgres+redis ok、镜像内前端可服务），**云上部署未开始、ADR-0009 仍草稿**）｜**2026-09-24**（**P1 运行护栏与前端体验已交付并合入 master**：PR #8 ⇒ `aafcf22`；超时闸 / 并发限制 / 状态查询 / 结构化错误 / 报告导出 / 浏览器 E2E；复核修复强制收口竞态（ADR-0008，+2 条交错回归）；**C 后续增量**：#8 planner 治理运行级聚合（+4）、#9 SSE 刷新恢复（E2E +1）、#10 取消/超时时序测试（+4）、#13 资源管理核查（+3）、#12 前端异常体验（终局后保留 + 详情折叠，E2E +1）、#14 运行级统计展示（+1）、#15 E2E 接 CI（job 109s）、#11 后端错误场景补齐（422 结构化 + 图内 STOP_ERROR，+2）；测试 **455 全绿** + E2E **10/10**）｜2026-09-24（**P0 本地交付基线已复验**；W9 前端交付、测试与 CI 事实已同步）｜**2026-09-22**（**W9 前端板块补齐 + §7.8 CI 与边界守卫已落地**：23 条 Web/SSE 测试全绿、ruff 全过、MD 表格 0 不一致、`DR_DEMO=1` 实跑完整流程）｜骨架落地于 **2026-09-20**（`iter_run()` + SSE/AG-UI + 取消 + FastAPI/React 最小骨架，**352 全绿**）｜承接：呈现层重构**已拍板 D-20**（FastAPI + React/Vite/TS + Tailwind + SSE，对齐 AG-UI 语义不引 CopilotKit；ADR-0001 追加 §1.1 修订——允许升级呈现层、不扩展产品边界）；after 基线 3 轮结案、**D-18 已拍板不续跑**、确定性 DoD 8/8、代码冻结于 f723c2d |
| 阶段 | W1~W7 已收官；**W8 已收尾**（Arm 1~7 + 命名三分 + 台账 review + after 基线 3 轮全部结案；确定性 DoD 8/8；剩余 = 对外材料）→ **W9 Web UI 重构基础交付已完成**（FastAPI + SSE/AG-UI + 协作式取消 + React 基础交互 + 前端构建 CI + 边界守卫）。浏览器 E2E、任务持久化与生产部署属于后续增量，不再混写成“W9 待完成” |
| 最近 CI | **Py3.11/3.12/3.13 + ruff + pytest 三档全绿**（零 LLM、零 key），`frontend` job 同跑 `npm ci` / `tsc --noEmit` / `vite build`；**新增 `e2e` job**（官方 Chromium，`DR_DEMO=1`，首轮 `36006923861` = job 109s / 用例 10 passed 45.0s）。**PR #8 这一轮（P1）**：dev push `35962115290` + PR 事件 `35962244144` + master 合并后 `35962331569` —— 全 success；后续 dev push：看板 `90efe96` = `35962503965`、setup-node v7 `be03ac1` = `35965038662`、C 增量 `67cdbe2`/`5411d2c`/`f1affab`/`64f2ab1`/`4f11426`/`dd56e2b` 与 e2e 接入 `7b55481` = `36006923861`，均 success。⚠️ 仅剩一条非阻断提示：`ubuntu-latest` 将于 2026-10-19 起迁移 Ubuntu 26（信息性，迁移日前复验） |
| 最近本地验证 | **2026-09-24，基线 `73b37a8` + P1/后续增量工作区**：Python **3.13.14** 项目专属 venv 按两份 lock 安装，`pip check` 通过；ruff 全过；pytest **455/455 全绿**（含 P1 运行护栏 35 条、流式 16 条与 planner 治理聚合 4 条）；评测白名单与前端边界守卫均通过；Playwright 浏览器 E2E **10/10**（桌面 + 移动两个视口，约 40s，含刷新恢复与完成后续看）。Node **24.15.0** 下 `npm ci` 安装 236 包（0 漏洞），`tsc --noEmit` 与 Vite production build 均通过 |
| 最近交付 | **W8 after 基线结案 + 确定性 DoD 验收表 ⇒ `53ad3b1`**（新增 `docs/eval-w8-after-baseline.md`、`docs/eval-w8-dod.md`，`tools/paired_before_after.py` 转正，**D-18 拍板不续跑**）｜**PR #5 / #6 / #7 均已合入 master ⇒ `65aabee`**　[#5](https://github.com/TianJinYing2006/DeepResearch/pull/5) / [#6](https://github.com/TianJinYing2006/DeepResearch/pull/6) / [#7](https://github.com/TianJinYing2006/DeepResearch/pull/7)（PR #7 = W8 命名三分。**看板自身不单独开 PR**，随下一次合并并入）｜**PR #8 = P1 运行护栏 + 强制收口原子化，已合入 master ⇒ `aafcf22`**　[#8](https://github.com/TianJinYing2006/DeepResearch/pull/8)（dev 侧 `0a7d940`；合并后已核对 `tree(master)==tree(dev)==1855077`，内容零差异） |
| 最近基线 | **after 基线**（2026-09-19，20 题 × 3 runs，冻结 `f723c2d`，20/20 零异常）；对照 **before 基线**（2026-09-16，20 题 × 3 runs）。**四指标全部不可判定** ⇒ 结论见 `docs/eval-w8-after-baseline.md` |
| 测试基线 | **461 全绿**（2026-09-24 P1 第一批本机完整复验 `pytest tests/ -q`：455 → +6）。其中 **Web 层 65 条**：流式 16 `tests/test_web_streaming.py` + HTTP 8 `tests/test_web_api.py` + **P1 运行护栏 35 `tests/test_web_guardrails.py`** + **P1 部署底座探针 6 `tests/test_web_health.py`**（live / ready / CORS）；eval 侧 25 条（`tests/test_eval_metrics.py`，含 planner 治理聚合 4 条）；前端另由 `tsc --noEmit` + `vite build` 验证，浏览器 E2E **10 条**见下节 |
| Python | **仅支持 3.11~3.13**（以 CI matrix 为准）。2026-09-24 本机复验使用项目专属 venv `C:\Users\Administrator\.workbuddy\binaries\python\envs\deepresearch`，解释器实测 **3.13.14**；按 `requirements-lock.txt` + `requirements-dev-lock.txt` 安装后 `pip check`、ruff、pytest **441/441** 均通过。<br>⚠️ 跑 pytest 必须加 `--basetemp=<干净的新目录>`，且每次换新目录（不加会被沙箱批量删除守卫卡在临时目录 GC 上；复用非空目录会报假 ERROR） |
| 结论文档 | W7：`docs/eval-w7-conclusion.md`；W8 设计：`docs/requirements/8-fault-transparency-and-reproducibility.md`；**W8 after 基线结论**：`docs/eval-w8-after-baseline.md`；**W8 确定性 DoD 验收表**：`docs/eval-w8-dod.md` |

## 决策记录

| 编号 | 决策 | 日期 | 依据 |
| --- | --- | --- | --- |
| **D-01** | **采用本文件作为唯一实时状态看板**。`docs/requirements/8-fault-transparency-and-reproducibility.md` 只维护设计要求、DoD 与验收原则，并在 §1.1 声明指向本文件，**不再重复维护 Arm 1~7 的实时勾选状态** | 2026-09-17 | 两份文档生命周期不同（设计低频 / 状态高频）。实测反例：§5.4.2 落点清单行号已全部过期，证明设计文档不适合承担实施看板 |
| **D-02** | **Arm 4 为 RAG 引入独立 `RetrieveResponse`**，不做 researcher 层临时适配 | 2026-09-17 ✅ 已落地 | `HybridRetriever.retrieve()` 原返回裸 `List[dict]`，`[]` 无法区分「正常无命中 / 未配置 / 向量库不可用 / embedding 失败 / 解析失败 / 部分 backend 失败」。临时包裹会在信息到达 researcher 前就丢掉事实，与「故障可归因」的主目标冲突 |
| **D-03** | **`empty_result`（零命中）不算故障**：记在 `failure_reason` 供 planner 当轮决策（换源 / 改查询），但**不进 `degradation_log`、不推导 `run_status`** | 2026-09-17 ✅ 已落地 | 算术理由：`resolve_run_status()`（`state.py:229`）是「`degradation_log` 非空 + 有报告 ⇒ `degraded`」。多跳检索里零命中几乎必然发生，若算降级 ⇒ **几乎每轮 run 都是 `degraded`**，`run_status` 不再是健康度信号，Arm 1 的「故障可归因」退化成噪声。**一处定义**：`failure_reasons.NON_FAULT_REASONS` + `is_fault_reason()`；要翻转口径只需改这一处 |
| **D-04** | Arm 4 后：provider **不再靠抛异常表达失败**；冒泡到 researcher 的未预期异常一律归为**非工具类**（`internal` 等），**不得**再被猜成 `provider_error` | 2026-09-17 ✅ 已落地 | 工具层是 5 值的唯一产生点（B4 单向派生契约）。把 provider 自身的 bug（如 `TypeError`）伪装成 `provider_error`，会让「真故障」与「工具不可用」混为一谈 |
| **D-05** | **指标未算出（缺失 / 非数值）不触发 `suspicious`**：这类情况由 `metrics_partial` / `metrics_failed` 表达；`verdict` 只判断**已经成功算出**的指标是否落入异常区间 | 2026-09-17 ✅ 已落地 | 语义分工：`suspicious` = 运行完成了但**结果质量可疑**；`partial/failed` = **评测本身没完整完成**。两者混在一起会让质量闸同时表达「结果质量」与「执行完整性」，后续统计失真（一次「指标没算出来」会被误记成「被测变差」）。实现见 `eval/quality.py::_below()` —— 非数值直接返回 False |

| **D-06** | **`prompt_hash` 覆盖【运行时渲染结果】，不是常量模板**：5 个 system 构建器抽成纯函数作唯一产生点，指纹对「开关翻转但代码未变」敏感 | 2026-09-18 ✅ 已落地 | 反例即动机：`critic_gap_enabled` 往 critic 提示词插裁决标准、writer/validator 开关在两套提示词间选版 —— 这些**都在** `config_snapshot` 里，但要回答「提示词变了吗」还得再推一层（开关→分支→哪套）。哈希常量模板会得到「开关翻了指纹没变」的假象。user 段含运行时数据（topic/findings），**按设计排除**，其模板由 `git_commit` 兜 |
| **D-07** | **历史 raw 缺 Arm 6 新字段时如实为 `None`，不回填当期值** | 2026-09-18 ✅ 已落地 | 回填会抹掉「这批 run 产生于 Arm 6 之前」这个事实 —— 那正是 §10.4/Arm 6 要消灭的问题。报告对应显示「❔ 未记录」而非假装数字有效。实测 66 个历史 run 全判为未记录 |
| **D-08** | **`citation_judge_independent` 是事实判断而非配置项**（恒 False） | 2026-09-18 ✅ 已落地 | 评测层不重跑裁判，直读主链路 validator 产物（`metrics.compute_citation` 注释明文写了「不再重跑 validator」）。想变 True 必须**先改评测实现**引入独立复判，不能只改常量——否则常量与实现对不上就是自欺 |

| **D-09** | **`.gitignore` 的出处置于独立注释行，绝不跟在 `!` 规则行尾** | 2026-09-18 ✅ 已落地 | 实测：`.gitignore` 只认**行首** `#`，`!path/  # 出处` 会把注释吞进 pattern ⇒ **`!` 规则整条失效**（去掉注释即放行、带上仍被忽略）。这正是原三条白名单「看着有注释、实际不匹配」的根因。且**空行会打断注释归属** ⇒ 出处必须写在规则**紧邻上方**（已固化进单测 `test_blank_line_breaks_comment_ownership`） |
| **D-10** | **自动枚举的文档不构成「引用」**：`docs/eval-report.md` 不算入库证据 | 2026-09-18 ✅ 已落地 | `report_gen.py` 自动写出的报告实测含 **74 个**互不相同的 run id（≈全量枚举），而手写结论 `docs/eval-w7-conclusion.md` 只提 **4 个** ⇒ 若承认自动表，「引用即入库」判据自我作废。要点在一件事：**只有「被人挑选过」才算数**。口径由 `AUTO_ENUMERATED_DOCS` 常量单点定义，白名单裁判与台账用 `spec_from_file_location` 加载同一份，避免两处分叉 |
| **D-11** | **Arm 7 双轨的处置纪律不同：git 侧可以动手（可回溯），磁盘侧只能出账（不可回溯）** | 2026-09-18 ✅ 已落地 | git 侧误伤可用 `git checkout` 恢复；磁盘侧 `results/raw/*.raw.json` 是 W7「零成本可复算」的唯一证据源（`tools/w7_backfill_*.py` 直接读），误删等于销毁证据且**无任何回滚**。⇒ 第一刀只做只读台账，清理须在 review 后单独拍板 |

| **D-12** | **做过「移出索引」类操作（`git rm --cached`）后，本机不得再跨该边界 checkout** —— master 与 dev 必须**同指向** | 2026-09-18 ⚠️ **用事故换来** | `git rm --cached` **只保护执行它那一刻的工作树**：这些路径变成「被忽略」，对后续 checkout / reset / pull 而言它们**属于旧版本**。实测：Arm 7 把 189 个跟踪文件降到 8 条白名单后，一次跨边界 `git checkout` 让 git 把产物**从磁盘物理删除**（删目录时连未跟踪的 raw 一起带走）⇒ `results/` **143.6 MB → 31 MB**。本地已用 `git branch -f master dev` 消除边界（**⚠️ 远端无法维持同指向，见 D-15**）。**推广**：任何「让已跟踪文件转为未跟踪」的治理动作，都必须配套「禁止跨边界切换」的纪律，否则等于给协作者埋雷 |
| **D-13** | **台账的「有手写引用」要区分「引用含结论文档/代码」与「引用仅来自看板/需求文档」，且后者只是软标记、不得当作删除依据** | 2026-09-18 ✅ 已落地 | 两个相反方向的陷阱：① **登记的反转** —— 治理过程把产物名写进处置记录，于是「描述过」被当成「有证据」（实测手写引用数 17 → 18 → 20）；② 反向误判更危险 —— 自动把「仅被看板/需求文档引用」判成「不算证据」时，**before 基线三个 run**（`run_20260916_001005` / `011813` / `022440`，靠 §10.4/§10.5.8 验收记录存续）会被误标为可删。故该分类只做提示（`tools/w8_artifact_ledger.py` `GOVERNANCE_DOCS`），**删除仍须人工确认** |

| **D-14** | **命名三分的方向 =「写侧只出新键 + 读侧 dual-read」，历史产物一律不回填** | 2026-09-18 ✅ 已落地 | 三条理由：① 历史 raw/eval 是 **Arm 7 台账的取证对象**，产物只读；② `tools/w7_backfill_*.py` 的「W7 零成本可复算」**依赖 raw 逐字节不变**；③ 回填会抹掉「这批数产生于旧口径」这个事实（与 **D-07** 拒回填 `prompt_hash` 同源）。⚠️ **与 §5.5.2 的「旧键同时写入」方向相反，这是刻意的**：那里产物**可重写**（summary 每次重算），所以把兼容成本压在写侧；这里产物**不可重写**，所以兼容责任落在读侧。代价已实测可接受：**1,498 raw + 1,443 eval 经 dual-read 全部读出、取值零越界** |

| **D-15** | **远端 master 与 dev 长期分叉：master 恒为 merge 提交 ⇒ D-12 的「同指向」只能在本机成立** | 2026-09-18，处置方式由于晏拍板（**选 merge 提交**，不重写远端历史） | **发现（PR #7 合并时暴露）**：PR #5 / #6 实际是以 **merge 提交**合入的（`184f11b` 双父 `[0144705, ce0b8ba]`、`5f9aece` 双父 `[184f11b, f2d5c3c]`）⇒ 远端 master 的尖端落在 dev 祖先链**之外**，对 dev 做 `PATCH force:false` 会得 `422 not a fast forward`；且 `5f9aece` 这个提交在本地仓库**根本不存在**。**内容层面零风险（已实测）**：`tree(5f9aece) == tree(f2d5c3c) == a831cd09` —— master 上没有任何 dev 历史里没有的东西，merge 提交只是把 dev 侧裹了壳；PR #7 合入后的 `3736bee` 其 tree 也 `== tree(79845c6)`。**⇒ D-12 口径据此收窄**：`git rm --cached` 边界的危险性靠**本机** master/dev 同指向消除（本次已 `git branch -f master dev` ⇒ 两者均 `79845c6`），远端 master 是否线性**不影响本机 checkout 安全**。**已知代价**：每次合并都会让远端分叉再深一层；将来若要线性历史，需另开议题（改走 rebase 合并，或一次性 force 对齐） |
| **D-16** | **台账判据补第四档「结构登记」；review 结案 = 维持现状、不删任何产物** | 2026-09-18 ✅ 已落地并结案 | **发现（review 前的判据自检）**：`tools/w8_artifact_ledger.py` 的 `SCAN_SKIP_DIRS` 含 `results` ⇒ 整目录跳过引用扫描。原注释理由是「产物自己会引用自己」—— 那只对**自指**成立；`w7_experiment_*/manifest.json` 的 `runs[].run_dir` 是**包含 / 溯源登记**，不是自引用 ⇒ **58 个 run / 110.0 MB 被误判成「无引用」**。⚠️ 若按原口径治理，会销毁 W7 配对实验的证据基座（ρ=0.551 / MDE=12.38pp 的逐题输入就在这批 run 的 `eval/*.eval.json` 里）。**修法**：走进去扫 + `_results_owner()` 逐条排除自指；新增「结构登记」独立列；软标记 `governance_only` **只看手写引用**（结构登记**不得**压掉它 —— 实测踩到：before 基线三个 run 会因 `history.json` 登记了 run_id 而失去「只有处置记录撑着」的人工确认提示）。**结果**：候选 83 条 / 128.2 MB ⇒ **15 条 / 4.2 MB**；`.workbuddy` 仍**必须**跳过（事故物理备份 + 逐日流水会把 run 名当处置记录写入）。**结案理由**：真正可清的量级约 **2.0 MB**（2 个零字节空目录 + `_tmp_backup_not_committed` + `*_DISCARDED`），而 `results/` 共 143.5 MB、D 盘可用 29 GB ⇒ **清理收益为零**，却要再承担一次 D5 那类「跨边界删除产物」的风险 ⇒ **维持现状**。另两条硬约束：10 个 `w7_experiment_*` 是 W7 实验记录**本体**（arm 定义 + notes，即使只有几 KB 也保留）；`run_20260916_*` 三条 before 基线 run 是对照臂，由 `history.json` 与 §10.4 验收记录双重支撑 |
| **D-17** | **`results/history.json` 的空转 `status` 字段：删（不是改名）** | 2026-09-19 ✅ 已落地 | 该字段自称「回归判定」，实际写 `summary.struct.regression`，而全仓**从未有任何代码写入 `summary.struct`**（只有 `run.py` 空结果分支写过 `"struct": {}`）⇒ 恒为 `"PASS"`。⚠️ **差点误判**：`report_gen.py:250` 的 `rec.get("status")` 看着像读取方，实则读的是 **W7 manifest 的 `runs[].status`**（登记表第 3 条落点），数据源不同 —— 逐条核对确认**零读取方**后才动手。**处置**：① 写侧 `append_history` 不再产出；② 已跟踪的 `history.json` **75 条记录全量剥离该键**（外科式文本删除 ⇒ diff 恰 75 行删除、零其他字节变化，保住了 CRLF 与其余内容逐字节一致）；③ `status_keys.BARE_STATUS_SITES` **移除**该条 —— 收敛后不留「已解决」说明，否则 `len(BARE_STATUS_SITES)` 永远虚高、还让人误以为有活体落点；④ 单测改名 `test_history_has_no_vacuous_status_field`，方向反转成锁「不再存在」。**教训**：`report_gen.py` 的注释里**不能写出那个键名的英文原文** —— 新用例靠「全仓搜该词零落点」锁这次删除，注释写它会自打脸（已踩到并修） |
| **D-18** | **after 基线：以 3 轮结案，不续跑后 6 轮** | 2026-09-19 ✅ **已拍板** | 实测**四指标全部不可判定**：coverage Δ=−5.1pp（SE 3.81 / MDE 10.66）、citation +3.1pp（9.21）、retrieval −2.5pp（3.88）、steps −1.2（1.36）。**关键发现：精度瓶颈在 before 侧** —— `SE = sqrt(σ_b²/R_b + σ_a²/R_a)/√n` 里 `R_b=3` 固定，而 before 基线已永久错过（代码已改，不可重采）⇒ `σ_b²/3` 不随 after 轮次下降；coverage 的 before 侧方差占比 **73%**，即便 after 跑到无穷轮 MDE 也只从 10.66 降到 9.13。**外推到 9 轮**：MDE 仅降 9~25%，三指标仍不可判定；唯一勉强过线的 steps（9 轮时 MDE 1.18 vs \|Δ\| 1.22）按 **D2 必须归因于 Arm 2 的功能性修复**，不得计入质量提升。**⇒ 修正 §10.5 的隐含假设**：「唯一出路是加 runs（不是加题）」只对**两侧同时加**成立；只加 after 侧会被 before 侧的固定方差锁死。**建议不续跑**：再花 ¥6~8 + 6~8h 换「大概率仍不可判定」不划算；且 W8 验收本就用 **A 类确定性断言（故障可归因率 0%→100%，零噪声）**，不依赖基线统计。**顺带的观察（标为观察、不作结论）**：after 三轮的轮次间极差 **2.92pp vs before 12.50pp**、σ_within 16.93 vs 23.96、ρ 0.793 vs 0.551 ⇒ 系统的**方差**显著变小（不是均值）；但 σ 只由 3 个轮次对估出、自由度极小（F≈2.0 处于边缘），要坐实就得加轮次 —— 而那恰是上面算过性价比极差的事。详见 `docs/eval-w8-after-baseline.md` |
| **D-19** | **W9 Web UI 重构：任务模型定为「前台跑 + 可取消」，不做任务队列** | 2026-09-19 ✅ 已拍板 | 单轮实测 **48~51 分钟**，若做「后台任务 + 断开可重连」就要引入任务持久化/队列 ⇒ 与 **ADR-0001「不做完整产品、聚焦核心链路」** 正面冲突。前台模型下页面开着即可看实时进度并随时中断，代价是**关掉页面任务即丢失**（主理人明确接受该代价）。⚠️ 由此推导出的硬约束：**UI 必须支持取消**，且取消要能真正停掉后端（否则只是关页面，后台继续烧 token —— 而这正是 D-18 拍板「不续跑」时主理人最在意的成本问题）。技术栈调研见 `docs/requirements/9-web-ui-rewrite.md` |
| **D-20** | **W9 拍板：FastAPI + React/Vite/TS + Tailwind + SSE，对齐 AG-UI 语义（不引 CopilotKit）；呈现层升级，但**不扩展产品边界** | 2026-09-20 ✅ **已拍板** | **拍板表述**：「W9 采用 FastAPI + React/Vite 作为呈现层技术栈，使用 SSE 传输并对齐 AG-UI 事件语义；不引入 CopilotKit、任务队列、用户系统或多租户。取消采用节点边界上的协作式取消，保证取消请求后不启动新的研究节点和 LLM 调用。W9 不修改 `research_engine/` 的核心判定口径。」**选型理由不是「React 更潮」**，而是它最直接命中三个真实痛点（视觉粗糙 / 难维护 / 长任务无实时反馈），且 §5.1 依赖预检已证明不破坏核心环境。<br>**取消契约（不得写成模糊的「支持取消」）**：核心 = **取消请求立即生效，执行停止在下一个安全边界**。C1 前端立即显示「正在停止」／C2 后端不再启动下一节点／C3 当前同步节点允许自然结束／C4 当前节点结束后流程停止／C5 取消后不再产生新 LLM 调用／C6 最坏等待 = 当前节点剩余时间／C7 若某节点常跑数分钟则拆细。<br>**C7 已实测判定：暂不拆** —— 62 题 `wall_clock_s` + `progress` 条目分析，且已证 `progress` 条目与 graph 节点 **1:1**（一题 stage 序列 `plan → (research→critic→revise)×6 → research → critic → write → validate → render`，24 条 = 24 段每段 ×1）⇒ **单节点耗时中位 14.4 s / p90 17.7 s / 最大 31.4 s**，无节点达「数分钟」量级。⚠️ 口径局限：这是 `wall_clock/节点数` 的**均值估算**（隐含各节点耗时均匀），尾部 `write`/`validate`/`render` 可能更长 ⇒ 视为**乐观下界**；W9 实施时应补**节点级时间戳**校准。<br>**三条硬边界**：① 前端不承载研究逻辑（只展示状态/提交配置/发取消请求，不得复制 `research_engine` 判定逻辑，可写 CI 检查：前端目录不得 import `research_engine`）；② 不引入产品化功能（登录/用户系统/任务队列/持久化任务/多租户/权限/云端部署）；③ **完成标准封顶 7 项** = 提交研究、实时阶段进度、降级事件展示、token/cost 展示、取消、报告与引用展示、文档摄取状态 —— **不做**聊天框/复杂工作台/历史任务中心/CopilotKit。<br>**ADR 处置**：**追加** `docs/decisions/0001-initial-design.md` **§1.1 修订**（不推翻），把「轻量 Web UI」解释为「**允许升级呈现层技术栈，但不扩展产品边界**」= 用什么做可以升级、做什么不得扩展；与 ADR 原理由（面试官最看重核心编排深度而非 UI 完整度）不冲突 —— UI 仍不是卖点，只是不再成为短板。<br>**HTMX 反悔条件（存档）**：仅当下列任一成立才应改选 —— 只想最快修掉假进度条／不想维护 npm 链／UI 只是临时工具不用于展示／愿牺牲视觉上限与 AG-UI 扩展能力。<br>⚠️ **协作式取消是语言级限制，不应归咎于 React**（HTMX/NiceGUI/Streamlit 同样受限） |

`SearchResponse`（外部搜索：Bocha、arXiv）与 `RetrieveResponse`（本地向量 / 混合检索）**分离但共享** `FailureReason`、`failure_detail`、`ok` 与统一的 `degradation_log` 派生语义 —— 两者语义不同（外部 provider 响应 vs 本地检索器响应），强行复用会在后续扩展 rerank / source score / document metadata 时持续变形。

## 模块状态

| 范围 | 状态 | 证据 |
| --- | --- | --- |
| W1~W6（MVP 主链路 / Langfuse / 评测 / 开源） | ✅ 完成 | `.workbuddy/deepresearch-plan.md`，六周 DoD 全绿 |
| W7（6 臂对照实验） | ✅ 完成（**结论为否定**） | `docs/eval-w7-conclusion.md` |
| W8 前置 §10.4（config_snapshot 记效值） | ✅ 完成 | `research_engine/eval/provenance.py` |
| W8 before 基线 | ✅ 完成（3 轮） | W8 §10.5.8 |
| W8 Arm 1（状态三态 + 异常退出契约） | ✅ 完成 | `state.py` / `graph.py` / `failure_reasons.py` / `tests/test_arm1_run_status.py` |
| W8 Arm 2（代码执行注入） | ✅ 完成 | 注：**实际是功能性修复**，见下方已知缺陷 D2 |
| W8 Arm 3（依赖锁定三刀） | ✅ 完成 | `requirements-lock.txt` + CI 三档证据（`0477ea3` / `9698182`） |
| W8 Arm 4（provider 失败原因结构化） | ✅ **已落地**（2026-09-17） | `rag/response.py` 新增 + `bocha.py`/`arxiv.py`/`retriever.py` 三路真填充 + 删 `classify_tool_exception`；受控对照以 mock 形式落在 `tests/test_arm4_failure_reasons.py`（零网络更稳） |
| W8 Arm 5（评测口径 / 质量闸 / stderr） | ✅ **已完成**（含历史回填，2026-09-17） | `eval/quality.py`（闸）+ `eval/stats.py`（bootstrap）+ `eval/aggregate.py`（聚合一处定义）+ `tools/w8_backfill_stderr.py`（回填）。**DoD 全部闭环**，回填记录见下节 |
| W8 Arm 6（可复现元数据） | ✅ **已交付**（2026-09-18，PR #4 已合并 ⇒ `fa66d27`） | `eval/prompt_hash.py` 新增（6 slot）；provenance 从 4 字段扩到 **10 字段**（+`prompt_hash`/`prompt_slots`/`scorer_version`/`citation_judge_model`/`coverage_judge_model`/`citation_judge_independent`）；raw 三条落盘路径统一走 `raw_provenance_fields()`；报告**在指标表之前**显著呈现裁判独立性；趋势表新增「尺子变了」检查。当前 `prompt_hash=cf95dafc78f98348`、`scorer_version=w8.1` |
| W8 Arm 7（产物治理双轨） | ✅ **已交付**（2026-09-18） | **轨道 1**：`.gitignore` 白名单重写为「引用即入库」+ 出处独立注释行 ⇒ `git ls-files research_engine/eval/results` 顶层 **恰 8 项 ≡ 白名单集合**（原 94 项偏离全收敛）；`history_bak_v10.json` 因零引用 `git rm --cached`（磁盘保留）。新增单点裁判 `tools/check_results_whitelist.py`（**已接 CI**，变异测试验证非空转）+ 13 条单测。**轨道 2**：`tools/w8_artifact_ledger.py`（只读）⇒ `docs/eval-artifact-ledger.md`（100 条目 / 143.5 MB；引用四档：手写 20 个、**结构登记 76 个 / 131.1 MB**、无任何证据 **15 个 / 4.2 MB**）。**✅ 台账 review 已于 2026-09-18 结案：维持现状、不删除任何产物**（判据修正与结案理由见决策记录 **D-16**）；review 全程未删除/移动/重命名任何产物。**⚠️ 中途出过一次数据事故（已恢复，见缺陷 D5）** |
| W8 命名三分（Arm 1 遗留项） | ✅ **已交付并合入 master**（2026-09-18，PR #7 ⇒ `65aabee`） | 键名契约唯一真相源 `research_engine/eval/status_keys.py`（新增）：层② `invoke_status`（raw 顶层）/ 层③ `metrics_status`（eval 顶层）/ `LEGACY_STATUS` 只读。`run.py` **7 处落盘键**改名；读侧（`phase2` / `_summarize` / `report_gen` 异常附录 / `tools/measure_paired_rho.py`）全部 dual-read。**核心决策：历史产物一律不回填**（取证对象 + W7 零成本复算）⇒ 全量只读对账 **1,498 raw + 1,443 eval 全部读出、取值零越界**。新增 `tests/test_status_naming.py` **26 条**（含端到端落盘扫描 + 两份反向守卫 + AST 源码守卫）。**副作用登记**：发现 `history.json` 的 `status` 是恒为 `"PASS"` 的空转字段（见下方「未决 / 待拍板」） |
| W8 after 基线 | ✅ **已结案**（2026-09-19，3/3 轮） | 四指标**全部不可判定**；外推显示跑满 9 轮 MDE 仅降 9~25%，仍判不动 ⇒ **D-18 拍板不续跑**。20/20 零异常 ×3、`git_dirty=false` ×3、¥2.5 / 2h29m。结论见 `docs/eval-w8-after-baseline.md` |
| W8 确定性 DoD | ✅ **8/8 完成** | 故障可归因 / 状态分层 / 异常退出 / 成本守恒 / 质量闸 / 可复现元数据 / 产物治理 / 历史兼容 —— 逐项有测试与 CI 证据（**330 全绿**），见 `docs/eval-w8-dod.md`。⚠️ 与统计侧严格分开：**机制可验收 ≠ 均值质量提升成立** |
| W9 Web UI 重构 | ✅ **基础交付完成**；后续产品化能力另立项 | FastAPI 后端 + SSE/AG-UI 事件流 + 协作式取消 + React/Vite/TS/Tailwind 基础交互；23 条 Web/SSE 测试、前端边界守卫、CI `npm ci`/类型检查/production build 已落地；2026-09-24 本地复验再次通过 |
| L3 P1 第一批（部署底座本地骨架） | ✅ **已交付并本地验证（2026-09-24）** | 新增 `Dockerfile.api`（多阶段：node 构建前端 + python 3.13 运行时）、`docker-compose.staging.yml`（PG 16 + Redis 7 + 迁移 + API）、`tools/migrate.sh`（只前向 + `schema_migrations`，CI 锁二次幂等）、`/api/health/live` 与 `/api/health/ready`（TCP 探针，未配置不算失败）、CORS 环境变量化（`DR_CORS_ORIGINS`）。测试 **455 → 461 全绿**、ruff 全过；本机 Docker 实跑：迁移两次 `applied=0`、ready=`postgres+redis ok`、镜像内前端可服务（342 KB JS）。CI 新增 `infra` job（compose config + 迁移幂等 + 镜像构建），**尚未 push 首跑**；⚠️ Dockerfile.worker / 真实云上 staging / 域名与备案仍待 P3 与外部事实；ADR-0009 保持草稿 |
| L3 多用户生产化 | **草稿（P0 推荐基线 v2 + 设计细化，2026-09-24）** | 首发范围按已确认外部事实收缩为**个人主体下的封闭验证**：L3-A 5 人 / L3-B 10 人，并发 2/3，1 次/日，预算 ¥1,500 / ¥2,000 月，**L3-C 暂缓**；境外服务默认关闭（恢复须独立评审）。落点：`docs/requirements/10-l3-production.md` §3.1 + §5.9~§5.13（任务状态迁移 / 租约 / API 面 / 审计计量 / 配置清单）、`docs/operations/production-readiness.md`（含 §7 附录：SSE 反代样例 / 备份恢复 / 发布检查单 / 告警阈值候选）；`docs/decisions/0009-l3-multiuser-production.md` 继续为草稿（含 §5 内存态迁移映射），**仍待最终域名、云报价、供应商授权与合规书面口径；确认前零实现改动** |

## P0 本地交付基线复验（2026-09-24）

| 验证项 | 结果 |
| --- | --- |
| Python 环境 | 新建项目专属 venv `C:\Users\Administrator\.workbuddy\binaries\python\envs\deepresearch`；Python 3.13.14；按两份 lock 安装；`pip check` 通过 |
| Python 质量门禁 | `ruff check .` 通过；`pytest tests/ -q --basetemp=<新目录>` **415/415 全绿**（27 个测试文件） |
| 仓库治理门禁 | `tools/check_results_whitelist.py` 通过（8 条白名单 = 8 个跟踪顶层条目）；`tools/check_frontend_boundary.py` 通过 |
| 前端依赖与构建 | Node 22.22.2；`npm ci` 安装 233 包、0 漏洞；`tsc --noEmit` 通过；Vite 6.4.3 production build 通过（283 modules） |
| 工作区整理 | 18 组未跟踪的简历、浏览器配置和临时产物已**原样迁移**到忽略目录 `local-artifacts/`，未删除个人文件；仓库根不再暴露这些未跟踪项 |
| 非阻断告警 | Starlette `TestClient` 对 `anyio.abc.BlockingPortal` 的弃用告警 1 条；首次测试进程退出时宿主 OTel exporter 到本地端口超时，最终复验显式设置 `OTEL_SDK_DISABLED=true` 后 stderr 为空，测试结果不受影响 |

## Arm 5 历史回填记录（2026-09-17 迁移，已完成）

| 项 | 结果 |
| --- | --- |
| 迁移对象 | `research_engine/eval/results/*/summary.json` — **75 个 run，全部回填成功，0 跳过、0 异常** |
| 新增字段 | `metrics_stderr`（含 `n` / `stderr` / `ci95`）、`verdict`、`verdict_reasons`、`metrics_ok` / `metrics_partial` / `metrics_failed`、`deprecated_keys` |
| `verdict` 分布 | **ok 73 / suspicious 1 / broken 1** |
| 回归验收（真实 run） | `run_20260910_173540` ⇒ **suspicious** ✅；`run_20260910_171054` ⇒ **broken** ✅ |
| D1 修复 | **2 个 run**：`run_20260916_022440`（token 0 → **1,017,784**）、`run_v11_compare`（0 → **683,649**）；均改为 `cost_yuan=None` + `cost_degraded=true` + `cost_basis=raw_token_sum` |
| 幂等性 | 回填后再次 dry-run ⇒ **75 个全部 `unchanged`**，D1 标记清零 |
| 迁移日志 | `local-artifacts/root-logs/_arm5_backfill_20260917_0111.log`（2026-09-24 随根目录日志隔离迁移；含迁移前 git 状态 / dry-run / apply / 复核全过程） |
| 备份 | `_arm5_backup_20260917_0111/`（75 份迁移前 summary.json）—— ✅ **已清理**（2026-09-24 复核：本机已不存在） |
| 测试 | ruff 全绿；pytest **269 全绿**（267 → +2 条回填回归：D1 修复 + 幂等 + 跳过无 eval 的 run） |

## 已知缺陷（登记待修）

| 编号 | 缺陷 | 处置归属 |
| --- | --- | --- |
| D1 | ~~**成本静默归零**：`phase1_global_stats.json` 缺失时返回 `{}` ⇒ 成本直接取 0，且 **shape 与真实结果完全相同、无任何标记**。已在 before 基线第 3 轮发生（cost=¥0 而 raw token=1,017,784）~~ | ✅ **已修**（Arm 5 附带，2026-09-17）：降级为 raw 累加 token + `cost_yuan=None` + `cost_degraded=true` + `cost_basis`；报告层显示「⚠️ 不可重建」而非 ¥0。⚠️ **历史污染未清**：dry-run 扫出 2 个 run（`run_20260916_022440`、`run_v11_compare`）记的 token 少于 raw 实际 |
| D2 | Arm 2 是**功能性修复而非纵深防御**：旧模板引号叠加 ⇒ 对任何 query 都 `SyntaxError` ⇒ 基线三轮 code_exec 144 条、**成功 0**。⇒ **after 基线里 code_exec 会真的产出结果，这部分差异必须单独归因，不得计入 W8 质量提升** | 归因口径 |
| ~~D3~~ | ~~裸 `status` 字段在三层的语义混用（run / invoke / metrics）~~ | ✅ **已修**（2026-09-18，命名三分）：层② `invoke_status` / 层③ `metrics_status` 落盘，读侧 dual-read；其余四处裸 `status` 已逐条登记在 `status_keys.BARE_STATUS_SITES` 并由单测验证，见「W8 命名三分」行 |
| **D5** | 🚨 **Arm 7 期间的数据事故（已恢复，但非 100%）**：一次**跨 Arm 7 边界**的 `git checkout` 让 git 把「旧 HEAD 跟踪、新 HEAD 不再跟踪」的路径连同其目录下**未跟踪**的 raw/eval 一起删除 ⇒ `results/` 从 **143.6 MB 掉到 31 MB**。**恢复**：① 从 D 盘回收站按 `$I` 元数据里的原始路径定向还原 **2,418 个文件**；② 用 git index 补回被删除的 tracked 文件；③ 从物理备份回拷 ⇒ 现 **3,206 文件 / 151.8 MB**（台账口径 143.5 MB）。**永久丢失 5 个文件**（≈0.1 MB）：`run_20260910_033127/raw/q_014.raw.json`、`run_20260911_004201/eval/q_016` / `q_018` / `q_019` / `q_020.eval.json` —— 均属无引用、非白名单的历史 run，**不影响** W7 权威容器与任何结论文档。**副作用**：还原把全部结果文件 mtime 刷成恢复时刻（台账该列失去取证价值）；上述两 run 的 `summary.json` 仍写 `complete=20/20` ⇒ **文件缺失与 summary 不自洽** | 已立铁律（决策 D-12）；台账已自述该列失效；两 run 若要继续引用需重新生成 raw |
| D4 | 不要用 coverage 提升验收 W8：实测 MDE=12.38pp > 预期效应 4~10pp，**统计上不可判定**。W8 的验收口径是**确定性断言（故障可归因率 0%→100%）** | 验收口径 |

## 执行顺序（按顺序串行，勿并行铺开）

1. ✅ **已完**：文档状态同步 → Arm 4 真填充 → 删临时异常映射 → 补测试（20 条）
2. ✅ **已完**：Arm 5 质量闸 → **D1 成本守恒** → stderr/bootstrap → 字段重命名 → 报告带 ±stderr 与 §0 质量闸小节 → **75 个历史 run 回填完成并验收**（见上节）
3. ✅ **已完**：Arm 6 可复现元数据 —— 5 个提示词构建器抽为唯一产生点 → `prompt_hash`（对开关敏感）→ 裁判模型/独立性结构化 → `scorer_version` → raw/summary/history/report 四处落地 + 22 条测试
4. ✅ **已完**：Arm 7 产物治理双轨 —— 白名单判据升级为「引用即入库」→ 出处写法踩坑修正（行尾 `#` 废规则）→ 引用分两档（自动枚举不算证据）→ 单点裁判 + 13 条单测 + 接进 CI → 只读台账出账（**未删任何产物**）
5. ✅ **已完**：台账 review 结案（2026-09-18）—— 修掉判据盲区（第四档「结构登记」）⇒ 候选从 83 条 / 128.2 MB 收敛到 **15 条 / 4.2 MB** ⇒ 拍板「维持现状、不删任何产物」（D-16）
6. ✅ **已完**：命名三分 —— `invoke_status` / `metrics_status` 落盘（写侧只出新键）+ 读侧 dual-read；**历史产物不回填**（Arm 7 取证纪律 + `tools/w7_backfill_*.py` 零成本复算）；新增 26 条单测（含端到端落盘扫描与两份反向守卫）
7. ✅ **已完**：冻结代码 `f723c2d` → after 基线 **3 runs**（48~51 分钟/轮、¥0.79~0.92/轮，远快于原估 1.2h）→ 四指标**全部不可判定** → **D-18 拍板：以 3 轮结案、不续跑后 6 轮**。⚠️ 原估「±7.3pp / ¥11 / 6.5h」均被实测推翻（coverage 的 MDE(3v9) 实测 **9.66pp** > 预期效应 4~10pp；实际 3 轮仅 ¥2.5 / 2h29m）
8. ✅ **已完**：**确定性 DoD 验收表**（`docs/eval-w8-dod.md`，8/8 项有测试 + CI 证据）+ 实测结果**回写设计文档**（新增 §3.3.1-b「瓶颈在 before 侧」、§10.5.6 补实测行、§10.5.7 拍板处补执行更正）
9. 🟡 **最后对外材料**：README 能力与限制 → 博客③ → 引用链接 —— **README 的 W9 部分已于 2026-09-23 同步**（见下表），剩博客③ → 引用链接

### 对外材料：README 同步记录（2026-09-23）

| 位置 | 改动 | 原因 |
| --- | --- | --- |
| 「核心能力 · 多跳检索」 | 「上限 5 跳」→ 全局 `max_total_hops=20` + 每子问题动态切分 `ceil(20/n)`；`per_subq_hop_cap=5` 降级为静态兜底 | 与「防幻觉三件套」上方「超 20 跳了就停」的表述自相矛盾；动态口径才是 W9 后的真实行为 |
| 「运行 · Web UI 方式」 | `streamlit run web/app.py` → `uvicorn web.backend.main:app` + Vite dev server；补 SSE/AG-UI/协作式取消（14.4s/31.4s）说明；标注 `web/app.py` **已废弃** | W9（D-20）已交付 FastAPI + React/Vite；README 仍写 Streamlit |
| 「目录结构」 | `web/app.py` → `web/backend/{main,agui,runner}.py` + `web/frontend/` + `tools/check_frontend_boundary.py` | 同上 |
| 「eval 诚实数字 · 单元测试」 | 330 项 → **352 项**（含 Web/SSE 专项 23 条，另 `frontend` job 跑 `tsc --noEmit` + `vite build`） | W9 新增测试后未回写 |

✅ **已处理（2026-09-23）**：`requirements.txt` 已移除 Streamlit，`requirements-lock.txt` 已按文件头命令重编译；旧入口 `web/app.py` 已删除。

## W8 收尾纪律：产物冻结（2026-09-19，D-18 拍板后生效）

**以下对象不再改动**，除非另开议题：

| 对象 | 冻结范围 |
| --- | --- |
| `research_engine/eval/results/` 历史产物 | 不再删除 / 移动 / 改名 / **回填**（含 before 三个 run 与 after 三个 run 的 `raw` / `eval` / `summary`） |
| before / after 原始 `raw` | W7「零成本可复算」的**唯一证据源**（`tools/w7_backfill_*.py` 直接读它） |
| 冻结代码 `f723c2d` | after 三轮均产自该树（产物落盘提交 `462e318` / `6dc6173` / `84fed03` 只改 `docs/eval-report.md` 与 `results/history.json`，`research_engine/` 树未动） |
| 已落盘的 summary / history / report | 不再人工编辑（它们由 `run.py` 自动整覆盖） |
| W8 关键统计数字 | coverage Δ=−5.1pp（SE 3.81）、citation +3.1pp（3.29）、retrieval −2.5pp（1.39）、steps −1.2（0.48）；MDE(3v3) = 10.66 / 9.21 / 3.88 / 1.36 |

**后续只允许做对外材料**：README 能力与限制 → 博客③ → 引用链接与出处整理。

## W9 后续增量：Planner 输出治理

| 项 | 状态 / 决策 |
| --- | --- |
| 动态子问题预算与 Planner 输出收口 | ✅ 已完成（`01292e3`）：`ceil(max_total_hops / 实际子问题数)`、plan/replan 共用解析收口、截断留痕、空问题回退、重复 ID 规范化；CI `35752032986` success |
| replan LLM fallback 留痕 | ✅ 已完成（`918c79e`）：replan LLM 异常写入 `degradation_log`，`reason=llm_error`、`fallback_action=keep_previous_subquestions`；不与策略事件通道耦合 |
| `planner_output_truncated` 语义 | ✅ 已落地：已从 `FailureReason` / `degradation_log` 删除；截断、空问题过滤、重复 ID 重写改走独立 `planner_events`，不再推导 `run_status=degraded` |
| `planner_events` 独立通道 | ✅ 已完成：state 纯追加、plan/replan 共用、报告尾部「规划治理」、`STATE_DELTA` 下发累计计数；事件类型为 `subquestions_truncated` / `empty_question_dropped` / `duplicate_id_rewritten` |
| replan 解析后为空 | ✅ 已完成：与 replan LLM 异常同样进入 `degradation_log`，`fallback_action=keep_previous_subquestions` |
| 截断指标统计 | ✅ **已完成（2026-09-24）**：eval 侧新增 `metrics.compute_planner_governance()`，把 `planner_events` 聚合为运行级计数（`totals` + `by_phase`：returned / accepted / dropped / truncation_count / empty_question_dropped / duplicate_id_rewritten），随 `compute_all` 落进每条 `*.eval.json`；历史 raw 无事件时如实全零（不回填）。**纯计数不判质量**（与 coverage 的 join 分析属后续），不改变调度顺序。新增 4 条单测；测试基线 441 → **445** |
| round-robin 调度 | ⛔ 当前不做；只有实测证明 FIFO 存在偏斜时才单独立项，避免改变 W8 查询顺序基线 |

## P1 运行护栏与前端体验（2026-09-24 已交付）

**范围前提**：全部落在 D-19/D-20 硬边界内 —— 仍是「前台跑 + 可取消」的单用户模型，
**没有**引入任务持久化、任务队列、多租户、历史任务中心。

| 项 | 落点 | 状态 / 关键口径 |
| --- | --- | --- |
| ① 前端浏览器 E2E | `web/frontend/{playwright.config.ts,e2e/}` + `npm run e2e` | ✅ **10/10**：主流程 / 刷新恢复 / 完成后续看 / 降级实时可见 / 后端导出 / 取消语义 / 结构化错误 429 + 重试 + 详情折叠 / 窄屏无横向滚动。<br>✅ **已接 CI**（C #15）：`e2e` job 用**官方 Chromium**，首轮实测 job 总耗时 **109s**、用例 **10 passed / 45.0s**（run `36006923861`），不依赖 runner 预装浏览器 |
| ② 运行超时闸 | `runner.RunManager`（`_deadlines` + 节点边界谓词） | ✅ 默认 **3600s**（实测单轮 48~51 分钟 ⇒ 留 1.2 倍余量）；`stop_reason=timeout`，**不进 RUN_ERROR、不写 run_status**（与取消同口径，不污染故障归因） |
| ③ 单进程并发限制 | `runner.start()` 抛 `ApiError(concurrency_limit)` → 429 | ✅ 默认 **1**（前台模型一次一个）；`DR_MAX_CONCURRENT_RUNS` 可调 |
| ④ 任务状态查询 | `GET /api/research/{run_id}` | ✅ 内存态画像：状态 / 已跑时长 / 剩余时间 / 事件数 / 最后事件 / stop_reason / has_report |
| ⑤ 结构化错误详情 | `web/backend/errors.py`（唯一真相源） | ✅ `{code, message, component, node, detail, retryable, hint}`；HTTP 与 SSE `RUN_ERROR` 共用。**`detail` 由字符串改为对象**（前端已同步兼容两种形状） |
| ⑥ 报告导出 | `GET /api/research/{run_id}/report?format=md\|json` + `export.py` | ✅ md = 正文 + 审计元数据 + 引用清单；json = 结构化载荷。**不落盘**（有测试锁） |
| ⑦ 前端体验 | `ReportView.tsx` / 错误卡 / 移动端 | ✅ 重试（**重发同样参数**，非断点续跑）；错误卡展示 code + hint + 归因组件；报告**分段渲染**（2 万字/段 + `useDeferredValue`）+ 宽表可横滚 + 窄屏零横向滚动（E2E 判定 `scrollWidth <= clientWidth`） |

**三条踩过的坑（写在代码注释里了）**

| 坑 | 表现 | 处置 |
| --- | --- | --- |
| 并发闸把用例**互相挡住** | 一条用例失败留下 running 的 run ⇒ 后续用例全被 429 拒掉，失败「传染」 | E2E 加 `settleRun()` 清理钩子；pytest 侧在非闸用例里显式放开 `max_concurrent_runs` |
| `beforeEach` 里 `test.skip()` 仍会跑 `afterEach` | 页面从未导航 ⇒ 清理钩子访问已关闭的页面 ⇒ 挂到 120s 超时 | 改为**按 project 用 `testMatch` 分流**（流程/错误卡只跑 desktop，响应式两个视口都跑） |
| 停止按钮 disabled 时盲点 | Playwright 等 action timeout（30s/次）⇒ 清理拖成分钟级 | 清理钩子里先判 `isEnabled()` 再点；任何异常一律吞掉（清理失败不得把通过的用例判红） |

⚠️ **诚实限制**：超时与强制收口都是**协作式**的（Python 线程无法 kill）。节点内部挂死时，
硬截止只保证传输层补 `RUN_ERROR(stop_forced)` 收口，后台线程可能仍在收尾，进程重启才彻底释放。

**复核补丁（2026-09-24 同日，见 ADR-0008）**：复核时用确定性交错复现出两个窄窗口 TOCTOU ——
① 强制收口后仍可能存入**迟到报告**并覆盖 `stop_reason`；② `RUN_FINISHED` 之后又补
`RUN_ERROR(stop_forced)` 的**双终局**。处置：终局帧 / 结果 / 状态改为在**同一把 condition 锁**下原子完成
（`_emit_terminal_frame`），`_finished` 置位前移到终局帧产生时；新增 2 条确定性交错回归
（用 monkeypatch 把工作线程钉在临界区）。该修复后护栏 **26** 条（全量数字以「测试基线」行为准）。

## C 后续增量：功能与工程质量（2026-09-24 起）

**范围前提**：仍在 D-19/D-20 硬边界内（前台跑 + 可取消、呈现层不扩产品边界）；
逐项落地、每项独立提交与 CI 证据。

| 项 | 落点 | 状态 / 关键口径 |
| --- | --- | --- |
| #8 截断指标统计（W9 增量②） | `research_engine/eval/metrics.py` | ✅ 见上「W9 后续增量」表（`67cdbe2`） |
| #9 SSE 断线重连 / 刷新恢复 | `web/frontend/src/hooks/useResearchStream.ts` + `App.tsx` + `e2e/research.spec.ts` | ✅ **已交付**：① 短暂断网 = 浏览器 `EventSource` 自动重连 + `Last-Event-ID` 续传（原有能力，本轮补文档口径）；② 刷新恢复 = `sessionStorage` 存 run_id + `GET /api/research/{id}` 快照 + **从 0 回放全部帧**重建界面（回放**零 LLM 调用**），时长按快照 `elapsed_seconds` 对齐；③ 终局/未知 run 自动清存储。**不可恢复**：后端进程重启（D-19 内存态）与跨标签页（sessionStorage 按标签隔离）。**不改后端协议**；E2E +1（**9/9**，35.9s） |
| #10 取消/超时时序测试扩展 | `tests/test_web_streaming.py` + `tests/test_web_guardrails.py` | ✅ **已交付**：① 节点执行中取消 ⇒ 在飞节点自然结束（耗时 ≥ 节点剩余时长）后停在边界、不再启动新节点（C3）；② 完成与超时同时发生（完成先落终局）⇒ 记 `completed`、**不得改判 timeout、不得再补强制收口**；③ 取消后并发槽释放且可复用；④ 崩溃后并发槽释放且可复用；⑤ 强制收口后后台线程 finally 二次释放并发位是幂等的。+4 条新用例（另加固 2 处既有用例）；测试 445 → **449** |
| #13 资源管理核查 | `tests/test_web_guardrails.py` + README | ✅ **已交付**：① 工作线程在完成 / 取消 / 崩溃三条终局路径均释放（按 `research-{run_id}` 线程名断言），强制收口后随节点自然收尾退出；② **无人消费事件（≈客户端断开）不影响执行**——运行照常完成、帧留内存可回放；③ SSE 等待超时（心跳路径）返回 `(None, False)` 且不扰动运行；④ README 写明内存 / 断开 / 线程的诚实边界。+3 条用例；测试 449 → **452**。⚠️ **TestClient 会把响应整段缓冲，无法模拟「流中途断连」**（已在用例 docstring 如实注明；真实断连由浏览器 E2E 覆盖） |
| #12 前端异常体验 | `web/frontend/src/hooks/useResearchStream.ts` + `App.tsx` + E2E | ✅ **已交付**：① **终局后保留 run_id**（存储语义从「活跃」改为「最近一次」）⇒ 刷新仍可回看最终报告 / 错误卡（回放零 LLM 调用）；② 错误详情改为**默认折叠** `<details>`（长 detail 不再挤占错误卡，展开可查，E2E 覆盖展开交互）；③ 连接断开提示（`正在重连` 徽标）与重试反馈（disabled + 文案）经核验已具备。E2E +1（**10/10**，41.2s） |
| #14 运行级统计展示 | `web/backend/runner.py` + `export.py` + `App.tsx` + E2E | ✅ **已交付**：① `RUN_FINISHED` 增加 `elapsed_seconds`（**后端记录**，刷新回放后同样权威，不依赖前端计时器），并进导出元数据（md 头行加「耗时 Xs」）；② 前端新增**运行摘要条**（总耗时 / 完成节点 / 检索跳数 / 降级条目 / 报告字数 / 成本估算，`data-testid=run-summary`）；③ 仍为**运行级**统计，**不引入持久化 / 历史中心**（D-19 边界）。+1 单测（导出 md 断言加固）；测试 452 → **453**；E2E 主流程补断言（10/10，40.0s） |
| #15 E2E 接 CI | `.github/workflows/ci.yml`（新增 `e2e` job） | ✅ **已交付**：官方 Chromium（`E2E_CHANNEL=chromium` + `playwright install --with-deps chromium`），`DR_PYTHON=python` + `DR_DEMO=1`。**首轮实测**（run `36006923861`）：job 总耗时 **109s**（含 Python 依赖 / npm ci / build / Chromium 安装），用例 **10 passed / 45.0s** ⇒ 成本远低于接入前预估（原估 4~6 分钟），稳定性以持续运行观察为准。README「未接 CI」措辞同步改写 |
| #11 后端错误场景补齐 | `web/backend/{main,errors}.py` + `tests/test_web_guardrails.py` | ✅ **已交付**：① **FastAPI 参数校验错误（422）纳入结构化契约**——新增 `RequestValidationError` handler + `invalid_request` 规格（此前 422 是 FastAPI 裸形状，与「所有 HTTP 错误共用同一结构」的声明不符）；② 补**图内 `STOP_ERROR` 终局**用例（错误帧带 code / `component=graph` / node，不产出报告、释放并发位）。provider 不可用 / 空结果 / 格式错误 / 报告缺失 / run_id 不存在此前已覆盖（Arm 4 + P1）。+2 条用例；测试 453 → **455** |

## 未决 / 待拍板

| 项 | 说明 |
| --- | --- |
| after 基线后 6 轮是否续跑 | ✅ **已结案**（2026-09-19 拍板 = **不续跑**，见 **D-18**）：四指标**全部不可判定**，且外推显示跑满 9 轮仍判不动（`R_b=3` 锁死 before 侧方差）。**以 3 轮结案**。⚠️ 若将来推翻本决策，driver 仍在 `.workbuddy/after_baseline_driver.sh`，且**配对时必须排除冒烟 run `run_20260919_004620`**（pilot 2 题，会混进 `run_20260919_*`） |
| W7 五开关去留 | ✅ **已文档化并保留当前默认**：五个开关仍默认全开；`.env.example` 已记录有效值、成本与待 W8 重测项，README 已补充跑批时的 provenance 记录要求。运行行为暂不改，后续仅在 W8 重测后再裁定。见 `docs/w7-switch-disposition.md` |
| L3 首发参数与立项（20 项） | **推荐基线 v2 已成型，外部事实部分确认**：已确认个人主体 / 可备独立域名 / 不接受 ¥3,000 月级预算 / 倾向禁用境外服务 ⇒ 规模收缩为 L3-A 5 人、L3-B 10 人，并发 2/3，1 次/日，预算 ¥1,500 / ¥2,000，L3-C 暂缓（见需求 10 §3.1 与 §3.1.2）；**仍待最终域名、云报价、供应商授权与属地合规书面口径**。ADR-0009 暂为草稿，确认前不进入生产实现 |
| GitHub 凭据链 / 代理 | ✅ **已定位并绕过**（2026-09-19，三重根因，**不是**单靠 `gh auth setup-git` 能修的）：① `~/.gitconfig` 写死 `http.proxy=http://127.0.0.1:7897`（ClashVerge）—— 该端口在沙箱会话里**根本没监听**（curl 探测返回 000；当前可用代理是 `http_proxy` 的 63261），而 **git 的配置优先于环境变量** ⇒ 这才是推送失败的**主因**；② helper 写成反斜杠 `!'D:\GitHub CLI\gh.exe' auth git-credential`，Git Bash 下 git 的 shell 执行不了 ⇒ `could not read Username`；③ **空的 `credential.helper=` 会把 helper 列表重置**（trace 显示 git 一个 helper 都没调），而 `gh auth setup-git` 写入的**就是**反斜杠版，且删掉空值后 PortableGit 自带的 `credential.helper=helper-selector`(GCM) 会介入并**卡住等交互**。<br>**⇒ 可用做法**：`.workbuddy/git_push.sh push origin dev`（自动覆盖代理 + 正斜杠 helper；实测同一条 `ls-remote`：裸 git 失败、脚本成功）。**不要**改 `~/.gitconfig` 的 `http.proxy` —— 那是 ClashVerge 正常运行时的有效配置 |
| ~~W9 Web UI 技术栈~~ | ✅ **已拍板（D-20，2026-09-20）**：**FastAPI + React/Vite/TS + Tailwind + SSE，对齐 AG-UI 语义不引 CopilotKit；呈现层升级，不扩展产品边界**。拍板表 11 项 + 取消契约 C1~C7 + 三条硬边界 + 完成标准封顶 7 项 + HTMX 反悔条件，见 `docs/requirements/9-web-ui-rewrite.md` §6；**ADR-0001 已追加 §1.1 修订（不推翻）**。⚠️ 初轮我推过 HTMX、也曾推 NiceGUI（后者已撤回）⇒ **最终以 D-20 为准** |
| Gitee 凭据存储 | ⬜ 是否把令牌存入 wincred（安全决策）。**2026-09-24 已用一次性内联 token 完成两次镜像同步**：`d52da2b → 472169d`、`472169d → eabf685`（dev + master 均已核对；推送时 `-c credential.helper=` 确保未落盘/未存 wincred），存储方式仍待拍板 |
| 台账 review | ✅ **已结案**（2026-09-18，见 **D-16**）—— `docs/eval-artifact-ledger.md`：判据补第四档「结构登记」后候选从 83 条 / 128.2 MB 收敛到 **15 条 / 4.2 MB**，主理人拍板 **维持现状、不删任何产物**。⚠️ 两条反向陷阱仍然有效：① **引用只增不减是有偏的** —— 治理过程会把产物名写进处置记录，「写了说明」≠「原本被引用」；② **「引用仅来自看板/需求文档」也不等于可删**（before 基线三个 run 正是靠 §10.4 验收记录存续）。②③ 之外新增第四条：**「扫不到引用」也不等于可删** —— `tools/measure_paired_rho.py --runs` 是 argv 传参、从不落盘，那批 run 名在仓库里天生无迹可循 |
| 事故残留清理 | `.workbuddy/_arm7_results_backup/`（**3,206 文件 / 151.8 MB** 物理备份）与 `.workbuddy/restore_results_from_recycle.py`（一次性还原脚本）—— 事故已恢复完毕，**待主理人确认后**才可删（`git rm --cached` 边界已用 `git branch -f master dev` 消除） |
| **其余裸 `status` 是否收敛** | ✅ **已处置**（2026-09-19，**D-17**）—— 层②③ 之外残留的裸 `status` 原登记 4 条，`history.json` 那条已**删除字段**（写侧不再产出 + 75 条记录全量剥离 + 登记表移除该条 + 单测反向锁「不再存在」）；`BARE_STATUS_SITES` 现为 **3 条**，全部建议保持：`state.status`（graph 流转状态，嵌在 `state` 命名空间内，改名会与 Pydantic schema 脱钩）、W7 manifest 的 `runs[].status`（历史证据，口径以 W7 结论文档为准）、Langfuse trace 的 `status`（外部字段）。⚠️ 查证 `history.json` 是否真「零读取方」时踩到一次假阳性：`report_gen.py:250` 的 `rec.get("status")` 读的其实是 **W7 manifest 的 `runs[]`**，不是 history —— 数据源不同，逐条核对才确认零读取方。现状已由 `tests/test_status_naming.py::test_history_status_is_vacuous` 锁住，不会悄悄漂移 |
| CI action 版本维护 | ✅ **已处置 setup-node 部分**（2026-09-24，`be03ac1`）：`v4 → v7`（v7 运行时 node24），dev push `35965038662` 复验 success，Node 20 弃用告警消失。⬜ 剩余：`ubuntu-latest` 将于 **2026-10-19 起迁移 Ubuntu 26**（信息性提示）⇒ 迁移日前复验 CI |
