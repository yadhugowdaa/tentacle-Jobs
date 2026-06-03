"""Local text embeddings via fastembed (BGE-small). Downloaded once, runs on CPU, free."""

from __future__ import annotations

from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    from tentacle_apply.config import settings

    return TextEmbedding(model_name=settings.embed_model)


def embed(texts: list[str]) -> np.ndarray:
    """Return an (n, dim) float32 matrix of embeddings for the given texts."""
    vectors = list(_model().embed(texts))
    return np.asarray(vectors, dtype="float32")
