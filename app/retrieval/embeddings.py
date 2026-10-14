import hashlib
import math
from collections import Counter
from functools import lru_cache
from typing import Protocol

from app.config import get_settings
from app.retrieval.text import terms


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic, offline embedding (signed feature hashing of unigrams + bigrams).

    Not a semantic model. It exists so the whole pipeline, the test-suite and CI run with
    zero API keys and zero model downloads. Production uses OpenAIEmbedder.
    """

    def __init__(self, dim: int):
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        toks = terms(text)
        feats = Counter(toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])])
        vec = [0.0] * self.dim
        for feat, tf in feats.items():
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += (1.0 if (h >> 63) & 1 else -1.0) * (1.0 + math.log(tf))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, dim: int):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, max_retries=4, timeout=30)
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 96):
            resp = self.client.embeddings.create(
                model=self.model, input=texts[i : i + 96], dimensions=self.dim
            )
            out.extend(d.embedding for d in resp.data)
        return out


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "openai":
        if not s.openai_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAIEmbedder(s.openai_api_key, s.openai_embedding_model, s.embedding_dim)
    return HashingEmbedder(s.embedding_dim)
