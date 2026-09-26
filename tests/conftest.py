# -*- coding: utf-8 -*-
"""pytest 全局夹具（W3，grill Q5 三重隔离①）。

强制禁用 Langfuse 观测：测试矩阵 100% 零外发、零报错，
不依赖任何本地 .env / 环境变量配置（即使本地恰好配了 LANGFUSE_*）。

W8 追加（2026-09-16）：在此统一把仓库根加进 `sys.path`。
此前没有任何地方做这件事，全靠 `test_bm25_chinese.py` / `test_citation_alignment.py`
在模块级 `sys.path.insert` 的**副作用**兜着 —— 而 pytest 是按文件名字母序导入测试模块，
所以一旦新增字母序更靠前的测试文件（本次 `test_arm1_run_status.py`），
它就会在副作用生效前 import `research_engine` 而 `ModuleNotFoundError`；
同理由也让 `pytest tests/<单个文件>` 一直不可用。此处一次修掉，不再依赖文件名字母序。
"""
import os
import sys
from pathlib import Path

# 仓库根（tests/ 的上一级）——使 `import research_engine` 在任何收集顺序下都成立
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Q5 三重隔离①：强制（覆盖式，非 setdefault —— .env 里已有值也必须压掉）
os.environ["LANGFUSE_ENABLED"] = "false"

# P4-B：单测进程内关掉登录/提交限流（默认 10/分钟会跨用例累计，导致假失败）；
# 限流本身由 tests/test_ratelimit.py 与 test_quotas.py 用显式限流器覆盖。
os.environ.setdefault("DR_LOGIN_RATE_PER_MINUTE", "0")
os.environ.setdefault("DR_SUBMIT_RATE_PER_MINUTE", "0")