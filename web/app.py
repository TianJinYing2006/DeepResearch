# -*- coding: utf-8 -*-
"""Streamlit Web UI。

实时展示研究进度（当前节点、检索情况），输出带引用的报告。
用法：streamlit run web/app.py
"""
from __future__ import annotations

import sys
import os

# 确保能 import research_engine
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from research_engine.graph import create_graph

st.set_page_config(page_title="DeepResearch 深度研究 Agent", layout="wide")
st.title("🔍 DeepResearch 深度研究 Agent")
st.caption("多 Agent 编排 + 多跳检索 + RAG 多源融合 + 交叉验证防幻觉")

# 侧边栏：文档摄取
with st.sidebar:
    st.header("📚 RAG 知识库")
    st.info("上传文档到知识库，供研究时检索（可选）")
    uploaded = st.file_uploader("上传文档", type=["pdf", "docx", "md", "txt"], accept_multiple_files=True)
    if uploaded and st.button("摄取到知识库"):
        from research_engine.rag.ingest import DocumentIngester
        import tempfile
        ingester = DocumentIngester()
        total = 0
        for f in uploaded:
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(f.name)[1]) as tmp:
                tmp.write(f.getvalue())
                tmp_path = tmp.name
            try:
                total += ingester.ingest_file(tmp_path, doc_id=f.name)
            finally:
                os.unlink(tmp_path)
        st.success(f"已摄取 {len(uploaded)} 个文档，共 {total} 个分块")

    st.divider()
    st.header("⚙️ 研究设置")
    max_depth = st.slider("多跳检索最大深度", 1, 5, 3)
    breadth = st.slider("每轮检索查询数", 1, 5, 3)

# 主区域：研究输入
topic = st.text_input("研究主题", placeholder="例如：2026 年 RAG 技术的最新进展")
instructions = st.text_area("附加要求（可选）", placeholder="例如：重点关注多模态 RAG，输出中文报告")

if st.button("开始研究", type="primary"):
    if not topic.strip():
        st.warning("请输入研究主题")
    else:
        graph = create_graph()
        # 覆盖配置
        from config import config
        config.research.max_depth = max_depth
        config.research.breadth = breadth

        progress_bar = st.progress(0)
        status_text = st.empty()

        # 运行（同步，展示进度）
        result = graph.run(topic, instructions)

        # 展示进度
        for i, p in enumerate(result.progress):
            status_text.write(f"**{p['stage']}**: {p['msg']}")
            progress_bar.progress(min((i + 1) / max(len(result.progress), 1), 1.0))

        st.divider()
        st.subheader("📄 研究报告")
        st.markdown(result.report)

        # 引用校验结果
        st.divider()
        st.subheader("✅ 引用校验")
        if result.citations:
            verified = sum(1 for c in result.citations if c.verified)
            st.metric("引用存在性通过率", f"{verified}/{len(result.citations)}")
            for c in result.citations:
                icon = "✅" if c.verified else "❌"
                st.write(f"{icon} **{c.claim[:80]}** — 来源: {c.source}")
        else:
            st.info("报告中未检测到引用标注")
