# -*- coding: utf-8 -*-
"""W8 Arm 2：代码执行注入面收敛 + 工具功能性修复（§5.2）。

覆盖 `docs/requirements/8-fault-transparency-and-reproducibility.md`：
- §5.2.1 `_default_code_script()` 零参数，脚本源码**不含任何查询文本**
- §5.2.2 query 经 `exec_code(script, query=)` 进 `script_hash`（保唯一性）+ `metadata["query"]`
  （审计侧关联通道，替代「把 query 拼进被执行的脚本」）
- §5.2.3 中文/引号/反斜杠/换行/emoji 等敌意 query 下模板脚本**必须执行成功**
- §5.2 预检第 3 条回归护栏：query 仍参与 `script_hash`，同题多条 code 证据不得塌缩成同一 source

**本条最重要的实测事实（2026-09-16，推翻文档 §5.2 的朴素叙述）**：
旧模板 `f"print('query={query!r}')"` 的外层 `'` 与 `!r` 自带引号**叠加成双层引号**，
对**任何** query 都是 `SyntaxError: invalid syntax`（连纯 ASCII 也爆）——
即 `code_exec` 在主链路上**一直是 100% 失败**的。
基线三轮真实产量：code_exec 产出 **144 条、成功 0 条、失败 144 条（失败率 100%）**。
故 Arm 2 的实质是**工具功能性修复**，而非单纯的纵深防御；
失败 note 里原本还会把 query 原文（经 stderr）带回 findings。

全部零 LLM / 零 API（沙箱子进程除外，与 `test_code_exec.py` 同性质）。
"""
from __future__ import annotations

import inspect

import pytest

from research_engine.agents import researcher as R
from research_engine.state import DegradationSink
from research_engine.tools.code_exec import CodeExecOutput, exec_code

# 敌意查询：纯中文 / 中英混合 / 单引号 / 双引号 / 混合引号 / 反斜杠+制表符 / 换行 / emoji / 注入尝试
HOSTILE_QUERIES = [
    "模型参数量怎么算",
    "中英 mixed GPT-4 vs Llama-3 对比",
    "包含单引号 ' 的查询",
    '包含双引号 " 的查询',
    "同时含 ' 和 \" 两种引号",
    "含反斜杠 \\ 与制表符 \t 的查询",
    "含换行\n的查询",
    "emoji 🚀🔥 混合 English",
    "'); import os; os.system('echo pwned')  #",
    "'''\nimport os\nos.system('pwned')\n'''",
]


def _bare_researcher() -> R.Researcher:
    """跳过 __init__（避免建 provider/retriever/向量库），只装降级缓冲区。"""
    r = R.Researcher.__new__(R.Researcher)
    r.degradations = DegradationSink()
    return r


# ---------------- §5.2.1 脚本模板不再接收 query ----------------

def test_default_code_script_takes_no_query():
    """模板零参数 —— 从签名上就杜绝 query 进入脚本源码。"""
    sig = inspect.signature(R._default_code_script)
    assert list(sig.parameters) == [], "模板不应再接收任何参数（W8 Arm 2 §5.2.1）"
    assert isinstance(R._default_code_script(), str)


def test_default_code_script_is_query_independent():
    """模板是常量：多次调用逐字节相同（原先会随 query 变化）。"""
    a, b = R._default_code_script(), R._default_code_script()
    assert a == b
    assert "query" not in a, "模板里不应再出现 query 字样"


# ---------------- §5.2.1/§5.2.2 query 改走元数据，且仍进 hash ----------------

@pytest.mark.parametrize("query", HOSTILE_QUERIES)
def test_query_never_enters_executed_script(monkeypatch, query):
    """核心断言：被执行的 code 里不含 query 文本；但 query 仍作为参数传给 exec_code。"""
    captured: dict = {}

    def fake_exec_code(code, query="", params=""):
        captured["code"], captured["query"], captured["params"] = code, query, params
        return CodeExecOutput(
            ok=True, stdout="sequence_length=8192\n", elapsed=0.001,
            script_hash="deadbeef01", metadata={"exit_code": 0, "query": query},
        )

    monkeypatch.setattr(R, "exec_code", fake_exec_code)
    findings = _bare_researcher()._search_code(query)

    assert captured["code"] == R._default_code_script(), "被执行的就是常量模板"
    assert query not in captured["code"], "查询文本不得出现在被执行的脚本里"
    assert captured["query"] == query, "query 仍须进 exec_code（script_hash 唯一性的唯一来源）"
    assert len(findings) == 1


@pytest.mark.parametrize("query", ["模型参数量怎么算", "含换行\n的查询", "emoji 🚀🔥"])
def test_query_recorded_in_finding_metadata(monkeypatch, query):
    """success 路径：query 经 metadata 关联产出（审计通道，替代脚本内拼接）。"""
    monkeypatch.setattr(
        R, "exec_code",
        lambda code, query="", params="": CodeExecOutput(
            ok=True, stdout="x=1\n", elapsed=0.001, script_hash="h",
            metadata={"exit_code": 0, "query": query},
        ),
    )
    f = _bare_researcher()._search_code(query)[0]
    assert f.metadata["query"] == query
    assert f.source_type == "code_exec"


