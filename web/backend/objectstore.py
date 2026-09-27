"""对象存储（P1-6）：报告 / 导出迁 S3 兼容存储（MinIO 自托管）。

- 未配置 `DR_S3_ENDPOINT` ⇒ 完全关闭，产物回落 PostgreSQL（双轨可切换）；
- 配置后：写入前先 put 对象（计算 sha256），元数据（key/hash/size）随终局事务落库；
  `runs/` 前缀设置生命周期（`DR_S3_REPORT_RETENTION_DAYS`，默认 90 天，与隐私政策对齐）；
- 桶为私有；读取走应用鉴权（P0-4 flagged 闸在读取前执行），不暴露直链；
- S3 写入失败 ⇒ 调用方回落到 PG（报告仍可用），失败原因打印到 stderr。
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Any, Dict, Optional

DEFAULT_BUCKET = "deepresearch"
#: 与隐私政策「报告与运行元数据 90 天」对齐（需求 10 §3.1 推荐基线）
DEFAULT_RETENTION_DAYS = 90


def _log(message: str) -> None:
    print(f"[objectstore] {message}", file=sys.stderr, flush=True)


class ObjectStore:
    """S3 兼容客户端（boto3 懒加载；只在配置后才实例化）。"""

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str,
                 bucket: str, region: str = "us-east-1"):
        self.endpoint = endpoint
        self.bucket = bucket
        self.region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._client: Optional[Any] = None

    def _s3(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name=self.region,
            )
        return self._client

    def ensure_bucket(self, retention_days: Optional[int] = None) -> None:
        """幂等：建桶 + `runs/` 前缀生命周期（到期自动删除）。"""
        client = self._s3()
        existing = {item["Name"] for item in client.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            client.create_bucket(Bucket=self.bucket)
        days = (retention_days if retention_days is not None
                else int(os.getenv("DR_S3_REPORT_RETENTION_DAYS",
                                   str(DEFAULT_RETENTION_DAYS))))
        client.put_bucket_lifecycle_configuration(
            Bucket=self.bucket,
            LifecycleConfiguration={"Rules": [{
                "ID": "expire-runs",
                "Status": "Enabled",
                "Filter": {"Prefix": "runs/"},
                "Expiration": {"Days": max(1, days)},
            }]},
        )

    def put_text(self, key: str, body: str, *,
                 content_type: str = "text/plain; charset=utf-8") -> Dict[str, Any]:
        data = body.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        self._s3().put_object(Bucket=self.bucket, Key=key, Body=data,
                              ContentType=content_type)
        return {"key": key, "sha256": digest, "size_bytes": len(data)}

    def get_text(self, key: str) -> str:
        response = self._s3().get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read().decode("utf-8")

    def ping(self) -> bool:
        self._s3().head_bucket(Bucket=self.bucket)
        return True


_store: Optional[ObjectStore] = None
_initialized = False


def get_object_store() -> Optional[ObjectStore]:
    """按环境构造单例；未配置 `DR_S3_ENDPOINT` 返回 None（回落 PG）。"""
    global _store, _initialized
    if not _initialized:
        _initialized = True
        endpoint = (os.getenv("DR_S3_ENDPOINT") or "").strip()
        if endpoint:
            _store = ObjectStore(
                endpoint=endpoint,
                access_key=(os.getenv("DR_S3_ACCESS_KEY") or "").strip(),
                secret_key=(os.getenv("DR_S3_SECRET_KEY") or "").strip(),
                bucket=(os.getenv("DR_S3_BUCKET") or DEFAULT_BUCKET).strip(),
                region=(os.getenv("DR_S3_REGION") or "us-east-1").strip(),
            )
    return _store


def reset_object_store() -> None:
    """重置单例（测试 / 配置热切换用）。"""
    global _store, _initialized
    _store = None
    _initialized = False
