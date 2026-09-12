# 需求-8-fault-transparency-and-reproducibility

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> 本稿为 **草稿（2026-09-10 建稿）**，基于项目外部评审报告（综合评分 6.5/10）编排，聚焦"故障状态透明化 + 可复现性 + 评测口径收敛"三大短板。
> 飞书镜像：待创建（`lark-cli docs +create --doc-format markdown --content @./docs/requirements/8-fault-transparency-and-reproducibility.md --parent-token MYR6fazL5la0ardJdUecOBkVnd8 --title "第八周需求文档" --as user`）

## 1. 元信息

| 项        | 值                                                                                                       |
| -------- | ------------------------------------------------------------------------------------------------------- |
| 编号       | #8                                                                                                      |
| 标题       | 故障状态透明化 + 可复现性 + 评测口径收敛                                                                                 |
| 优先级      | **P0**（Arm 1/2/3）、P1（Arm 4/5/6）、P2（Arm 7）                                                               |
| 状态       | 草稿                                                                                                      |
| 负责人      | TianJinYing2006                                                                                         |
| 关联 Issue | #8（待建）                                                                                                  |
| 关联 PR    | <br />                                                                                                  |
| 创建 / 更新  | 2026-09-10                                                                                              |
| 实现顺序     | **Arm 1（状态分层）→ Arm 2（代码注入修复）→ Arm 3（依赖锁定）→ Arm 4（工具失败原因）→ Arm 5（评测口径收敛）→ Arm 6（可复现元数据）→ Arm 7（评测产物治理）** |

## 2. 问题背景

外部评审对项目给出综合评分 6.5/10，核心判断是：

> 故障状态不够透明、评测结果不够稳定、运行环境不够可复现。

这不是功能缺失，而是工程可信度缺口。项目在 Agent 架构（8/10）、工程规范（7.5/10）方面表现不错，但研究结果可靠性（4.5/10）、可复现性（4/10）、生产准备度（4/10）三条线拖了后腿。

最新一次评测 `run_20260910_173540` 暴露的问题最尖锐：20 条任务全部写出 raw 文件（`complete=20`），但 `completion_rate=0`、`citation_accuracy=0`、`coverage=0`、`token_used=0`。raw 文件中可见 Planner 退化为单主题子问题、Critic 直接 `hard_stop`、Writer 返回系统兜底报告、Validator 未产生有效引用。系统进入了大量降级路径，但代码没有把根因完整记录下来——`state.status` 仍然是 `"done"`，`state.error` 仍然是 `None`。

这意味着"Graph 运行结束"和"研究结果可信"之间没有状态边界。UI 和评测系统看到一个"完成的任务"，用户拿到的其实是降级结果。

## 3. 需求分析

### 3.1 目标（量化成功定义）

| Arm           | 目标                    | 量化指标                                                                                                                |
| ------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Arm 1（状态分层）   | 运行状态能区分成功与降级          | `state.run_status` 字段覆盖 `success/degraded/partial/failed` 四态；任何 LLM/搜索/代码执行/Validator 失败后 `run_status != "success"` |
| Arm 2（代码注入修复） | 计算脚本不包含原始 query 文本    | `researcher.py` 的 `_default_code_script()` 不再拼接 query；query 通过 stdin/JSON 参数传入；中文和特殊字符不触发语法错误                       |
| Arm 3（依赖锁定）   | 同一 commit 在不同机器上可复现安装 | 生成 `requirements-lock.txt`；CI 使用锁定版本；至少一条无外部 API 的 smoke test 通过                                                    |
| Arm 4（工具失败原因） | "没有搜到"和"搜索坏了"可区分      | 搜索结果携带 `failure_reason` 字段，取值 `not_configured/timeout/provider_error/empty_result/parse_error` 之一                   |
| Arm 5（评测口径收敛） | `complete` 不再误导       | 拆分为 `raw_written`（文件写出）/ `quality_complete`（质量达标）/ `degraded`（走过 fallback）/ `research_success`（证据充分且引用验证完成）         |
| Arm 6（可复现元数据） | 评测结果可追溯到完整环境          | 每条 raw 记录包含 `git_dirty`、`git_diff_hash`、`python_version`、`deps_frozen_hash`、`model_name`、`search_provider`          |
| Arm 7（评测产物治理） | 仓库不再臃肿                | `.gitignore` 忽略 `research_engine/eval/results/run_*`；仓库只保留 `baseline.json` + summary + 少量脱敏样例                       |

