"""One retrieval/generation path for Streamlit, CLI and the evaluation harness."""
import hashlib
import json
import time
from pathlib import Path

from src.agent.Agent import DB_PATH, ElectionSQLAgent, runtime_fingerprint
from src.retrieval.context import ContextBuilder, Retrievers
from src.retrieval.corpus import CARDS_PATH, PDF_PATH, corpus_hash, verified_cards

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'configs/core.json'
APP_CONDITIONS = ('B', 'C', 'D', 'A', 'F1', 'F3')


def rag_fingerprint(condition, db_path=DB_PATH, config_path=CONFIG_PATH):
    digest = hashlib.sha256(runtime_fingerprint(db_path).encode())
    for path in [Path(config_path), CARDS_PATH, PDF_PATH, ROOT / 'src/semantic/glossary.json',
                 ROOT / 'docs/eda/tables/data_dictionary.csv',
                 *sorted((ROOT / 'src/retrieval').glob('*.py'))]:
        digest.update(path.read_bytes())
    digest.update(condition.encode())
    return digest.hexdigest()


def run_with_context(agent, builder, condition, record):
    """Return a normal agent result plus retrieval provenance; never uses an answer cache.

    record contains only the question in production. Oracle labels are supplied solely
    by the evaluation runner, and that diagnostic is not offered by the app.
    """
    start = time.monotonic()
    ids, _ = builder.build(condition, record)
    seconds = time.monotonic() - start
    searches = []
    kwargs = {'context': builder.render(ids)}
    if condition.get('max_retrievals'):
        kwargs.update(retrieve=builder.agent_callback(condition, ids, searches),
                      max_retrievals=condition['max_retrievals'])
    result = agent.run_query(record['question'], **kwargs)
    used = list(dict.fromkeys(ids + result.get('added_context_ids', [])))
    return {**result, 'context_ids': ids, 'agent_searches': searches,
            'retrieval_seconds': seconds, 'sources': [builder.by_id[cid] for cid in used]}


class ElectionRAG:
    def __init__(self, condition='B', *, agent=None, db_path=DB_PATH, config_path=CONFIG_PATH):
        if condition not in APP_CONDITIONS:
            raise ValueError(f'Unknown application condition: {condition}')
        self.condition = condition
        self.config = json.loads(Path(config_path).read_text())
        cards = verified_cards(db_path)
        self.corpus_version = corpus_hash(cards)
        retrievers = Retrievers(cards, self.config['dense_model'], self.corpus_version,
                                self.config.get('dense_revision'))
        self.builder = ContextBuilder(cards, self.config, retrievers)
        self.agent = agent
        self.db_path = db_path
        self.pdf_sha256 = hashlib.sha256(PDF_PATH.read_bytes()).hexdigest()

    def search(self, question):
        ids, _ = self.builder.build(self.config['conditions'][self.condition], {'question': question})
        return [self.builder.by_id[cid] for cid in ids]

    def run_query(self, question):
        if not question.strip():
            raise ValueError('La question est vide')
        if self.agent is None:
            self.agent = ElectionSQLAgent(db_path=self.db_path)
        result = run_with_context(self.agent, self.builder, self.config['conditions'][self.condition],
                                  {'question': question})
        return {**result, 'condition': self.condition, 'corpus_version': self.corpus_version,
                'dataset_source': {'file': PDF_PATH.name, 'sha256': self.pdf_sha256}}
