from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration comes from environment variables (or .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/rag"

    # "openai" for production quality, "anthropic" for Claude, "local" runs fully offline
    # (hashing embeddings + extractive answers): used by CI and the no-key demo.
    llm_provider: Literal["openai", "anthropic", "local"] = "local"
    embedding_provider: Literal["openai", "local"] = "local"

    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    anthropic_api_key: str | None = None
    anthropic_chat_model: str = "claude-opus-5-5"
    cohere_api_key: str | None = None  # optional: cross-encoder reranking via Cohere Rerank

    # Must match the embedding model: 1536 for text-embedding-3-small, any value for local.
    embedding_dim: int = 1536

    # Chunking
    chunk_max_tokens: int = 220
    chunk_overlap_tokens: int = 40

    # Retrieval
    retrieval_candidates: int = 20  # per retriever, before fusion
    top_k: int = 5  # chunks passed to the generator
    min_relevance: float = 0.45  # below this the assistant refuses instead of guessing

    api_key: str | None = None  # if set, write endpoints require the X-API-Key header
    seed_on_startup: bool = True
    seed_dir: str = "data/policies"


@lru_cache
def get_settings() -> Settings:
    return Settings()
