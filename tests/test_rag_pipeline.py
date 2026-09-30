import json
from types import SimpleNamespace

import pytest

from src.agent.Agent import ElectionSQLAgent
from src.retrieval.corpus import build_cards, verified_cards, write_cards
from src.retrieval.pipeline import ElectionRAG, run_with_context
from src.retrieval.context import ContextBuilder as EvaluationContext
from src.retrieval.context import ContextBuilder


class ScriptedLLM:
    def __init__(self, *payloads):
        self.payloads = iter(payloads)
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(content=json.dumps(next(self.payloads)), usage_metadata={})


def test_app_and_evaluation_use_identical_context_and_execute(database):
    assert EvaluationContext is ContextBuilder
    llm = ScriptedLLM({'status': 'answerable', 'sql': 'SELECT SUM(inscrits) AS n FROM mart.vw_circonscriptions'})
    agent = ElectionSQLAgent(llm=llm, db_path=database)
    rag = ElectionRAG('B', agent=agent, db_path=database)
    q = 'Quel est le nombre total des inscrits ?'
    expected, _ = rag.builder.build(rag.config['conditions']['B'], {'question': q})
    result = rag.run_query(q)
    assert result['ok'] and result['rows'] == [(8597092,)]
    assert result['context_ids'] == expected
    assert [c['id'] for c in result['sources']] == expected
    assert rag.builder.render(expected) in llm.messages[0][0].content
    assert len(result['dataset_source']['sha256']) == 64
    assert result['condition'] == 'B'


def test_rag_source_search_without_llm_or_key(monkeypatch, database):
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    rag = ElectionRAG('B', db_path=database)
    cards = rag.search('circonscription 001')
    card = next(c for c in cards if c['id'] == 'entity:circ:001')
    assert card['source_pages'] == [1]
    assert rag.agent is None


def test_stale_corpus_rejected(database, tmp_path):
    cards = build_cards(database)
    cards[0]['text'] += ' outdated'
    path = tmp_path / 'cards.jsonl'
    write_cards(cards, path)
    with pytest.raises(ValueError, match='périmé'):
        verified_cards(database, path)


def test_agent_retrieval_error_has_structured_trace(database):
    llm = ScriptedLLM({'status': 'needs_context', 'search': 'Poro'})
    agent = ElectionSQLAgent(llm=llm, db_path=database)
    def broken(query):
        raise RuntimeError('local index unavailable')
    result = agent.run_query('Participation Poro', context='', retrieve=broken, max_retrievals=1)
    assert not result['ok'] and result['stage'] == 'retrieval'
    assert result['api_calls'] == 1 and result['retrieval_calls'] == 1


def test_additional_sources_are_preserved_and_context_is_per_question(database):
    llm = ScriptedLLM(
        {'status': 'needs_context', 'search': 'circonscription 205'},
        {'status': 'answerable', 'sql': 'SELECT COUNT(*) AS n FROM mart.vw_circonscriptions'},
        {'status': 'unsupported', 'sql': None, 'response': 'Donnée absente.'})
    agent = ElectionSQLAgent(llm=llm, db_path=database)
    rag = ElectionRAG('B', agent=agent, db_path=database)
    cond = {'context': 'retrieval', 'retriever': 'bm25', 'max_retrievals': 1}
    result = run_with_context(agent, rag.builder, cond, {'question': 'Participation nationale'})
    assert result['ok'] and result['retrieval_calls'] == 1
    assert 'entity:circ:205' in result['added_context_ids']
    assert set(result['added_context_ids']) <= {c['id'] for c in result['sources']}
    assert result['agent_searches'][0]['search'] == 'circonscription 205'
    rag.run_query('Résultats de 2020 ?')
    assert '[entity:circ:205]' not in llm.messages[-1][0].content


def test_oracle_labels_cannot_be_selected_in_application():
    with pytest.raises(ValueError, match='condition'):
        ElectionRAG('ORACLE')


def test_gemini_typed_text_blocks_execute_without_repair(database):
    class BlockLLM:
        def invoke(self, messages):
            return SimpleNamespace(content=[
                {'type': 'reasoning', 'reasoning': 'ignored'},
                {'type': 'text', 'text': '{"status":"answerable",'},
                {'type': 'text', 'text': '"sql":"SELECT COUNT(*) FROM mart.vw_circonscriptions"}'},
            ], usage_metadata={})
    result = ElectionSQLAgent(llm=BlockLLM(), db_path=database).run_query('Combien de circonscriptions ?')
    assert result['ok'] and result['rows'] == [(205,)]
    assert result['api_calls'] == 1


def test_budget_refusal_does_not_count_as_provider_call(database):
    from src.evaluation.runner import MeteredLLM
    metered = MeteredLLM(ScriptedLLM(), max_calls=0)
    result = ElectionSQLAgent(llm=metered, db_path=database).run_query('Combien ?')
    assert result['error_type'] == 'BudgetExceeded'
    assert result['api_calls'] == 0 == metered.calls
