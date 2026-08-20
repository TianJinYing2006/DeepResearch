# -*- coding: utf-8 -*-
"""文档摄取：解析、分块、向量化、写入 Qdrant。

支持 PDF / Word / Markdown / 纯文本。分块策略：按段落/标题切分，控制块大小。
"""
from __future__ import annotations

import os
from typing import List

from openai import OpenAI
from qdrant_client.models import PointStruct

from config import config
from research_engine.rag.store import VectorStore


class DocumentIngester:
    """文档摄取器。"""

    def __init__(self):
        self.store = VectorStore()
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        """懒加载 embedding 客户端。"""
        if self._client is None:
            if not config.llm.api_key:
                raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用 Embedding")
            self._client = OpenAI(
                base_url=config.llm.base_url,
                api_key=config.llm.api_key,
            )
        return self._client

    # ---- 文档解析 ----
    def parse_file(self, path: str) -> str:
        """按扩展名解析文档为纯文本。"""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            return self._parse_pdf(path)
        if ext == ".docx":
            return self._parse_docx(path)
        if ext in (".md", ".markdown"):
            return self._parse_markdown(path)
        if ext in (".txt", ".text"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        raise ValueError(f"不支持的文档类型: {ext}")

    def _parse_pdf(self, path: str) -> str:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def _parse_docx(self, path: str) -> str:
        import docx
        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs)

    def _parse_markdown(self, path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    # ---- 分块 ----
    def chunk_text(self, text: str) -> List[str]:
        """按段落分块，合并小段，控制块大小。"""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: List[str] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) > config.rag.chunk_size and current:
                chunks.append(current)
                current = para
            else:
                current = (current + "\n\n" + para) if current else para
        if current:
            chunks.append(current)
        return chunks

    # ---- 向量化 ----
    def embed(self, texts: List[str]) -> List[List[float]]:
        """调用百炼 embedding 生成向量。"""
        resp = self._get_client().embeddings.create(
            model=config.rag.embedding_model,
            input=texts,
        )
        return [d.embedding for d in resp.data]

    # ---- 摄取入口 ----
    def ingest_file(self, path: str, doc_id: str) -> int:
        """摄取单个文档，返回写入的块数。"""
        text = self.parse_file(path)
        chunks = self.chunk_text(text)
        if not chunks:
            return 0
        vectors = self.embed(chunks)
        points = [
            PointStruct(
                id=hash(f"{doc_id}:{i}") % (2**63),
                vector=vectors[i],
                payload={
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "text": chunks[i],
                    "source": os.path.basename(path),
                },
            )
            for i in range(len(chunks))
        ]
        self.store.upsert(points)
        return len(points)

    def ingest_directory(self, dir_path: str) -> dict:
        """摄取目录下所有支持的文档，返回 {文件: 块数}。"""
        result = {}
        for fname in os.listdir(dir_path):
            fpath = os.path.join(dir_path, fname)
            if os.path.isfile(fpath):
                try:
                    count = self.ingest_file(fpath, doc_id=fname)
                    result[fname] = count
                except Exception as e:  # noqa: BLE001
                    result[fname] = f"error: {e}"
        return result
