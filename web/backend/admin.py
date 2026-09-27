"""管理员 CLI（P4-A）：用户与邀请码管理。

不暴露 HTTP 管理面（L3-A 阶段够用；管理后台属 L3-B/P6）。用法：

    python -m web.backend.admin create-user --email a@b.c [--password ...]
    python -m web.backend.admin create-invite [--expires-days 7] [--created-by cli]
    python -m web.backend.admin list-invites
    python -m web.backend.admin reset-password --email a@b.c
    python -m web.backend.admin revoke-invite --code <邀请码>
    python -m web.backend.admin ban-user --email a@b.c
    python -m web.backend.admin unban-user --email a@b.c
    python -m web.backend.admin run-report --run-id <run_id>   # 人工复核原文（P0-4）
    python -m web.backend.admin deletion-list [--limit 50]     # 注销清理进度（P0-7）
    python -m web.backend.admin retry-deletion --request-id <id>
    python -m web.backend.admin process-deletions [--batch 5]  # 手工跑一批 outbox
    python -m web.backend.admin ingestion-list [--status ready] [--limit 50]
    python -m web.backend.admin delete-doc --doc-id <user:hash16>  # 同步删向量并验证
    python -m web.backend.admin audit-list [--action login_failed] [--actor u] [--limit 100]
    python -m web.backend.admin create-reset-token --email a@b.c [--expires-minutes 30]
    python -m web.backend.admin usage-summary [--run-id r] [--days 30]

需要 `DR_DATABASE_URL`。邀请码 / 临时密码**只在创建时打印一次**（库内只存摘要）。
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta

from .auth import hash_password, new_invite_code, normalize_email, token_hash
from .store import RunStore


def _store() -> RunStore:
    dsn = (os.getenv("DR_DATABASE_URL") or "").strip()
    if not dsn:
        print("需要 DR_DATABASE_URL（任务库）", file=sys.stderr)
        raise SystemExit(2)
    return RunStore(dsn)


def _audit(store: RunStore, action: str, **detail) -> None:
    """P1-5：管理动作审计（best-effort，不打断 CLI 主流程）。"""
    try:
        store.record_audit(action, detail={"via": "cli", **detail})
    except Exception:  # noqa: BLE001
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="web.backend.admin", description="DeepResearch 管理员 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create_user = sub.add_parser("create-user", help="创建用户（不消耗邀请码）")
    create_user.add_argument("--email", required=True)
    create_user.add_argument("--password", default="", help="不传则生成随机临时密码并打印一次")

    create_invite = sub.add_parser("create-invite", help="生成一次性邀请码")
    create_invite.add_argument("--expires-days", type=int, default=7, help="0 = 不过期")
    create_invite.add_argument("--created-by", default="cli")

    sub.add_parser("list-invites", help="列出邀请码摘要与状态")

    moderation = sub.add_parser("moderation-list", help="列出内容审核 / 申诉记录（P7-A）")
    moderation.add_argument("--kind", default="", help="过滤：input_blocked / output_flagged / appeal / admin_action")
    moderation.add_argument("--limit", type=int, default=50)

    delete_user = sub.add_parser("delete-user", help="管理员删除用户（P7-A 注销；RAG 向量需另行清理）")
    delete_user.add_argument("--email", required=True)

    run_report = sub.add_parser("run-report", help="查看某 run 的报告原文（人工复核用；P0-4）")
    run_report.add_argument("--run-id", required=True)

    deletion_list = sub.add_parser("deletion-list", help="注销清理台账与 outbox 进度（P0-7）")
    deletion_list.add_argument("--limit", type=int, default=50)

    retry_deletion = sub.add_parser("retry-deletion", help="人工重试被放弃的注销清理（P0-7）")
    retry_deletion.add_argument("--request-id", required=True)

    process_deletions = sub.add_parser("process-deletions", help="手工执行一批注销 outbox（P0-7）")
    process_deletions.add_argument("--batch", type=int, default=5)

    ingestion_list = sub.add_parser("ingestion-list", help="列出 RAG 摄取台账（P0-8b）")
    ingestion_list.add_argument("--status", default="", help="pending / processing / ready / rejected / deleted")
    ingestion_list.add_argument("--limit", type=int, default=50)

    delete_doc = sub.add_parser("delete-doc", help="按 doc_id 删除知识库文档（P0-8b；同步 + 验证）")
    delete_doc.add_argument("--doc-id", required=True)

    audit_list = sub.add_parser("audit-list", help="查看安全审计日志（P1-5）")
    audit_list.add_argument("--action", default="")
    audit_list.add_argument("--actor", default="")
    audit_list.add_argument("--limit", type=int, default=100)

    reset_token = sub.add_parser("create-reset-token",
                                 help="发放一次性密码重置 token（P1-10；默认 30 分钟）")
    reset_token.add_argument("--email", required=True)
    reset_token.add_argument("--expires-minutes", type=int, default=30)

    usage = sub.add_parser("usage-summary", help="用量账本汇总（P1-4；先对请求数再对钱）")
    usage.add_argument("--run-id", default="")
    usage.add_argument("--days", type=int, default=30)

    reset = sub.add_parser("reset-password", help="管理员重置密码（无邮件通道；临时密码打印一次）")
    reset.add_argument("--email", required=True)

    revoke = sub.add_parser("revoke-invite", help="撤销未使用的邀请码")
    revoke.add_argument("--code", required=True)

    for name in ("ban-user", "unban-user"):
        item = sub.add_parser(name, help=f"{name} --email a@b.c")
        item.add_argument("--email", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = _store()

    if args.command == "create-user":
        email = normalize_email(args.email)
        password = args.password or secrets.token_urlsafe(12)
        user_id = uuid.uuid4().hex[:12]
        store.create_user(user_id, email, hash_password(password))
        _audit(store, "admin_create_user", target_id=user_id, detail={"email": email})
        print(f"created user_id={user_id} email={email}")
        if not args.password:
            print(f"临时密码（仅本次打印）：{password}")
        return 0

    if args.command == "create-invite":
        code = new_invite_code()
        expires_at = (
            datetime.now(UTC) + timedelta(days=args.expires_days)
            if args.expires_days > 0 else None
        )
        store.create_invite(token_hash(code), created_by=args.created_by, expires_at=expires_at)
        _audit(store, "admin_create_invite", detail={"expires_days": args.expires_days})
        print(f"邀请码（仅本次打印）：{code}")
        return 0

    if args.command == "list-invites":
        for row in store.list_invites():
            state = "used" if row["used_at"] else ("revoked" if row["revoked_at"] else "unused")
            expires = row["expires_at"].isoformat() if row["expires_at"] else "never"
            print(f"{row['code_hash'][:12]}...  {state}  expires={expires}")
        return 0

    if args.command == "reset-password":
        user = store.get_user_by_email(normalize_email(args.email))
        if user is None:
            print("user not found", file=sys.stderr)
            return 1
        password = secrets.token_urlsafe(12)
        store.update_password(user["user_id"], hash_password(password))
        revoked = store.revoke_user_sessions(user["user_id"])
        _audit(store, "admin_reset_password", actor_user_id=user["user_id"],
               detail={"revoked_sessions": revoked})
        print(f"已重置 {user['email']}；吊销 {revoked} 个会话")
        print(f"临时密码（仅本次打印）：{password}")
        return 0

    if args.command == "moderation-list":
        rows = store.list_moderation(kind=args.kind or None, limit=args.limit)
        for row in rows:
            print(f"#{row['id']}  {row['kind']:<15} user={row['user_id'] or '-'} run={row['run_id'] or '-'} "
                  f"at={row['created_at']:%Y-%m-%d %H:%M}  detail={row['detail']}")
        return 0

    if args.command == "run-report":
        row = store.get_run(args.run_id)
        if row is None:
            print("run not found", file=sys.stderr)
            return 1
        body = store.get_artifact(args.run_id, "report_md")
        if not body:
            print("no report artifact", file=sys.stderr)
            return 1
        status = row.get("moderation_status") or "-"
        print(f"# run={args.run_id} status={row['status']} moderation={status}", file=sys.stderr)
        print(body)
        return 0

    if args.command == "deletion-list":
        for row in store.list_deletions(limit=args.limit):
            print(f"{row['request_id']}  {row['status']:<10} user={row['user_id']} "
                  f"attempts={row['attempts']} requested={row['requested_at']:%Y-%m-%d %H:%M} "
                  f"error={row['last_error'] or '-'}")
        return 0

    if args.command == "retry-deletion":
        count = store.retry_deletion(args.request_id)
        print(f"reset {count} outbox entr{'y' if count == 1 else 'ies'}")
        return 0 if count else 1

    if args.command == "process-deletions":
        from .deletion import process_deletions_once

        summary = process_deletions_once(store, batch=args.batch)
        print(f"deletions: {summary}")
        return 0

    if args.command == "ingestion-list":
        rows = store.list_ingestions(status=args.status or None, limit=args.limit)
        for row in rows:
            print(f"{row['ingestion_id']}  {row['status']:<10} doc={row['doc_id']} "
                  f"user={row['user_id'] or '-'} chunks={row['chunks']} "
                  f"attempts={row['attempts']} scan={row['scan_status']} "
                  f"error={row['last_error'] or '-'}")
        return 0

    if args.command == "delete-doc":
        from research_engine.rag.store import VectorStore

        from .ingestion import quarantine_path

        vector_store = VectorStore()
        reason = vector_store.unavailable_reason
        if reason:
            print(f"qdrant unavailable: {reason}", file=sys.stderr)
            return 1
        vector_store.delete_by_doc(args.doc_id, wait=True)
        remaining = vector_store.count_by_doc(args.doc_id)
        if remaining:
            print(f"delete incomplete: {remaining} points remain", file=sys.stderr)
            return 1
        cleaned = 0
        for row in store.list_ingestions(limit=1000):
            if row["doc_id"] != args.doc_id or row["status"] == "deleted":
                continue
            if row["stored_name"]:
                try:
                    os.remove(quarantine_path(row["stored_name"]))
                except FileNotFoundError:
                    pass
                cleaned += 1
            store.mark_ingestion_deleted(row["ingestion_id"], reason="admin")
        print(f"deleted doc {args.doc_id}（清理隔离区文件 {cleaned} 个）")
        return 0

    if args.command == "audit-list":
        rows = store.list_audit(action=args.action or None,
                                actor_user_id=args.actor or None, limit=args.limit)
        for row in rows:
            print(f"#{row['id']} {row['at']:%Y-%m-%d %H:%M:%S} {row['action']:<28} "
                  f"actor={row['actor_user_id'] or '-'} ip={row['ip'] or '-'} "
                  f"req={row['request_id'] or '-'} detail={row['detail']}")
        return 0

    if args.command == "create-reset-token":
        user = store.get_user_by_email(normalize_email(args.email))
        if user is None:
            print("user not found", file=sys.stderr)
            return 1
        token = secrets.token_urlsafe(32)
        store.create_password_reset(
            token_hash(token), user["user_id"],
            datetime.now(UTC) + timedelta(minutes=args.expires_minutes))
        _audit(store, "admin_create_reset_token", actor_user_id=user["user_id"])
        print(f"重置 token（仅本次打印，{args.expires_minutes} 分钟内有效）：{token}")
        print("用户调用 POST /api/auth/reset {token, new_password} 完成重置（将吊销全部会话）")
        return 0

    if args.command == "usage-summary":
        since = datetime.now(UTC) - timedelta(days=args.days)
        summary = store.usage_summary(run_id=args.run_id or None, since=since)
        for row in summary["rows"]:
            print(f"{row['kind']:<10} {row['model'] or '-':<22} calls={row['calls']} "
                  f"tokens={row['tokens']} cost≈¥{row['cost_cny']:.4f} ({row['cost_source']})")
        print(f"TOTAL calls={summary['calls']} tokens={summary['tokens']} "
              f"cost≈¥{summary['cost_cny']:.4f}")
        return 0

    if args.command == "delete-user":
        user = store.get_user_by_email(normalize_email(args.email))
        if user is None:
            print("user not found", file=sys.stderr)
            return 1
        request_id = uuid.uuid4().hex[:12]
        store.request_account_deletion(request_id, user["user_id"])
        _audit(store, "admin_delete_user", actor_user_id=user["user_id"],
               target_id=request_id)
        print(f"deleted {args.email}（deletion_request_id={request_id}；"
              f"任务与审核记录匿名保留；RAG 清理由 Worker 重试执行，`deletion-list` 查看）")
        return 0

    if args.command == "revoke-invite":
        ok = store.revoke_invite(token_hash(args.code.strip()))
        if ok:
            _audit(store, "admin_revoke_invite")
        print("revoked" if ok else "not found / already used / already revoked")
        return 0 if ok else 1

    if args.command in ("ban-user", "unban-user"):
        user = store.get_user_by_email(normalize_email(args.email))
        if user is None:
            print("user not found", file=sys.stderr)
            return 1
        status = "banned" if args.command == "ban-user" else "active"
        ok = store.set_user_status(user["user_id"], status)
        if ok:
            _audit(store, f"admin_{args.command}", actor_user_id=user["user_id"])
        print(f"{status} {user['user_id']}" if ok else "failed")
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
