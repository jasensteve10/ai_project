"""Dense bi-encoder retrieval with a pretrained multilingual E5 model (no training)."""
import hashlib
import os
import tempfile
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / 'dataset/retrieval/embeddings'
DEFAULT_MODEL = 'intfloat/multilingual-e5-small'
DEFAULT_REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'


class DenseRetriever:
    def __init__(self, cards, model_name=DEFAULT_MODEL, cache_dir=CACHE_DIR, corpus_version=None, device=None,
                 revision=None, download=False):
        from sentence_transformers import SentenceTransformer
        if not cards:
            raise ValueError('Dense retrieval requires a nonempty corpus')
        self.cards, self.model_name = cards, model_name
        self.revision = revision or (DEFAULT_REVISION if model_name == DEFAULT_MODEL else None)
        if not self.revision:
            raise ValueError('An explicit dense model revision is required')
        try:
            self.model = SentenceTransformer(model_name, revision=self.revision, device=device or 'cpu',
                                              local_files_only=not download)
        except OSError as exc:
            raise RuntimeError('Modèle local absent : python -m src.retrieval.cli prepare --download-model') from exc
        # E5 expects "query: " / "passage: " prefixes; vectors are L2-normalized for cosine.
        passages = [f"passage: {c['title']}\n{c['text']}" for c in cards]
        key = hashlib.sha256((model_name + self.revision + (corpus_version or '') + '\x00'.join(passages)).encode()).hexdigest()[:16]
        path = Path(cache_dir) / f'{model_name.replace("/", "__")}_{key}.npy'
        if path.exists():
            self.matrix = np.load(path, allow_pickle=False)
        else:
            self.matrix = self.model.encode(passages, batch_size=32, normalize_embeddings=True,
                                            convert_to_numpy=True, show_progress_bar=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.npy', delete=False) as f:
                temporary = f.name
                np.save(f, self.matrix, allow_pickle=False)
            os.replace(temporary, path)
        expected = (len(cards), self.model.get_embedding_dimension())
        if self.matrix.shape != expected or not np.isfinite(self.matrix).all():
            raise ValueError(f'Invalid embedding cache: {path}; remove it and rebuild')

    @property
    def model_revision(self):
        return self.revision

    def search(self, query, k=10):
        q = self.model.encode([f'query: {query}'], normalize_embeddings=True, convert_to_numpy=True,
                              show_progress_bar=False)[0]
        sims = self.matrix @ q
        order = np.argsort(-sims, kind='stable')[:k]
        return [(self.cards[i]['id'], float(sims[i])) for i in order]
