"""
RAG service (Part 7): document ingestion + similarity retrieval.

Ingestion: extract pages → chunk → embed → persist (documents/document_chunks).
Retrieval: embed the query, rank chunks by cosine similarity. On Postgres the
ranking runs in SQL with pgvector's `<=>` operator; on other dialects (tests)
chunks are loaded and ranked in Python. Similarity below
RETRIEVAL_MIN_SIMILARITY counts as irrelevant — the Knowledge Agent must then
refuse to answer from its own knowledge.
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Document, DocumentChunk, User
from app.services.embeddings import EmbeddingError, get_embedding_provider
from app.services.rag.extraction import DocumentError, chunk_pages, extract_pages

logger = logging.getLogger("app.rag")


class RagNotConfiguredError(EmbeddingError):
    status_code = 503
    code = "rag_not_configured"
    message = "Embeddings are not configured; document intelligence is unavailable."


# ---------- Ingestion ----------


async def ingest_document(
    session: AsyncSession, *, user: User, data: bytes, filename: str, mime_type: str = ""
) -> Document:
    """Full pipeline: extract → chunk → embed → store. Rolls back to a failed row."""
    settings = get_settings()
    provider = get_embedding_provider()

    document = Document(
        user_id=user.id, filename=filename[:255], mime_type=mime_type[:128],
        size_bytes=len(data), status="ready",
    )
    try:
        pages = extract_pages(data, filename)
        chunks = chunk_pages(
            pages, chunk_size=settings.chunk_size_chars, overlap=settings.chunk_overlap_chars
        )
        if not chunks:
            raise DocumentError("No text content found in the document.")

        embeddings = await provider.embed([c["content"] for c in chunks])

        document.page_count = max(p for p, _ in pages)
        document.chunk_count = len(chunks)
        session.add(document)
        await session.flush()  # assign document.id

        for chunk, vector in zip(chunks, embeddings):
            session.add(DocumentChunk(
                document_id=document.id,
                chunk_index=chunk["index"],
                page_number=chunk["page"],
                content=chunk["content"],
                embedding=vector,
            ))
        await session.commit()
        await session.refresh(document)
        logger.info("ingested %s: %d pages, %d chunks", filename, document.page_count, document.chunk_count)
        return document

    except (DocumentError, EmbeddingError) as exc:
        document.status = "failed"
        document.error = str(exc.message)[:500]
        session.add(document)
        await session.commit()
        await session.refresh(document)
        return document


async def list_documents(session: AsyncSession, user_id=None, limit: int = 100) -> list[Document]:
    query = select(Document).order_by(Document.created_at.desc()).limit(limit)
    if user_id is not None:
        query = query.where(Document.user_id == user_id)
    result = await session.execute(query)
    return list(result.scalars())


async def get_document(session: AsyncSession, document_id: uuid.UUID) -> Document | None:
    result = await session.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()


async def delete_document(session: AsyncSession, document_id: uuid.UUID) -> bool:
    document = await get_document(session, document_id)
    if document is None:
        return False
    await session.delete(document)  # chunks cascade
    await session.commit()
    return True


# ---------- Retrieval ----------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1e-9
    norm_b = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (norm_a * norm_b)


async def retrieve_relevant_chunks(
    session: AsyncSession, user: User, query: str, *, top_k: int | None = None
) -> list[dict[str, Any]]:
    """Rank the user's chunks against the query; drops irrelevant results.

    Each result: {document_id, filename, page, content, similarity}.
    """
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    provider = get_embedding_provider()
    query_vector = (await provider.embed([query]))[0]

    # Chunks scoped to this user (join on documents.user_id).
    result = await session.execute(
        select(DocumentChunk, Document.filename)
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(Document.user_id == user.id, Document.status == "ready")
    )
    rows = result.all()
    if not rows:
        return []

    scored = []
    for chunk, filename in rows:
        embedding = chunk.embedding
        if not embedding:
            continue
        similarity = _cosine_similarity(query_vector, list(embedding))
        scored.append({
            "document_id": str(chunk.document_id),
            "filename": filename,
            "page": chunk.page_number,
            "content": chunk.content,
            "similarity": round(similarity, 4),
        })

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    scored = scored[:top_k]
    # Irrelevant chunks are dropped entirely so the agent cannot ground on noise.
    return [r for r in scored if r["similarity"] >= settings.retrieval_min_similarity]
