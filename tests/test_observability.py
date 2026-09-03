# -*- coding: utf-8 -*-
"""W3 observability 离线单测（全 mock / 不配 key，零外发；grill Q1~Q7 验收）。

覆盖：
- Q4/Q5：三态开关判定、get_langfuse() 惰性 None（fail-silent）；
- Q2：create_trace_id 唯一性 + 32-hex 格式（4.x 硬性要求）；span/trace 未启用时 no-op；
- Q4：mask_callback 长度裁剪与敏感替换（默认关）；
- Q3=D'：LLMClient 类级 tokens_total 无条件累计（state=None 也记），run 差值语义；
- Q6：format_cost_report 保守上界与人民币格式。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research_engine.observability as obs  # noqa: E402
from research_engine.llm.client import LLMClient  # noqa: E402


def _reset_obs():
    """重置模块级惰性单例缓存（测试间隔离）。"""
    obs._langfuse = None
    obs._langfuse_ready = False


@pytest.fixture(autouse=True)
def _clean_obs():
    _reset_obs()
    yield
    _reset_obs()


# ---------- Q4/Q5：三态开关 ----------

def test_conftest_forces_disabled():
    """Q5 三重隔离①：conftest 必须在测试集合中强制禁用（覆盖 .env 里的任何配置）。"""
    assert os.environ.get("LANGFUSE_ENABLED") == "false"
    from config import config
    assert config.langfuse.enabled == "false"


def test_enabled_false_force_disables(monkeypatch):
    from config import config
    monkeypatch.setattr(config.langfuse, "enabled", "false")
    monkeypatch.setattr(config.langfuse, "public_key", "pk-x")
    monkeypatch.setattr(config.langfuse, "secret_key", "sk-x")
    assert obs._enabled() is False
    assert obs.get_langfuse() is None


def test_triple_missing_auto_disables(monkeypatch):
    from config import config
    monkeypatch.setattr(config.langfuse, "enabled", "auto")
    monkeypatch.setattr(config.langfuse, "public_key", "")
    monkeypatch.setattr(config.langfuse, "secret_key", "")
    assert obs._enabled() is False
    assert obs.get_langfuse() is None


def test_triple_ready_auto_enables_and_zero_network(monkeypatch):
    """三件套齐备自动启用：构造 client 不联网（纯本地），并能拿到 32-hex trace id。"""
    from config import config
    monkeypatch.setattr(config.langfuse, "enabled", "auto")
    monkeypatch.setattr(config.langfuse, "public_key", "pk-fake")
    monkeypatch.setattr(config.langfuse, "secret_key", "sk-fake")
    monkeypatch.setattr(config.langfuse, "host", "http://127.0.0.1:1")  # 不可达：后台 export 快速失败，零外发流量
    lf = obs.get_langfuse()
    assert lf is not None  # 构造成功（不联网；auth_check 才联网，此处不调）
    tid = lf.create_trace_id(seed="s1")
    assert len(tid) == 32 and all(c in "0123456789abcdef" for c in tid), tid


def test_status_line_disabled_message():
    _reset_obs()
    line = obs.status_line()
    assert "已禁用" in line


# ---------- Q2：trace id / span 语义 ----------

def test_create_trace_id_unique_same_thread(monkeypatch):
    from config import config
    config.langfuse.enabled = "auto"
    config.langfuse.public_key = "pk-fake"
    config.langfuse.secret_key = "sk-fake"
    config.langfuse.host = "https://cloud.langfuse.com"
    a = obs.create_trace_id("dr-abc")
    b = obs.create_trace_id("dr-abc")  # 同 thread：seed 含 ts+uuid → 唯一，防覆盖（Q2=A1'）
    assert a is not None and b is not None
    assert a != b
    assert len(a) == 32 and len(b) == 32


def test_span_node_disabled_noop(monkeypatch):
    from config import config
    monkeypatch.setattr(config.langfuse, "enabled", "false")  # 声明式禁用，不依赖隐式还原
    with obs.span_node("检索 R1", node="research", depth=1) as sp:
        assert sp is None  # 禁用态 no-op，节点代码零成本降级


def test_start_trace_disabled_noop(monkeypatch):
    from config import config
    monkeypatch.setattr(config.langfuse, "enabled", "false")
    with obs.start_trace("a" * 32, "topic", thread_id="dr-1") as root:
        assert root is None


# ---------- Q4：mask 回调 ----------

def test_mask_callback_truncates_long_str(monkeypatch):
    from config import config
    config.langfuse.mask_sensitive = False
    long_text = "长" * (obs.TRUNCATE_LEN + 100)
    out = obs.mask_callback(data=long_text)
    assert len(out) <= obs.TRUNCATE_LEN + 20  # 截断 + 后缀
    assert out.endswith("…[truncated]")


def test_mask_callback_sensitive_off_preserves_digits(monkeypatch):
    from config import config
    config.langfuse.mask_sensitive = False
    text = "论文编号 12345678901 是特征"
    out = obs.mask_callback(data=text)
    assert "12345678901" in out  # 默认不替换，避免误伤数字信息（Q4）


def test_mask_callback_sensitive_on_replaces_11_digits(monkeypatch):
    from config import config
    config.langfuse.mask_sensitive = True
    text = "请联系 12345678901 手机号"
    out = obs.mask_callback(data=text)
    assert "12345678901" not in out
    assert "***" in out


def test_mask_callback_passthrough_non_str(monkeypatch):
    from config import config
    config.langfuse.mask_sensitive = False
    data = {"a": [1, 2, 3]}
    assert obs.mask_callback(data=data) == data  # 非 str 字段原样返回


# ---------- Q3=D'：类级无条件计数 ----------

def test_tokens_total_accumulates_without_state():
    LLMClient.tokens_total = 0  # 重置类级计数（测试隔离）
    client = LLMClient()
    from types import SimpleNamespace
    resp = SimpleNamespace(usage=SimpleNamespace(total_tokens=100))
    client._accumulate_usage(resp, None)  # 未传 state
    assert LLMClient.tokens_total == 100  # 无条件记账
    # 传 state 时两边都累计
    from research_engine.state import ResearchState
    st = ResearchState(topic="t")
    client._accumulate_usage(resp, st)
    assert LLMClient.tokens_total == 200
    assert st.token_used == 100
    LLMClient.tokens_total = 0  # 还原


# ---------- Q6：成本报告 ----------

def test_format_cost_report_conservative(monkeypatch):
    from config import config
    config.llm.pricing = {
        "qwen-turbo": {"input": 0.0001, "output": 0.0002},
        "qwen-plus": {"input": 0.0002, "output": 0.001},
    }
    report = obs.format_cost_report(100_000)
    assert "100,000 tokens" in report
    assert "¥" in report
    # 100k/1000 * 0.001（最贵 output）= ¥0.10，保守上界
    assert "¥0.10" in report


# ---------- Q5：flush / URL 未启用时安全 ----------

def test_flush_and_get_url_disabled_returns_none():
    assert obs.flush_and_get_url("") is None  # 未启用：安全空操作