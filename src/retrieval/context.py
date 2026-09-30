"""Experimental conditions: which cards each condition puts in the prompt.

A       all schema + domain cards (full-schema baseline with fixed definitions), no entity cards
B/C/D   per-kind top-k from BM25 / dense / RRF(BM25, dense)
C_FT    per-kind top-k from the fine-tuned dense retriever (src/pipeline/train.py)
F1/F3   the dev-selected static retriever + bounded agent search (1 or 3 retrieval calls)
ORACLE  human-labelled relevant cards (diagnostic: isolates generation/execution errors)
FULL    every card (optional diagnostic: long-context upper bound on coverage)
"""
from pathlib import Path

from src.retrieval.bm25 import BM25
from src.retrieval.corpus import format_cards
from src.retrieval.fusion import rrf

KINDS = ('schema', 'domain', 'entity')
ROOT = Path(__file__).resolve().parents[2]


class Retrievers:
    """Lazily built retrievers returning full rankings of card IDs."""

    def __init__(self, cards, dense_model, corpus_version, dense_revision=None, dense_ft_model=None):
        self.cards, self.dense_model, self.corpus_version = cards, dense_model, corpus_version
        self._bm25 = self._dense = self._dense_ft = None
        self.dense_revision, self.dense_ft_model = dense_revision, dense_ft_model

    @property
    def dense(self):
        if self._dense is None:
            from src.retrieval.dense import DenseRetriever
            self._dense = DenseRetriever(self.cards, self.dense_model, corpus_version=self.corpus_version,
                                         revision=self.dense_revision)
        return self._dense

    @property
    def dense_ft(self):
        if self._dense_ft is None:
            if not self.dense_ft_model:
                raise ValueError('No fine-tuned model configured (dense_ft_model)')
            from src.retrieval.dense import DenseRetriever
            self._dense_ft = DenseRetriever(self.cards, str(ROOT / self.dense_ft_model),
                                            corpus_version=self.corpus_version)
        return self._dense_ft

    def rank(self, name, query):
        n = len(self.cards)
        if name == 'bm25':
            if self._bm25 is None:
                self._bm25 = BM25(self.cards)
            return [cid for cid, _ in self._bm25.search(query, n)]
        if name == 'dense':
            return [cid for cid, _ in self.dense.search(query, n)]
        if name == 'dense_ft':
            return [cid for cid, _ in self.dense_ft.search(query, n)]
        if name == 'hybrid':
            return [cid for cid, _ in rrf([[(c, 0) for c in self.rank('bm25', query)],
                                           [(c, 0) for c in self.rank('dense', query)]], top=n)]
        raise ValueError(f'unknown retriever {name}')


def resolve_retriever(cond, config):
    name = cond.get('retriever')
    return config['agent']['static_retriever'] if name == '@agent' else name


class ContextBuilder:
    def __init__(self, cards, config, retrievers):
        self.cards = cards
        self.by_id = {c['id']: c for c in cards}
        self.config, self.retrievers = config, retrievers

    def quota_select(self, ranking, quotas, exclude=()):
        chosen, counts = [], dict.fromkeys(KINDS, 0)
        for cid in ranking:
            kind = self.by_id[cid]['kind']
            if cid not in exclude and counts[kind] < quotas.get(kind, 0):
                chosen.append(cid)
                counts[kind] += 1
        return chosen

    def build(self, cond, record, quotas=None):
        """Return (context_ids, ranking or None) for one question under one condition."""
        ctx = cond['context']
        if ctx == 'full_schema':
            return [c['id'] for c in self.cards if c['kind'] in ('schema', 'domain')], None
        if ctx == 'full_corpus':
            return [c['id'] for c in self.cards], None
        if ctx == 'oracle':
            ids = sorted({c for slot in record.get('evidence_slots') or [] for c in slot})
            return ids, None
        if ctx == 'retrieval':
            ranking = self.retrievers.rank(resolve_retriever(cond, self.config), record['question'])
            return self.quota_select(ranking, quotas or self.config['retrieval']['quotas']), ranking
        raise ValueError(f'unknown context {ctx}')

    def render(self, ids):
        return format_cards([self.by_id[i] for i in ids])

    def agent_callback(self, cond, context_ids, log):
        """Agent search: top ``search_k`` new cards of any kind for the model's own query."""
        seen = set(context_ids)
        name, k = resolve_retriever(cond, self.config), self.config['agent']['search_k']

        def retrieve(search):
            new = [cid for cid in self.retrievers.rank(name, search) if cid not in seen][:k]
            seen.update(new)
            log.append({'search': search, 'card_ids': new})
            return new, self.render(new)

        return retrieve
