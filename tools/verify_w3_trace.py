"""W3 真实冒烟验证：从 CLI 日志提取 trace_id → Langfuse API 核对落库结构与 usage。"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langfuse import Langfuse  # noqa: E402

from config import config  # noqa: E402

LOG = sys.argv[1] if len(sys.argv) > 1 else None


def _resolve_log() -> str:
    if LOG:
        return LOG
    for cand in (
        "/tmp/w3_smoke_full.log",
        os.path.join(os.environ.get("TEMP", ""), "w3_smoke_full.log"),
        os.path.join(os.environ.get("TMP", ""), "w3_smoke_full.log"),
    ):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError("未找到冒烟日志，请传入路径: python tools/verify_w3_trace.py <log>")


def main() -> int:
    log = open(_resolve_log(), encoding="utf-8").read()

    # 1) 从 CLI 输出提取 trace URL / token 数
    url_m = re.search(r"Trace URL: (\S+)", log)
    tok_m = re.search(r"消耗 ([\d,]+) tokens", log)
    print("== CLI 输出解析 ==")
    print("trace_url:", url_m.group(1) if url_m else "（未找到，可能观测未初始化）")
    print("token_used(本地):", tok_m.group(1) if tok_m else "（未找到）")
    trace_id = None
    if url_m:
        trace_id = url_m.group(1).rstrip("/").split("/")[-1]

    # 2) Langfuse API 核对 trace 落库
    lf = Langfuse(
        public_key=config.langfuse.public_key,
        secret_key=config.langfuse.secret_key,
        host=config.langfuse.host,
    )
    if not trace_id:
        print("== 无 trace_id 可验证 ==")
        return 1
    try:
        t = lf.api.trace.get(traceId=trace_id)
    except TypeError:
        t = lf.api.trace.get(trace_id=trace_id)
    if hasattr(t, "model_dump"):  # pydantic 模型 → dict
        t = t.model_dump()
    print("\n== Langfuse API 落库核对 ==")
    print("trace name:", t.get("name"))
    print("status:", t.get("status"))
    obs = t.get("observations", []) or []
    spans = [o for o in obs if o.get("type") == "SPAN"]
    gens = [o for o in obs if o.get("type") == "GENERATION"]
    print(f"观测点总数: {len(obs)} | span: {len(spans)} | generation: {len(gens)}")
    names = [o.get("name") for o in spans]
    print("span 名称序列:", names)
    usage_total = sum((g.get("usage") or {}).get("total", 0) for g in gens)
    print("generation usage.total 合计:", usage_total)
    # 3) 与本地对账（Q3 D'）
    local_tokens = int(tok_m.group(1).replace(",", "")) if tok_m else None
    if local_tokens is not None:
        print(f"对账: 本地 token_used={local_tokens} vs Langfuse usage合计={usage_total} 差值={local_tokens - usage_total}")
    # 4) 关键 span 断言
    checks = {
        "含 规划 span": any(n == "规划" for n in names),
        "含 R?-检索 span": any(bool(re.match(r"^R\d+-检索$", n)) for n in names),
        "含 R?-裁决 span": any(bool(re.match(r"^R\d+-裁决$", n)) for n in names),
        "含 写作/校验/渲染": all(any(n == x for n in names) for x in ["写作", "校验", "渲染"]),
        "generation > 0": len(gens) > 0,
    }
    for k, v in checks.items():
        print(("✅" if v else "❌"), k)
    ok = all(checks.values())
    print("\n结论:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())