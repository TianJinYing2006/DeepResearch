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
    # Q7=A：W1 后图消费 max_total_hops/per_subq_hop_cap，旧的 max_depth/breadth slider 是 dead UI，改绑全局跳数上限
    max_total_hops = st.slider("全局检索跳数上限", 1, 30, 20)

# 主区域：研究输入
topic = st.text_input("研究主题", placeholder="例如：2026 年 RAG 技术的最新进展")
instructions = st.text_area("附加要求（可选）", placeholder="例如：重点关注多模态 RAG，输出中文报告")

if st.button("开始研究", type="primary"):
    if not topic.strip():
        st.warning("请输入研究主题")
    else:
        graph = create_graph()
        # 覆盖配置（Q7=A：改绑 W1 后的有效配置 max_total_hops）
        from config import config
        config.research.max_total_hops = max_total_hops

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
        # W2（Q1/Q4=A）：展示 render 节点产出的可审计版（类型标注 + ⚠️ + 附录 + 溯源）
        st.markdown(result.report_display or result.report)

        # 引用校验结果（Q6=A 双口径）
        st.divider()
        st.subheader("✅ 引用校验（双口径）")
        if result.citations:
            total = len(result.citations)
            existence = sum(1 for c in result.citations if c.existence)
            faithful = sum(1 for c in result.citations if c.verified)
            c1, c2 = st.columns(2)
            c1.metric("来源存在性通过", f"{existence}/{total}")
            c2.metric("论断忠实度通过", f"{faithful}/{existence}")
            for c in result.citations:
                icon = "✅" if c.verified else "❌"
                badge = ""
                if c.source_type == "web":
                    badge = "🔵web"
                elif c.source_type == "rag":
                    badge = f"🟢rag：{c.source}"
                note = f" — 原因: {c.note}" if (not c.verified and c.note) else ""
                st.write(f"{icon} **{c.claim[:100]}** — {badge or c.source}{note}")
        else:
            st.info("报告中未检测到引用标注")

        # 运行溯源（R2.5）
        st.divider()
        st.subheader("📊 运行溯源")
        from collections import Counter
        signals = Counter(p.get("signal", "") for p in result.reflection_log)
        vs = result.visited_sources
        rag_count = sum(1 for s in vs if str(s).startswith("rag:"))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("检索总跳数", result.depth)
        m2.metric("critic 决策", f"继续 {signals.get('continue', 0)} / 重来 {signals.get('revise', 0)} / 停止 {signals.get('stop', 0)}")
        m3.metric("web / rag 命中", f"{len(vs) - rag_count} / {rag_count}")
        m4.metric("Token 消耗", result.token_used)
