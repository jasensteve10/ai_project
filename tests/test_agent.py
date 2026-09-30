import json
from types import SimpleNamespace
from google.api_core.exceptions import ResourceExhausted
from src.agent.Agent import ElectionSQLAgent
from src.agent.cache import ResultCache


class FakeLLM:
    def __init__(self, *responses):
        self.responses = iter(responses)

    def invoke(self, messages):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(content=json.dumps(response), usage_metadata={'total_tokens': 10})


def answer(sql):
    return {'status': 'answerable', 'sql': sql, 'response': None}


def test_initial_quota_failure_is_caught_and_retried(database):
    waits = []
    llm = FakeLLM(*[ResourceExhausted('quota') for _ in range(3)])
    result = ElectionSQLAgent(llm=llm, db_path=database, sleep=waits.append).run_query('question')
    assert result['stage'] == 'transport'
    assert result['attempts'] == 1
    assert result['api_calls'] == 3
    assert waits == [1, 2]


def test_transient_failure_then_success(database):
    llm = FakeLLM(ResourceExhausted('quota'), answer('SELECT COUNT(*) FROM mart.vw_vainqueur'))
    result = ElectionSQLAgent(llm=llm, db_path=database, sleep=lambda _: None).run_query('winners')
    assert result['ok'] and result['rows'] == [(205,)]
    assert result['api_calls'] == 2 and result['attempts'] == 1


def test_repair_counts_initial_attempt(database):
    llm = FakeLLM(answer('SELECT voix FROM mart.vw_vainqueur'), answer('SELECT score FROM mart.vw_vainqueur'))
    result = ElectionSQLAgent(llm=llm, db_path=database).run_query('scores')
    assert result['ok'] and result['attempts'] == 2
    assert result['proposed_sql'].startswith('SELECT voix')
    assert any(t['stage'] == 'execution' and not t['ok'] for t in result['trace'])


def test_abstention_and_clarification_do_not_execute_sql(database):
    for status in ['unsupported', 'needs_clarification']:
        llm = FakeLLM({'status': status, 'sql': None, 'response': 'Donnée absente ou question imprécise.'})
        result = ElectionSQLAgent(llm=llm, db_path=database).run_query('question')
        assert result['status'] == status
        assert result['attempts'] == 1 and result['error'] is None
        assert not any(t['stage'] == 'execution' for t in result['trace'])


def test_nontransient_transport_failure_does_not_retry(database):
    result = ElectionSQLAgent(llm=FakeLLM(ValueError('bad key')), db_path=database).run_query('question')
    assert result['stage'] == 'transport' and result['api_calls'] == 1


def test_failure_and_negative_answers_are_not_cached():
    cache, calls = ResultCache(), []
    def fail(question):
        calls.append(question)
        return {'ok': False, 'status': 'unsupported'}
    cache.query('q', 'v1', fail)
    cache.query('q', 'v1', fail)
    assert len(calls) == 2


def test_cache_version_ttl_copy_and_clear():
    now, calls = [0], []
    cache = ResultCache(ttl=5, max_entries=2, clock=lambda: now[0])
    def run(question):
        calls.append(question)
        return {'ok': True, 'status': 'answerable', 'rows': [[1]]}
    first = cache.query('q', 'v1', run)
    first['rows'][0][0] = 9
    hit = cache.query('q', 'v1', run)
    assert hit['cache_hit'] and hit['rows'] == [[1]]
    assert not cache.query('q', 'v2', run)['cache_hit']
    now[0] = 6
    assert not cache.query('q', 'v2', run)['cache_hit']
    cache.clear()
    assert not cache.query('q', 'v2', run)['cache_hit']
    assert len(calls) == 4


def test_malformed_generation_can_be_repaired(database):
    llm = FakeLLM({'invalid': True}, answer('SELECT 1'))
    result = ElectionSQLAgent(llm=llm, db_path=database).run_query('question')
    assert result['ok'] and result['attempts'] == 2
    assert any(t['stage'] == 'generation' and not t['ok'] for t in result['trace'])


def test_repair_budget_is_finite(database):
    llm = FakeLLM(*[answer('SELECT missing_column FROM mart.vw_vainqueur') for _ in range(4)])
    result = ElectionSQLAgent(llm=llm, db_path=database).run_query('question')
    assert not result['ok'] and result['stage'] == 'execution'
    assert result['attempts'] == 4 and result['api_calls'] == 4


def test_current_sdk_rate_limit_wrapper_retries(database):
    from langchain_google_genai.chat_models import GoogleRateLimitError
    llm = FakeLLM(GoogleRateLimitError('quota'), answer('SELECT 1'))
    result = ElectionSQLAgent(llm=llm, db_path=database, sleep=lambda _: None).run_query('question')
    assert result['ok'] and result['api_calls'] == 2


def test_fingerprint_changes_with_model_and_database(database, tmp_path):
    from src.agent.Agent import runtime_fingerprint
    v1 = runtime_fingerprint(database, model='model-a')
    assert v1 != runtime_fingerprint(database, model='model-b')
    altered = tmp_path / 'altered.duckdb'
    altered.write_bytes(database.read_bytes() + b'changed')
    assert v1 != runtime_fingerprint(altered, model='model-a')
