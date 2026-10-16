"""需求 38 F3：DOCX 导出端点单测（零网络；store / manager 全注入）。

覆盖：内存路径与 store 路径**同一装配同结构**、格式枚举（md|json|docx）、
下载名（.docx + RFC 6266）、flagged 输出 gate（既有端点口径，回归锁定）。
"""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

from docx import Document
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.export import build_export_payload

RID = "run-38f3"


def _payload() -> dict:
    return build_export_payload(
        run_id=RID,
        topic="DOCX 端点主题",
        meta={"run_status": "success", "stop_reason": "completed", "cancelled": False,
              "token_used": 123, "cost_estimate_cny": 0.0021, "elapsed_seconds": 12.5,
              "degradation_count": 1, "depth": 2},
        result={
            "report": "# 报告\n\n正文段落。\n\n## 引用清单",
            "citations": [{"claim": "论断一", "source": "https://e.com/a", "source_type": "web",
                           "confidence": 0.9, "existence": True, "supported": True}],
            "visited_sources": ["https://e.com/a"],
        },
    )


def _row(**overrides) -> dict:
    created = datetime(2026, 10, 16, 8, 0, 0, tzinfo=UTC)
    row = {
        "run_id": RID,
        "topic": "DOCX 端点主题",
        "finished_at": created + timedelta(seconds=30),
        "created_at": created,
        "research_status": "success",
        "stop_reason": "completed",
        "token_used": 123,
        "cost_estimate_cny": 0.0021,
        "moderation_status": None,
        "request": {},
    }
    row.update(overrides)
    return row


class _FakeStore:
    """只读 fake（无写方法 ⇒ 导出链路若尝试写库将直接 AttributeError）。"""

    def __init__(self, row: dict, artifacts: dict):
        self.row = row
        self.artifacts = artifacts

    def get_run(self, run_id):
        return self.row if run_id == self.row["run_id"] else None

    def get_artifact_row(self, run_id, kind):
        return self.artifacts.get((run_id, kind))


def _artifacts(payload: dict) -> dict:
    return {
        (RID, "export_json"): {"body": json.dumps(payload, ensure_ascii=False),
                               "storage": "pg", "object_key": None},
        (RID, "report_md"): {"body": payload["result"]["report"],
                             "storage": "pg", "object_key": None},
    }


def _assert_docx_structure(content: bytes, topic: str) -> None:
    document = Document(io.BytesIO(content))
    styled = [(p.style.name, p.text) for p in document.paragraphs]
    assert styled[0][0] == "Title" and topic in styled[0][1]
    assert ("Heading 1", "报告") in styled
    assert ("Heading 2", "引用清单") in styled
    assert any(style == "List Number" and "论断一 — <https://e.com/a>" in text
               for style, text in styled)
    assert ("List Bullet", "<https://e.com/a>") in styled


def test_export_docx_memory_path(monkeypatch):
    payload = _payload()
    monkeypatch.setattr(api.manager, "exists", lambda rid: True)
    monkeypatch.setattr(api.manager, "has_result", lambda rid: True)
    monkeypatch.setattr(api.manager, "export_payload", lambda rid: payload)
    client = TestClient(api.app)

    r = client.get(f"/api/research/{RID}/report?format=docx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert ".docx" in r.headers["content-disposition"]
    _assert_docx_structure(r.content, "DOCX 端点主题")


def test_export_docx_store_path_same_structure(monkeypatch):
    payload = _payload()
    fake = _FakeStore(_row(), _artifacts(payload))
    monkeypatch.setattr(api, "store", fake)
    monkeypatch.setattr(api.manager, "exists", lambda rid: False)
    client = TestClient(api.app)

    r = client.get(f"/api/research/{RID}/report?format=docx")
    assert r.status_code == 200
    _assert_docx_structure(r.content, "DOCX 端点主题")

    # 与内存路径同装配同结构（F1 口径延续：store 历史导出带引用清单）
    monkeypatch.setattr(api.manager, "exists", lambda rid: True)
    monkeypatch.setattr(api.manager, "has_result", lambda rid: True)
    monkeypatch.setattr(api.manager, "export_payload", lambda rid: payload)
    r2 = client.get(f"/api/research/{RID}/report?format=docx")
    assert r2.status_code == 200
    _assert_docx_structure(r2.content, "DOCX 端点主题")


def test_export_format_enum_and_gate(monkeypatch):
    payload = _payload()
    monkeypatch.setattr(api.manager, "exists", lambda rid: True)
    monkeypatch.setattr(api.manager, "has_result", lambda rid: True)
    monkeypatch.setattr(api.manager, "export_payload", lambda rid: payload)
    client = TestClient(api.app)

    # 未支持的格式（如 pptx）⇒ 422（枚举外）
    assert client.get(f"/api/research/{RID}/report?format=pptx").status_code == 422

    # flagged ⇒ 403（与 md/json 同口径）
    fake = _FakeStore(_row(moderation_status="flagged"), _artifacts(payload))
    monkeypatch.setattr(api, "store", fake)
    monkeypatch.setattr(api.manager, "exists", lambda rid: False)
    r = client.get(f"/api/research/{RID}/report?format=docx")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "output_under_review"
