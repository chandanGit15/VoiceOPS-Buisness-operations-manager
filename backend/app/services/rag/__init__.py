"""RAG package: extraction, chunking, ingestion, retrieval."""

from app.services.rag.extraction import (
    DocumentError,
    chunk_pages,
    extract_pages,
)
from app.services.rag.service import (
    delete_document,
    get_document,
    ingest_document,
    list_documents,
    retrieve_relevant_chunks,
)

__all__ = [
    "DocumentError",
    "chunk_pages",
    "extract_pages",
    "delete_document",
    "get_document",
    "ingest_document",
    "list_documents",
    "retrieve_relevant_chunks",
]
