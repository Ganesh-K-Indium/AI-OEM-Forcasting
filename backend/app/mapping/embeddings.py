"""Account / alias embeddings. Default: deterministic signed feature-hashing of char n-grams + words
(no network, no model download, reproducible). Swap in a sentence-transformer via `EMBEDDER=st:<model>`."""
from __future__ import annotations

import os
import zlib
from functools import lru_cache
from typing import Protocol

import numpy as np

from app.core.config import get_settings
from app.mapping.normalize import normalize_name


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingNgramEmbedder:
    def __init__(self, dim: int = 256, ngrams: tuple[int, ...] = (3, 4)):
        self.dim, self.ngrams = dim, ngrams

    def _features(self, text: str) -> list[str]:
        t = f" {normalize_name(text)} "
        feats = [f"c{n}:{t[i:i + n]}" for n in self.ngrams for i in range(len(t) - n + 1)]
        feats += [f"w:{w}" for w in t.split()] * 2
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim))
        for i, tx in enumerate(texts):
            for f in self._features(tx):
                h = zlib.crc32(f.encode())
                out[i, h % self.dim] += 1.0 if (h >> 16) & 1 else -1.0
            n = np.linalg.norm(out[i])
            if n > 0:
                out[i] /= n
        return out


class SentenceTransformerEmbedder:  # pragma: no cover - optional dependency
    def __init__(self, model: str, dim: int):
        from sentence_transformers import SentenceTransformer

        self.m = SentenceTransformer(model)
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        v = self.m.encode([normalize_name(t) for t in texts], normalize_embeddings=True)
        if v.shape[1] != self.dim:
            raise ValueError(f"model dim {v.shape[1]} != EMBEDDING_DIM {self.dim}")
        return v


@lru_cache
def get_embedder() -> Embedder:
    dim = get_settings().embedding_dim
    spec = os.environ.get("EMBEDDER", "hash")
    if spec.startswith("st:"):
        return SentenceTransformerEmbedder(spec[3:], dim)
    return HashingNgramEmbedder(dim)
