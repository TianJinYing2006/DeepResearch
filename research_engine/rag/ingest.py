"""文档摄取：解析、分块、向量化、写入 Qdrant。

支持 PDF / Word / Markdown / 纯文本。分块策略：按段落/标题切分，控制块大小。

P0-8a 解析限额（防资源耗尽 / 压缩炸弹）：页数 / 抽取字符数 / 分块数 / 解析墙钟，
超限抛 :class:`IngestLimitExceeded`（API 层转结构化错误）。
"""
from __future__ import annotations

import os
import time
from typing import List

from openai import OpenAI
from qdrant_client.models import PointStruct

from config import config
from research_engine.rag.ids import stable_id as _stable_id
from research_engine.rag.store import VectorStore


class IngestLimitExceeded(ValueError):
    """文档超过解析限额（P0-8a）。"""


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
        """按扩展名解析文档为纯文本（P0-8a：受页数 / 字符数 / 墙钟限额约束）。"""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            return self._parse_pdf(path)
        if ext == ".docx":
            return self._parse_docx(path)
        if ext in (".md", ".markdown"):
            return self._parse_markdown(path)
        if ext in (".txt", ".text"):
            return self._parse_markdown(path)
        raise ValueError(f"不支持的文档类型: {ext}")

    def _check_chars(self, chars: int) -> None:
        if chars > config.rag.max_chars:
            raise IngestLimitExceeded(
                f"抽取字符数超过上限 {config.rag.max_chars}（当前 {chars}）")

    def _parse_pdf(self, path: str) -> str:
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = reader.pages
        if len(pages) > config.rag.max_pages:
            raise IngestLimitExceeded(
                f"PDF 页数 {len(pages)} 超过上限 {config.rag.max_pages}")
        deadline = time.monotonic() + config.rag.parse_timeout_seconds
        parts: List[str] = []
        chars = 0
        for page in pages:
            if time.monotonic() > deadline:
                raise IngestLimitExceeded(
                    f"解析超时（上限 {config.rag.parse_timeout_seconds:g}s）")
            text = page.extract_text() or ""
            chars += len(text)
            self._check_chars(chars)
            parts.append(text)
        return "\n".join(parts)

    def _parse_docx(self, path: str) -> str:
        import docx

        doc = docx.Document(path)
        deadline = time.monotonic() + config.rag.parse_timeout_seconds
        parts: List[str] = []
        chars = 0
        for index, paragraph in enumerate(doc.paragraphs):
            if index % 200 == 0 and time.monotonic() > deadline:
                raise IngestLimitExceeded(
                    f"解析超时（上限 {config.rag.parse_timeout_seconds:g}s）")
            chars += len(paragraph.text)
            self._check_chars(chars)
            parts.append(paragraph.text)
        return "\n".join(parts)

    def _parse_markdown(self, path: str) -> str:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read(config.rag.max_chars + 1)
        self._check_chars(len(text))
        return text

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
    EMBED_BATCH_SIZE = 10  # 百炼 embedding 单次调用上限，超了报 400 InvalidParameter

    def embed(self, texts: List[str]) -> List[List[float]]:
        """调用百炼 embedding 生成向量（分批，单批 ≤ EMBED_BATCH_SIZE）。"""
        vectors: List[List[float]] = []
        for i in range(0, len(texts), self.EMBED_BATCH_SIZE):
            batch = texts[i : i + self.EMBED_BATCH_SIZE]
            resp = self._get_client().embeddings.create(
                model=config.rag.embedding_model,
                input=batch,
            )
            vectors.extend(d.embedding for d in resp.data)
        return vectors

    # ---- 摄取入口 ----
    def ingest_file(self, path: str, doc_id: str, *, user_id: str | None = None,
                    tenant_id: str | None = None, visibility: str = "private") -> int:
        """摄取单个文档，返回写入的块数。

        P5 多租户隔离：`visibility` 必写（默认 private）；`user_id` / `tenant_id`
        仅在非空时写入 payload —— 无主块（历史 / 本地匿名摄入）保持「无字段」形态，
        由 `rag.scope.payload_matches` 统一判定可见性。
        """
        text = self.parse_file(path)
        chunks = self.chunk_text(text)
        if len(chunks) > config.rag.max_chunks:
            raise IngestLimitExceeded(
                f"分块数 {len(chunks)} 超过上限 {config.rag.max_chunks}")
        if not chunks:
            return 0
        vectors = self.embed(chunks)
        points = []
        for i in range(len(chunks)):
            payload = {
                "doc_id": doc_id,
                "chunk_index": i,
                "text": chunks[i],
                "source": os.path.basename(path),
                "visibility": visibility,
            }
            if user_id:
                payload["user_id"] = user_id
            if tenant_id:
                payload["tenant_id"] = tenant_id
            points.append(PointStruct(id=_stable_id(doc_id, i), vector=vectors[i], payload=payload))
        self.store.upsert(points)
        return len(points)

    def ingest_directory(self, dir_path: str, *, user_id: str | None = None,
                         tenant_id: str | None = None, visibility: str = "private") -> dict:
        """摄取目录下所有支持的文档，返回 {文件: 块数}。"""
        result = {}
        for fname in os.listdir(dir_path):
            fpath = os.path.join(dir_path, fname)
            if os.path.isfile(fpath):
                try:
                    count = self.ingest_file(fpath, doc_id=fname, user_id=user_id,
                                             tenant_id=tenant_id, visibility=visibility)
                    result[fname] = count
                except Exception as e:  # noqa: BLE001
                    result[fname] = f"error: {e}"
        return result
