"""测试用内存替身：FakeStore（RunStore 语义子集）与 TinyGraph（零 LLM 假图）。

供 `test_web_persistence.py`（P2-C 接线）与 `test_worker.py`（P3-A Worker）共用；
真实 PostgreSQL / Redis 的集成测试见 `test_run_store.py` 与 `test_worker_integration.py`。
"""
from __future__ import annotations

import time
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
        self.deletions: dict[str, dict] = {}
        self.deletion_outbox: dict[tuple, dict] = {}
        self._deletion_outbox_seq = 0
        self.ingestions: dict[str, dict] = {}
        self.audits: list[dict] = []
        self.fail_events = False

    def ping(self) -> None:
        return None

    def create_run(self, run_id, topic, request=None, *, user_id=None, tenant_id=None,
                   idempotency_key=None, request_hash=None, status="CREATED", timeout_at=None,
                   budget_limit_cny=None):
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
            "budget_used_cny": 0.0, "attempt": 1, "retry_of": None,
            "idempotency_key": idempotency_key, "request_hash": request_hash,
            "worker_id": None, "worker_status": None,
            "lease_expires_at": None, "timeout_at": timeout_at, "hard_deadline_at": None,
            "cancel_requested_at": None, "created_at": now,
            "queued_at": now if status == "QUEUED" else None,
            "started_at": None, "finished_at": None, "moderation_status": None,
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
                     allowed_from, fields=None, artifacts=None):
        """与 RunStore.finalize_run 同语义：迁移失败 ⇒ 不写事件 / 产物（P0-6）。"""
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
        for kind, body in (artifacts or {}).items():
            self.artifacts.setdefault(run_id, {})[kind] = body
        return True

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def create_run_admitted(self, run_id, topic, request=None, *, user_id=None, tenant_id=None,
                            idempotency_key=None, request_hash=None, status="CREATED",
                            timeout_at=None, budget_limit_cny=None, global_active_limit=None,
                            user_active_limit=None, daily_limit=None,
                            monthly_budget_cny=None, daily_since=None):
        """与 RunStore.create_run_admitted 同语义（P0-3；内存版无并发竞争）。"""
        if idempotency_key is not None:
            existing = self.get_run_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                return existing, False
        if monthly_budget_cny is not None and monthly_budget_cny > 0:
            spent = self.month_cost_cny()
            if spent >= monthly_budget_cny:
                raise QuotaExceeded("monthly_budget", f"spent={spent:.4f}; limit={monthly_budget_cny}")
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
        return self.create_run(run_id, topic, request, user_id=user_id, tenant_id=tenant_id,
                               idempotency_key=idempotency_key, request_hash=request_hash,
                               status=status, timeout_at=timeout_at,
                               budget_limit_cny=budget_limit_cny)

    def get_run_by_idempotency(self, user_id, idempotency_key):
        for row in self.runs.values():
            if row["user_id"] == user_id and row["idempotency_key"] == idempotency_key:
                return row
        return None

    def list_runs(self, *, user_id=None, statuses=None, limit=20, offset=0):
        rows = [r for r in self.runs.values()
                if (user_id is None or r["user_id"] == user_id)
                and (not statuses or r["status"] in statuses)]
        rows.sort(key=lambda r: (r["created_at"], r["run_id"]), reverse=True)
        out = []
        for row in rows[offset:offset + limit]:
            item = dict(row)
            item["has_report"] = "report_md" in self.artifacts.get(row["run_id"], {})
            out.append(item)
        return out

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
        self.artifacts.setdefault(run_id, {})[kind] = body

    def get_artifact(self, run_id, kind):
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

    def create_session(self, session_hash, user_id, expires_at):
        self.sessions[session_hash] = {
            "token_hash": session_hash, "user_id": user_id,
            "created_at": datetime.now(UTC), "expires_at": expires_at,
        }

    def get_session_user(self, session_hash):
        session = self.sessions.get(session_hash)
        if session is None or session["expires_at"] < datetime.now(UTC):
            return None
        user = self.users.get(session["user_id"])
        if user is None or user["status"] != "active":
            return None
        return user

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
        now = datetime.now(UTC)
        row = {
            "ingestion_id": ingestion_id, "doc_id": doc_id, "user_id": user_id,
            "source": source, "sha256": sha256, "size_bytes": size_bytes,
            "stored_name": stored_name, "status": "pending", "chunks": 0,
            "attempts": 0, "next_attempt_at": now, "lease_expires_at": None,
            "claimed_by": None, "scan_status": "skipped", "last_error": None,
            "created_at": now, "updated_at": now, "processed_at": None,
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

    def purge_expired_sessions(self):
        now = datetime.now(UTC)
        expired = [key for key, value in self.sessions.items() if value["expires_at"] <= now]
        for key in expired:
            del self.sessions[key]
        return len(expired)


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
