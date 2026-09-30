from streamlit.testing.v1 import AppTest
from src.agent.Agent import ElectionSQLAgent

APP = '../app/app.py'


def _forbid_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('This mode must not construct or call a model client')
    monkeypatch.setattr(ElectionSQLAgent, '__init__', forbidden)
    monkeypatch.setattr(ElectionSQLAgent, 'run_query', forbidden)


def _live(monkeypatch, responses):
    monkeypatch.setattr(ElectionSQLAgent, '__init__', lambda self, **kw: None)
    monkeypatch.setattr(ElectionSQLAgent, 'run_query', lambda self, q, **kw: next(responses))


def test_live_answers_abstention_and_clear(monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO', '0')  # the Docker image defaults to the public demo
    monkeypatch.delenv('APP_ACCESS_CODE', raising=False)
    monkeypatch.setenv('GEMINI_MODEL', 'offline-test')
    _live(monkeypatch, iter([
        {'ok': True, 'status': 'answerable', 'columns': ['count'], 'rows': [(205,)],
         'final_sql': 'SELECT COUNT(*) FROM mart.vw_vainqueur'},
        {'ok': False, 'status': 'unsupported', 'response': 'Ces données ne contiennent pas les sièges.'},
    ]))
    app = AppTest.from_file(APP).run(timeout=10)
    assert not app.exception
    app.chat_input[0].set_value('Combien de listes élues ?').run()
    assert not app.exception and app.session_state['messages'][-1]['result']['rows'] == [(205,)]
    app.chat_input[0].set_value('Combien de sièges ?').run()
    assert not app.exception and not app.error
    assert any('sièges' in i.value for i in app.info)
    [b for b in app.button if 'Effacer' in b.label][0].click().run()
    assert not app.session_state['messages'] and not app.session_state['result_cache'].entries


def test_public_demo_examples_saved_page_and_modes(monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO', '1')
    monkeypatch.setenv('RAG_CONDITION', 'B')
    monkeypatch.setenv('GEMINI_MODEL', 'must-not-be-used')
    _forbid_model(monkeypatch)
    app = AppTest.from_file(APP).run(timeout=10)
    assert not app.exception
    # Typed question: retrieval context only.
    app.chat_input[0].set_value('circonscription 001').run()
    result = app.session_state['messages'][-1]['result']
    assert result['status'] == 'search' and any(c['id'] == 'entity:circ:001' for c in result['sources'])
    # "Needs RAG" example: the saved BM25 answer (correct), not a live call.
    app.button(key='ex-dev-001').click().run()
    saved = app.session_state['messages'][-1]['result']
    assert not app.exception and saved['saved'] and saved['condition'] == 'B' and saved['outcome'] == 'correct'
    # "Works without RAG" example runs in mode A.
    app.button(key='ex-dev-009').click().run()
    assert app.session_state['messages'][-1]['result']['condition'] == 'A'
    # Saved-evaluation tab: headline accuracies from the archived run.
    assert [m.value for m in app.metric] == ['11/14', '14/14', '13/14', '13/14']
    # Mode A context has no entity cards.
    app.selectbox(key='mode').select('A').run()
    app.chat_input[0].set_value('participation').run()
    result = app.session_state['messages'][-1]['result']
    assert {c['kind'] for c in result['sources']} == {'schema', 'domain'}


def test_access_code_gates_live_answers(monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO', '0')
    monkeypatch.setenv('APP_ACCESS_CODE', 'secret-code')
    monkeypatch.setenv('GEMINI_MODEL', 'offline-test')
    _forbid_model(monkeypatch)
    app = AppTest.from_file(APP).run(timeout=10)
    app.chat_input[0].set_value('Combien de listes élues ?').run()
    assert not app.exception and app.session_state['messages'][-1]['result']['status'] == 'search'
    app.text_input[0].set_value('wrong').run()
    app.chat_input[0].set_value('Combien de listes élues ?').run()
    assert app.session_state['messages'][-1]['result']['status'] == 'search'
    _live(monkeypatch, iter([{'ok': True, 'status': 'answerable', 'columns': ['n'], 'rows': [(205,)],
                              'final_sql': 'SELECT 205'}]))
    app.text_input[0].set_value('secret-code').run()
    app.chat_input[0].set_value('Combien de listes élues ?').run()
    assert not app.exception and app.session_state['messages'][-1]['result']['rows'] == [(205,)]
