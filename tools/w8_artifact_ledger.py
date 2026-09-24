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

# 扫描引用时要跳过的目录。
# ⚠️ 2026-09-18 修正（本工具的核心判据 bug）：原先把 `results` 一并跳过，理由是「产物自己当然会
# 引用自己」—— 那只对**自指**成立。`w7_experiment_*/manifest.json` 里的 `runs[].run_dir` 是
# **包含 / 溯源登记**，不是自引用；整目录跳过导致 58 个 run / 110.0 MB 被误判成「无引用」
# （详见 docs/eval-artifact-ledger.md 的第四档说明）。现在改为「走进去扫、逐条排除自指」，
# 见 `_results_owner()`。
# `.workbuddy` 仍**必须**跳过：它装着事故物理备份（`_arm7_results_backup/`）与逐日流水，
# 后者会把 run 名当「处置记录」写进去 —— 计入即重现「登记的反转」（写了说明 ≠ 原本被引用）。
SCAN_SKIP_DIRS = {".git", ".workbuddy", "__pycache__", "node_modules", ".venv", ".deps"}
SCAN_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".txt", ".json"}
MAX_TEXT_BYTES = 4 * 1024 * 1024


def _results_owner(rel_posix: str) -> str | None:
    """仓库相对路径若位于 `results/` 内，返回它所属的**顶层条目名**，否则 `None`。

    用途有二：① 判定「结构登记」这一档引用（第四档，见 D-13）；② 排除自指。
    """
    prefix = RESULTS_REL + "/"
    if not rel_posix.startswith(prefix):
        return None
    return rel_posix[len(prefix) :].split("/", 1)[0]


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
    """全仓扫描，返回 {条目名: [引用文件...]}。

    `results/` **也扫**（见 SCAN_SKIP_DIRS 的说明），但逐条排除自指：`results/<X>/...` 里提到
    `<X>` 不算证据。反过来，`results/<X>/manifest.json` 提到 `<Y>` 是**结构登记**，算第四档证据。
    """
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
            rel = file.relative_to(repo_root).as_posix()
            owner = _results_owner(rel)
            for name in names:
                if owner == name:
                    continue  # 自指：产物写自己的名字不构成引用证据
                if name in text and len(hits[name]) < 5:
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
        # 自动枚举文档（如 docs/eval-report.md）会把几乎所有 run 无差别列一遍 ⇒ 不算证据
        outside = [p for p in cited if p not in auto_docs]
        # 第四档「结构登记」：来自 results/ 内部（manifest 的 runs[].run_dir 等）—— 机器登记，非处置记录
        structural = [p for p in outside if _results_owner(p) is not None]
        # 手写引用：来自结论文档 / 代码等 results/ 之外的文件
        prose = [p for p in outside if _results_owner(p) is None]
        rows.append(
            {
                "name": entry.name,
                "size": size,
                "size_human": _human(size),
                "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "-",
                "tracked": entry.name in tracked_top,
                "category": classify(entry.name),
                "refs": cited,
                "prose_refs": prose,
                "structural_refs": structural,
                "evidence_refs": prose + structural,
                "only_auto": not (prose or structural) and bool(cited),
                # ⚠️ 软标记只看**手写**引用：不得因「结构登记里有它」就放过人工确认。
                # 反例（2026-09-18 实测）：before 基线三个 run 的唯一手写提法在 §10.4 验收记录里，
                # 但 `results/history.json` 也登记了它们的 run_id —— 若让结构登记压掉软标记，
                # 台账就不再提示「这条只有处置记录撑着」。
                "governance_only": bool(prose) and all(p in GOVERNANCE_DOCS for p in prose),
            }
        )
    rows.sort(key=lambda r: r["size"], reverse=True)

    prose_named = [r for r in rows if r["prose_refs"]]
    prose_effective = [r for r in prose_named if not r["governance_only"]]
    gov_only = [r for r in prose_named if r["governance_only"]]
    structural_named = [r for r in rows if r["structural_refs"]]
    structural_only = [r for r in structural_named if not r["prose_refs"]]
    evidence_backed = [r for r in rows if r["evidence_refs"]]
    no_evidence = [r for r in rows if not r["evidence_refs"]]
    total = sum(r["size"] for r in rows)
    summary = {
        "total": total,
        "total_human": _human(total),
        "tracked": sum(r["size"] for r in rows if r["tracked"]),
        "untracked": sum(r["size"] for r in rows if not r["tracked"]),
        "count": len(rows),
        "prose_named": len(prose_named),
        "prose_named_size": sum(r["size"] for r in prose_named),
        "prose_effective": len(prose_effective),
        "prose_effective_size": sum(r["size"] for r in prose_effective),
        "governance_only": len(gov_only),
        "governance_only_size": sum(r["size"] for r in gov_only),
        "structural_named": len(structural_named),
        "structural_named_size": sum(r["size"] for r in structural_named),
        "structural_only": len(structural_only),
        "structural_only_size": sum(r["size"] for r in structural_only),
        "evidence_backed": len(evidence_backed),
        "evidence_backed_size": sum(r["size"] for r in evidence_backed),
        "no_evidence": len(no_evidence),
        "no_evidence_size": sum(r["size"] for r in no_evidence),
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
        "| 目录 / 文件 | 体积 | 最后修改 | 入库 | 类别 | 手写引用（结论文档 / 代码） | 结构登记（`results/` 内，如 manifest 的 `runs[].run_dir`） | 仅出现在自动表痕 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        prose = "<br>".join(f"`{p}`" for p in r["prose_refs"]) or "—"
        if r["governance_only"]:
            prose += "<br>⚠️ **（引用仅来自看板/需求文档 —— 需人工确认是否只是处置记录；不可据此删除）**"
        structural = "<br>".join(f"`{p}`" for p in r["structural_refs"]) or "—"
        lines.append(
            f"| `{r['name']}` | {r['size_human']} | {r['mtime']} | "
            f"{'✅' if r['tracked'] else '—'} | {r['category']} | {prose} | {structural} | "
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
        f"| 有【证据】的条目（手写引用 **或** 结构登记） | {summary['evidence_backed']} 个 / {_human(summary['evidence_backed_size'])} |",
        f"| └─ 有【手写引用】的条目 | {summary['prose_named']} 个 / {_human(summary['prose_named_size'])} |",
        f"| &nbsp;&nbsp;&nbsp;&nbsp;└─ 其中引用**含**结论文档 / 代码 | {summary['prose_effective']} 个 / {_human(summary['prose_effective_size'])} |",
        f"| &nbsp;&nbsp;&nbsp;&nbsp;└─ 其中引用**仅来自**看板 / 需求文档（⚠️ **需人工确认**，不可据此删除） | {summary['governance_only']} 个 / {_human(summary['governance_only_size'])} |",
        f"| └─ 有【结构登记】的条目（第四档，登记在 `results/` 内部） | {summary['structural_named']} 个 / {_human(summary['structural_named_size'])} |",
        f"| &nbsp;&nbsp;&nbsp;&nbsp;└─ 其中**没有**任何手写引用（结构登记独立支撑） | {summary['structural_only']} 个 / {_human(summary['structural_only_size'])} |",
        f"| **无任何证据**（手写与结构登记都没有） | **{summary['no_evidence']} 个 / {_human(summary['no_evidence_size'])}** |",
        "",
        f"> ⚠️ **自动枚举不算证据**：`{named_namespace}` 由脚本自动写出、会把几乎所有 run 无差别地"
        "列进表格（实测 `docs/eval-report.md` 含 74 个 run id，而手写的 `docs/eval-w7-conclusion.md` 只提 4 个）"
        " ⇒ **只出现在自动表里 = 没有被人挑选过**，不能充当入库证据。表中「⚠️ 是」的正是这类。",
        "",
        "> 🧩 **第四档「结构登记」（2026-09-18 新增）**：登记写在 `results/` **内部**的机器可读文件里，"
        "典型是 `w7_experiment_*/manifest.json` 的 `runs[].run_dir` —— 它逐条记录了「这次实验实际跑出了哪些 run」，"
        "属**溯源/包含**关系，与「处置记录」无关，**与手写引用等效**。另有一类同样查不到的："
        "`tools/measure_paired_rho.py --runs` 是 **argv 传参且从不落盘**，那些 run 名在仓库里天生无迹可循"
        " ⇒ **不得因为「扫不到引用」就判它可删**。",
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
        "⚠️ **下一刀（尚未执行，需单独拍板）**：只有「手写引用 = —」**且**「结构登记 = —」**且**"
        "「仅出现在自动表痕 = ⚠️ 是」三条同时成立的条目才是归档候选；"
        "而且任何删除动作都必须先确认该 run 不在 `tools/w7_backfill_*.py` 的输入集合里 —— "
        "那两个脚本直接读 `run_dir/raw/*.raw.json`，那是 W7 零成本可复算的唯一证据源。",
        "",
        "> ✅ **2026-09-18 主理人 review 已结案：维持现状，不删除任何产物。** 15 条待决条目已逐条定性，"
        "记录见 `docs/project-status.md` 的决策记录。结案理由：真正「可考虑清」的量级约 **2.0 MB**"
        "（2 个零字节空目录 + `_tmp_backup_not_committed` + `*_DISCARDED`），而 `results/` 总计 143.5 MB、"
        "D 盘可用 29 GB ⇒ **清理收益为零**，却要再承担一次 D5 那类「跨边界删除产物」的风险。"
        "另：10 个 `w7_experiment_*` 是 W7 实验记录**本体**（arm 定义 + notes），即使只有几 KB 也必须保留；"
        "`run_20260916_*` 三条 before 基线的 run 是对照臂，由 `history.json` 与 §10.4 验收记录双重支撑。",
        "",
        "> 🔧 **2026-09-18 判据修正（本表口径变化的原因）**：`SCAN_SKIP_DIRS` 原先把 `results` 整个跳过，"
        "理由是「产物自己会引用自己」—— 那只对**自指**成立。修正为「走进去扫、逐条排除自指」后，"
        "原先 58 个被判「无引用」的 run 恢复为「有结构登记」（合计 110.0 MB）。"
        "**修正前若按原口径清理，会销毁 W7 配对实验的证据基座**（ρ=0.551 / MDE=12.38pp 的逐题输入）。",
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
        print(f"[WRITE] 台账已写入：{args.out}")

    print(f"顶层条目 {summary['count']} 个 / 总占用 {summary['total_human']}")
    print(f"  已入库 {_human(summary['tracked'])} / 未入库 {_human(summary['untracked'])}")
    print(f"  有手写引用 {summary['prose_named']} 个 / {_human(summary['prose_named_size'])}")
    print(f"  有结构登记 {summary['structural_named']} 个 / {_human(summary['structural_named_size'])}")
    print(f"  无任何证据 {summary['no_evidence']} 个 / {_human(summary['no_evidence_size'])}")
    for r in rows[:10]:
        flag = "[TRACKED]" if r["tracked"] else "[LOCAL]  "
        if r["prose_refs"]:
            proof = "手写"
        elif r["structural_refs"]:
            proof = "结构登记"
        elif r["only_auto"]:
            proof = "仅自动表痕"
        else:
            proof = "无"
        print(f"  {flag} {r['name']:<34} {r['size_human']:>10}  {r['category']:<6} 引用={proof}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
