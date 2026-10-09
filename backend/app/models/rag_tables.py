"""
RAG models (Part 7): uploaded documents and their embedded chunks.

Portability: the embedding column stores a pgvector `vector` on Postgres
(cosine search in SQL) and JSON on other dialects (SQLite tests compute
similarity in Python). Same model, dialect-appropriate storage.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from app.models.base import Base
from app.models.tables import _uuid

EMBED_DIM = 768  # text-embedding-004


class _PgVector(UserDefinedType):
    """pgvector column type (postgresql dialect only)."""

    cache_ok = True

    def get_col_spec(self) -> str:
        return f"vector({EMBED_DIM})"


def EmbeddingColumn():  # noqa: N802 - factory, mirrors SQLAlchemy naming
    return _PgVector().with_variant(JSON(), "sqlite", "mysql")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "ready" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(EmbeddingColumn(), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")
