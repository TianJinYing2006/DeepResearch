# -*- coding: utf-8 -*-
"""pytest 全局夹具（W3，grill Q5 三重隔离①）。

强制禁用 Langfuse 观测：测试矩阵 100% 零外发、零报错，
不依赖任何本地 .env / 环境变量配置（即使本地恰好配了 LANGFUSE_*）。
"""
import os

# Q5 三重隔离①：强制（覆盖式，非 setdefault —— .env 里已有值也必须压掉）
os.environ["LANGFUSE_ENABLED"] = "false"