### 3.2 成功定义约束

沿用 W5 血泪教训：单轮 eval 数字是量级参考不是精确值，跨版本对比必须多次重跑取均值（≥3 次）。本次新增的 Arm 1/4/5 改动都需要在受控条件下做"改前 vs 改后"对照，每项至少 3 轮取均值。

## 4. 当前设计（代码现状，2026-09-10 盘点）

### 4.1 Arm 1：状态分层缺失

`ResearchState`（state.py:54-91）的 `status` 字段取值范围为 `pending/planning/researching/writing/validating/done/failed`，但没有"降级"语义。`error` 字段默认为 `None`，且在 graph.py 全部节点中从未被赋值为非 None。

| 节点            | status 赋值       | 行号           | error 赋值 |
| ------------- | --------------- | ------------ | -------- |
| `_plan()`     | `"planning"`    | graph.py:101 | 无        |
| `_research()` | `"researching"` | graph.py:170 | 无        |
| `_write()`    | `"writing"`     | graph.py:251 | 无        |
| `_validate()` | `"done"`        | graph.py:262 | 无        |
| `_render()`   | `"done"`        | graph.py:278 | 无        |

graph.py:282-314 的 `run()` 方法执行 `self.graph.invoke(initial, cfg)`，没有 try/except 捕获并写回 `state.error`。

### 4.2 降级路径全景

以下 `except Exception: return fallback` 模式使得失败被静默吞掉：

| 位置             | 文件:行号                 | 降级行为                                                            | 问题                            |
| -------------- | --------------------- | --------------------------------------------------------------- | ----------------------------- |
| Planner        | planner.py:70-72      | `plan()` 失败 → 返回 `[SubQuestion(topic=topic)]`                   | 退化为单主题，不被标记                   |
| Planner replan | planner.py:105-107    | `replan()` 失败 → 返回原 `subs`                                      | 不被标记                          |
| Writer         | writer.py:90-93       | LLM 失败 → `_fallback_report()`                                   | 生成兜底报告，status 仍为 done         |
| Writer 兜底      | writer.py:99-104      | 每个子问题写"信息不足"                                                    | 用户无法区分"真信息不足"和"系统故障"          |
| Validator LLM  | validator.py:354-356  | LLM 失败 → `llm_failed=True`                                      | 后续降级为存在性判定                    |
| Validator 降级   | validator.py:380-383  | `llm_failed` → `verified=True, supported=False, confidence=0.5` | 按"来源存在"通过                     |
| Validator 缺失   | validator.py:402-404  | 无 LLM 反馈 → 保守通过                                                 | 同上                            |
| Web 搜索         | researcher.py:88-89   | `except Exception: return []`                                   | "没搜到"和"搜索坏了"不可区分              |
| RAG 搜索         | researcher.py:109-110 | `except Exception: return []`                                   | 同上                            |
| 单工具            | researcher.py:180     | `except Exception: return []`                                   | 同上                            |
| arXiv          | arxiv.py:78-79        | `except Exception: return SearchResponse(results=[])`           | 同上                            |
| RAG store      | store.py:47-50        | Qdrant 初始化失败 → `_available=False`                               | 后续 search/scroll\_all 返回 `[]` |
| 上下文压缩          | manager.py:74-75      | 压缩失败 → 原样返回                                                     | 不被标记                          |

### 4.3 Arm 2：代码执行注入

`researcher.py:196-210` 的 `_default_code_script()` 在第 207 行将 query 直接拼接进 Python 源码：

```python
f"print('query={query!r}')\n"
```

`repr()` 理论上能处理引号转义，但中文和特殊字符在特定编码环境下会触发语法错误。`run_20260910_173540` 的 raw 结果中已确认至少一条代码执行因中文查询被拼进 Python 源码后出现乱码语法错误。

