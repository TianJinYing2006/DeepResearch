"""Arm 7「轨道 2」只读台账扫描器。

⚠️ 铁律：本脚本**只读**。它不删除、不移动、不重命名、不修改任何产物（唯一的写入是台账文件本身）。
原因是 `tools/w7_backfill_hallucination.py:59-66` 与 `tools/w7_backfill_insufficient.py` 直接读
`run_dir/raw/*.raw.json` —— 这些 raw 是 W7「零成本可复算」资产的**唯一证据源**，
治理若误伤等于销毁证据。任何清理动作必须在本台账经人工 review 之后**单独拍板**。

输出列：目录名 / 体积 / 最后修改时间 / 跟踪状态 / 是否被 docs 引用 / 类别。

用法：
    python tools/w8_artifact_ledger.py                    # 写 docs/eval-artifact-ledger.md
    python tools/w8_artifact_ledger.py --stdout-only      # 只看汇总、不写文件
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_REL = "research_engine/eval/results"
DEFAULT_OUT = "docs/eval-artifact-ledger.md"

# 「哪些文档算自动枚举」由白名单裁判单点定义，台账这边复用同一个集合，避免两处口径打架。
_WHITELIST_TOOL = REPO_ROOT / "tools" / "check_results_whitelist.py"


def _load_auto_enumerated_docs() -> set[str]:
    spec = importlib.util.spec_from_file_location("check_results_whitelist", _WHITELIST_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {_WHITELIST_TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.AUTO_ENUMERATED_DOCS)


AUTO_ENUMERATED_DOCS = _load_auto_enumerated_docs()

# 「看板 / 需求文档」类引用：这两份文档**既**记录实质验收证据（如 §10.4 用某个 run 验收 DoD），
# **也**记录处置动作（「把 X 删掉」「X 零引用」）⇒ 光看「被它引用」分不清是哪种。
# 因此这一类**只作为软标记**（提示人工确认），**绝不是「不算证据」**：
# 反过来误判才是真风险 —— before 基线的三个 run 正是靠 §10.4/§10.5.8 的验收记录存续的。
# 实测来源（2026-09-18）：事故复盘把 `run_20260910_033127` / `run_20260911_004201` 写进文档后，
# 台账「有手写引用」条目数从 18 跳到 20 —— 见 render() 里的「登记的反转」注。
GOVERNANCE_DOCS = frozenset(
    {
        "docs/project-status.md",
        "docs/requirements/8-fault-transparency-and-reproducibility.md",
    }
)

# 扫描引用时要跳过的目录：产物自己当然会「引用」自己，排除掉才是有效证据
SCAN_SKIP_DIRS = {".git", ".workbuddy", "results", "__pycache__", "node_modules", ".venv", ".deps"}
SCAN_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".txt", ".json"}
MAX_TEXT_BYTES = 4 * 1024 * 1024

CATEGORY_RULES = [
    ("_SUPERSEDED", "被取代"),
    ("_DISCARDED", "已废弃"),
    ("_tmp_", "临时"),
    ("_bak_", "备份快照"),
]


def _human(size: int) -> str:
    step = 1024.0
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= step
    raise AssertionError("unreachable")


def dir_size_and_mtime(path: Path) -> tuple[int, float]:
    if path.is_file():
        st = path.stat()
        return st.st_size, st.st_mtime
    total = 0
    latest = 0.0
    for child in path.rglob("*"):
        if child.is_file():
            st = child.stat()
            total += st.st_size
            latest = max(latest, st.st_mtime)
    return total, latest


def tracked_paths(repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", RESULTS_REL],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p.strip() for p in out.splitlines() if p.strip()]


def _readable_text(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size <= MAX_TEXT_BYTES
    except OSError:
        return False


def scan_references(repo_root: Path, names: set[str]) -> dict[str, list[str]]:
    """全仓扫描（排除产物目录自身），返回 {条目名: [引用文件...]}。"""
    hits: dict[str, list[str]] = {name: [] for name in names}

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
        for filename in filenames:
            file = Path(dirpath) / filename
            if file.suffix.lower() not in SCAN_SUFFIXES or not _readable_text(file):
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for name in names:
                if name in text and len(hits[name]) < 5:
                    rel = file.relative_to(repo_root).as_posix()
                    if rel not in hits[name]:
                        hits[name].append(rel)
    return hits


def classify(name: str) -> str:
    for marker, label in CATEGORY_RULES:
        if marker in name:
            return label
    return "未知"


def build_rows(repo_root: Path, extra_auto: frozenset[str] = frozenset()) -> tuple[list[dict], dict]:
    """扫描产物目录。

    `extra_auto`：额外视为「自动枚举」的相对路径 —— 最关键的是**台账输出自己**。
    台账必然列出所有 run 名，若不排除它，回扫时每个 run 都会被判成「有手写引用」，
    这是本次实测踩到的自指污染（2026-09-18）。
    """
    auto_docs = AUTO_ENUMERATED_DOCS | set(extra_auto)
    results_dir = repo_root / RESULTS_REL
    entries = sorted(results_dir.iterdir(), key=lambda p: p.name)
    names = {p.name for p in entries}

    tracked = tracked_paths(repo_root)
    tracked_top = {p[len(RESULTS_REL) :].strip("/").split("/", 1)[0] for p in tracked}
    refs = scan_references(repo_root, names)

    rows = []
    for entry in entries:
        size, mtime = dir_size_and_mtime(entry)
        cited = refs.get(entry.name, [])
        manual = [p for p in cited if p not in auto_docs]
        rows.append(
            {
                "name": entry.name,
                "size": size,
                "size_human": _human(size),
                "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "-",
                "tracked": entry.name in tracked_top,
                "category": classify(entry.name),
                "refs": cited,
                "manual_refs": manual,
                "only_auto": not manual and bool(cited),
                "governance_only": bool(manual) and all(p in GOVERNANCE_DOCS for p in manual),
            }
        )
    rows.sort(key=lambda r: r["size"], reverse=True)

    manual_named = [r for r in rows if r["manual_refs"]]
    effective = [r for r in manual_named if not r["governance_only"]]
    gov_only = [r for r in manual_named if r["governance_only"]]
    total = sum(r["size"] for r in rows)
    summary = {
        "total": total,
        "total_human": _human(total),
        "tracked": sum(r["size"] for r in rows if r["tracked"]),
        "untracked": sum(r["size"] for r in rows if not r["tracked"]),
        "count": len(rows),
        "manual_named": len(manual_named),
        "manual_named_size": sum(r["size"] for r in manual_named),
        "manual_effective": len(effective),
        "manual_effective_size": sum(r["size"] for r in effective),
        "governance_only": len(gov_only),
        "governance_only_size": sum(r["size"] for r in gov_only),
    }
    return rows, summary


def render(rows: list[dict], summary: dict) -> str:
    lines = [
        "# 评测产物台账（Arm 7 轨道 2 · 只读出账）",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　生成器：`tools/w8_artifact_ledger.py`",
        "> **本台账由只读扫描生成，未删除/移动/重命名任何产物。** 磁盘清理须在本台账 review 之后单独拍板 —— "
        "原因是 `tools/w7_backfill_*.py` 直接读 `run_dir/raw/*.raw.json`，那是 W7 零成本可复算的唯一证据源。",
        "",
        "| 目录 / 文件 | 体积 | 最后修改 | 入库 | 类别 | 引用来源（手写文档 / 代码，可作入库证据） | 仅出现在自动表痕 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        refs = "<br>".join(f"`{p}`" for p in r["manual_refs"]) or "—"
        if r["governance_only"]:
            refs += "<br>⚠️ **（引用仅来自看板/需求文档 —— 需人工确认是否只是处置记录；不可据此删除）**"
        lines.append(
            f"| `{r['name']}` | {r['size_human']} | {r['mtime']} | "
            f"{'✅' if r['tracked'] else '—'} | {r['category']} | {refs} | "
            f"{'⚠️ 是' if r['only_auto'] else '—'} |"
        )

    named_namespace = "、".join(sorted(AUTO_ENUMERATED_DOCS))
    lines += [
        "",
        "## 汇总",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| 顶层条目数 | {summary['count']} |",
        f"| `results/` 总占用 | **{summary['total_human']}** |",
        f"| 其中已入库（受白名单约束） | {_human(summary['tracked'])} |",
        f"| 其中未入库（本地仅作追溯） | **{_human(summary['untracked'])}** |",
        f"| 有【手写引用】的条目 | {summary['manual_named']} 个 / {_human(summary['manual_named_size'])} |",
        f"| └─ 其中引用**含**结论文档 / 代码 | {summary['manual_effective']} 个 / {_human(summary['manual_effective_size'])} |",
        f"| └─ 其中引用**仅来自**看板 / 需求文档（⚠️ **需人工确认**，不可据此删除） | {summary['governance_only']} 个 / {_human(summary['governance_only_size'])} |",
        "",
        f"> ⚠️ **引用分两档，别混为一谈**：`{named_namespace}` 由脚本自动写出、会把几乎所有 run 无差别地"
        "列进表格（实测 `docs/eval-report.md` 含 74 个 run id，而手写的 `docs/eval-w7-conclusion.md` 只提 4 个）"
        " ⇒ **只出现在自动表里 = 没有被人挑选过**，不能充当入库证据。表中「⚠️ 是」的正是这类。",
        "",
        "> 类别判定：`*_SUPERSEDED` = 被取代、`*_DISCARDED` = 已废弃、`_tmp_*` = 临时、`_bak_*` = 备份快照；"
        "其余为「未知」，需人工定性后再决定是否归档。",
        "",
        "> ⚠️ **注意「登记的反转」：写下来 ≠ 原本被引用**（2026-09-18 实测）。本表统计的是「**此刻**哪些文档提到了它」，"
        "而治理过程本身会往文档里写它的名字 —— `history_bak_v10.json` 原本零引用，正是因为被写入了"
        "`docs/project-status.md` 与 §5.7.1 的处置说明，**在第二次生成时变成「有手写引用」**（手写引用数 17 → 18）。"
        "⇒ 引用数**只增不减**是有偏的：review 时应以「引用是否来自**结论文档 / 代码**」为准，"
        "写进「处置记录」「台账」「变更日志」的一律不算。这是 **`git rm --cached` 与评审相反的方向**："
        "前者担心误删证据，这里担心的是「描述过就被当成有证据」。",
        "",
        "⚠️ **下一刀（尚未执行，需单独拍板）**：「入库 = —」且「仅出现在自动表痕 = ⚠️ 是」的条目才是真正的归档候选；"
        "而且任何删除动作都必须先确认该 run 不在 `tools/w7_backfill_*.py` 的输入集合里 —— "
        "那两个脚本直接读 `run_dir/raw/*.raw.json`，那是 W7 零成本可复算的唯一证据源。",
        "",
        "> 🚨 **「最后修改」列自 2026-09-18 起已失去取证价值**：一次**跨越 Arm 7 边界**的 `git checkout`（`git rm --cached` 把路径移出索引后，"
        "跨边界切换会让 git 认为这些路径属于旧版本）把**产物**连同未跟踪的 raw 一起从磁盘删除，`results/` 一度从 143.6 MB 掉到 31 MB。"
        "恢复动作（从回收站按 `$I` 元数据定向还原 + 物理备份回拷）把**全部**文件的 mtime 刷成了恢复时刻 —— 故本列现在只反映"
        "「最后一次触碰」，不再反映「产物何时产生」。**另有 5 个文件被 git 硬删除且从未进过对象库，永久丢失**"
        "（`run_20260910_033127/raw/q_014.raw.json`、`run_20260911_004201/eval/q_016|q_018|q_019|q_020.eval.json`）。"
        "事故详情见 `docs/requirements/8-fault-transparency-and-reproducibility.md` §5.7.2 与 `docs/project-status.md` 缺陷 D5。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Arm 7 轨道 2：只读扫描评测产物并出账")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"台账输出路径（默认 {DEFAULT_OUT}）")
    parser.add_argument("--stdout-only", action="store_true", help="只打印汇总，不写台账文件")
    args = parser.parse_args()

    rows, summary = build_rows(REPO_ROOT, extra_auto=frozenset({args.out}))
    if not args.stdout_only:
        out_path = REPO_ROOT / args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render(rows, summary), encoding="utf-8")
        print(f"📒 台账已写入：{args.out}")

    print(f"顶层条目 {summary['count']} 个 / 总占用 {summary['total_human']}")
    print(f"  已入库 {_human(summary['tracked'])} / 未入库 {_human(summary['untracked'])}")
    print(f"  有手写引用 {summary['manual_named']} 个 / {_human(summary['manual_named_size'])}")
    for r in rows[:10]:
        flag = "✅" if r["tracked"] else "  "
        proof = "手写" if r["manual_refs"] else ("仅自动表痕" if r["only_auto"] else "无")
        print(f"  {flag} {r['name']:<34} {r['size_human']:>10}  {r['category']:<6} 引用={proof}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
