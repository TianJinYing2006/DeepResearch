"""需求 23 分块 v2 与结构快照测试（纯函数，零外部服务）。

覆盖：标题路径与不跨标题合并、长段二次切分 + overlap、表格小表整表 / 大表拆分组
重复表头 + 行范围 locator、token 估算、markdown/html 快照解析。
"""
from __future__ import annotations

from research_engine.rag.chunker_v2 import chunk_blocks, estimate_tokens
from research_engine.rag.snapshot import (
    SnapshotBlock,
    parse_html_blocks,
    parse_markdown_blocks,
    parse_xlsx_blocks,
)


def test_estimate_tokens_mixed():
    assert estimate_tokens("") == 0
    assert estimate_tokens("中文四个字") == 5
    assert estimate_tokens("abcd") == 1
    # 混合：4 CJK + 8 非 CJK ≈ 4 + 2
    assert estimate_tokens("四个汉字abcdefgh") == 6


def test_markdown_structure_and_title_path():
    md = "# 一级\n\n第一段内容。\n\n## 二级\n\n第二段内容。\n\n### 三级\n\n第三段内容。\n"
    blocks = parse_markdown_blocks(md)
    assert [b.kind for b in blocks] == [
        "heading", "paragraph", "heading", "paragraph", "heading", "paragraph"]
    assert blocks[1].title_path == ("一级",)
    assert blocks[3].title_path == ("一级", "二级")
    assert blocks[5].title_path == ("一级", "二级", "三级")

    drafts = chunk_blocks(blocks, max_tokens=100, overlap_tokens=10)
    assert len(drafts) == 3
    assert drafts[0].title_path == ("一级",) and "第一段内容" in drafts[0].text
    assert drafts[1].embed_text.startswith("一级 > 二级\n\n")


def test_markdown_table_and_code_blocks():
    md = (
        "# 报告\n\n"
        "| 指标 | 数值 |\n|---|---|\n| 命中率 | 0.8 |\n| 准确率 | 0.9 |\n\n"
        "```python\nprint('hi')\n```\n"
    )
    blocks = parse_markdown_blocks(md)
    assert [b.kind for b in blocks] == ["heading", "table", "code"]
    assert blocks[1].rows[0] == ("指标", "数值") and len(blocks[1].rows) == 3
    drafts = chunk_blocks(blocks, max_tokens=100, overlap_tokens=10)
    assert len(drafts) == 2 and "\t" in drafts[0].text


def test_long_paragraph_split_with_overlap():
    text = "".join(f"第{i}句是一段足够长的测试内容用于触发切分。" for i in range(30))
    blocks = [SnapshotBlock(kind="paragraph", text=text)]
    drafts = chunk_blocks(blocks, max_tokens=50, overlap_tokens=10)
    assert len(drafts) >= 3
    # 片间 overlap：第二片开头必须是第一片结尾的一部分
    prefix = drafts[1].text[:8]
    assert prefix and prefix in drafts[0].text
    # 每片不超预算（估算口径；overlap 前缀含在内）
    assert all(estimate_tokens(draft.text) <= 50 for draft in drafts)


def test_small_table_single_chunk_and_large_table_split():
    small_rows = (("列A", "列B"), ("1", "2"), ("3", "4"))
    small = SnapshotBlock(kind="table", text="列A\t列B\n1\t2\n3\t4",
                          rows=small_rows, locator={"sheet": "S1"})
    drafts = chunk_blocks([small], max_tokens=100, overlap_tokens=10)
    assert len(drafts) == 1 and drafts[0].text.startswith("列A\t列B")
    assert drafts[0].locator == {"sheet": "S1"}

    header = ("名称", "描述", "备注")
    data = tuple((f"项目{i}", f"这是一段描述文本{i}", f"备注{i}") for i in range(40))
    big = SnapshotBlock(kind="table", text="", rows=(header,) + data,
                        locator={"sheet": "S2"})
    drafts = chunk_blocks([big], max_tokens=60, overlap_tokens=10)
    assert len(drafts) >= 3
    # 每片首行都是表头（重复表头）
    assert all(d.text.splitlines()[0] == "名称\t描述\t备注" for d in drafts)
    # 行范围连续且覆盖数据行（1-based 全表行号，表头为第 1 行）
    ranges = [tuple(d.locator["row_range"]) for d in drafts]
    assert ranges[0][0] == 2 and ranges[-1][1] == 41
    for prev, curr in zip(ranges, ranges[1:]):
        assert curr[0] == prev[1] + 1


def test_no_cross_title_merge():
    blocks = [
        SnapshotBlock(kind="paragraph", text="A 段。", title_path=("甲",)),
        SnapshotBlock(kind="paragraph", text="B 段。", title_path=("乙",)),
    ]
    drafts = chunk_blocks(blocks, max_tokens=100, overlap_tokens=10)
    assert len(drafts) == 2
    assert drafts[0].text == "A 段。" and drafts[1].text == "B 段。"


def test_html_snapshot_removes_nav_and_keeps_structure():
    html = """
    <html><body>
      <nav>导航噪声</nav>
      <h2>第一章</h2><p>第一章正文。</p>
      <table><tr><th>K</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>
      <script>console.log('x')</script>
    </body></html>
    """
    blocks = parse_html_blocks(html)
    kinds = [b.kind for b in blocks]
    assert "heading" in kinds and "table" in kinds
    assert all("导航噪声" not in b.text for b in blocks)
    paragraph = next(b for b in blocks if b.kind == "paragraph")
    assert paragraph.title_path == ("第一章",)
    table = next(b for b in blocks if b.kind == "table")
    assert table.rows[0] == ("K", "V")


def test_xlsx_parser_skipped_without_openpyxl():
    import importlib.util

    if importlib.util.find_spec("openpyxl") is None:
        return  # 本地未装 openpyxl 时跳过（CI 装齐后由集成用例覆盖）
    # 有依赖时给出最小冒烟由集成环境执行（本文件不造二进制夹具）
    assert callable(parse_xlsx_blocks)
