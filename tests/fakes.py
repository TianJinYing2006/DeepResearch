"""测试用内存替身：FakeStore（RunStore 语义子集）与 TinyGraph（零 LLM 假图）。

供 `test_web_persistence.py`（P2-C 接线）与 `test_worker.py`（P3-A Worker）共用；
真实 PostgreSQL / Redis 的集成测试见 `test_run_store.py` 与 `test_worker_integration.py`。
"""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

from research_engine.state import ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED, RunStep
from web.backend.store import QuotaExceeded

ACTIVE = ("CREATED", "QUEUED", "RUNNING", "CANCEL_REQUESTED")
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST")


class TinyGraph:
    """最小假 graph：可配节点数 / 延迟 / 报告正文 / 每步 token（零 LLM）。"""

    def __init__(self, steps: int = 3, delay: float = 0.01, report: str = "",
                 token_per_step: int = 0):
        self.steps = steps
        self.delay = delay
        self.report = report
        self.token_per_step = token_per_step

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        state = ResearchState(topic=topic)
        for i in range(self.steps):
            time.sleep(self.delay)
            state.token_used += self.token_per_step
            state.progress.append({"stage": f"n{i}", "msg": "m"})
            yield RunStep(index=i, node=f"n{i}", state=state, duration_ms=1)
            if should_cancel is not None and should_cancel():
                yield RunStep(index=i + 1, node=None, state=state,
                              terminal=True, stop_reason=STOP_CANCELLED)
                return
        state.report = self.report
        state.report_display = self.report
        yield RunStep(index=self.steps, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


class BoomGraph:
    """第一步就抛异常：验证 Worker 的 crash 兜底。"""

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        raise RuntimeError("boom")
        yield  # pragma: no cover —— 生成器语法占位


class FakeUniqueViolation(Exception):
    """模拟 psycopg 唯一约束冲突（API 通过 sqlstate 识别 email_taken）。"""

    sqlstate = "23505"


class FakeQueue:
    def __init__(self) -> None:
        self.items: list[str] = []

    def enqueue(self, run_id: str) -> int:
        self.items.insert(0, run_id)
        return len(self.items)

    def dequeue(self, timeout: float = 0) -> Optional[str]:
        return self.items.pop() if self.items else None

    def depth(self) -> int:
        return len(self.items)

    def ping(self) -> bool:
        return True


class FakeStore:
    """内存版 RunStore：语义与 web/backend/store.py 对齐，供接线 / Worker 测试使用。"""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}
        self.artifacts: dict[str, dict[str, str]] = {}
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.invites: dict[str, dict] = {}
        self.moderation: list[dict] = []
        self.appeals: dict[str, dict] = {}
        self.deletions: dict[str, dict] = {}
        self.deletion_outbox: dict[tuple, dict] = {}
        self._deletion_outbox_seq = 0
        self.ingestions: dict[str, dict] = {}
        self.audits: list[dict] = []
        self.workers: dict[str, dict] = {}
        self.password_resets: dict[str, dict] = {}
        self.usage: list[dict] = []
        self.quota_reservations: dict[str, dict] = {}
        self.alert_states: dict[str, dict] = {}
        self.alert_deliveries: list[dict] = []
        self._alert_delivery_seq = 0
        self.fail_events = False
        # 需求 23：三层数据（解析快照 / 分块产物 / 版本状态机）+ 全局修订号
        self.rag_snapshots: dict[str, list[dict]] = {}
        self.rag_chunks: dict[tuple, list[dict]] = {}
        self.rag_generations: dict[str, list[dict]] = {}
        self.rag_revision = 0
        # 需求 26：报告只读分享（token_hash → 行）
        self.report_shares: dict[str, dict] = {}

    def ping(self) -> None:
        return None

    def create_run(self, run_id, topic, request=None, *, user_id=None, tenant_id=None,
                   idempotency_key=None, request_hash=None, retry_of=None, status="CREATED",
                   timeout_at=None, budget_limit_cny=None):
        if idempotency_key is not None:
            for row in self.runs.values():
                if row["user_id"] == user_id and row["idempotency_key"] == idempotency_key:
                    return row, False
        now = datetime.now(UTC)
        row = {
            "run_id": run_id, "user_id": user_id, "tenant_id": tenant_id, "status": status,
            "research_status": None, "stop_reason": None, "current_node": None,
            "topic": topic, "request": request or {}, "token_used": 0,
            "cost_estimate_cny": 0.0, "budget_limit_cny": budget_limit_cny,
            "budget_used_cny": 0.0, "attempt": 1, "retry_of": retry_of,
            "idempotency_key": idempotency_key, "request_hash": request_hash,
            "worker_id": None, "worker_status": None,
            "lease_expires_at": None, "timeout_at": timeout_at, "hard_deadline_at": None,
            "cancel_requested_at": None, "created_at": now,
            "queued_at": now if status == "QUEUED" else None,
            "started_at": None, "finished_at": None, "moderation_status": None,
            "pinned_at": None, "archived_at": None,
        }
        self.runs[run_id] = row
        self.events[run_id] = []
        return row, True

    def update_status(self, run_id, new_status, *, allowed_from, **fields):
        row = self.runs.get(run_id)
        if row is None or row["status"] not in tuple(allowed_from):
            return False
        row["status"] = new_status
        row.update(fields)
        return True

    def finalize_run(self, run_id, *, event_type, payload, sequence, new_status,
                     allowed_from, fields=None, artifacts=None, moderation=None):
        """与 RunStore.finalize_run 同语义：迁移失败 ⇒ 不写事件 / 产物（P0-6）。

        P0-4：`moderation`（{kind, detail}）与终局同事务写审核证据；
        P0-2：同事务结算 `quota_reservations`（reserved → settled）。
        """
        row = self.runs.get(run_id)
        if row is None or row["status"] not in tuple(allowed_from):
            return False
        if self.fail_events:
            raise RuntimeError("db down")
        seq = sequence if sequence is not None else len(self.events[run_id])
        if not any(item["sequence"] == seq for item in self.events[run_id]):
            self.events[run_id].append({"run_id": run_id, "sequence": seq,
                                        "event_type": event_type, "payload": payload or {}})
            self.events[run_id].sort(key=lambda item: item["sequence"])
        row["status"] = new_status
        row.update(fields or {})
        for kind, meta in (artifacts or {}).items():
            self.artifacts.setdefault(run_id, {})[kind] = {
                "run_id": run_id, "kind": kind, "body": meta.get("body", ""),
                "storage": meta.get("storage", "db"),
                "object_key": meta.get("object_key"), "sha256": meta.get("sha256"),
                "size_bytes": meta.get("size_bytes"),
            }
        if moderation:
            self.record_moderation(str(moderation.get("kind") or "output_decision"),
                                   user_id=row.get("user_id"), run_id=run_id,
                                   detail=moderation.get("detail") or {})
        res = self.quota_reservations.get(run_id)
        if res is not None and res["status"] == "reserved":
            actual = float(row.get("cost_estimate_cny") or 0.0)
            res["actual_cny"] = actual
            res["released_cny"] = max(res["reserved_cny"] - actual, 0.0)
            res["status"] = "settled"
        return True

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def create_run_admitted(self, run_id, topic, request=None, *, user_id=None, tenant_id=None,
                            idempotency_key=None, request_hash=None, retry_of=None,
                            status="CREATED",
                            timeout_at=None, budget_limit_cny=None, global_active_limit=None,
                            user_active_limit=None, daily_limit=None,
                            monthly_budget_cny=None, reserve_cny=None, daily_since=None):
        """与 RunStore.create_run_admitted 同语义（P0-3 / P0-2；内存版无并发竞争）。"""
        if idempotency_key is not None:
            existing = self.get_run_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                return existing, False
        if monthly_budget_cny is not None and monthly_budget_cny > 0:
            committed = self._month_committed()
            if committed >= monthly_budget_cny:
                raise QuotaExceeded(
                    "monthly_budget",
                    f"committed={committed:.4f}; limit={monthly_budget_cny}")
        if global_active_limit is not None and global_active_limit > 0:
            active = self.count_active()
            if active >= global_active_limit:
                raise QuotaExceeded("global_concurrency", f"active={active}; limit={global_active_limit}")
        if user_id is not None:
            if user_active_limit is not None and user_active_limit > 0:
                active = self.count_active(user_id)
                if active >= user_active_limit:
                    raise QuotaExceeded("user_concurrency", f"active={active}; limit={user_active_limit}")
            if daily_limit is not None and daily_limit > 0 and daily_since is not None:
                used = self.count_user_runs_since(user_id, daily_since)
                if used >= daily_limit:
                    raise QuotaExceeded("daily_runs", f"used={used}; limit={daily_limit}")
        row, created = self.create_run(run_id, topic, request, user_id=user_id, tenant_id=tenant_id,
                                       idempotency_key=idempotency_key, request_hash=request_hash,
                                       retry_of=retry_of, status=status, timeout_at=timeout_at,
                                       budget_limit_cny=budget_limit_cny)
        if created and reserve_cny is not None and reserve_cny > 0:
            self.quota_reservations[run_id] = {
                "reservation_id": f"qr_{run_id}", "run_id": run_id, "user_id": user_id,
                "reserved_cny": float(reserve_cny), "actual_cny": 0.0, "released_cny": 0.0,
                "status": "reserved", "created_at": datetime.now(UTC),
            }
        return row, created

    def _month_committed(self) -> float:
        """P0-2：已发生成本 + 未决预留（hold - 已记成本，不双重计数）。"""
        committed = 0.0
        for row in self.runs.values():
            committed += float(row.get("cost_estimate_cny") or 0.0)
        for res in self.quota_reservations.values():
            if res["status"] != "reserved":
                continue
            run = self.runs.get(res["run_id"]) or {}
            committed += max(res["reserved_cny"]
                             - float(run.get("cost_estimate_cny") or 0.0), 0.0)
        return round(committed, 6)

    def get_run_by_idempotency(self, user_id, idempotency_key):
        for row in self.runs.values():
            if row["user_id"] == user_id and row["idempotency_key"] == idempotency_key:
                return row
        return None

    def list_runs(self, *, user_id=None, statuses=None, q=None, archived=False,
                  limit=20, offset=0):
        """需求 22：关键词 + 归档过滤 + 置顶优先（与 RunStore.list_runs 同语义）。"""
        rows = [r for r in self.runs.values()
                if (user_id is None or r["user_id"] == user_id)
                and (not statuses or r["status"] in statuses)
                and (bool(r.get("archived_at")) == archived)
                and (not q or q.lower() in r["topic"].lower())]
        def _ts(value):
            return value.timestamp() if value is not None else 0.0

        rows.sort(key=lambda r: (
            0 if r.get("pinned_at") else 1,
            -_ts(r.get("pinned_at") or r["created_at"]),
            -_ts(r["created_at"]),
            r["run_id"],
        ))
        out = []
        for row in rows[offset:offset + limit]:
            item = dict(row)
            item["has_report"] = "report_md" in self.artifacts.get(row["run_id"], {})
            out.append(item)
        return out

    def update_topic(self, run_id, topic, *, user_id):
        row = self.runs.get(run_id)
        if row is None or row["user_id"] != user_id:
            return False
        row["topic"] = topic
        return True

    def set_pinned(self, run_id, pinned, *, user_id):
        row = self.runs.get(run_id)
        if row is None or row["user_id"] != user_id:
            return False
        row["pinned_at"] = datetime.now(UTC) if pinned else None
        return True

    def set_archived(self, run_id, archived, *, user_id):
        row = self.runs.get(run_id)
        if row is None or row["user_id"] != user_id:
            return False
        row["archived_at"] = datetime.now(UTC) if archived else None
        return True

    def claim_run(self, run_id, worker_id, lease_seconds):
        row = self.runs.get(run_id)
        if row is None or row["status"] != "QUEUED":
            return None
        row["status"] = "RUNNING"
        row["worker_id"] = worker_id
        row["worker_status"] = "alive"
        row["started_at"] = row["started_at"] or datetime.now(UTC)
        row["queued_at"] = row["queued_at"] or row["created_at"]
        row["lease_expires_at"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        return row

    def claim_next_queued(self, worker_id, lease_seconds):
        """与 RunStore.claim_next_queued 同语义（P0-2）：按排队时间取最早一条。"""
        now = datetime.now(UTC)
        candidates = [row for row in self.runs.values()
                      if row["status"] == "QUEUED"
                      and (row["timeout_at"] is None or row["timeout_at"] > now)]
        if not candidates:
            return None
        candidates.sort(key=lambda row: (row["queued_at"] or row["created_at"], row["run_id"]))
        return self.claim_run(candidates[0]["run_id"], worker_id, lease_seconds)

    def renew_lease(self, run_id, worker_id, lease_seconds):
        row = self.runs.get(run_id)
        if row is None or row["worker_id"] != worker_id:
            return False
        if row["status"] not in ("RUNNING", "CANCEL_REQUESTED"):
            return False
        row["lease_expires_at"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        row["worker_status"] = "alive"
        return True

    def count_active(self, user_id=None):
        return sum(1 for row in self.runs.values()
                   if row["status"] in ACTIVE and (user_id is None or row["user_id"] == user_id))

    def count_queued(self):
        return sum(1 for row in self.runs.values() if row["status"] == "QUEUED")

    def count_user_runs_since(self, user_id, since):
        return sum(1 for row in self.runs.values()
                   if row["user_id"] == user_id and row["created_at"] >= since)

    def month_cost_cny(self):
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return sum(float(row.get("cost_estimate_cny") or 0.0)
                   for row in self.runs.values() if row["created_at"] >= month_start)

    def status_counts_since(self, since):
        counts: dict[str, int] = {}
        for row in self.runs.values():
            if row["created_at"] >= since:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    def count_stale_leases(self):
        now = datetime.now(UTC)
        return sum(1 for row in self.runs.values()
                   if row["status"] in ("RUNNING", "CANCEL_REQUESTED")
                   and row["lease_expires_at"] is not None
                   and row["lease_expires_at"] < now)

    def sweep_stale_runs(self, max_attempts: int = 2):
        """租约超时清扫：与 RunStore.sweep_stale_runs 同语义（含过期 QUEUED 收口）。"""
        now = datetime.now(UTC)
        results = []
        for row in self.runs.values():
            if (row["status"] == "QUEUED" and row["timeout_at"] is not None
                    and row["timeout_at"] < now):
                row.update(status="TIMED_OUT", stop_reason="timeout", finished_at=now)
                results.append({"run_id": row["run_id"], "action": "timed_out"})
        for row in self.runs.values():
            if row["status"] not in ("RUNNING", "CANCEL_REQUESTED"):
                continue
            lease = row["lease_expires_at"]
            if lease is None or lease >= now:
                continue
            if row["cancel_requested_at"] is not None:
                row.update(status="CANCELLED", stop_reason="user_cancelled", finished_at=now)
                results.append({"run_id": row["run_id"], "action": "cancelled"})
            elif row["attempt"] < max_attempts:
                attempt = row["attempt"] + 1
                row.update(status="QUEUED", attempt=attempt, worker_id=None,
                           worker_status=None, lease_expires_at=None, queued_at=now)
                results.append({"run_id": row["run_id"], "action": "requeued",
                                "attempt": attempt})
            else:
                row.update(status="LOST", stop_reason="lost", finished_at=now)
                results.append({"run_id": row["run_id"], "action": "lost"})
        return results

    def update_usage(self, run_id, *, token_used, cost_estimate_cny, budget_used_cny):
        row = self.runs.get(run_id)
        if row is not None:
            row["token_used"] = token_used
            row["cost_estimate_cny"] = cost_estimate_cny
            row["budget_used_cny"] = budget_used_cny

    def request_cancel(self, run_id):
        row = self.runs.get(run_id)
        if row is None:
            return None
        now = datetime.now(UTC)
        if row["status"] in ("CREATED", "QUEUED"):
            row.update(status="CANCELLED", stop_reason="user_cancelled",
                       cancel_requested_at=now, finished_at=now)
            return "CANCELLED"
        if row["status"] == "RUNNING":
            row.update(status="CANCEL_REQUESTED", cancel_requested_at=now)
            return "CANCEL_REQUESTED"
        return row["status"]

    def append_event(self, run_id, event_type, payload=None, *, sequence=None):
        if self.fail_events:
            raise RuntimeError("db down")
        if run_id not in self.runs:
            raise LookupError(f"run not found: {run_id}")
        seq = len(self.events[run_id]) if sequence is None else sequence
        if any(item["sequence"] == seq for item in self.events[run_id]):
            return seq
        self.events[run_id].append({"run_id": run_id, "sequence": seq,
                                    "event_type": event_type, "payload": payload or {}})
        self.events[run_id].sort(key=lambda item: item["sequence"])
        return seq

    def get_events(self, run_id, after=None):
        return [item for item in self.events.get(run_id, [])
                if after is None or item["sequence"] > after]

    def count_events(self, run_id, event_type=None):
        return sum(1 for item in self.events.get(run_id, [])
                   if event_type is None or item["event_type"] == event_type)

    def last_event_type(self, run_id):
        items = self.events.get(run_id) or []
        return items[-1]["event_type"] if items else None

    def put_artifact(self, run_id, kind, body):
        self.artifacts.setdefault(run_id, {})[kind] = {
            "run_id": run_id, "kind": kind, "body": body, "storage": "db",
            "object_key": None, "sha256": None, "size_bytes": None,
        }

    def get_artifact(self, run_id, kind):
        row = self.artifacts.get(run_id, {}).get(kind)
        return row["body"] if row else None

    def get_artifact_row(self, run_id, kind):
        return self.artifacts.get(run_id, {}).get(kind)

    def has_artifact(self, run_id, kind):
        return kind in self.artifacts.get(run_id, {})

    def mark_stale_as_lost(self, reason="lost"):
        count = 0
        for row in self.runs.values():
            if row["status"] in ACTIVE:
                row.update(status="LOST", stop_reason=reason, finished_at=datetime.now(UTC))
                count += 1
        return count

    # ---- 账号 / 会话 / 邀请（P4-A）----

    def create_user(self, user_id, email, password_hash):
        if any(user["email"].lower() == email.lower() for user in self.users.values()):
            raise FakeUniqueViolation("duplicate email")
        row = {
            "user_id": user_id, "email": email, "password_hash": password_hash,
            "status": "active", "created_at": datetime.now(UTC), "last_login_at": None,
        }
        self.users[user_id] = row
        return row

    def get_user(self, user_id):
        return self.users.get(user_id)

    def get_user_by_email(self, email):
        for row in self.users.values():
            if row["email"].lower() == email.lower():
                return row
        return None

    def touch_last_login(self, user_id):
        row = self.users.get(user_id)
        if row is not None:
            row["last_login_at"] = datetime.now(UTC)

    def set_user_status(self, user_id, status):
        row = self.users.get(user_id)
        if row is None:
            return False
        row["status"] = status
        return True

    def register_with_invite(self, user_id, email, password_hash, invite_hash):
        invite = self.invites.get(invite_hash)
        now = datetime.now(UTC)
        if (invite is None or invite["used_at"] is not None or invite["revoked_at"] is not None
                or (invite["expires_at"] is not None and invite["expires_at"] < now)):
            raise ValueError("invite_invalid")
        user = self.create_user(user_id, email, password_hash)
        invite["used_by"] = user_id
        invite["used_at"] = now
        return user

    def create_invite(self, code_hash, *, created_by=None, expires_at=None):
        self.invites[code_hash] = {
            "code_hash": code_hash, "created_by": created_by, "created_at": datetime.now(UTC),
            "expires_at": expires_at, "used_by": None, "used_at": None, "revoked_at": None,
        }

    def revoke_invite(self, code_hash):
        invite = self.invites.get(code_hash)
        if invite is None or invite["used_at"] is not None or invite["revoked_at"] is not None:
            return False
        invite["revoked_at"] = datetime.now(UTC)
        return True

    def list_invites(self, limit=50):
        return list(self.invites.values())[:limit]

    def create_session(self, session_hash, user_id, expires_at, *, ip=None, user_agent=None):
        session_id = uuid.uuid4().hex[:12]
        self.sessions[session_hash] = {
            "token_hash": session_hash, "user_id": user_id, "session_id": session_id,
            "created_at": datetime.now(UTC), "last_seen_at": datetime.now(UTC),
            "expires_at": expires_at, "ip": ip, "user_agent": user_agent,
        }
        return session_id

    def get_session_user(self, session_hash, *, idle_seconds=0):
        session = self.sessions.get(session_hash)
        now = datetime.now(UTC)
        if session is None or session["expires_at"] < now:
            return None
        if idle_seconds > 0 and session["last_seen_at"] < now - timedelta(seconds=idle_seconds):
            return None
        user = self.users.get(session["user_id"])
        if user is None or user["status"] != "active":
            return None
        return {
            **user,
            "session_id": session["session_id"],
            "session_created_at": session["created_at"],
            "session_last_seen_at": session["last_seen_at"],
            "session_ip": session["ip"],
            "session_user_agent": session["user_agent"],
            "session_token_hash": session["token_hash"],
        }

    def touch_session(self, session_hash):
        session = self.sessions.get(session_hash)
        if session is not None:
            session["last_seen_at"] = datetime.now(UTC)

    def list_sessions(self, user_id):
        rows = [dict(row) for row in self.sessions.values() if row["user_id"] == user_id]
        rows.sort(key=lambda row: row["last_seen_at"], reverse=True)
        return rows

    def revoke_session_by_id(self, user_id, session_id):
        for key, value in list(self.sessions.items()):
            if value["user_id"] == user_id and value["session_id"] == session_id:
                del self.sessions[key]
                return True
        return False

    def revoke_other_sessions(self, user_id, keep_token_hash):
        keys = [key for key, value in self.sessions.items()
                if value["user_id"] == user_id and key != keep_token_hash]
        for key in keys:
            del self.sessions[key]
        return len(keys)

    def revoke_session(self, session_hash):
        return self.sessions.pop(session_hash, None) is not None

    def revoke_user_sessions(self, user_id):
        keys = [key for key, value in self.sessions.items() if value["user_id"] == user_id]
        for key in keys:
            del self.sessions[key]
        return len(keys)

    def update_password(self, user_id, password_hash):
        row = self.users.get(user_id)
        if row is None:
            return False
        row["password_hash"] = password_hash
        return True

    # ---- 内容安全（P7-A）----

    def record_moderation(self, kind, *, user_id=None, run_id=None, detail=None):
        self.moderation.append({
            "id": len(self.moderation) + 1, "user_id": user_id, "run_id": run_id,
            "kind": kind, "detail": detail or {}, "created_at": datetime.now(UTC),
        })
        return len(self.moderation)

    def list_moderation(self, *, kind=None, limit=50):
        rows = [row for row in self.moderation if kind is None or row["kind"] == kind]
        return list(reversed(rows))[:limit]

    def has_appeal(self, user_id, run_id):
        return any(row["kind"] == "appeal" and row["user_id"] == user_id
                   and row["run_id"] == run_id for row in self.moderation)

    def set_moderation_status(self, run_id, status):
        row = self.runs.get(run_id)
        if row is None:
            return False
        row["moderation_status"] = status
        return True

    # ---- 申诉/复核状态机（P2-5b）----

    def create_appeal(self, appeal_id, *, message, run_id=None, user_id=None,
                      sla_due_at=None):
        now = datetime.now(UTC)
        row = {
            "appeal_id": appeal_id, "run_id": run_id, "user_id": user_id,
            "message": message, "status": "pending", "decision_note": None,
            "reviewed_by": None, "sla_due_at": sla_due_at, "decided_at": None,
            "created_at": now, "updated_at": now,
        }
        self.appeals[appeal_id] = row
        # P0-5：证据与状态行同一入口（真实实现是同一事务）
        self.record_moderation("appeal", user_id=user_id, run_id=run_id,
                               detail={"message": message, "appeal_id": appeal_id})
        return dict(row)

    def get_appeal(self, appeal_id):
        row = self.appeals.get(appeal_id)
        return dict(row) if row else None

    def list_appeals(self, *, status=None, user_id=None, limit=100):
        rows = [dict(row) for row in self.appeals.values()
                if (status is None or row["status"] == status)
                and (user_id is None or row["user_id"] == user_id)]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[:limit]

    def claim_appeal(self, appeal_id, *, reviewer=None):
        row = self.appeals.get(appeal_id)
        if row is None or row["status"] != "pending":
            return None
        row["status"] = "reviewing"
        row["reviewed_by"] = reviewer
        row["updated_at"] = datetime.now(UTC)
        return dict(row)

    def decide_appeal(self, appeal_id, *, decision, note=None, reviewer=None):
        """原子决策（P0-5）：必须先领取（reviewing）且决策者 = 领取者。"""
        if decision not in ("accepted", "rejected"):
            raise ValueError(f"invalid decision: {decision}")
        row = self.appeals.get(appeal_id)
        if row is None or row["status"] != "reviewing":
            return None
        if row.get("reviewed_by") != reviewer:
            return None
        now = datetime.now(UTC)
        row["status"] = decision
        row["decision_note"] = note
        if reviewer is not None:
            row["reviewed_by"] = reviewer
        row["decided_at"] = now
        row["updated_at"] = now
        if decision == "accepted" and row.get("run_id"):
            run = self.runs.get(row["run_id"])
            if run is not None:
                run["moderation_status"] = "cleared"
        self.record_moderation(f"appeal_{decision}", user_id=row.get("user_id"),
                               run_id=row.get("run_id"),
                               detail={"appeal_id": appeal_id, "note": note,
                                       "reviewed_by": reviewer})
        self.record_audit(f"appeal_{decision}", target_type="moderation_appeal",
                          target_id=appeal_id,
                          detail={"run_id": row.get("run_id"), "reviewed_by": reviewer})
        return dict(row)

    def count_appeals_overdue(self, before):
        return sum(
            1 for row in self.appeals.values()
            if row["status"] in ("pending", "reviewing")
            and row["sla_due_at"] is not None and row["sla_due_at"] < before
        )

    def delete_user(self, user_id):
        """镜像真实外键行为：删用户、级联会话、runs/邀请/记录脱钩。"""
        if user_id not in self.users:
            return False
        del self.users[user_id]
        self.sessions = {key: value for key, value in self.sessions.items()
                         if value["user_id"] != user_id}
        for row in self.runs.values():
            if row["user_id"] == user_id:
                row["user_id"] = None
        for invite in self.invites.values():
            if invite.get("used_by") == user_id:
                invite["used_by"] = None
        for record in self.moderation:
            if record["user_id"] == user_id:
                record["user_id"] = None
        return True

    # ---- 注销台账与 outbox（P0-7）----

    def request_account_deletion(self, request_id, user_id, *,
                                 targets=("qdrant",), payload=None):
        """与 RunStore.request_account_deletion 同语义（同事务：台账 + outbox + 删用户）。"""
        self.deletions[request_id] = {
            "request_id": request_id, "user_id": user_id, "status": "pending",
            "attempts": 0, "last_error": None, "requested_at": datetime.now(UTC),
            "completed_at": None, "updated_at": datetime.now(UTC),
        }
        for target in targets:
            key = (request_id, target)
            if key in self.deletion_outbox:
                continue
            self._deletion_outbox_seq += 1
            self.deletion_outbox[key] = {
                "id": self._deletion_outbox_seq, "request_id": request_id, "target": target,
                "payload": dict(payload or {"user_id": user_id}), "status": "pending",
                "attempts": 0, "next_attempt_at": datetime.now(UTC),
                "lease_expires_at": None, "claimed_by": None, "last_error": None,
            }
        self.delete_user(user_id)

    def claim_deletion_outbox(self, claimed_by, lease_seconds, limit=5):
        now = datetime.now(UTC)
        due = [row for row in self.deletion_outbox.values()
               if (row["status"] == "pending" and row["next_attempt_at"] <= now)
               or (row["status"] == "in_progress" and row["lease_expires_at"] is not None
                   and row["lease_expires_at"] < now)]
        due.sort(key=lambda row: row["id"])
        claimed = due[:limit]
        for row in claimed:
            row["status"] = "in_progress"
            row["attempts"] += 1
            row["claimed_by"] = claimed_by
            row["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
            parent = self.deletions.get(row["request_id"])
            if parent is not None and parent["status"] == "pending":
                parent["status"] = "in_progress"
        return claimed

    def mark_deletion_done(self, outbox_id):
        row = next((item for item in self.deletion_outbox.values()
                    if item["id"] == outbox_id), None)
        if row is None:
            return
        row["status"] = "done"
        row["lease_expires_at"] = None
        row["last_error"] = None
        parent = self.deletions.get(row["request_id"])
        if parent is not None:
            siblings = [item for item in self.deletion_outbox.values()
                        if item["request_id"] == row["request_id"]]
            if all(item["status"] == "done" for item in siblings):
                parent["status"] = "completed"
                parent["completed_at"] = datetime.now(UTC)
            else:
                parent["status"] = "in_progress"

    def mark_deletion_retry(self, outbox_id, error, *, backoff_seconds, max_attempts):
        row = next((item for item in self.deletion_outbox.values()
                    if item["id"] == outbox_id), None)
        if row is None:
            return "missing"
        exhausted = row["attempts"] >= max_attempts
        row["status"] = "abandoned" if exhausted else "pending"
        row["lease_expires_at"] = None
        row["claimed_by"] = None
        row["next_attempt_at"] = datetime.now(UTC) + timedelta(seconds=max(1, backoff_seconds))
        row["last_error"] = error[:500]
        if exhausted:
            parent = self.deletions.get(row["request_id"])
            if parent is not None:
                parent["status"] = "abandoned"
                parent["last_error"] = error[:500]
        return row["status"]

    def retry_deletion(self, request_id):
        count = 0
        for row in self.deletion_outbox.values():
            if row["request_id"] == request_id and row["status"] != "done":
                row.update(status="pending", attempts=0, next_attempt_at=datetime.now(UTC),
                           lease_expires_at=None, claimed_by=None, last_error=None)
                count += 1
        parent = self.deletions.get(request_id)
        if parent is not None and parent["status"] == "abandoned":
            parent["status"] = "pending"
            parent["last_error"] = None
        return count

    def deletion_status(self, request_id):
        row = self.deletions.get(request_id)
        if row is None:
            return None
        out = dict(row)
        out["targets"] = [dict(item) for item in self.deletion_outbox.values()
                          if item["request_id"] == request_id]
        return out

    def list_deletions(self, limit=50):
        rows = sorted(self.deletions.values(), key=lambda row: row["requested_at"], reverse=True)
        return [dict(row) for row in rows[:limit]]

    def count_deletions_by_status(self):
        counts = {}
        for row in self.deletions.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    # ---- RAG 摄取台账（P0-8b）----

    def create_ingestion(self, ingestion_id, doc_id, *, user_id=None, source, sha256,
                         size_bytes, stored_name):
        # P0-11：同一 (user, doc_id) 活跃记录唯一；冲突返回 None（调用方回查既有）
        if self.find_ingestion_by_doc(user_id, doc_id) is not None:
            return None
        now = datetime.now(UTC)
        row = {
            "ingestion_id": ingestion_id, "doc_id": doc_id, "user_id": user_id,
            "source": source, "sha256": sha256, "size_bytes": size_bytes,
            "stored_name": stored_name, "status": "pending", "chunks": 0,
            "attempts": 0, "next_attempt_at": now, "lease_expires_at": None,
            "claimed_by": None, "scan_status": "skipped", "last_error": None,
            "created_at": now, "updated_at": now, "processed_at": None,
            # 需求 23：任务类型 / 展示元数据 / 活动版本 / 修订号
            "task": "ingest", "display_name": None, "tags": [],
            "active_generation": None, "index_revision": 0,
        }
        self.ingestions[ingestion_id] = row
        return row

    def get_ingestion(self, ingestion_id):
        return self.ingestions.get(ingestion_id)

    def find_ingestion_by_doc(self, user_id, doc_id):
        rows = [row for row in self.ingestions.values()
                if row["user_id"] == user_id and row["doc_id"] == doc_id
                and row["status"] != "deleted"]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[0] if rows else None

    def claim_next_ingestion(self, claimed_by, lease_seconds):
        now = datetime.now(UTC)
        due = [row for row in self.ingestions.values()
               if (row["status"] == "pending" and row["next_attempt_at"] <= now)
               or (row["status"] == "processing" and row["lease_expires_at"] is not None
                   and row["lease_expires_at"] < now)]
        due.sort(key=lambda row: row["created_at"])
        if not due:
            return None
        row = due[0]
        row.update(status="processing", attempts=row["attempts"] + 1, claimed_by=claimed_by,
                   lease_expires_at=now + timedelta(seconds=lease_seconds))
        return row

    def mark_ingestion_ready(self, ingestion_id, chunks, scan_status="skipped"):
        row = self.ingestions.get(ingestion_id)
        if row is None:
            return
        row.update(status="ready", chunks=chunks, scan_status=scan_status,
                   lease_expires_at=None, claimed_by=None, last_error=None,
                   processed_at=datetime.now(UTC))

    def mark_ingestion_rejected(self, ingestion_id, error, *, scan_status=None):
        row = self.ingestions.get(ingestion_id)
        if row is None:
            return
        row.update(status="rejected", last_error=error[:500], lease_expires_at=None,
                   claimed_by=None, processed_at=datetime.now(UTC))
        if scan_status is not None:
            row["scan_status"] = scan_status

    def mark_ingestion_retry(self, ingestion_id, error, *, backoff_seconds, max_attempts):
        row = self.ingestions.get(ingestion_id)
        if row is None:
            return "missing"
        if row["attempts"] >= max_attempts:
            row.update(status="rejected", last_error=error[:500], lease_expires_at=None,
                       claimed_by=None, processed_at=datetime.now(UTC))
            return "rejected"
        row.update(status="pending", last_error=error[:500], lease_expires_at=None,
                   claimed_by=None,
                   next_attempt_at=datetime.now(UTC) + timedelta(seconds=max(1, backoff_seconds)))
        return "pending"

    def list_ingestions(self, *, user_id=None, status=None, limit=50):
        rows = [row for row in self.ingestions.values()
                if (user_id is None or row["user_id"] == user_id)
                and (status is None or row["status"] == status)]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return [dict(row) for row in rows[:limit]]

    def list_ingestion_files_for_user(self, user_id):
        return [row["stored_name"] for row in self.ingestions.values()
                if row["user_id"] == user_id and row["status"] != "deleted"
                and row["stored_name"]]

    def mark_ingestions_deleted_for_user(self, user_id):
        count = 0
        for row in self.ingestions.values():
            if row["user_id"] == user_id and row["status"] != "deleted":
                row.update(status="deleted", stored_name="", source="(deleted)")
                count += 1
        return count

    def delete_ingestion_by_doc(self, user_id, doc_id):
        names = []
        for row in self.ingestions.values():
            if row["user_id"] == user_id and row["doc_id"] == doc_id and row["status"] != "deleted":
                if row["stored_name"]:
                    names.append(row["stored_name"])
                row.update(status="deleted", stored_name="", source="(deleted)")
        return names

    def list_expired_ingestions(self, before, limit=50):
        rows = [row for row in self.ingestions.values()
                if row["status"] == "ready" and row["created_at"] < before]
        rows.sort(key=lambda row: row["created_at"])
        return [dict(row) for row in rows[:limit]]

    # ---- 需求 23：三层数据（解析快照 / 分块产物 / 版本状态机）----

    def replace_parse_snapshot(self, doc_id, blocks):
        self.rag_snapshots[doc_id] = [dict(block) for block in blocks]
        return len(self.rag_snapshots[doc_id])

    def get_parse_snapshot(self, doc_id):
        return [dict(block) for block in self.rag_snapshots.get(doc_id, [])]

    def count_parse_snapshot(self, doc_id):
        return len(self.rag_snapshots.get(doc_id, []))

    def create_index_generation(self, doc_id, *, chunker_version, embedding_model,
                                embedding_dim):
        generations = self.rag_generations.setdefault(doc_id, [])
        generation = max((item["generation"] for item in generations), default=0) + 1
        generations.append({
            "doc_id": doc_id, "generation": generation,
            "chunker_version": chunker_version, "embedding_model": embedding_model,
            "embedding_dim": embedding_dim, "status": "building",
            "chunk_count": 0, "error": None, "activated_at": None,
        })
        return generation

    def insert_rag_chunks(self, doc_id, generation, chunks):
        self.rag_chunks[(doc_id, generation)] = [dict(chunk) for chunk in chunks]

    def count_rag_chunks(self, doc_id, generation):
        return len(self.rag_chunks.get((doc_id, generation), []))

    def list_rag_chunks(self, doc_id, generation, *, offset=0, limit=20):
        rows = self.rag_chunks.get((doc_id, generation), [])
        return [dict(row) for row in rows[offset:offset + limit]], len(rows)

    def get_rag_chunk_rows(self, doc_id, generation):
        return [dict(row) for row in self.rag_chunks.get((doc_id, generation), [])]

    def activate_index_generation(self, doc_id, generation):
        generations = self.rag_generations.get(doc_id, [])
        target = next((g for g in generations if g["generation"] == generation), None)
        if target is None or target["status"] != "building":
            raise ValueError("generation_not_building")
        retired = [g["generation"] for g in generations
                   if g["status"] == "active" and g["generation"] != generation]
        for item in generations:
            if item["status"] == "active" and item["generation"] != generation:
                item["status"] = "retired"
        target.update(status="active", activated_at=datetime.now(UTC),
                      chunk_count=self.count_rag_chunks(doc_id, generation))
        for row in self.ingestions.values():
            if row["doc_id"] == doc_id:
                row["active_generation"] = generation
                row["index_revision"] = row.get("index_revision", 0) + 1
        self.rag_revision += 1
        return retired

    def fail_index_generation(self, doc_id, generation, error):
        for item in self.rag_generations.get(doc_id, []):
            if item["generation"] == generation and item["status"] == "building":
                item.update(status="failed", error=error[:300])

    def delete_rag_generation_layers(self, doc_id, generation):
        self.rag_chunks.pop((doc_id, generation), None)

    def mark_generation_cleaned(self, doc_id, generation):
        self.rag_chunks.pop((doc_id, generation), None)
        self.rag_generations[doc_id] = [
            item for item in self.rag_generations.get(doc_id, [])
            if not (item["generation"] == generation
                    and item["status"] in ("retired", "failed"))]

    def list_retired_generations(self, limit=20):
        rows = []
        for doc_id, generations in self.rag_generations.items():
            for item in generations:
                if item["status"] in ("retired", "failed"):
                    rows.append({"doc_id": doc_id, "generation": item["generation"],
                                 "status": item["status"]})
        return rows[:limit]

    def get_active_generation(self, doc_id):
        for item in self.rag_generations.get(doc_id, []):
            if item["status"] == "active":
                return item["generation"]
        return None

    def get_rag_revision(self):
        return self.rag_revision

    def bump_rag_revision(self):
        self.rag_revision += 1
        return self.rag_revision

    def list_rag_docs(self, user_id):
        rows = [dict(row) for row in self.ingestions.values()
                if row["user_id"] == user_id and row["status"] != "deleted"]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows

    def get_rag_doc_for_user(self, doc_id, user_id):
        return self.find_ingestion_by_doc(user_id, doc_id)

    def update_rag_doc_meta(self, doc_id, user_id, *, display_name=None, tags=None):
        row = self.find_ingestion_by_doc(user_id, doc_id)
        if row is None:
            return False
        if display_name is not None:
            row["display_name"] = display_name
        if tags is not None:
            row["tags"] = list(tags)
        return True

    def rag_usage_bytes(self, user_id):
        return sum(int(row.get("size_bytes") or 0) for row in self.ingestions.values()
                   if row["user_id"] == user_id and row["status"] != "deleted")

    def requeue_ingestion_for_task(self, ingestion_id, task):
        row = self.ingestions.get(ingestion_id)
        if row is None or row["status"] != "ready":
            return False
        row.update(status="pending", task=task, attempts=0, last_error=None,
                   next_attempt_at=datetime.now(UTC), lease_expires_at=None,
                   claimed_by=None)
        return True

    def delete_rag_layers(self, doc_id):
        self.rag_snapshots.pop(doc_id, None)
        for key in [key for key in self.rag_chunks if key[0] == doc_id]:
            self.rag_chunks.pop(key, None)
        self.rag_generations.pop(doc_id, None)

    def list_rag_doc_ids_for_user(self, user_id):
        return [row["doc_id"] for row in self.ingestions.values()
                if row["user_id"] == user_id and row["status"] != "deleted"]

    def mark_ingestion_deleted(self, ingestion_id, reason="expired"):
        row = self.ingestions.get(ingestion_id)
        if row is None:
            return
        row.update(status="deleted", stored_name="", source=f"({reason})")

    def count_ingestions_by_status(self):
        counts = {}
        for row in self.ingestions.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    # ---- 安全审计日志（P1-5）----

    def record_audit(self, action, *, actor_user_id=None, target_type=None, target_id=None,
                     ip=None, user_agent=None, request_id=None, detail=None):
        self.audits.append({
            "id": len(self.audits) + 1, "at": datetime.now(UTC), "action": action,
            "actor_user_id": actor_user_id, "target_type": target_type,
            "target_id": target_id, "ip": ip, "user_agent": user_agent,
            "request_id": request_id, "detail": detail or {},
        })
        return len(self.audits)

    def list_audit(self, *, action=None, actor_user_id=None, limit=100):
        rows = [row for row in self.audits
                if (action is None or row["action"] == action)
                and (actor_user_id is None or row["actor_user_id"] == actor_user_id)]
        rows.sort(key=lambda row: row["at"], reverse=True)
        return [dict(row) for row in rows[:limit]]

    # ---- 告警状态与外部交付（P2-6）----

    def get_alert_state(self, fingerprint):
        row = self.alert_states.get(fingerprint)
        return dict(row) if row else None

    def list_alert_states(self, *, status=None, limit=200):
        rows = [dict(row) for row in self.alert_states.values()
                if status is None or row["status"] == status]
        rows.sort(key=lambda row: row["last_seen_at"], reverse=True)
        return rows[:limit]

    def upsert_alert_state(self, fingerprint, *, severity, status, detail=None,
                           notified_at=None):
        now = datetime.now(UTC)
        row = self.alert_states.get(fingerprint)
        if row is None:
            row = {
                "fingerprint": fingerprint, "severity": severity, "status": status,
                "detail": detail or {}, "first_seen_at": now, "last_seen_at": now,
                "last_notified_at": notified_at, "updated_at": now,
            }
            self.alert_states[fingerprint] = row
        else:
            row["severity"] = severity
            row["status"] = status
            row["detail"] = detail or {}
            row["last_seen_at"] = now
            if notified_at is not None:
                row["last_notified_at"] = notified_at
            row["updated_at"] = now
        return dict(row)

    def enqueue_alert_delivery(self, fingerprint, kind, severity, *, payload=None,
                               max_attempts=5):
        self._alert_delivery_seq += 1
        self.alert_deliveries.append({
            "delivery_id": self._alert_delivery_seq, "fingerprint": fingerprint,
            "kind": kind, "severity": severity, "payload": payload or {},
            "attempts": 0, "max_attempts": max(1, max_attempts),
            "next_attempt_at": datetime.now(UTC), "delivered_at": None,
            "given_up": False, "last_error": None, "created_at": datetime.now(UTC),
        })
        return self._alert_delivery_seq

    def claim_due_alert_deliveries(self, *, limit=20, lease_seconds=120):
        now = datetime.now(UTC)
        due = [row for row in self.alert_deliveries
               if row["delivered_at"] is None and not row["given_up"]
               and row["next_attempt_at"] <= now][:limit]
        for row in due:
            row["attempts"] += 1
            row["next_attempt_at"] = now + timedelta(seconds=max(1, lease_seconds))
        return [dict(row) for row in due]

    def finish_alert_delivery(self, delivery_id):
        for row in self.alert_deliveries:
            if row["delivery_id"] == delivery_id and row["delivered_at"] is None:
                row["delivered_at"] = datetime.now(UTC)
                row["last_error"] = None
                return True
        return False

    def fail_alert_delivery(self, delivery_id, *, delay_seconds, error=None):
        for row in self.alert_deliveries:
            if row["delivery_id"] == delivery_id and row["delivered_at"] is None:
                row["last_error"] = error
                row["given_up"] = row["attempts"] >= row["max_attempts"]
                if not row["given_up"]:
                    row["next_attempt_at"] = (
                        datetime.now(UTC) + timedelta(seconds=max(0, delay_seconds)))
                return True
        return False

    def retry_alert_delivery(self, delivery_id, *, delay_seconds=0):
        for row in self.alert_deliveries:
            if row["delivery_id"] == delivery_id and row["delivered_at"] is None:
                row["attempts"] = 0
                row["given_up"] = False
                row["last_error"] = None
                row["next_attempt_at"] = (
                    datetime.now(UTC) + timedelta(seconds=max(0, delay_seconds)))
                return True
        return False

    def list_alert_deliveries(self, *, undelivered_only=False, limit=100):
        rows = [dict(row) for row in self.alert_deliveries
                if not undelivered_only or row["delivered_at"] is None]
        return rows[::-1][:limit]

    # ---- 保留期清理（P2-2）----

    _PURGE_TARGETS = frozenset({
        ("run_events", "created_at"),
        ("runs", "finished_at"),
        ("usage_ledger", "created_at"),
        ("moderation_records", "created_at"),
        ("moderation_appeals", "created_at"),
        ("audit_logs", "at"),
    })

    def _purge_rows(self, table):
        if table == "runs":
            return list(self.runs.values())
        if table == "run_events":
            return [event for events in self.events.values() for event in events]
        if table == "usage_ledger":
            return list(self.usage)
        if table == "moderation_records":
            return list(self.moderation)
        if table == "moderation_appeals":
            return list(self.appeals.values())
        if table == "audit_logs":
            return list(self.audits)
        raise ValueError(f"purge target not allowed: {table}")

    def _purge_eligible(self, table, row, time_column, before):
        stamp = row.get(time_column)
        if stamp is None or stamp >= before:
            return False
        if table == "runs" and row["status"] not in TERMINAL:
            return False
        return True

    def count_table(self, table):
        if table not in {name for name, _ in self._PURGE_TARGETS}:
            raise ValueError(f"count target not allowed: {table}")
        return len(self._purge_rows(table))

    def count_before(self, table, time_column, before, *, where_extra=""):
        if (table, time_column) not in self._PURGE_TARGETS:
            raise ValueError(f"purge target not allowed: {table}.{time_column}")
        return sum(1 for row in self._purge_rows(table)
                   if self._purge_eligible(table, row, time_column, before))

    def purge_before(self, table, time_column, before, *, where_extra="", batch=5000):
        if (table, time_column) not in self._PURGE_TARGETS:
            raise ValueError(f"purge target not allowed: {table}.{time_column}")
        eligible = [row for row in self._purge_rows(table)
                    if self._purge_eligible(table, row, time_column, before)][:batch]
        for row in eligible:
            if table == "runs":
                run_id = row["run_id"]
                self.runs.pop(run_id, None)
                self.events.pop(run_id, None)
                self.artifacts.pop(run_id, None)  # 级联语义（真实库为 FK CASCADE）
            elif table == "run_events":
                self.events[row["run_id"]].remove(row)
            elif table == "usage_ledger":
                self.usage.remove(row)
            elif table == "moderation_records":
                self.moderation.remove(row)
            elif table == "moderation_appeals":
                self.appeals.pop(row["appeal_id"], None)
            elif table == "audit_logs":
                self.audits.remove(row)
        return len(eligible)

    # ---- Worker 注册表（P1-3）----

    def register_worker(self, worker_id, *, version=None, hostname=None):
        now = datetime.now(UTC)
        row = self.workers.get(worker_id)
        if row is None:
            self.workers[worker_id] = {
                "worker_id": worker_id, "version": version, "hostname": hostname,
                "status": "active", "started_at": now, "last_heartbeat_at": now,
                "in_flight": 0, "current_run_id": None, "stopped_at": None,
            }
        else:
            row.update(version=version, hostname=hostname, status="active",
                       last_heartbeat_at=now, stopped_at=None)

    def heartbeat_worker(self, worker_id, *, in_flight=0, current_run_id=None,
                         status="active"):
        row = self.workers.get(worker_id)
        if row is None:
            return False
        row.update(last_heartbeat_at=datetime.now(UTC), in_flight=in_flight,
                   current_run_id=current_run_id, status=status)
        return True

    def mark_worker_status(self, worker_id, status):
        row = self.workers.get(worker_id)
        if row is None:
            return
        row["status"] = status
        if status == "stopped":
            row["stopped_at"] = datetime.now(UTC)

    def count_live_workers(self, *, within_seconds=90):
        cutoff = datetime.now(UTC) - timedelta(seconds=within_seconds)
        return sum(1 for row in self.workers.values()
                   if row["status"] == "active" and row["last_heartbeat_at"] > cutoff)

    def list_workers(self, limit=50):
        rows = sorted(self.workers.values(),
                      key=lambda row: row["last_heartbeat_at"], reverse=True)
        return [dict(row) for row in rows[:limit]]

    def purge_stale_workers(self, *, days=7):
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stale = [key for key, row in self.workers.items()
                 if row["status"] in ("stopped", "draining")
                 and row["last_heartbeat_at"] < cutoff]
        for key in stale:
            del self.workers[key]
        return len(stale)

    def purge_expired_sessions(self):
        now = datetime.now(UTC)
        expired = [key for key, value in self.sessions.items() if value["expires_at"] <= now]
        for key in expired:
            del self.sessions[key]
        return len(expired)

    # ---- 密码重置 token（P1-10）----

    def create_password_reset(self, token_hash, user_id, expires_at, *, created_by=None):
        for key in [k for k, v in self.password_resets.items()
                    if v["user_id"] == user_id and v["consumed_at"] is None]:
            del self.password_resets[key]
        self.password_resets[token_hash] = {
            "token_hash": token_hash, "user_id": user_id, "created_by": created_by,
            "created_at": datetime.now(UTC), "expires_at": expires_at, "consumed_at": None,
        }

    def consume_password_reset(self, token_hash):
        row = self.password_resets.get(token_hash)
        now = datetime.now(UTC)
        if row is None or row["consumed_at"] is not None or row["expires_at"] <= now:
            return None
        row["consumed_at"] = now
        return row["user_id"]

    def delete_password_resets(self, user_id):
        keys = [k for k, v in self.password_resets.items() if v["user_id"] == user_id]
        for key in keys:
            del self.password_resets[key]
        return len(keys)

    def purge_expired_password_resets(self):
        now = datetime.now(UTC)
        keys = [k for k, v in self.password_resets.items()
                if v["consumed_at"] is not None
                or v["expires_at"] < now - timedelta(days=1)]
        for key in keys:
            del self.password_resets[key]
        return len(keys)

    def has_recent_password_reset(self, user_id, within_seconds):
        now = datetime.now(UTC)
        return any(
            row["user_id"] == user_id and row["consumed_at"] is None
            and row["created_at"] > now - timedelta(seconds=within_seconds)
            for row in self.password_resets.values()
        )

    def complete_password_reset(self, token_hash, password_hash):
        user_id = self.consume_password_reset(token_hash)
        if user_id is None:
            return None
        row = self.users.get(user_id)
        if row is not None:
            row["password_hash"] = password_hash
        self.revoke_user_sessions(user_id)
        self.delete_password_resets(user_id)
        return user_id

    # ---- 报告只读分享（需求 26）----

    def _active_share(self, row):
        now = datetime.now(UTC)
        return row["revoked_at"] is None and (row["expires_at"] is None or row["expires_at"] > now)

    def create_report_share(self, share_id, token_hash, run_id, created_by, expires_at):
        for row in self.report_shares.values():
            if row["run_id"] == run_id and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(UTC)
        self.report_shares[token_hash] = {
            "share_id": share_id, "token_hash": token_hash, "run_id": run_id,
            "created_by": created_by, "created_at": datetime.now(UTC),
            "expires_at": expires_at, "revoked_at": None,
            "last_accessed_at": None, "access_count": 0,
        }

    def get_active_report_share(self, run_id):
        for row in reversed(list(self.report_shares.values())):
            if row["run_id"] == run_id and self._active_share(row):
                return dict(row)
        return None

    def resolve_report_share(self, token_hash):
        row = self.report_shares.get(token_hash)
        if row is None or not self._active_share(row):
            return None
        run = self.runs.get(row["run_id"])
        if run is None:
            return None
        result = dict(row)
        result["topic"] = run.get("topic")
        return result

    def revoke_report_share(self, run_id):
        changed = False
        for row in self.report_shares.values():
            if row["run_id"] == run_id and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(UTC)
                changed = True
        return changed

    def revoke_report_share_by_id(self, share_id):
        for row in self.report_shares.values():
            if row["share_id"] == share_id and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(UTC)
                return True
        return False

    def revoke_report_shares_for_user(self, user_id):
        count = 0
        for row in self.report_shares.values():
            if row["created_by"] == user_id and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(UTC)
                count += 1
        return count

    def touch_report_share(self, token_hash):
        row = self.report_shares.get(token_hash)
        if row is not None:
            row["last_accessed_at"] = datetime.now(UTC)
            row["access_count"] += 1

    def list_report_shares(self, *, active_only=False, limit=50):
        rows = [dict(row) for row in self.report_shares.values()
                if not active_only or self._active_share(row)]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)[:limit]

    def purge_expired_report_shares(self, keep_days=30):
        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        keys = [k for k, row in self.report_shares.items()
                if (row["revoked_at"] is not None and row["revoked_at"] < cutoff)
                or (row["expires_at"] is not None and row["expires_at"] < cutoff)]
        for key in keys:
            del self.report_shares[key]
        return len(keys)

    # ---- 用量账本（P1-4）----

    def record_usage(self, *, run_id=None, attempt=1, kind, provider="", model="", role="",
                     input_tokens=0, output_tokens=0, total_tokens=0,
                     cost_estimate_cny=0.0, cost_source="estimate", request_id=None,
                     detail=None):
        self.usage.append({
            "id": len(self.usage) + 1, "run_id": run_id, "attempt": attempt, "kind": kind,
            "provider": provider, "model": model, "role": role,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": total_tokens, "cost_estimate_cny": cost_estimate_cny,
            "cost_source": cost_source, "request_id": request_id, "detail": detail or {},
            "created_at": datetime.now(UTC),
        })
        return len(self.usage)

    def list_usage(self, *, run_id=None, limit=200):
        rows = [row for row in self.usage if run_id is None or row["run_id"] == run_id]
        return [dict(row) for row in rows[::-1][:limit]]

    def usage_summary(self, *, run_id=None, since=None):
        rows = [row for row in self.usage
                if (run_id is None or row["run_id"] == run_id)
                and (since is None or row["created_at"] >= since)]
        grouped: dict[tuple, dict] = {}
        for row in rows:
            key = (row["kind"], row["model"], row["cost_source"])
            item = grouped.setdefault(key, {
                "kind": row["kind"], "model": row["model"],
                "cost_source": row["cost_source"], "calls": 0, "tokens": 0, "cost_cny": 0.0,
            })
            item["calls"] += 1
            item["tokens"] += row["total_tokens"]
            item["cost_cny"] += float(row["cost_estimate_cny"])
        out = sorted(grouped.values(), key=lambda item: (item["kind"], item["model"]))
        for item in out:
            item["cost_cny"] = round(item["cost_cny"], 6)
        return {
            "rows": out,
            "calls": sum(item["calls"] for item in out),
            "tokens": sum(item["tokens"] for item in out),
            "cost_cny": round(sum(item["cost_cny"] for item in out), 6),
        }


def wait_terminal(store, run_id, timeout: float = 5.0) -> dict:
    """等待库内终局（内存终局先于落库的时序在 FakeStore 上同样成立）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = store.get_run(run_id)
        if row is not None and row["status"] in TERMINAL:
            return row
        time.sleep(0.01)
    raise AssertionError("run did not reach a terminal state in store")


def event_types(store, run_id) -> list[str]:
    return [item["event_type"] for item in store.get_events(run_id)]
