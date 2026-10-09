"""
Document text extraction: PDF, TXT, DOCX.

Returns a list of (page_number, text) tuples; page 1 for page-less formats.
Extraction failures raise DocumentError with a user-safe message.
"""

from __future__ import annotations

import io

from app.core.errors import APIError


class DocumentError(APIError):
    status_code = 422
    code = "document_error"
    message = "The document could not be processed."


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".docx"}


def extract_pages(data: bytes, filename: str) -> list[tuple[int, str]]:
    """Extract (page_number, text) from raw bytes, dispatched by extension."""
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        return _extract_pdf(data)
    if lower.endswith(".docx"):
        return _extract_docx(data)
    if lower.endswith(".txt"):
        return _extract_txt(data)
    raise DocumentError(
        f"Unsupported file type: {filename!r}. Supported: PDF, TXT, DOCX."
    )


def _extract_txt(data: bytes) -> list[tuple[int, str]]:
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception as exc:
        raise DocumentError("Could not decode the text file.") from exc
    return [(1, text)]


def _extract_pdf(data: bytes) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((i, text))
        if not pages:
            raise DocumentError("No extractable text in the PDF (scanned image?).")
        return pages
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"PDF extraction failed: {type(exc).__name__}") from exc


def _extract_docx(data: bytes) -> list[tuple[int, str]]:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    except Exception as exc:
        raise DocumentError(f"DOCX extraction failed: {type(exc).__name__}") from exc
    if not paragraphs:
        raise DocumentError("No text found in the DOCX document.")
    return [(1, "\n".join(paragraphs))]


def chunk_pages(
    pages: list[tuple[int, str]],
    *,
    chunk_size: int,
    overlap: int,
) -> list[dict]:
    """Split page texts into overlapping chunks, preserving page numbers.

    A chunk never crosses pages, so citations stay precise.
    """
    chunks: list[dict] = []
    index = 0
    for page_number, text in pages:
        text = text.strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            piece = text[start : start + chunk_size].strip()
            if piece:
                chunks.append({"index": index, "page": page_number, "content": piece})
                index += 1
            if start + chunk_size >= len(text):
                break
            start += chunk_size - overlap
    return chunks
