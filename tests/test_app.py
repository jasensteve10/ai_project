from streamlit.testing.v1 import AppTest
from src.agent.Agent import ElectionSQLAgent


def test_app_success_abstention_and_clear(monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO', '0')  # the Docker image defaults to the public demo
    monkeypatch.setenv('GEMINI_MODEL', 'offline-test')
    monkeypatch.setattr(ElectionSQLAgent, '__init__', lambda self, **kw: None)
    responses = iter([
        {'ok': True, 'status': 'answerable', 'columns': ['count'], 'rows': [(205,)],
         'attempts': 1, 'final_sql': 'SELECT COUNT(*) FROM mart.vw_vainqueur'},
        {'ok': False, 'status': 'unsupported', 'response': 'Ces données ne contiennent pas les sièges.'},
    ])
    monkeypatch.setattr(ElectionSQLAgent, 'run_query', lambda self, q, **kw: next(responses))
    app = AppTest.from_file('../app/app.py').run(timeout=10)
    assert not app.exception
    app.chat_input[0].set_value('Combien de listes élues ?').run()
    assert not app.exception and len(app.dataframe) == 1
    app.chat_input[0].set_value('Combien de sièges ?').run()
    assert not app.exception and not app.error
    assert 'sièges' in app.info[-1].value
    app.button[0].click().run()
    assert not app.session_state['messages']
    assert not app.session_state['result_cache'].entries


def test_local_search_and_condition_switch(monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO', '0')
    monkeypatch.setenv('RAG_CONDITION', 'B')
    def unexpected(*args, **kwargs):
        raise AssertionError('Local search must not call the LLM')
    monkeypatch.setattr(ElectionSQLAgent, 'run_query', unexpected)
    app = AppTest.from_file('../app/app.py').run(timeout=10)
    app.toggle[0].set_value(True).run()
    app.chat_input[0].set_value('circonscription 001').run()
    assert not app.exception and not app.error
    result = app.session_state['messages'][-1]['result']
    assert result['status'] == 'search' and result['condition'] == 'B'
    assert any(c['id'] == 'entity:circ:001' for c in result['sources'])
    app.selectbox[0].select('A').run()
    app.chat_input[0].set_value('participation').run()
    assert not app.exception and not app.error
    result = app.session_state['messages'][-1]['result']
    assert result['condition'] == 'A'
    assert {c['kind'] for c in result['sources']} == {'schema', 'domain'}


def test_public_demo_never_builds_a_model_client(monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO', '1')
    monkeypatch.setenv('RAG_CONDITION', 'B')
    monkeypatch.setenv('GEMINI_MODEL', 'must-not-be-used')

    def forbidden(*args, **kwargs):
        raise AssertionError('Public demo must not construct or call a model client')
    monkeypatch.setattr(ElectionSQLAgent, '__init__', forbidden)
    monkeypatch.setattr(ElectionSQLAgent, 'run_query', forbidden)
    app = AppTest.from_file('../app/app.py').run(timeout=10)
    assert not app.exception
    assert all('sources uniquement' not in t.label for t in app.toggle)  # no model-call switch offered
    app.chat_input[0].set_value('Qui a gagné à Yopougon ?').run()
    assert not app.exception and not app.error
    assert app.session_state['messages'][-1]['result']['status'] == 'search'
    # Saved answer from the archived Haiku run, rendered with its retrieval context.
    app.selectbox[1].select('dev-002').run()
    saved = app.session_state['messages'][-1]['result']
    assert not app.exception and saved['saved'] and saved['condition'] == 'B'
    assert 129515 in saved['rows'][0] and saved['outcome'] == 'correct'
    assert len(app.dataframe) == 1