### 4.4 Arm 3：依赖环境

* 项目声明支持 Python 3.10+，CI 使用 3.11，当前工作区实际为 3.14

* `.deps/` 中 `pydantic_core` 是 `cp313` 二进制，Python 3.14 导入失败

* 当前环境无 `pytest`、无 `ruff`

* `compileall` 语法编译通过，但无法证明测试通过

* 依赖使用开放式版本范围（`>=x.y`），无锁文件

* LangGraph、Qdrant、OpenAI SDK、Langfuse 等快速变化依赖会产生兼容漂移

### 4.5 Arm 5：评测口径

`eval/run.py` 的 `_summarize()`（run.py:307-334）中，`complete` 字段（run.py:311）统计的是"成功写出 raw 文件的任务数"，不是"研究流程真正成功的任务数"。这导致 `complete=20` 但 `completion_rate=0` 的矛盾现象。

### 4.6 Arm 6：可复现元数据

`eval/run.py` 的 `_run_one()`（run.py:97-109）在 raw 记录中写入 `git_commit`（run.py:104），`_summarize()` 在 summary 中也写入 `git_commit`（run.py:331）。`report_gen.py` 的 `write_baseline()`（report\_gen.py:54-69）额外记录了 `config_snapshot`（第 61 行）和 `eval_env`（第 62-67 行，含 Python 版本、OS、并发数、墙钟）。

但缺少：`git_dirty`（是否 dirty）、`git_diff_hash`（工作区 diff hash）、`deps_frozen_hash`（锁定依赖 hash）、`model_name`（实际使用的模型名）、`search_provider`（搜索 provider 配置）。评测可能运行在"有未提交修改的工作区"上，但结果看起来像是来自干净的 commit。

### 4.7 Arm 7：评测产物

`.gitignore` 不包含 `research_engine/eval/results/` 的忽略规则。Git 中已有 118 个已跟踪评测结果路径，约 6.4 MB。当前工作区还有大量未跟踪的 run 目录。

## 5. 优化方案

### 5.1 Arm 1：运行状态分层（P0）

#### 5.1.1 新增 `run_status` 字段

`ResearchState`（state.py:90-91）新增 `run_status: str` 字段，取值范围：

| 值          | 含义               | 触发条件                            |
| ---------- | ---------------- | ------------------------------- |
| `success`  | 全链路无降级           | 所有节点正常完成，未走过任何 fallback         |
| `degraded` | 走过 fallback 但有输出 | 至少一个节点走了 fallback，但最终产出了报告      |
| `partial`  | 部分节点失败           | 部分研究节点失败，但 Writer 仍产出了报告（可能是兜底） |
| `failed`   | 研究流程失败           | 核心节点失败导致无法产出报告                  |

#### 5.1.2 降级追踪器

新增 `DegradationTracker`（轻量 dataclass，挂在 `ResearchState` 上），记录每次 fallback：

```python
@dataclass
class DegradationEntry:
    node: str           # "planner" / "researcher" / "writer" / "validator"
    component: str      # "llm" / "web_search" / "rag_search" / "arxiv_search" / "code_exec"
    reason: str         # 人类可读原因
    fallback_action: str # "topic_only" / "empty_list" / "fallback_report" / "existence_only"
    timestamp: float
```

每个 `except Exception` 捕获点在返回 fallback 前，向 tracker 追加一条记录。`_validate` 和 `_render` 节点在设置 `status="done"` 时，根据 tracker 内容决定 `run_status`：

* tracker 为空 → `run_status = "success"`

* tracker 非空且有报告输出 → `run_status = "degraded"`

* 部分节点失败但有报告 → `run_status = "partial"`

* 核心节点失败无报告 → `run_status = "failed"`

#### 5.1.3 落点清单

