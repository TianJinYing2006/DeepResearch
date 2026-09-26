"""RAG 检索作用域（P5 多租户隔离）。

**为什么用 contextvars 而不是改 `ResearchState` / 图签名**：
W8 冻结纪律要求不动核心链路判定口径。作用域是「这次检索以谁的身份看知识库」，
与图语义无关；用上下文变量传递可让 `researcher` / `graph` 零改动、`eval` 与 CLI
不设置即保持历史行为（只命中无主文档）。

规则（首发，需求 10 §5.7）：
- **owner 作用域**（`user_id` 非空）：只命中 `payload.user_id == user_id` 且
  `visibility == 'private'` 的块；
- **匿名作用域**（默认，本地 / CLI / eval）：只命中**无主**块（`user_id` 缺失或空），
  因此历史数据可见、隔离后新增的他人私有块不可见；
- `tenant_id` 作为可选二级维度做等值匹配；
- `shared` / `public` 可见性仅预留字段，**首发不开放检索**（`ALLOWED_VISIBILITIES`）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional

#: 首发允许检索的可见性集合（放宽必须走需求 10 §5.7 的评审）
ALLOWED_VISIBILITIES: tuple[str, ...] = ("private",)

#: 无主块：payload 没有 user_id（P5 之前摄入的历史数据 / 匿名本地摄入）
_OWNERLESS = (None, "")


@dataclass(frozen=True)
class RagScope:
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None

    @property
    def is_owner_scope(self) -> bool:
        return self.user_id not in _OWNERLESS


_DEFAULT_SCOPE = RagScope()
_current: ContextVar[RagScope] = ContextVar("rag_scope", default=_DEFAULT_SCOPE)


def current_scope() -> RagScope:
    return _current.get()


def set_scope(user_id: Optional[str] = None, tenant_id: Optional[str] = None) -> None:
    """直接设置当前作用域（不返回 token）。

    专供「线程/进程按 run 串行执行」的执行器（RunManager 工作线程、Queue Worker）：
    每次开跑前调用覆盖即可；contextvars 不跨线程，线程结束作用域自然消失。
    库函数 / 测试请优先用 :func:`use_rag_scope`。
    """
    _current.set(RagScope(user_id=user_id, tenant_id=tenant_id))


@contextmanager
def use_rag_scope(user_id: Optional[str] = None,
                  tenant_id: Optional[str] = None) -> Iterator[RagScope]:
    """在 with 块内设定检索作用域（线程/任务隔离由 contextvars 保证）。"""
    token = _current.set(RagScope(user_id=user_id, tenant_id=tenant_id))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def _visibility_ok(visibility) -> bool:
    # 历史数据（P5 之前摄入）没有 visibility 字段，视为 private（向后兼容）
    return visibility in _OWNERLESS or visibility in ALLOWED_VISIBILITIES


def payload_matches(payload: dict, scope: RagScope) -> bool:
    """命中判定唯一实现：Qdrant 服务端过滤与 Python 后置过滤共用同一语义。"""
    if not _visibility_ok(payload.get("visibility")):
        return False
    if payload.get("tenant_id") != scope.tenant_id:
        return False
    if scope.is_owner_scope:
        return payload.get("user_id") == scope.user_id
    return payload.get("user_id") in _OWNERLESS
