# -*- coding: utf-8 -*-
r"""ADR-0007 修复验证：Qdrant 点 ID 跨进程稳定性。

离线测试，不调用真实 LLM / Qdrant / Embedding。
运行：python tests/test_stable_point_id.py

背景：旧 ingest.py 用 hash(f"{doc_id}:{i}") % (2**63) 生成 Qdrant 点 ID。
Python 字符串的 hash() 每个进程随机加盐（PYTHONHASHSEED），同一输入
在不同进程返回不同值。后果：同一文档被两次摄取时产生**不同的 ID**，
Qdrant upsert 按 ID 覆盖 → 不同 ID 不覆盖而是新增 → 重复点堆积，
破坏摄取的幂等性。

本测试通过 subprocess 启动新进程，直接验证"跨进程稳定"这个核心断言，
并对照复现内置 hash() 的不稳定行为。
"""
from __future__ import annotations

import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def run_in_subprocess(code: str, hash_seed: str = "random") -> str:
    """在新 Python 进程里执行 code，返回 stdout。可通过 PYTHONHASHSEED 控制哈希种子。"""
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    out = subprocess.check_output(
        [sys.executable, "-c", code],
        env=env,
        cwd=PROJECT_ROOT,
    )
    return out.decode().strip()


STABLE_ID_CODE = (
    "import sys; sys.path.insert(0, r'{root}'); "
    "from research_engine.rag.ids import stable_id; "
    "print(stable_id('doc1', 0))"
).format(root=PROJECT_ROOT)


def main():
    from research_engine.rag.ids import stable_id as _stable_id

    print("========== 场景 1：_stable_id 跨进程稳定（修复核心） ==========")
    main_proc = _stable_id("doc1", 0)
    sub_seed_a = int(run_in_subprocess(STABLE_ID_CODE, hash_seed="0"))
    sub_seed_b = int(run_in_subprocess(STABLE_ID_CODE, hash_seed="12345"))
    print(f"  主进程            : {main_proc}")
    print(f"  子进程 SEED=0     : {sub_seed_a}")
    print(f"  子进程 SEED=12345 : {sub_seed_b}")
    assert main_proc == sub_seed_a == sub_seed_b, "stable id 跨进程/跨 SEED 必须一致"
    print("  ✅ 三个进程返回完全相同的 ID（hashlib 不受 PYTHONHASHSEED 影响）")

    print("\n========== 场景 2：对照复现旧 bug（内置 hash 跨进程不稳定） ==========")
    hash_code = "print(hash('doc1:0') % (2**63))"
    old_a = int(run_in_subprocess(hash_code, hash_seed="1"))
    old_b = int(run_in_subprocess(hash_code, hash_seed="2"))
    print(f"  hash() SEED=1 : {old_a}")
    print(f"  hash() SEED=2 : {old_b}")
    if old_a != old_b:
        print("  ✅ 复现：内置 hash() 跨进程不同 → 旧逻辑会产生不同 ID → 重复点堆积")
    else:
        print("  ⚠️ 本轮两次恰好相同（小概率），多跑几次大概率会不同；本质问题已由场景 1 反证")

    print("\n========== 场景 3：不同输入产生不同 ID（无退化） ==========")
    ids = {(_stable_id(f"doc{i}", j)) for i in range(5) for j in range(5)}
    assert len(ids) == 25, f"25 个不同输入应得 25 个不同 ID，实际 {len(ids)}"
    print(f"  25 组 (doc_id, chunk_index) → {len(ids)} 个不同 ID ✅ 无碰撞退化")

    print("\n========== 场景 4：ID 在 Qdrant 合法范围内 ==========")
    big = _stable_id("a" * 1000, 9999)
    assert 0 < big < (1 << 63), f"ID 必须落在 (0, 2^63)，实际 {big}"
    print(f"  ID={big} ∈ (0, 2^63) ✅ Qdrant 接受为正整数 ID")

    print("\n========== 场景 5：幂等性推断 ==========")
    a1 = _stable_id("report.pdf", 3)
    a2 = _stable_id("report.pdf", 3)
    assert a1 == a2
    print(f"  同一 (doc_id, i) 两次调用 → 相同 ID ({a1})")
    print("  → Qdrant upsert 按 ID 覆盖 → 同文档重摄取不会新增重复点 ✅")

    print("\n========== 全部断言通过：点 ID 跨进程稳定，摄取幂等 ==========")


if __name__ == "__main__":
    main()
