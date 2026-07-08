"""
Real ChromaDB + Ollama embedding integration tests for RAG retrieval.

Kept separate from tests/test_violation2_closure.py because that file is a
fast mocked regression suite; these tests require a running Ollama instance,
nomic-embed-text, and chromadb, and take tens of seconds per run.
"""

from __future__ import annotations

from pathlib import Path

import chromadb
import pytest

from aura.core.ollama_client import OllamaClient
from aura.memory.context_retriever import augment_prompt_with_rag, retrieve_context

_EMBEDDING_MODEL = "nomic-embed-text"

_SEED_DOCS: dict[str, str] = {
    "auth_jwt": (
        "Project decision: use JWT bearer tokens for API authentication "
        "with refresh rotation and short-lived access tokens."
    ),
    "auth_session": (
        "Project decision: use HttpOnly session cookies for browser login, "
        "separate from API JWT authentication."
    ),
    "db": (
        "Project decision: PostgreSQL is the primary relational database; "
        "Redis is used for session cache only, not as the system of record."
    ),
    "deploy": (
        "Project decision: deploy production workloads to Kubernetes on AWS EKS "
        "with GitHub Actions CI/CD pipelines and blue-green rollouts."
    ),
}


def _require_ollama_embeddings() -> OllamaClient:
    config = {
        "ollama": {
            "base_url": "http://localhost:11434",
            "timeout": 60,
            "retries": 1,
            "health_check_timeout": 5,
        },
        "models": {"embeddings": _EMBEDDING_MODEL},
    }
    client = OllamaClient(config)
    if not client.health_check():
        pytest.skip("Ollama not running at localhost:11434")
    probe = client.embed(_EMBEDDING_MODEL, "embedding availability probe")
    if len(probe) < 64:
        pytest.skip(f"Embeddings model {_EMBEDDING_MODEL!r} not available or returned empty vector")
    return client


def _require_chromadb() -> None:
    try:
        import chromadb  # noqa: F401, PLC0415
    except ImportError as exc:
        pytest.skip(f"chromadb not installed: {exc}")


def _seed_collection(persist_path: str, ollama: OllamaClient) -> None:
    """Seed aura_memory with real Ollama embeddings — same collection contract as production."""
    client = chromadb.PersistentClient(path=persist_path)
    try:
        client.delete_collection("aura_memory")
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name="aura_memory",
        metadata={"hnsw:space": "cosine"},
    )
    for doc_id, text in _SEED_DOCS.items():
        embedding = ollama.embed(_EMBEDDING_MODEL, text)
        assert embedding, f"Ollama returned empty embedding for seeded doc {doc_id!r}"
        collection.add(ids=[doc_id], documents=[text], embeddings=[embedding])


@pytest.fixture(scope="module")
def ollama_client() -> OllamaClient:
    _require_chromadb()
    return _require_ollama_embeddings()


@pytest.fixture()
def seeded_rag_config(tmp_path: Path, ollama_client: OllamaClient) -> dict:
    persist = str(tmp_path / "chroma_store")
    _seed_collection(persist, ollama_client)
    return {
        "models": {"embeddings": _EMBEDDING_MODEL},
        "memory": {"persist_path": persist, "max_results": 3},
        "routing": {"rag_confidence_threshold": 0.50, "rag_rank_margin": 0.03},
        "ollama": {
            "base_url": "http://localhost:11434",
            "timeout": 60,
            "retries": 1,
            "health_check_timeout": 5,
        },
    }


@pytest.fixture()
def empty_rag_config(tmp_path: Path) -> dict:
    persist = str(tmp_path / "empty_chroma")
    client = chromadb.PersistentClient(path=persist)
    client.get_or_create_collection(
        name="aura_memory",
        metadata={"hnsw:space": "cosine"},
    )
    return {
        "models": {"embeddings": _EMBEDDING_MODEL},
        "memory": {"persist_path": persist, "max_results": 3},
        "routing": {"rag_confidence_threshold": 0.50, "rag_rank_margin": 0.03},
        "ollama": {
            "base_url": "http://localhost:11434",
            "timeout": 60,
            "retries": 1,
            "health_check_timeout": 5,
        },
    }


class TestRagIntegrationReal:
    """End-to-end: real Ollama embeddings → real ChromaDB query → real prompt augmentation."""

    def test_relevant_auth_query_returns_auth_document(
        self, seeded_rag_config: dict, ollama_client: OllamaClient
    ) -> None:
        query = "what did we decide about authentication"
        chunks = retrieve_context(query, seeded_rag_config, ollama_client)

        assert chunks, "retrieve_context returned empty for a query that should match auth seed"
        assert chunks[0] == _SEED_DOCS["auth_jwt"], (
            f"Top result must be JWT auth doc; got: {chunks[0]!r}"
        )
        assert _SEED_DOCS["db"] not in chunks, (
            "PostgreSQL doc must not bleed into auth query after rank-margin filter"
        )
        assert _SEED_DOCS["deploy"] not in chunks

        augmented = augment_prompt_with_rag(query, chunks)
        assert "JWT bearer tokens" in augmented
        assert "PostgreSQL" not in augmented
        assert query in augmented

    def test_auth_adjacent_documents_both_included_when_closely_ranked(
        self, seeded_rag_config: dict, ollama_client: OllamaClient
    ) -> None:
        """JWT + session-cookie docs should both survive margin filter for broad auth queries."""
        query = "what did we decide about authentication"
        chunks = retrieve_context(query, seeded_rag_config, ollama_client)

        assert _SEED_DOCS["auth_jwt"] in chunks
        assert _SEED_DOCS["auth_session"] in chunks
        assert len(chunks) == 2

    def test_augmented_prompt_excludes_postgres_noise_for_auth_query(
        self, seeded_rag_config: dict, ollama_client: OllamaClient
    ) -> None:
        """Traced example: full augmented prompt must not inject cross-topic rank-2 bleed."""
        query = "what did we decide about authentication"
        chunks = retrieve_context(query, seeded_rag_config, ollama_client)
        augmented = augment_prompt_with_rag(query, chunks)

        # augment_prompt_with_rag includes every chunk from retrieve_context — no top-1-only step.
        assert "Relevant project context:" in augmented
        assert "JWT bearer tokens" in augmented
        assert "HttpOnly session cookies" in augmented
        assert "PostgreSQL" not in augmented, (
            "Postgres rank-2 bleed would add database noise to an auth question; "
            f"full prompt was:\n{augmented}"
        )

    def test_irrelevant_weather_query_returns_empty_below_threshold(
        self, seeded_rag_config: dict, ollama_client: OllamaClient
    ) -> None:
        query = "what is the weather forecast for Tuesday"
        chunks = retrieve_context(query, seeded_rag_config, ollama_client)

        assert chunks == [], (
            "Irrelevant query should yield no chunks above rag_confidence_threshold "
            f"({seeded_rag_config['routing']['rag_confidence_threshold']}); got {chunks!r}"
        )
        assert augment_prompt_with_rag(query, chunks) == query

    def test_empty_collection_returns_empty_list_cleanly(
        self, empty_rag_config: dict, ollama_client: OllamaClient
    ) -> None:
        query = "what did we decide about authentication"
        chunks = retrieve_context(query, empty_rag_config, ollama_client)
        assert chunks == []


@pytest.fixture(scope="session", autouse=True)
def _chromadb_installed() -> None:
    _require_chromadb()