| 改动点            | 文件            | 行号                        | 改动内容                                                                                  |
| -------------- | ------------- | ------------------------- | ------------------------------------------------------------------------------------- |
| 新增字段           | state.py      | 90-91 后                   | `run_status: str = "success"` + `degradation_log: list = field(default_factory=list)` |
| Planner 降级标记   | planner.py    | 70-72                     | fallback 前追加 tracker entry                                                            |
| Writer 降级标记    | writer.py     | 90-93                     | fallback 前追加 tracker entry                                                            |
| Validator 降级标记 | validator.py  | 354-356, 380-383, 402-404 | 每条降级路径追加 tracker entry                                                                |
| 搜索降级标记         | researcher.py | 88-89, 109-110, 180       | fallback 前追加 tracker entry                                                            |
| arXiv 降级标记     | arxiv.py      | 78-79                     | fallback 前追加 tracker entry                                                            |
| 状态判定           | graph.py      | 262, 278                  | `_validate`/`_render` 设置 `run_status`                                                 |

### 5.2 Arm 2：代码执行注入修复（P0）

#### 5.2.1 移除 query 拼接

`researcher.py:196-210` 的 `_default_code_script()` 改为不接受 query 参数。计算脚本完全不包含原始 query 文本：

```python
def _default_code_script() -> str:
    return (
        "import math\n"
        "n = 8192\n"
        "flops_per_token = 6 * n * 2\n"
        "total_flops = n * flops_per_token\n"
        "print('sequence_length=' + str(n))\n"
        "print('approx_flops=' + '{:.3e}'.format(total_flops))\n"
    )
```

#### 5.2.2 query 通过元数据传入

调用方（`researcher.py` 中调用 `_default_code_script` 的位置）将 query 放到 `CodeExecOutput` 的元数据字段中，不进入脚本源码。如需在输出中关联 query，通过 `CodeExecInput.metadata["query"]` 传入，执行器将其写入 `CodeExecOutput.metadata["query"]`。

#### 5.2.3 中文/特殊字符测试

新增专项测试：query 包含中文、引号、反斜杠、换行符时，脚本执行不出现语法错误。测试用例覆盖：纯中文、中英混合、含 `'` 和 `"`、含 `\n` 和 `\t`、含 Unicode emoji。

### 5.3 Arm 3：依赖环境锁定（P0）

#### 5.3.1 锁文件生成

使用 `pip freeze` 或 `pip-compile` 生成 `requirements-lock.txt`，包含严格版本号。CI 安装时使用 `pip install -r requirements-lock.txt`。

#### 5.3.2 Python 版本明确

`pyproject.toml` 的 `requires-python` 从 `>=3.10` 改为 `>=3.11,<3.14`（与 CI 和 `.deps/` 二进制对齐）。明确声明支持 Python 3.11、3.12、3.13。

#### 5.3.3 Smoke test

新增一条无需外部 API 的 smoke test：使用 mock LLM 和 mock 搜索，跑完整 Graph 流程，验证端到端不崩溃。这条测试在 CI 中每次都跑。

### 5.4 Arm 4：工具失败原因暴露（P1）

#### 5.4.1 搜索结果结构扩展

`SearchResponse`（或等价结构）新增 `failure_reason: Optional[str]` 字段：

| 取值                 | 含义                                     |
| ------------------ | -------------------------------------- |
| `None`             | 正常返回（有结果或确认无结果）                        |
| `"not_configured"` | provider 未配置（如 Qdrant 未初始化、API key 缺失） |
| `"timeout"`        | 请求超时                                   |
| `"provider_error"` | provider 返回错误（HTTP 5xx、API 限流等）        |
| `"empty_result"`   | provider 正常响应但无匹配结果                    |
| `"parse_error"`    | provider 返回了数据但解析失败                    |

#### 5.4.2 落点清单

| 改动点       | 文件            | 行号      | 改动内容                                                     |
| --------- | ------------- | ------- | -------------------------------------------------------- |
| Web 搜索    | researcher.py | 88-89   | `except Exception` 中区分原因，写入 `failure_reason`             |
| RAG 搜索    | researcher.py | 109-110 | 同上                                                       |
| arXiv 搜索  | arxiv.py      | 78-79   | 同上                                                       |
| RAG store | store.py      | 47-50   | `_available=False` 时设置 `failure_reason="not_configured"` |
| 单工具异常     | researcher.py | 180     | 同上                                                       |

