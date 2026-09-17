"""W8 §10.4：run 级 provenance 的**唯一产生点**（代码修订 + 配置快照）。

动机（详见 `docs/requirements/8-fault-transparency-and-reproducibility.md` §10.4）

1. **「取 git HEAD」在本仓库曾有 3 份实现、3 种粒度** ——
   `report_gen._git_head`（完整 HEAD）/ `run.git_head`（**同一段代码整段复制**）/
   `w7_experiment._git_rev`（8 位短 rev + `+dirty` 后缀）。
   ⇒ 本模块是唯一产生点，三处调用方一律改为 import（Q8 = C6-A 拍板）。

2. **历史 run 的消融配置不可回溯** —— 原 `_config_snapshot()` 只记 9 个模型/预算字段，
   **一个 W7 开关都不记**；而主链路配置（5 开关全开 + `VALIDATOR_MODEL` 默认）
   在 W7 六臂里**没有任何一格被测过**（§10.4 依据 1）。
   ⇒ 本模块把配置快照升级为「全项目配置唯一真相源」。

3. **「裁判是谁」长期缺席**（W8 Arm 6 补齐）—— W7 结论的硬伤是「裁判兼任被测」：
   `citation_accuracy` 直读主链路 validator 的裁决，凡改动 validator 的 arm，
   其数值同时含**被测效应 + 裁判效应**（实测纯裁判效应 +7.53pp）。
   但这个事实**只写在结论文档里，没写进产物** ⇒ 任何一个 run 的 summary.json
   单独拿出来都看不出自己的数字是谁裁的。本模块把它变成结构化字段。

设计约束
- **纯读、无副作用、零 LLM 调用**（与现有 132 个测试同律）。
- git 不可用时**如实返回 unknown / 空值，绝不抛异常** —— eval 流水线不能因元数据挂掉。
- `embedding_model`（硬编码常量）与 `strategic_model`（`planner_model` 的兼容别名）
  **明确不记**：恒值 / 重复，零信息量，记了等于给未来的自己制造考古题。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config
from research_engine.eval.aggregate import SCORER_VERSION
from research_engine.eval.prompt_hash import prompt_hash, prompt_slots

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

UNKNOWN = "unknown"

# W7 五个实验开关 —— 与 `config.ExperimentConfig` 字段名一一对应。
# 记录的是**运行期生效值**（经 `_env()` 解析后的布尔），而非环境变量原始串：
# 开关未设置时会回落默认值，`VALIDATOR_ASSERTIVE_FILTER_ENABLED` 还会二次回落到
# `VALIDATOR_FIXES_ENABLED`，只记原始环境变量等于丢失真相。
EXPERIMENT_SWITCHES: tuple = (
    "critic_gap_enabled",
    "validator_fixes_enabled",
    "validator_assertive_filter_enabled",
    "writer_sectioned_feed_enabled",
    "validator_trim_enabled",
)


def _git_text(args: List[str], repo_root: Path) -> Optional[str]:
    """跑一条 git 命令取 stdout 文本；失败/超时/无 git 一律返回 None（不抛）。"""
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=str(repo_root),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _git_diff_bytes(repo_root: Path) -> Optional[bytes]:
    """`git diff --binary` 的原始字节流；失败返回 None。"""
    try:
        out = subprocess.run(
            ["git", "diff", "--binary"],
            capture_output=True,
            timeout=10,
            check=False,
            cwd=str(repo_root),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def code_revision(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """代码修订三元组 —— 三份重复实现的统一替代。

    返回:
        - `git_commit`：完整 40 位 HEAD（抓不到 → `"unknown"`）
        - `git_dirty`：工作区是否有未提交改动（`git status --porcelain` 非空）
        - `git_diff_hash`：`git diff --binary` 输出内容的 sha256 前 16 位

    ⚠️ `git_diff_hash` 的覆盖范围诚实标注：**已跟踪文件的未暂存改动**。
    untracked 文件不会出现在 `git diff` 里，由 `git_dirty` 兜住
    （`dirty=True` 但 `diff_hash` 为空 = 有新文件尚未入库）。
    工作区干净时 diff 为空，hash 恒为 `e3b0c44298fc1c14`（空串的 sha256），属预期。
    """
    root = Path(repo_root) if repo_root else REPO_ROOT
    commit = _git_text(["rev-parse", "HEAD"], root) or UNKNOWN
    dirty = bool(_git_text(["status", "--porcelain"], root))
    diff = _git_diff_bytes(root)
    diff_hash = hashlib.sha256(diff).hexdigest()[:16] if diff is not None else ""
    return {"git_commit": commit, "git_dirty": dirty, "git_diff_hash": diff_hash}


def experiment_snapshot() -> Dict[str, bool]:
    """5 个 W7 开关的运行期生效值（布尔，非环境变量原始串）。"""
    exp = config.experiment
    return {name: bool(getattr(exp, name)) for name in EXPERIMENT_SWITCHES}


def config_snapshot() -> Dict[str, Any]:
    """全项目配置唯一真相源（W8 §10.4 第 5 条升级）。

    在原有 9 个字段基础上补三项：
    - `validator_model`：**W7 唯一被换掉的模型旋钮**（arm6 = 全开关 + qwen-turbo），
      原只在 `experiment` 段里出现 ⇒ 提为顶层，否则「这次 run 用的哪个模型」
      这个最基础的问题答案被劈成两半。
    - `python_version`：原散在 `eval_env["python"]`。
    - `experiment`：5 个 W7 开关的运行期生效值。

    明确不记：`embedding_model`（硬编码常量）、`strategic_model`（`planner_model`
    的兼容别名）、`temperature`（硬编码 0.2、非 `_env()` 可配）—— 均零信息量。
    """
    return {
        "planner_model": config.llm.planner_model,
        "critic_model": config.llm.critic_model,
        "fast_model": config.llm.fast_model,
        "smart_model": config.llm.smart_model,
        "validator_model": config.llm.validator_model,
        "python_version": sys.version.split()[0],
        "token_budget": config.research.token_budget,
        "max_total_hops": config.research.max_total_hops,
        "per_subq_hop_cap": config.research.per_subq_hop_cap,
        "max_replan": config.research.max_replan,
        "search_provider": config.search.provider,
        "experiment": experiment_snapshot(),
    }


# ---------- W8 Arm 6：裁判 provenance ----------

#: `citation_accuracy` 的裁判是否**独立于被测对象**。
#:
#: 这是**事实判断，不是配置项**：本项目的评测层不重跑 validator，而是直读主链路
#: `state.citations`（`eval/metrics.compute_citation` 的注释明确写了「不再重跑
#: validator——那是又一次 LLM 对查」）。⇒ 裁判 = 被测链路的一部分，恒为 False。
#: 想变 True 必须先改评测实现（引入独立复判），**不能只改这个常量** ——
#: 常量与实现对不上就是自欺，故下面 `CITATION_JUDGE_NOTE` 把理由一并写进产物。
CITATION_JUDGE_INDEPENDENT = False

CITATION_JUDGE_NOTE = (
    "citation_accuracy 直读主链路 validator 的裁决产物，评测层不做独立复判；"
    "凡改动 validator 的 run，其数值同时含『被测效应 + 裁判效应』"
    "（W7 实测纯裁判效应 +7.53pp，与被测效应同量级）⇒ 不可与其他 run 直接比较"
)


def judge_provenance() -> Dict[str, Any]:
    """裁判侧 provenance（W8 Arm 6）：**谁裁的、裁得独不独立**。

    模型名一律走 agent / eval 侧的单一产生点（`validator_model_name` /
    `judge_model_name`），不在本文件里再写一遍 `config.llm.*` ——
    否则「改了配置但 provenance 记的是旧名」会成为一个极难发现的假象。
    """
    # 延迟导入：validator / metrics 会拉起 LLM 客户端与 pydantic 模型，
    # 而 provenance 被 report_gen 等纯文档路径引用，不该顺带把它们拖进来。
    from research_engine.agents.validator import validator_model_name
    from research_engine.eval.metrics import judge_model_name

    return {
        "citation_judge_model": validator_model_name(),
        "coverage_judge_model": judge_model_name(),
        "citation_judge_independent": CITATION_JUDGE_INDEPENDENT,
    }


#: 单条 raw 记录要从整轮 provenance 里带走的字段（**集中一处**，防止以后漏抄）。
#: `_run_one` 的 ok / failed / timeout 三条落盘路径全部走 `raw_provenance_fields()`，
#: 新增 provenance 字段只需改这里 + `run_provenance()`。
RAW_PROVENANCE_KEYS: tuple = (
    "git_commit",
    "git_dirty",
    "git_diff_hash",
    "config_snapshot",
    "prompt_hash",
    "prompt_slots",
    "scorer_version",
    "citation_judge_model",
    "coverage_judge_model",
    "citation_judge_independent",
)


#: 历史产物（Arm 6 之前的 raw）缺失这些字段时的**如实默认值**。
#: 一律用 None / UNKNOWN，**不回填当期值** —— 回填会让「历史 run 曾用旧提示词」
#: 这个事实消失，那正是 §10.4 与 Arm 6 要消灭的问题。
PROVENANCE_DEFAULTS: Dict[str, Any] = {
    "git_commit": UNKNOWN,
    "git_dirty": False,
    "git_diff_hash": "",
    "config_snapshot": None,
    "prompt_hash": None,
    "prompt_slots": None,
    "scorer_version": None,
    "citation_judge_model": None,
    "coverage_judge_model": None,
    "citation_judge_independent": None,
}


def raw_provenance_fields(prov: Dict[str, Any]) -> Dict[str, Any]:
    """从整轮 provenance 里挑出要写进**单条 raw** 的字段（缺键如实留空，不编）。"""
    return {k: prov.get(k) for k in RAW_PROVENANCE_KEYS}


def provenance_from_raw_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """从一条 raw 记录里读回跑批时刻的 provenance（缺字段走 `PROVENANCE_DEFAULTS`）。

    抽到这里是为了让 `run._provenance_from_raw` 与将来的离线回填脚本共用同一份读法
    —— 两处读法不同 ⇔ 同一个字段有两个真相。
    """
    return {k: raw.get(k, PROVENANCE_DEFAULTS[k]) for k in RAW_PROVENANCE_KEYS}


def run_provenance(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """一次算好整轮 run 的 provenance（git 子进程只跑一次，不按条目重复调）。

    返回 `{git_*, config_snapshot, prompt_hash, prompt_slots, scorer_version,
    citation_judge_model, coverage_judge_model, citation_judge_independent}`，
    可直接（经 `raw_provenance_fields`）并入 raw 记录。
    """
    rev = code_revision(repo_root)
    rev["config_snapshot"] = config_snapshot()
    rev["prompt_slots"] = prompt_slots()
    rev["prompt_hash"] = prompt_hash(rev["prompt_slots"])
    rev["scorer_version"] = SCORER_VERSION
    rev.update(judge_provenance())
    return rev
