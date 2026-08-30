# -*- coding: utf-8 -*-
"""Qdrant 点 ID 生成。

独立模块，避免被 ingest.py 的重依赖（qdrant_client/openai）牵连，
便于单元测试在不装这些依赖时也能直接验证 ID 稳定性（ADR-0007）。
"""
from __future__ import annotations

import hashlib


def stable_id(doc_id: str, chunk_index: int) -> int:
    """基于 (doc_id, chunk_index) 生成稳定的 Qdrant 点 ID。

    背景：Python 内置 hash() 对字符串每个进程随机加盐（PYTHONHASHSEED），
    同一输入在不同进程里返回不同值。旧代码用 hash() 生成点 ID，导致同一
    文档被两次摄取时产生**不同的 ID**——Qdrant 的 upsert 按 ID 覆盖，
    不同 ID 不会覆盖而是新增重复点，破坏摄取的幂等性（见 ADR-0007）。

    本函数用 SHA1 摘要前 8 字节转无符号整数，跨进程稳定，且 mask 到
    Qdrant 支持的 63-bit 正整数范围。

    返回值保证：0 < id < 2**63。
    """
    raw = f"{doc_id}:{chunk_index}".encode("utf-8")
    digest = hashlib.sha1(raw).digest()  # 20 bytes
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
