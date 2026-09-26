"""管理员 CLI（P4-A）：用户与邀请码管理。

不暴露 HTTP 管理面（L3-A 阶段够用；管理后台属 L3-B/P6）。用法：

    python -m web.backend.admin create-user --email a@b.c [--password ...]
    python -m web.backend.admin create-invite [--expires-days 7] [--created-by cli]
    python -m web.backend.admin list-invites
    python -m web.backend.admin revoke-invite --code <邀请码>
    python -m web.backend.admin ban-user --email a@b.c
    python -m web.backend.admin unban-user --email a@b.c

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
        print(f"邀请码（仅本次打印）：{code}")
        return 0

    if args.command == "list-invites":
        for row in store.list_invites():
            state = "used" if row["used_at"] else ("revoked" if row["revoked_at"] else "unused")
            expires = row["expires_at"].isoformat() if row["expires_at"] else "never"
            print(f"{row['code_hash'][:12]}...  {state}  expires={expires}")
        return 0

    if args.command == "revoke-invite":
        ok = store.revoke_invite(token_hash(args.code.strip()))
        print("revoked" if ok else "not found / already used / already revoked")
        return 0 if ok else 1

    if args.command in ("ban-user", "unban-user"):
        user = store.get_user_by_email(normalize_email(args.email))
        if user is None:
            print("user not found", file=sys.stderr)
            return 1
        status = "banned" if args.command == "ban-user" else "active"
        ok = store.set_user_status(user["user_id"], status)
        print(f"{status} {user['user_id']}" if ok else "failed")
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
