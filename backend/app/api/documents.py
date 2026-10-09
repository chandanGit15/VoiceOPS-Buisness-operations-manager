"""Documents endpoints (Part 7): upload, list, detail, delete, search."""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import rate_limit
from app.core.security import get_current_user
from app.database.session import get_session
from app.models import User
from app.services import rag
from app.services.embeddings import EmbeddingError

logger = logging.getLogger("app.api.documents")

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(rate_limit(window_seconds=60, per_minute_setting="upload_rate_limit_per_min"))],
)

SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".docx")


def _error(status_code: int, code: str, message: str):
    from fastapi.responses import JSONResponse

    from app.core.errors import error_payload

    return JSONResponse(status_code=status_code, content=error_payload(code, message))


class DocumentOut(BaseModel):
    id: str
    filename: str
    size_bytes: int
    page_count: int
    chunk_count: int
    status: str
    error: str | None = None
    created_at: str | None = None


@router.post("", response_model=DocumentOut, status_code=201, tags=["documents"])
async def upload_document(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Upload + ingest a document: extract, chunk, embed, store."""
    settings = get_settings()
    filename = file.filename or "unnamed"
    if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
        return _error(415, "unsupported_media_type",
                      "Supported formats: PDF, TXT, DOCX.")

    data = await file.read()
    if not data:
        return _error(422, "validation_error", "Uploaded file is empty.")
    if len(data) > settings.upload_max_mb * 1024 * 1024:
        return _error(413, "payload_too_large",
                      f"File exceeds {settings.upload_max_mb} MB.")

    document = await rag.ingest_document(session, user=user, data=data, filename=filename,
                                         mime_type=file.content_type or "")
    if document.status == "failed":
        return _error(422, "document_error", document.error or "Document processing failed.")

    return _doc_out(document)


@router.get("", response_model=list[DocumentOut], tags=["documents"])
async def list_documents(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    rows = await rag.list_documents(session, user.id)
    return [_doc_out(d) for d in rows]


@router.get("/search", tags=["documents"])
async def search_documents(
    q: str = Query(min_length=1, max_length=500),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Semantic search across the user's documents (returns cited passages)."""
    try:
        chunks = await rag.retrieve_relevant_chunks(session, user, q)
    except EmbeddingError as exc:
        return _error(exc.status_code, exc.code, exc.message)
    return {
        "query": q,
        "count": len(chunks),
        "results": [
            {
                "source": f"{c['filename']} — Page {c['page']}",
                "filename": c["filename"],
                "page": c["page"],
                "similarity": c["similarity"],
                "excerpt": c["content"][:300],
            }
            for c in chunks
        ],
    }


@router.delete("/{document_id}", tags=["documents"])
async def delete_document(document_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    import uuid as uuid_module

    try:
        doc_uuid = uuid_module.UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc

    # Ownership check: foreign documents look missing (404, not 403).
    document = await rag.get_document(session, doc_uuid)
    if document is None or document.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    deleted = await rag.delete_document(session, doc_uuid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True}


def _doc_out(d) -> DocumentOut:
    return DocumentOut(
        id=str(d.id), filename=d.filename, size_bytes=d.size_bytes,
        page_count=d.page_count, chunk_count=d.chunk_count,
        status=d.status, error=d.error,
        created_at=d.created_at.isoformat() if d.created_at else None,
    )