### 5.5 Arm 5：评测口径收敛（P1）

#### 5.5.1 字段拆分

`eval/run.py` 的 `_summarize()`（run.py:307-334）中，将 `complete` 拆为四个独立字段：

| 字段                 | 含义               | 计算方式                                                |
| ------------------ | ---------------- | --------------------------------------------------- |
| `raw_written`      | 成功写出 raw 文件的任务数  | 现有 `complete` 逻辑                                    |
| `quality_complete` | 报告达到质量要求的任务数     | `completion_rate >= 阈值` 且 `citation_accuracy >= 阈值` |
| `degraded`         | 走过 fallback 的任务数 | 依赖 Arm 1 的 `run_status != "success"`                |
| `research_success` | 证据充分且引用验证完成的任务数  | `run_status == "success"` 且 `coverage >= 阈值`        |

#### 5.5.2 eval-report 模板更新

`docs/eval-report.md` 的指标表更新，将 `complete` 列替换为 `raw_written`，新增 `quality_complete` / `degraded` / `research_success` 三列。历史趋势表同步更新。

### 5.6 Arm 6：可复现元数据（P1）

#### 5.6.1 raw 记录扩展

`eval/run.py` 的 `_run_one()`（run.py:97-109）中，每条 raw 记录新增：

| 字段                 | 来源                               | 用途              |
| ------------------ | -------------------------------- | --------------- |
| `git_dirty`        | `git status --porcelain` 非空      | 标记工作区是否 dirty   |
| `git_diff_hash`    | `git diff --binary \| sha256sum` | 工作区 diff 的指纹    |
| `python_version`   | `sys.version`                    | 实际 Python 版本    |
| `deps_frozen_hash` | `requirements-lock.txt` 的 sha256 | 依赖版本指纹          |
| `model_name`       | config 中的模型配置                    | 实际使用的模型         |
| `search_provider`  | config 中的搜索配置                    | 搜索 provider 及参数 |

#### 5.6.2 summary 记录扩展

`_summarize()`（run.py:307-334）同步新增上述字段的汇总值。

### 5.7 Arm 7：评测产物治理（P2）

#### 5.7.1 .gitignore 更新

新增规则：

```
research_engine/eval/results/run_*/
!research_engine/eval/results/.gitkeep
```

保留 `baseline.json`、`summary.json`、少量脱敏样例在仓库中（通过白名单或单独目录管理）。

#### 5.7.2 历史结果清理

对已跟踪的 118 个评测结果路径，执行 `git rm --cached -r` 移除跟踪（文件保留在本地）。保留最新一次运行的 `baseline.json` 和 `summary.json` 作为基线。

## 6. 设计策略

### 6.1 Arm 1 策略：轻量 tracker，不改图结构

`DegradationTracker` 不引入新节点或新边，只在现有节点的 `except` 块中追加记录。`run_status` 的判定逻辑放在 `_validate` 和 `_render` 节点的尾部，纯函数计算，不依赖额外 LLM 调用。

设计取舍：曾考虑在每个节点出口加一个"健康检查"子节点，但这会增加图的复杂度且与 W1 收敛单测冲突。当前方案的代价是每个 `except` 块需要手动追加 tracker entry——这是可接受的机械工作量。

### 6.2 Arm 2 策略：元数据通道，不改执行器协议

query 通过 `CodeExecInput.metadata` 传入，执行器将其透传到 `CodeExecOutput.metadata`。这不需要修改 `code_exec.py` 的执行协议（stdin/stdout 管道），只是调用方不再把 query 拼进脚本字符串。

### 6.3 Arm 3 策略：pip freeze 而非 uv/poetry

选择 `pip freeze` + `requirements-lock.txt` 而非 `uv.lock` 或 `poetry.lock`，原因是项目当前使用 `pyproject.toml` + `requirements.txt` 模式，引入新工具链会增加迁移成本。`pip freeze` 是零成本方案，CI 只需改一行安装命令。

