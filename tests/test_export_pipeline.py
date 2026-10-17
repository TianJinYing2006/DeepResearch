"""需求 38 F1：导出装配模型与双路径统一单测（零网络 / 零 PG；store 全注入）。

覆盖：
- `ExportDocument` 装配（内存载荷 / store export_json 优先 / 历史降级显式不可用）；
- `render_markdown` 口径（与既有导出一致 + 显式不可用标注 + 空清单省略）；
- store 导出路径与内存路径**同装配同渲染**（含端点级）；
- 分享链路：仅正文（内存/store 来源一致；不扩大披露——不含审计头/引用清单）；
- D-19：store 路径只读（read-only fake 可证明无写操作）。
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.export import (
    EXPORT_DOCUMENT_SCHEMA_VERSION,
    build_export_payload,
    document_from_payload,
    document_from_store,
    render_markdown,
)

RID = "run-38f1"


def _payload() -> dict:
    return build_export_payload(
        run_id=RID,
        topic="导出装配主题",
        meta={"run_status": "success", "stop_reason": "completed", "cancelled": False,
              "token_used": 123, "cost_estimate_cny": 0.0021, "elapsed_seconds": 12.5,
              "degradation_count": 1, "depth": 2},
        result={
            "report": "# 报告\n\n正文段落。",
            "citations": [{"claim": "论断一", "source": "https://e.com/a", "source_type": "web",
                           "confidence": 0.9, "existence": True, "supported": True}],
            "visited_sources": ["https://e.com/a", "https://e.com/b"],
        },
        egress={"search": {"provider": "bocha"}},
    )


def _row(**overrides) -> dict:
    created = datetime(2026, 10, 14, 8, 0, 0, tzinfo=UTC)
    row = {
        "run_id": RID,
        "topic": "导出装配主题",
        "finished_at": created + timedelta(seconds=30),
        "created_at": created,
        "research_status": "success",
        "stop_reason": "completed",
        "token_used": 123,
        "cost_estimate_cny": 0.0021,
        "moderation_status": None,
        "request": {"egress": {"search": {"provider": "bocha"}}},
    }
    row.update(overrides)
    return row


class _FakeStore:
    """只读 fake（无任何写方法 ⇒ 若导出链路尝试写库将直接 AttributeError）。"""

    def __init__(self, row: dict, artifacts: dict):
        self.row = row
        self.artifacts = artifacts
        self.calls: list = []

    def get_run(self, run_id):
        self.calls.append(("get_run", run_id))
        return self.row if run_id == self.row["run_id"] else None

    def get_artifact_row(self, run_id, kind):
        self.calls.append(("get_artifact_row", run_id, kind))
        return self.artifacts.get((run_id, kind))


def _artifacts_from_payload(payload: dict) -> dict:
    return {
        (RID, "export_json"): {"body": json.dumps(payload, ensure_ascii=False),
                               "storage": "pg", "object_key": None},
        (RID, "report_md"): {"body": payload["result"]["report"],
                             "storage": "pg", "object_key": None},
    }


# ---- 装配模型 ----


def test_document_from_payload_is_complete():
    doc = document_from_payload(_payload())
    assert doc.schema_version == EXPORT_DOCUMENT_SCHEMA_VERSION
    assert doc.run_id == RID and doc.topic == "导出装配主题"
    assert doc.run_status == "success" and doc.stop_reason == "completed"
    assert doc.report.startswith("# 报告")
    assert doc.citations and doc.visited_sources == ["https://e.com/a", "https://e.com/b"]
    assert doc.egress == {"search": {"provider": "bocha"}}


def test_document_from_store_prefers_export_json():
    payload = _payload()
    doc = document_from_store(_row(), export_json=json.dumps(payload, ensure_ascii=False),
                              report_md=None)
    assert doc == document_from_payload(payload)


def test_document_from_store_degrades_with_explicit_unavailable():
    doc = document_from_store(_row(), export_json=None, report_md="# 历史正文")
    assert doc.report == "# 历史正文"
    assert doc.citations is None and doc.visited_sources is None  # 显式不可用（不伪造）
    assert doc.degradation_count is None and doc.depth is None
    assert doc.run_status == "success" and doc.token_used == 123
    assert doc.elapsed_seconds == 30.0  # created→finished 差
    assert doc.egress == {"search": {"provider": "bocha"}}


def test_document_from_store_corrupt_export_json_falls_back():
    doc = document_from_store(_row(), export_json="{not json", report_md="# 历史正文")
    assert doc.report == "# 历史正文" and doc.citations is None


def test_document_from_store_report_falls_back_to_artifact():
    payload = _payload()
    payload["result"]["report"] = ""  # 载荷缺正文 ⇒ 回落 report_md
    doc = document_from_store(_row(), export_json=json.dumps(payload, ensure_ascii=False),
                              report_md="# 回落正文")
    assert doc.report == "# 回落正文"


# ---- 渲染口径 ----


def test_render_markdown_keeps_legacy_shape():
    md = render_markdown(document_from_payload(_payload()))
    assert md.startswith("# 导出装配主题：爱考据 iKaoju 报告导出")
    assert f"run_id `{RID}`" in md
    assert "run_status **success**" in md and "停止原因 **completed**" in md
    assert "降级条目 1 · 检索深度 2" in md
    assert "# 报告" in md
    assert "## 引用清单" in md and "论断一" in md
    assert "## 访问过的来源" in md and "https://e.com/b" in md


def test_render_markdown_explicit_unavailable_and_empty_omission():
    md = render_markdown(document_from_store(_row(), export_json=None,
                                             report_md="# 历史正文"))
    assert "降级条目 n/a · 检索深度 n/a" in md
    assert "（不可用：历史产物未保留引用数据）" in md
    assert "（不可用：历史产物未保留来源数据）" in md

    empty = replace(document_from_payload(_payload()), citations=[], visited_sources=[])
    md2 = render_markdown(empty)
    assert "## 引用清单" not in md2 and "## 访问过的来源" not in md2  # 已知为空：省略


# ---- store 导出路径 = 内存路径（同装配同渲染） ----


def test_export_from_store_matches_memory_rendering(monkeypatch):
    payload = _payload()
    fake = _FakeStore(_row(), _artifacts_from_payload(payload))
    monkeypatch.setattr(api, "store", fake)
    monkeypatch.setattr(api.manager, "exists", lambda rid: False)

    resp = api._export_from_store(RID, "md", fake.row)
    memory_md = render_markdown(document_from_payload(payload))
    assert resp.body.decode() == memory_md
    assert "## 引用清单" in resp.body.decode()  # 历史路径从「缺」变「有」（F1 修复）


def test_export_from_store_json_and_guards(monkeypatch):
    payload = _payload()
    fake = _FakeStore(_row(), _artifacts_from_payload(payload))
    monkeypatch.setattr(api, "store", fake)
    monkeypatch.setattr(api.manager, "exists", lambda rid: False)

    resp = api._export_from_store(RID, "json", fake.row)
    assert json.loads(resp.body)["run_id"] == RID

    with pytest.raises(Exception) as e:  # 未完成 ⇒ report_not_ready
        api._export_from_store(RID, "md", _row(finished_at=None))
    assert "report_not_ready" in str(getattr(e.value, "detail", e.value))

    fake_empty = _FakeStore(_row(), {})
    monkeypatch.setattr(api, "store", fake_empty)
    with pytest.raises(Exception) as e2:  # 无产物 ⇒ report_unavailable
        api._export_from_store(RID, "md", fake_empty.row)
    assert "report_unavailable" in str(getattr(e2.value, "detail", e2.value))


def test_report_endpoint_store_path_serves_unified_markdown(monkeypatch):
    payload = _payload()
    fake = _FakeStore(_row(), _artifacts_from_payload(payload))
    monkeypatch.setattr(api, "store", fake)
    monkeypatch.setattr(api.manager, "exists", lambda rid: False)

    client = TestClient(api.app)
    r = client.get(f"/api/research/{RID}/report")
    assert r.status_code == 200
    assert "run_status **success**" in r.text and "## 引用清单" in r.text


# ---- 分享链路：仅正文（内存/store 一致；不扩大披露） ----


def test_share_markdown_memory_is_body_only(monkeypatch):
    payload = _payload()
    monkeypatch.setattr(api.manager, "exists", lambda rid: True)
    monkeypatch.setattr(api.manager, "has_result", lambda rid: True)
    monkeypatch.setattr(api.manager, "export_payload", lambda rid: payload)
    body = api._share_markdown(RID)
    assert body == "# 报告\n\n正文段落。"
    assert "## 引用清单" not in body and "run_status" not in body  # 不含审计头/清单


def test_share_markdown_store_is_body_only(monkeypatch):
    payload = _payload()
    fake = _FakeStore(_row(), _artifacts_from_payload(payload))
    monkeypatch.setattr(api, "store", fake)
    monkeypatch.setattr(api.manager, "exists", lambda rid: False)
    assert api._share_markdown(RID) == "# 报告\n\n正文段落。"


def test_share_markdown_none_without_report(monkeypatch):
    monkeypatch.setattr(api.manager, "exists", lambda rid: True)
    monkeypatch.setattr(api.manager, "has_result", lambda rid: False)
    assert api._share_markdown(RID) is None
