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


def run_provenance(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """一次算好整轮 run 的 provenance（git 子进程只跑一次，不按条目重复调）。

    返回 `{git_commit, git_dirty, git_diff_hash, config_snapshot}`，可直接并入 raw 记录。
    """
    rev = code_revision(repo_root)
    rev["config_snapshot"] = config_snapshot()
    return rev