### 6.4 Arm 4 策略：failure\_reason 附加而非替换

搜索结果返回 `[]` 时，不改变现有的空列表语义（下游代码不需要改动），而是附加 `failure_reason` 字段。下游代码可以选择性消费该字段。这是一个非破坏性扩展。

### 6.5 行业参考

| 参考项目                                     | 相关实践                                                    |
| ---------------------------------------- | ------------------------------------------------------- |
| google-gemini/gemini-fullstack-langgraph | `is_sufficient` + `knowledge_gap` 同一次输出吐出，零额外调用（W7 已参考） |
| langchain-ai/open\_deep\_research        | `ResearchComplete` 工具调用作为显式停止信号，而非隐式状态推断                |
| dzhng/deep-research                      | 纯硬闸递归，但无降级追踪——本项目在硬闸基础上补追踪是增量改进                         |

## 7. 验收标准（DoD）

### Arm 1（P0）

* [ ] `ResearchState` 新增 `run_status` 和 `degradation_log` 字段

* [ ] 所有 `except Exception: return fallback` 路径在返回前追加 `DegradationEntry`

* [ ] `_validate` 和 `_render` 节点根据 `degradation_log` 设置 `run_status`

* [ ] `run_status` 取值覆盖 `success/degraded/partial/failed` 四态

* [ ] Web UI 和 eval 能读取 `run_status` 并展示

* [ ] 受控对照实验：注入一个 LLM 失败，验证 `run_status == "degraded"` 且 `degradation_log` 非空

### Arm 2（P0）

* [ ] `_default_code_script()` 不再接受 query 参数，脚本源码不含 query 文本

* [ ] query 通过 `CodeExecInput.metadata` 传入

* [ ] 中文/特殊字符测试用例全部通过（纯中文、中英混合、含引号、含换行符、含 emoji）

* [ ] `run_20260910_173540` 中的中文查询乱码场景不再复现

### Arm 3（P0）

* [ ] 生成 `requirements-lock.txt`，包含严格版本号

* [ ] `pyproject.toml` 的 `requires-python` 改为 `>=3.11,<3.14`

* [ ] CI 使用 `pip install -r requirements-lock.txt` 安装

* [ ] 新增无外部 API 的 smoke test，CI 中通过

* [ ] 在 Python 3.11 环境下完整测试套件通过

### Arm 4（P1）

* [ ] `SearchResponse` 新增 `failure_reason` 字段

* [ ] Web/RAG/arXiv 三条搜索路径的 `except` 块区分五种失败原因

* [ ] RAG store 初始化失败时 `failure_reason = "not_configured"`

* [ ] 受控对照实验：断开网络后搜索，验证 `failure_reason == "timeout"` 或 `"provider_error"` 而非空列表

### Arm 5（P1）

* [ ] `_summarize()` 输出 `raw_written` / `quality_complete` / `degraded` / `research_success` 四字段

* [ ] `eval-report.md` 模板更新，历史趋势表同步

* [ ] `complete=20` 但 `completion_rate=0` 的场景在新口径下显示为 `raw_written=20, quality_complete=0, degraded=20`

### Arm 6（P1）

* [ ] 每条 raw 记录包含 `git_dirty` / `git_diff_hash` / `python_version` / `deps_frozen_hash` / `model_name` / `search_provider`

* [ ] summary 记录包含上述字段的汇总

* [ ] 在 dirty 工作区上跑一次 eval，验证 `git_dirty=true` 且 `git_diff_hash` 非空

### Arm 7（P2）

* [ ] `.gitignore` 新增 `research_engine/eval/results/run_*/` 规则

* [ ] 已跟踪的 118 个结果路径执行 `git rm --cached -r`

* [ ] 仓库中保留最新 baseline.json 和 summary.json

* [ ] 仓库体积减少 ≥5 MB

## 8. 影响范围与风险

