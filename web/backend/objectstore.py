"""对象存储（P1-6）：报告 / 导出迁 S3 兼容存储。

- 未配置 `DR_S3_ENDPOINT` ⇒ 完全关闭，产物回落 PostgreSQL（双轨可切换）；
- 配置后：写入前先 put 对象（计算 sha256），元数据（key/hash/size）随终局事务落库；
  `runs/` 前缀设置生命周期（`DR_S3_REPORT_RETENTION_DAYS`，默认 90 天，与隐私政策对齐）；
- 桶为私有；读取走应用鉴权（P0-4 flagged 闸在读取前执行），不暴露直链；
- S3 写入失败 ⇒ 调用方回落到 PG（报告仍可用），失败原因打印到 stderr。

供应商兼容性（2026-09-30 核对官方文档所得，不是猜测）：

| 供应商 | 签名 | 寻址风格 | 桶名 |
| --- | --- | --- | --- |
| MinIO（本地 / compose） | V4（boto3 默认） | path（auto 自动选） | 任意合法名 |
| 腾讯云 COS | V4（boto3 默认） | auto；同地域用内网域名免流量费 | **必须带 appid 后缀**（`bucket-125xxxxxxx`） |
| 阿里云 OSS | **必须 `s3`（V2）** | **必须 `virtual`** | 不带 appid |

🚨 阿里云 OSS 的 V2 是硬要求：boto3 的 V4 实现与 `Transfer-Encoding: chunked` 强耦合，
OSS 不接受 ⇒ 默认配置会得到 `SignatureDoesNotMatch`。改这两个值只需设环境变量
`DR_S3_SIGNATURE_VERSION` / `DR_S3_ADDRESSING_STYLE`，不必改代码。
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
        # 签名与寻址风格（见模块 docstring 的兼容性表）：MinIO / COS 保持默认即可，
        # 阿里云 OSS 需 DR_S3_SIGNATURE_VERSION=s3 + DR_S3_ADDRESSING_STYLE=virtual。
        self._signature_version = (os.getenv("DR_S3_SIGNATURE_VERSION") or "s3v4").strip()
        self._addressing_style = (os.getenv("DR_S3_ADDRESSING_STYLE") or "auto").strip()

    def _s3(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            # 显式指定而不是依赖 boto3 默认值：默认值随 botocore 版本漂移，
            # 而「签名版本不对」的表现是偶发 SignatureDoesNotMatch，极难定位。
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name=self.region,
                config=Config(
                    signature_version=self._signature_version,
                    s3={"addressing_style": self._addressing_style},
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client

    def ensure_bucket(self, retention_days: Optional[int] = None) -> Dict[str, Any]:
        """幂等：建桶 + `runs/` 前缀生命周期（到期自动删除）。

        返回结构化结果而**不向上抛异常**。原因：`main.py` 的启动块用 `try/except: pass`
        包住本方法，一旦整体抛出，生命周期设置失败会被完全静默 ——
        表现为「服务正常启动、日志干净，但 90 天保留期根本没生效」，与隐私政策承诺冲突。

        因此这里把「建桶」与「生命周期」两步分别捕获，失败写入 `warnings`，
        由调用方显式打印（运维可据此在控制台手动补配过期规则）。
        """
        result: Dict[str, Any] = {"bucket_ready": False, "lifecycle_applied": None,
                                  "warnings": []}
        client = self._s3()
        try:
            existing = {item["Name"] for item in client.list_buckets().get("Buckets", [])}
            if self.bucket not in existing:
                client.create_bucket(Bucket=self.bucket)
            result["bucket_ready"] = True
        except Exception as exc:  # noqa: BLE001 —— 降级回落 PG，不阻断启动
            warning = (f"桶不可用（{type(exc).__name__}: {exc}）：报告将回落 PostgreSQL；"
                       f"请核对 DR_S3_ENDPOINT / DR_S3_BUCKET 与密钥"
                       f"（腾讯云 COS 桶名必须带 appid 后缀）")
            result["warnings"].append(warning)
            _log(warning)
            return result

        days = (retention_days if retention_days is not None
                else int(os.getenv("DR_S3_REPORT_RETENTION_DAYS",
                                   str(DEFAULT_RETENTION_DAYS))))
        try:
            client.put_bucket_lifecycle_configuration(
                Bucket=self.bucket,
                LifecycleConfiguration={"Rules": [{
                    "ID": "expire-runs",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "runs/"},
                    "Expiration": {"Days": max(1, days)},
                }]},
            )
            result["lifecycle_applied"] = True
        except Exception as exc:  # noqa: BLE001 —— 保留期不生效必须留痕，不能静默
            result["lifecycle_applied"] = False
            warning = (f"`runs/` 生命周期设置失败（{type(exc).__name__}: {exc}）：报告不会在 "
                       f"{days} 天后自动删除，请在对象存储控制台手动配置该前缀的过期规则")
            result["warnings"].append(warning)
            _log(warning)
        return result

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
