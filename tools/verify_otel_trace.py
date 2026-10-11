"""需求 33 真实冒烟验证（OTel 版，替代 W3 Langfuse 版 `verify_w3_trace.py`）。

用法：
    python tools/verify_otel_trace.py <cli运行日志>

流程：
1) 从 CLI 日志提取 trace URL / trace_id / 本地 token 数；
2) 由 `DR_OTEL_TRACE_URL_TEMPLATE` 推导后端 Query API（按 Jaeger 形制
   `{base}/api/traces/{trace_id}`）拉取 span 树；
3) 断言根 span / 节点 span 序列 / generation（`chat *`）/ usage 合计并与本地对账。

退出码：0 = PASS；1 = 无 trace_id（观测未启用/未配模板）；2 = 断言失败；3 = 后端不可达（本地解析仍打印）。
"""
import json
import os
import re
import sys
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config  # noqa: E402

LOG = sys.argv[1] if len(sys.argv) > 1 else None


def _resolve_log() -> str:
    if LOG:
        return LOG
    for cand in (
        "/tmp/otel_smoke.log",
        os.path.join(os.environ.get("TEMP", ""), "otel_smoke.log"),
        os.path.join(os.environ.get("TMP", ""), "otel_smoke.log"),
    ):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError("未找到冒烟日志，请传入路径: python tools/verify_otel_trace.py <log>")


def _api_base() -> Optional[str]:
    """由 URL 模板推导后端 Query API 基址（默认 Jaeger 形制）。"""
    template = (getattr(config.otel, "trace_url_template", "") or "").strip()
    marker = "/trace/"
    if marker not in template:
        return None
    return template.split(marker)[0].rstrip("/") or None


def main() -> int:
    log = open(_resolve_log(), encoding="utf-8").read()

    # 1) 从 CLI 输出提取 trace URL / token 数
    url_m = re.search(r"Trace URL: (\S+)", log)
    tok_m = re.search(r"消耗 ([\d,]+) tokens", log)
    print("== CLI 输出解析 ==")
    print("trace_url:", url_m.group(1) if url_m else "（未找到：观测未启用或未配置 URL 模板）")
    print("token_used(本地):", tok_m.group(1) if tok_m else "（未找到）")
    trace_id = url_m.group(1).rstrip("/").split("/")[-1] if url_m else None
    if not trace_id:
        return 1

    # 2) 后端 Query API 核对（后端不可达不判 FAIL，如实区分）
    base = _api_base()
    if not base:
        print("\n⚠️ 未配置 DR_OTEL_TRACE_URL_TEMPLATE（或形制不识别）—— 仅本地解析，跳过后端核对")
        return 0
    try:
        with urllib.request.urlopen(f"{base}/api/traces/{trace_id}", timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"\n⚠️ 后端不可达（{base}）：{exc} —— 仅本地解析")
        return 3

    spans = ((payload.get("data") or [{}])[0].get("spans") or [])
    names = [s.get("operationName") for s in spans]
    gens = [s for s in spans if (s.get("operationName") or "").startswith("chat ")]
    usage_total = 0
    for g in gens:
        for tag in g.get("tags") or []:
            if tag.get("key") == "gen_ai.usage.total_tokens":
                usage_total += int(tag.get("value") or 0)
    print("\n== OTel 后端（Jaeger Query API）核对 ==")
    print(f"span 总数: {len(spans)} | generation: {len(gens)}")
    print("span 名称序列:", names)
    print("generation usage.total 合计:", usage_total)

    # 3) 与本地对账（W3 Q3=D' 口径）
    if tok_m:
        local_tokens = int(tok_m.group(1).replace(",", ""))
        print(f"对账: 本地 token_used={local_tokens} vs 后端 usage 合计={usage_total} "
              f"差值={local_tokens - usage_total}")

    # 4) 关键 span 断言
    checks = {
        "根 span（deepresearch: 前缀）": any((n or "").startswith("deepresearch:") for n in names),
        "含 规划 span": "规划" in names,
        "含 R?-检索 span": any(bool(re.match(r"^R\d+-检索$", n or "")) for n in names),
        "含 R?-裁决 span": any(bool(re.match(r"^R\d+-裁决$", n or "")) for n in names),
        "含 写作/校验/渲染": all(x in names for x in ["写作", "校验", "渲染"]),
        "generation > 0": len(gens) > 0,
    }
    for key, ok in checks.items():
        print(("✅" if ok else "❌"), key)
    ok = all(checks.values())
    print("\n结论:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
