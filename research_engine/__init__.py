"""DeepResearch 核心引擎。

W8 Arm 3 第三刀（§5.3.3）：**运行时版本闸**。
为什么必须有它：本仓库**无 `[build-system]`** ⇒ `pyproject.toml` 里的 `requires-python`
只是声明，pip 根本不会执行该字段；若不在此显式拦截，版本不对的用户会先撞上
`pydantic_core` 之类的二进制导入错误，报错信息与「Python 版本不对」毫无关联、极难定位。
支持区间与 CI matrix 见 `docs/requirements/8-fault-transparency-and-reproducibility.md` §5.3。
"""

from __future__ import annotations

import sys

# 支持区间（闭区间）：下界 3.11 对齐 CI；上界 3.13 的依据见 §5.3.2
# （`.deps` 里 168 个 cp313 轮子的教训 + 既有先例 `openai>=1.50,<1.93` / `langfuse>=4.15,<5`）
PY_MIN = (3, 11)
PY_MAX = (3, 13)


def assert_supported_python(version_info: object = None) -> None:
    """校验解释器版本在支持区间内，否则抛 `RuntimeError`（信息里带上解释器实际路径）。

    抽成纯函数是为了让单测能直接喂边界版本，而不必真的换解释器。
    `version_info` 只要求支持下标与前三个属性（`sys.version_info` 即满足）。
    """
    vi = sys.version_info if version_info is None else version_info
    current = (vi[0], vi[1])  # type: ignore[index]
    if PY_MIN <= current <= PY_MAX:
        return
    raise RuntimeError(
        f"DeepResearch 需要 Python {PY_MIN[0]}.{PY_MIN[1]} ~ {PY_MAX[0]}.{PY_MAX[1]}，"
        f"当前解释器为 {vi[0]}.{vi[1]}.{vi[2]}（{sys.executable}）。"  # type: ignore[index]
        "请切换到受支持的版本后重试 —— "
        "见 README「环境要求」与 docs/requirements/8-fault-transparency-and-reproducibility.md §5.3。"
    )


# 真正的闸门：包被 import 的第一时间执行（早于任何子模块）
assert_supported_python()
