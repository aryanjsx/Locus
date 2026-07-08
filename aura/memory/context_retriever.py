"""
RAG context retrieval for PROJECT_CONTEXT and UNKNOWN intents.

Per spec §5.4: ChromaDB failures are logged and the pipeline continues
without retrieved context — never raises into the voice pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aura.memory")


def augment_prompt_with_rag(prompt: str, chunks: list[str]) -> str:
    """Prepend retrieved context to the user prompt for LLM streaming.

    All chunks returned by retrieve_context() are included (up to memory.max_results
    after threshold + rank-margin filtering). There is no separate top-1-only step.
    """
    if not chunks:
        return prompt
    context = "\n".join(f"- {chunk}" for chunk in chunks)
    return f"Relevant project context:\n{context}\n\nUser question: {prompt}"


def retrieve_context(
    query: str,
    config: dict[str, Any],
    ollama_client: Any | None = None,
) -> list[str]:
    """Retrieve memory chunks for *query*. Returns [] on any failure."""
    if not query or not isinstance(query, str):
        return []

    query = query.strip()
    if not query:
        return []

    memory_cfg = config.get("memory", {})
    max_results = int(memory_cfg.get("max_results", 3))
    routing_cfg = config.get("routing", {})
    confidence_threshold = float(routing_cfg.get("rag_confidence_threshold", 0.50))
    # Secondary chunks must be within this cosine-similarity margin of rank-1.
    # 0.03 excludes cross-topic bleed (e.g. Postgres doc at ~0.55 when auth top is
    # ~0.60) while keeping closely related docs (JWT + session cookies at ~0.58–0.60).
    rank_margin = float(routing_cfg.get("rag_rank_margin", 0.03))
    embedding_model = config.get("models", {}).get("embeddings", "")

    collection = _open_collection(memory_cfg.get("persist_path", ".aura/memory"))
    if collection is None:
        return []

    if ollama_client is None or not embedding_model:
        logger.warning("RAG skipped — no Ollama client or embeddings model configured")
        return []

    try:
        embedding = ollama_client.embed(embedding_model, query)
    except Exception as exc:
        logger.warning("RAG embedding failed (continuing without context): %s", exc)
        return []

    if not embedding:
        return []

    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=max_results,
            include=["documents", "distances"],
        )
    except Exception as exc:
        logger.warning("RAG query failed (continuing without context): %s", exc)
        return []

    documents = results.get("documents") or [[]]
    distances = results.get("distances") or [[]]
    scored: list[tuple[str, float]] = []

    for doc, distance in zip(documents[0], distances[0]):
        if not doc or not isinstance(doc, str):
            continue
        # Chroma cosine distance: 0 = identical. Map to similarity in [0, 1].
        similarity = 1.0 - float(distance)
        if similarity >= confidence_threshold:
            scored.append((doc.strip(), similarity))

    if not scored:
        return []

    scored.sort(key=lambda item: item[1], reverse=True)
    top_similarity = scored[0][1]
    chunks: list[str] = []
    for doc, similarity in scored:
        if similarity < top_similarity - rank_margin:
            break
        chunks.append(doc)
        if len(chunks) >= max_results:
            break

    return chunks


def _open_collection(persist_path: str) -> Any | None:
    try:
        import chromadb  # noqa: PLC0415 — optional Phase 6 dependency
    except ImportError:
        logger.debug("chromadb not installed — RAG retrieval disabled")
        return None

    try:
        client = chromadb.PersistentClient(path=persist_path)
        return client.get_or_create_collection(
            name="aura_memory",
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:
        logger.warning("ChromaDB unavailable (continuing without context): %s", exc)
        return None
