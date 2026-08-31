# DeepResearch 项目约束（CONTRIBUTING）

> 本文档为项目开发的**硬约束**，所有开发（含 AI 助手）必须严格遵守。
> 飞书镜像：飞书个人空间文件夹 `DeepResearch 需求文档`（folder token `MYR6fazL5la0ardJdUecOBkVnd8`），含：约束总文档、需求文档模板、第N周需求文档（命名见 §5）。与本文保持一致，双轨记录。
> 适用范围：DeepResearch 自主研究型 Agent 项目（补简历 agentic / 可观测 / 开源佐证缺口）。

---

## 1. 核心原则
- 仿照真实研发流程：**Issue → 从 dev 拉分支 → 开发 → 提 PR → review → 合 dev → 里程碑合 master**。
- 每个需求/优化/bug 都必须先有需求文档 + Issue，再动手。
- 双轨记录：重大设计决策**同时**写入飞书需求文档「变更记录」与仓库 `docs/decisions/` ADR，互相链接。
- 所有外部/破坏性操作先 review 再执行；git 操作保持可逆。

---

## 2. 分支模型
```
master   (保护主干，只收里程碑级合并，禁止直推)
  ▲ git merge dev + git tag vX.Y
dev      (集成分支，所有 PR 的目标，禁止直推)
  ▲ squash merge (PR 前先 git rebase dev 解冲突)
feature/<issue>-<slug> | fix/<issue>-<slug>
```
- **禁止直推 master / dev**，任何改动走 PR。
- 命名：`feature/1-critic-conditional-edge`、`fix/2-sufficiency-loop`（编号 = Issue 号）。
- 从 dev 拉新分支：`git checkout dev && git pull && git checkout -b feature/1-critic-conditional-edge`。
- 合并方式：squash 合到 dev；里程碑（W5 / 发版）把 dev 合 master 并打 `v0.x` tag。
- 合后删 feature 分支：`git branch -d feature/1-critic-conditional-edge`。

---

## 3. Issue 跟踪
- 每个需求 = 1 个 GitHub Issue；编号 = 需求文档编号。
- Issue 描述含：飞书需求文档链接 + 目标摘要 + DoD 勾选。
- PR 用 `Closes #<issue>` 关联，合并后自动关闭。

---

## 4. PR 规范
- **标题**：`<type>(<scope>): <简述>`，type ∈ `feat | fix | refactor | docs | test | chore`。
- **描述必含**：
  - 关联需求文档链接 + `Closes #<issue>`
  - 改动摘要（动了哪些模块/文件）
  - 自测结果（lint / test / eval 命令与输出）
  - DoD 逐条勾选
  - 风险与影响范围
  - Langfuse trace 截图（若涉及可观测）
- **门禁**：CI（ruff lint + pytest + eval 冒烟）必须绿；eval 不回归（引用准确率 ≥ 基线 −2%）。
- **合并前**：`git rebase dev` 解决冲突；squash 合入 dev。
- **合后**：删 feature 分支；需求文档状态置「已合」。

---

## 5. 需求文档规范
- **位置（双轨）**：
  - 飞书：个人空间文件夹 `DeepResearch 需求文档`（folder token `MYR6fazL5la0ardJdUecOBkVnd8`），含 `约束总文档`、`需求文档模板`、以及每周 `第N周需求文档`（规划活源）。
  - 仓库镜像：`docs/requirements/<编号>-<slug>.md`（与飞书同步）。
- **命名规范**：每周需求文档统一命名 `第N周需求文档`（N = 周序号，可加副标题说明本周主题，如 `第一周需求文档：Critic 节点化 + conditional_edge`）；`约束总文档`、`需求文档模板` 等元文档保持固定名。
- **同步方式**：仓库 markdown 为源，用 `lark-cli docs +create --parent-token MYR6fazL5la0ardJdUecOBkVnd8` 推到飞书；旧文档删除重建（my_library 与 Drive 文件夹跨空间不可直接 move）。
- **模板**：见 `docs/requirements/模板.md`（10 字段：元信息 / 问题背景 / 需求分析 / 当前设计 / 优化方案 / 设计策略 / 验收标准 / 影响范围与风险 / 测试策略 / 变更记录）。
- **每次优化 / 修复 bug**：在对应需求文档「变更记录」追加（日期 + 原因 + 改动摘要 + 关联 PR/commit），并同步写一条 `docs/decisions/` ADR。
- **周节奏**：以 Issue/需求为文档单位；周是推进节奏，需求跨周则更新状态，不强行 1:1。

---

## 6. AI 执行机制（pre-flight）
每次动手前必须核对：
1. 当前在 `feature/<issue>-*` 分支（非 master/dev）；
2. 有对应 Issue + 需求文档（飞书 + 仓库镜像）；
3. 改动范围与需求文档「当前设计/优化方案」匹配；
4. 提 PR 前描述含自测 + DoD 勾选。
- **提 PR 前需用户确认（human-in-the-loop）**，不自动合入。

---

## 7. 合规
- 密钥（Langfuse / API key）只在 `.env` 与 GitHub Secrets；**不进飞书、不进 git**。
- 飞书只放架构 / 方案 / 结论，不放密钥。
- 遵守 robots / 速率限制，引用保留来源（呼应 NFR6）。

---

## 8. 里程碑（W1–W5，详见 `docs/deepresearch-plan.md`）
- W1 Critic 节点化 + conditional_edge（P0）
- W2 Langfuse 接入（P0）
- W3 arXiv + 代码执行 + 强推理切换（P1）
- W4 eval 数据集 + 量化指标（P1）
- W5 开源 + 博客（P2）