| Arm   | 影响模块                                                                                       | 回归面             | 风险                                                  |
| ----- | ------------------------------------------------------------------------------------------ | --------------- | --------------------------------------------------- |
| Arm 1 | state.py, graph.py, planner.py, writer.py, validator.py, researcher.py, arxiv.py, store.py | 全链路状态流转；W1 收敛单测 | 低：新增字段不破坏现有逻辑；但每个 except 块需手动改，可能遗漏                 |
| Arm 2 | researcher.py, code\_exec.py                                                               | 代码执行路径          | 低：脚本模板是确定性函数，改动范围小                                  |
| Arm 3 | pyproject.toml, CI 配置, 新增 smoke test                                                       | 依赖安装；CI 流水线     | 中：锁文件可能遗漏某些平台特定依赖；Python 版本收窄可能影响已有用户               |
| Arm 4 | researcher.py, arxiv.py, store.py, SearchResponse 定义                                       | 搜索结果消费方         | 低：非破坏性扩展，下游可选消费                                     |
| Arm 5 | eval/run.py, eval/report\_gen.py, docs/eval-report.md                                      | 评测流水线           | 中：历史趋势表格式变化，需要兼容处理                                  |
| Arm 6 | eval/run.py, eval/report\_gen.py                                                           | 评测记录格式          | 低：新增字段，不影响现有字段                                      |
| Arm 7 | .gitignore, Git 历史                                                                         | 仓库结构            | 中：`git rm --cached` 不删本地文件但改变仓库跟踪状态；需确保不误删 baseline |

### 降级/兜底

* Arm 1 的 `DegradationTracker` 如果因遗漏某些 except 块而不完整，不影响系统运行——只是 `run_status` 可能误判为 `success`（与现状一致，不会更差）

* Arm 3 的锁文件如果无法覆盖全部依赖，回退到现有开放式版本范围（不会更差）

* Arm 7 的 `git rm --cached` 如果误删文件，可通过 `git checkout` 恢复

## 9. 测试策略

### 9.1 单元测试

| Arm   | 测试内容                                                                      | 预期                        |
| ----- | ------------------------------------------------------------------------- | ------------------------- |
| Arm 1 | mock LLM 抛异常 → `DegradationTracker` 记录 entry → `run_status == "degraded"` | tracker 非空，run\_status 正确 |
| Arm 1 | 全链路无异常 → `run_status == "success"`                                        | tracker 为空                |
| Arm 2 | query 包含中文/引号/换行 → 脚本执行成功                                                 | 无语法错误                     |
| Arm 2 | 脚本源码中不包含 query 字符串                                                        | grep 不到                   |
| Arm 4 | mock 搜索超时 → `failure_reason == "timeout"`                                 | 字段正确                      |
| Arm 4 | provider 未配置 → `failure_reason == "not_configured"`                       | 字段正确                      |

### 9.2 集成测试

| Arm   | 测试内容                                   | 预期       |
| ----- | -------------------------------------- | -------- |
| Arm 1 | 完整 Graph 运行，注入一个节点失败 → `run_status` 正确 | 降级被追踪    |
| Arm 3 | Smoke test（mock LLM + mock 搜索）端到端不崩溃   | 通过       |
| Arm 3 | Python 3.11 环境下完整测试套件                  | 全绿       |
| Arm 5 | 评测运行 → summary 包含四字段                   | 字段存在且值合理 |

### 9.3 Eval 对照

| Arm   | 对照方式                              | 预期                             |
| ----- | --------------------------------- | ------------------------------ |
| Arm 1 | 改前 vs 改后各 3 轮 → `degraded` 任务数可统计 | 降级任务被正确标记                      |
| Arm 4 | 改前 vs 改后各 3 轮 → 失败原因分布可统计         | 五种原因可区分                        |
| Arm 5 | 新口径 summary 在历史 run 上回填 → 无矛盾     | `raw_written` 与原 `complete` 一致 |

## 10. 变更记录

| 日期         | 类型 | 原因                 | 改动摘要                   | 关联 PR/commit |
| ---------- | -- | ------------------ | ---------------------- | ------------ |
| 2026-09-10 | 新建 | 外部评审报告识别的 P0/P1 短板 | 基于 6.5/10 评审编排 7 个 Arm | —            |