@pytest.mark.parametrize("query", ["模型参数量怎么算", "含换行\n的查询"])
def test_query_recorded_in_metadata_on_failure_path(monkeypatch, query):
    """failure 路径：query 与 note 一并留在元数据，且降级记录可归因。"""
    monkeypatch.setattr(
        R, "exec_code",
        lambda code, query="", params="": CodeExecOutput(
            ok=False, note="SyntaxError: invalid syntax", elapsed=0.001,
            script_hash="h", metadata={"exit_code": 1, "query": query},
        ),
    )
    r = _bare_researcher()
    f = r._search_code(query)[0]
    assert f.metadata["query"] == query
    assert f.confidence == 0.2, "失败 finding 保持低可信度"
    log = r.drain_degradations()
    assert len(log) == 1
    assert log[0].reason == "parse_error", "语法错应归 parse_error 而非 provider_error"


# ---------------- §5.1.2 工具类失败原因分类 ----------------

@pytest.mark.parametrize(
    "note,expect",
    [
        ("SyntaxError: invalid syntax", "parse_error"),
        ("  File \"main.py\", line 28\n    语法错误", "parse_error"),
        ("导入被沙箱拒绝: os", "provider_error"),
        ("执行超时（>5s），已终止", "timeout"),
        ("Process timed out", "timeout"),
        ("exit code 2", "provider_error"),
        ("", "provider_error"),
    ],
)
def test_classify_code_exec_failure(note, expect):
    """三类真实失败（脚本语法 / 超时 / 运行时非零退出）各归其位。"""
    assert R._classify_code_exec_failure(note) == expect


# ---------------- §5.2.3 敌意 query 下脚本必须真的跑通（沙箱实跑） ----------------

@pytest.mark.parametrize("query", HOSTILE_QUERIES)
def test_template_executes_successfully_for_hostile_queries(query):
    """新模板在敌意 query 下必须 ok —— 旧模板在此全数 SyntaxError。"""
    out = exec_code(R._default_code_script(), query=query)
    assert out.ok, f"模板执行失败: {out.note[:200]}"
    assert "sequence_length=8192" in out.stdout
    assert "SyntaxError" not in (out.stderr or "")


def test_old_style_template_was_always_broken():
    """回归护栏（反事实留档）：旧写法对**任何** query 都是语法错误。

    旧模板 = `f"print('query={query!r}')"` —— 外层 `'` 与 `!r` 自带引号叠加成双层引号。
    这条测试把「文档 §5.2 原先设想的『特殊字符才出错』」纠正为「**无条件出错**」，
    防止后人按错误前提重新推导出并不成立的漏洞模型。
    """
    old_script = (
        "import math\n"
        "n = 8192\n"
        "flops_per_token = 6 * n * 2\n"
        "total_flops = n * flops_per_token\n"
        "print('query=" + repr("模型参数量怎么算") + "')\n"
        "print('sequence_length=' + str(n))\n"
    )
    with pytest.raises(SyntaxError):
        compile(old_script, "<old_template>", "exec")

    out = exec_code(old_script, query="模型参数量怎么算")
    assert not out.ok, "旧模板应执行失败（这正是基线 100% 失败的原因）"


# ---------------- §5.2 预检第 3 条：query 必须继续进 hash ----------------

def test_same_script_different_query_yields_distinct_source():
    """同一个模板 + 不同 query ⇒ source 不重复（防 dedupe/compress 静默并条）。"""
    script = R._default_code_script()
    hashes = {exec_code(script, query=q).script_hash for q in ("q1", "q2", "q3")}
    assert len(hashes) == 3, "query 若不进 hash，同题多条 code 证据会塌缩成同一 source"


def test_exec_code_writes_query_into_metadata():
    """§5.2.2 的新增行为：`exec_code` 把 query 写进 `metadata['query']`。"""
    out = exec_code("print('ok')", query="中文 query")
    assert out.metadata.get("query") == "中文 query"


# ---------------- 沙箱输出编码（DoD「中文乱码不再复现」根因） ----------------

def test_sandbox_stdout_keeps_chinese_intact():
    """沙箱回传中文不得乱码。

    根因：父进程按 utf-8 解码管道，子进程在 Windows 默认按 cp936 写管道
    ⇒ `print('中文测试')` 旧版回传 `���Ĳ��ԣ`。修法 = 子进程命令行加 `-X utf8`。
    """
    out = exec_code("print('中文测试：参数量 8192')")
    assert out.ok, out.note[:200]
    assert "中文测试" in out.stdout, f"中文被破坏: {out.stdout!r}"
    assert "参数量 8192" in out.stdout


def test_sandbox_stderr_keeps_chinese_intact():
    """stderr 通道同样不得乱码（失败 note 会经此回传）。

    用抛异常而非 `print(file=sys.stderr)`：沙箱 AST 白名单拒 `import sys`，
    异常回溯是唯一不引入 import 的 stderr 输出通道。
    """
    out = exec_code("raise ValueError('中文错误消息')")
    assert not out.ok
    assert "中文错误消息" in out.stderr, f"stderr 中文被破坏: {out.stderr!r}"
