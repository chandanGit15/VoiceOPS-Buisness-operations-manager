"""
Knowledge Agent tools (Part 7): grounded Q&A over the user's uploaded documents.

Grounding rule (enforced in code + prompts): when the user explicitly asks
about their documents, the agent may only answer from retrieved chunks. If
retrieval finds nothing relevant, answer_from_documents returns an explicit
"not found in your documents" result — never a model-knowledge guess.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services import rag
from app.services.embeddings import EmbeddingError
from app.services.llm import get_llm_provider  # module-level: monkeypatch target
from app.tools.base import Tool, ToolError


class KnowledgeTool(Tool):
    """Shared plumbing: lazy RAG context from the injected request context."""

    ctx: dict | None = None

    def _session_and_user(self):
        if not self.ctx or "session" not in self.ctx:
            raise ToolError("Session context is missing; cannot access documents.")
        return self.ctx["session"], self.ctx["user"]

    async def _embed_or_refuse(self, texts: list[str]) -> list[list[float]]:
        from app.services.embeddings import get_embedding_provider

        try:
            return await get_embedding_provider().embed(texts)
        except EmbeddingError as exc:
            raise ToolError(exc.message) from exc


class SearchDocumentsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class SearchDocumentsTool(KnowledgeTool):
    name = "search_documents"
    description = (
        "Semantic search over the user's uploaded documents. Returns the most "
        "relevant passages with filename, page number, and similarity."
    )
    agent = "knowledge"

    class Args(SearchDocumentsArgs):
        pass

    async def _run(self, args: Args) -> dict:
        session, user = self._session_and_user()
        chunks = await rag.retrieve_relevant_chunks(session, user, args.query)
        if not chunks:
            return {"count": 0, "results": [], "note": "No relevant passages found in your documents."}
        return {
            "count": len(chunks),
            "results": [
                {
                    "source": f"{c['filename']} — Page {c['page']}",
                    "similarity": c["similarity"],
                    "excerpt": c["content"][:300],
                }
                for c in chunks
            ],
        }


class RetrieveRelevantChunksArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


class RetrieveRelevantChunksTool(KnowledgeTool):
    name = "retrieve_relevant_chunks"
    description = "Retrieve the full text of the top-k most relevant document chunks."
    agent = "knowledge"

    class Args(RetrieveRelevantChunksArgs):
        pass

    async def _run(self, args: Args) -> dict:
        session, user = self._session_and_user()
        chunks = await rag.retrieve_relevant_chunks(session, user, args.query, top_k=args.top_k)
        if not chunks:
            return {"count": 0, "chunks": [], "note": "No relevant passages found in your documents."}
        return {
            "count": len(chunks),
            "chunks": [
                {"source": f"{c['filename']} — Page {c['page']}", "content": c["content"], "similarity": c["similarity"]}
                for c in chunks
            ],
        }


class AnswerFromDocumentsArgs(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AnswerFromDocumentsTool(KnowledgeTool):
    """RAG answer: retrieve → grounded LLM call → answer + citations.

    This tool makes its own LLM call with a strict grounding prompt: the
    model sees ONLY the retrieved chunks and must cite them; with no
    relevant chunks it is never called at all.
    """

    name = "answer_from_documents"
    description = (
        "Answer a question strictly from the user's uploaded documents, with "
        "citations. Refuses to answer if the documents contain nothing relevant."
    )
    agent = "knowledge"

    class Args(AnswerFromDocumentsArgs):
        pass

    GROUNDED_PROMPT = (
        "You are the VoiceOps Knowledge Agent. Answer the user's question using "
        "ONLY the document passages provided below.\n"
        "Rules:\n"
        "- Cite every claim with its source in the exact format:\n"
        "  Source:\n  <filename>\n  Page <n>\n"
        "- If the passages do not contain the answer, reply exactly: "
        "\"I could not find that in your documents.\" and nothing else.\n"
        "- Never answer from general knowledge.\n"
        "- Be concise and voice-friendly (no markdown).\n\n"
        "Document passages:\n{context}\n\nQuestion: {question}"
    )

    async def _run(self, args: Args) -> dict:
        from app.services.llm import LLMError, LLMMessage, with_llm_retries

        session, user = self._session_and_user()
        chunks = await rag.retrieve_relevant_chunks(session, user, args.question)
        if not chunks:
            return {
                "answer": "I could not find that in your documents.",
                "sources": [],
                "grounded": False,
                "note": "No relevant passages; the model was not asked, so it could not guess.",
            }

        context = "\n\n".join(
            f"[Source {i + 1}: {c['filename']} — Page {c['page']}]\n{c['content']}"
            for i, c in enumerate(chunks)
        )
        prompt = self.GROUNDED_PROMPT.format(context=context, question=args.question)

        provider = get_llm_provider()
        try:
            response = await with_llm_retries(
                lambda: provider.generate(
                    [LLMMessage(role="user", content=prompt)],
                    system_prompt="You are a strictly document-grounded assistant.",
                    temperature=0.1,
                )
            )
        except LLMError as exc:
            raise ToolError(exc.message) from exc

        sources = [{"filename": c["filename"], "page": c["page"]} for c in chunks]
        return {"answer": response.text, "sources": sources, "grounded": True}
