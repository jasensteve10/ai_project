import os

import pytest

from src.retrieval.bm25 import BM25, tokenize
from src.retrieval.corpus import build_cards, corpus_hash
from src.retrieval.fusion import rrf

TOY = [
    {'id': 'a', 'kind': 'entity', 'title': 'Région HAUT- SASSANDRA', 'text': "region = 'HAUT- SASSANDRA'"},
    {'id': 'b', 'kind': 'entity', 'title': 'Parti PDCI-RDA', 'text': "parti = 'PDCI-RDA'"},
    {'id': 'c', 'kind': 'domain', 'title': 'Participation', 'text': 'participation = SUM(votants) / SUM(inscrits)'},
]


def test_tokenizer_folds_accents_and_keeps_compound_labels():
    assert 'gbeke' in tokenize('Gbêkê')
    toks = tokenize('PDCI-RDA 001')
    assert {'pdci-rda', 'pdci', 'rda', '001'} <= set(toks)


def test_bm25_ranks_exact_labels_first():
    bm = BM25(TOY)
    assert bm.search('participation dans le Haut-Sassandra', 3)[0][0] == 'a'
    assert bm.search('voix du PDCI', 3)[0][0] == 'b'
    assert bm.search('xyz', 3) == []


def test_rrf_combines_rankings():
    fused = rrf([[('a', 0), ('b', 0)], [('b', 0), ('c', 0)]], k=60)
    assert [cid for cid, _ in fused] == ['b', 'a', 'c']
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)


def test_corpus_ids_are_stable_and_complete(database):
    first, second = build_cards(database), build_cards(database)
    assert corpus_hash(first) == corpus_hash(second)
    ids = {c['id'] for c in first}
    assert len(ids) == len(first)
    assert sum(c['kind'] == 'entity' for c in first) == 33 + 205 + 43
    assert {'schema:vw_vainqueur', 'domain:regional_turnout', 'entity:circ:115',
            "entity:region:DISTRICT AUTONOME D'ABIDJAN", 'entity:party:INDEPENDANT'} <= ids


def test_dense_retriever_offline_if_model_cached(database, tmp_path):
    pytest.importorskip('sentence_transformers')
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    from src.retrieval.dense import DenseRetriever
    cards = build_cards(database)
    try:
        dense = DenseRetriever(cards, cache_dir=tmp_path)
    except Exception as exc:  # model not downloaded: optional dependency
        pytest.skip(f'dense model unavailable offline: {exc}')
    top = [cid for cid, _ in dense.search('participation dans la région du Haut-Sassandra', 5)]
    assert 'entity:region:HAUT- SASSANDRA' in top
    assert list(tmp_path.glob('*.npy'))  # embedding cache written
