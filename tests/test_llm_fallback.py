"""Claude fallback: offline tests with fake clients (no Anthropic or Google calls)."""
import json
from types import SimpleNamespace

import pytest

from src.agent import llm
from src.agent.Agent import ElectionSQLAgent, _parse_response, runtime_fingerprint
from src.agent.llm import (ClaudeChat, ClaudeRefusal, FallbackBudgetExceeded, FallbackLLM, RESPONSE_SCHEMA,
                           is_transient)

@pytest.fixture(autouse=True)
def _isolate_gemini_chain(monkeypatch):
    """src.agent.Agent loads the developer's .env at import; tests set the chain explicitly."""
    monkeypatch.delenv('GEMINI_FALLBACK_MODELS', raising=False)


ANSWER = {'status': 'answerable', 'sql': 'SELECT COUNT(*) FROM mart.vw_vainqueur', 'response': None, 'search': None}


class FakeMessages:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def claude_response(payload, stop_reason='end_turn', model='claude-opus-5'):
    return SimpleNamespace(
        content=[SimpleNamespace(type='thinking', thinking=''),
                 SimpleNamespace(type='text', text=json.dumps(payload))],
        stop_reason=stop_reason, stop_details=SimpleNamespace(category='cyber') if stop_reason == 'refusal' else None,
        model=model, usage=SimpleNamespace(input_tokens=1200, output_tokens=40, cache_read_input_tokens=0))


def fake_claude(*responses, model='claude-opus-5'):
    messages = FakeMessages(*responses)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeChat(model, client=client), messages


def msg(kind, content):
    return SimpleNamespace(type=kind, content=content)


class Scripted:
    """Primary provider stand-in: raises or answers in order."""

    def __init__(self, *items):
        self.items, self.calls = list(items), 0

    def invoke(self, messages):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=json.dumps(item), usage_metadata={'input_tokens': 10, 'output_tokens': 5},
                               response_metadata={'model_name': 'gemini-test'})


class Transient(Exception):
    code = 503


class AuthFailure(Exception):
    code = 401


class BudgetExceeded(Exception):
    pass


# ---------------------------------------------------------------- Claude adapter
def test_claude_adapter_request_shape_and_response():
    chat, messages = fake_claude(claude_response(ANSWER))
    out = chat.invoke([msg('system', 'SYS'), msg('human', 'Question ?')])
    call = messages.calls[0]
    assert call['model'] == 'claude-opus-5' and call['system'] == 'SYS'
    assert call['messages'] == [{'role': 'user', 'content': 'Question ?'}]
    assert call['output_config']['format'] == {'type': 'json_schema', 'schema': RESPONSE_SCHEMA}
    assert call['fallbacks'] == 'default' and call['betas'] == ['server-side-fallback-2026-07-01']
    assert 'temperature' not in call  # rejected by Claude Opus 5
    assert json.loads(out.content) == ANSWER  # thinking blocks are skipped
    assert out.usage_metadata['input_tokens'] == 1200 and out.usage_metadata['output_tokens'] == 40
    assert out.response_metadata['provider'] == 'anthropic'


def test_haiku_runs_at_temperature_zero_without_refusal_fallback():
    chat, messages = fake_claude(claude_response(ANSWER, model='claude-haiku-4-5'), model='claude-haiku-4-5')
    out = chat.invoke([msg('system', 'SYS'), msg('human', 'Q')])
    call = messages.calls[0]
    assert call['extra_body'] == {'temperature': 0} and 'fallbacks' not in call and 'betas' not in call
    assert out.response_metadata['temperature'] == 0


def test_claude_refusal_raises_and_is_not_retried():
    chat, _ = fake_claude(claude_response(ANSWER, stop_reason='refusal'))
    with pytest.raises(ClaudeRefusal):
        chat.invoke([msg('system', 's'), msg('human', 'q')])
    assert not is_transient(ClaudeRefusal('x'))


@pytest.mark.parametrize('payload', [
    ANSWER,
    {'status': 'unsupported', 'sql': None, 'response': 'Donnée absente.', 'search': None},
    {'status': 'needs_clarification', 'sql': None, 'response': 'Laquelle ?', 'search': None},
])
def test_schema_payloads_parse_with_the_agent_contract(payload):
    assert _parse_response(json.dumps(payload))['status'] == payload['status']
    assert set(RESPONSE_SCHEMA['required']) == set(payload)


def test_anthropic_errors_are_classified_transient():
    for name in ('RateLimitError', 'OverloadedError', 'InternalServerError', 'APIConnectionError'):
        assert is_transient(type(name, (Exception,), {})())
    assert is_transient(SimpleNamespace(status_code=529))
    assert not is_transient(SimpleNamespace(status_code=400))


# ---------------------------------------------------------------- fallback policy
def fallback(primary, *claude_payloads, cap=5, clock=None):
    claude, messages = fake_claude(*[claude_response(p) for p in claude_payloads])
    kwargs = {'clock': clock} if clock else {}
    return FallbackLLM(primary, claude, primary_model='gemini-test', secondary_model='claude-opus-5',
                       max_fallback_calls=cap, **kwargs), messages


def test_healthy_gemini_never_calls_claude():
    wrapper, messages = fallback(Scripted(ANSWER))
    out = wrapper.invoke([msg('human', 'q')])
    assert out.response_metadata['provider'] == 'google' and messages.calls == []


def test_non_transient_gemini_failure_switches_immediately():
    wrapper, messages = fallback(Scripted(AuthFailure('revoked key')), ANSWER)
    out = wrapper.invoke([msg('human', 'q')])
    assert out.response_metadata['provider'] == 'anthropic' and len(messages.calls) == 1
    assert 'AuthFailure' in out.response_metadata['fallback_reason']


def test_transient_failure_retries_gemini_before_falling_back():
    wrapper, messages = fallback(Scripted(Transient('503'), Transient('503')), ANSWER)
    with pytest.raises(Transient):
        wrapper.invoke([msg('human', 'q')])  # first transient: the agent retries Gemini
    assert messages.calls == []
    assert wrapper.invoke([msg('human', 'q')]).response_metadata['provider'] == 'anthropic'


def test_circuit_breaker_skips_gemini_during_cooldown_then_retries_it():
    now = [0.0]
    primary = Scripted(AuthFailure('down'), ANSWER)
    wrapper, _ = fallback(primary, ANSWER, ANSWER, clock=lambda: now[0])
    wrapper.invoke([msg('human', 'q')])
    now[0] = 100
    assert wrapper.invoke([msg('human', 'q')]).response_metadata['provider'] == 'anthropic'
    assert primary.calls == 1  # skipped while open
    now[0] = 301
    assert wrapper.invoke([msg('human', 'q')]).response_metadata['provider'] == 'google'


def test_paid_fallback_cap_is_enforced():
    wrapper, messages = fallback(None, ANSWER, cap=1)
    wrapper.invoke([msg('human', 'q')])
    with pytest.raises(FallbackBudgetExceeded):
        wrapper.invoke([msg('human', 'q')])
    assert len(messages.calls) == 1


def test_outer_call_cap_is_not_rerouted_to_claude():
    wrapper, messages = fallback(Scripted(BudgetExceeded('cap')), ANSWER)
    with pytest.raises(BudgetExceeded):
        wrapper.invoke([msg('human', 'q')])
    assert messages.calls == []


# ---------------------------------------------------------------- agent integration
def test_agent_records_fallback_provenance(database):
    wrapper, _ = fallback(Scripted(AuthFailure('model retired')), ANSWER)
    result = ElectionSQLAgent(llm=wrapper, model='gemini-test', db_path=database).run_query('winners')
    assert result['ok'] and result['rows'] == [(205,)]
    assert result['fallback_used'] and result['served_by'] == ['claude-opus-5']
    assert result['temperature'] is None and result['input_tokens'] == 1200
    transport = [t for t in result['trace'] if t['stage'] == 'transport']
    assert transport[-1]['provider'] == 'anthropic' and 'fallback_reason' in transport[-1]


def test_agent_retries_gemini_once_then_claude_answers(database):
    wrapper, _ = fallback(Scripted(Transient('503'), Transient('503')), ANSWER)
    result = ElectionSQLAgent(llm=wrapper, model='gemini-test', db_path=database,
                              sleep=lambda _: None).run_query('winners')
    assert result['ok'] and result['api_calls'] == 2 and result['fallback_used']


def test_agent_reports_spent_fallback_cap(database):
    wrapper, _ = fallback(None, cap=0)
    result = ElectionSQLAgent(llm=wrapper, model='gemini-test', db_path=database).run_query('q')
    assert not result['ok'] and result['error_type'] == 'FallbackBudgetExceeded'


# ---------------------------------------------------------------- configuration
def test_fallback_is_off_by_default(monkeypatch):
    monkeypatch.delenv('LLM_FALLBACK', raising=False)
    sentinel = object()
    monkeypatch.setattr(llm, 'make_gemini_llm', lambda model: sentinel)
    assert llm.make_llm('gemini-test') is sentinel
    assert llm.llm_descriptor('gemini-test') == {'primary': 'gemini-test', 'gemini_chain': ['gemini-test'],
                                                 'fallback': None}


def test_enabled_fallback_wraps_gemini_with_capped_claude(monkeypatch):
    monkeypatch.setenv('LLM_FALLBACK', 'claude')
    monkeypatch.setenv('ANTHROPIC_MODEL', 'claude-opus-5')
    monkeypatch.setenv('CLAUDE_FALLBACK_MAX_CALLS', '3')
    monkeypatch.setattr(llm, 'make_gemini_llm', lambda model: Scripted())
    monkeypatch.setattr(llm, 'ClaudeChat', lambda model: SimpleNamespace(model=model))
    wrapper = llm.make_llm('gemini-test')
    assert isinstance(wrapper, FallbackLLM) and wrapper.max_fallback_calls == 3
    assert wrapper.secondary_model == 'claude-opus-5'


def test_unconfigured_gemini_serves_from_claude_when_enabled(monkeypatch):
    monkeypatch.setenv('LLM_FALLBACK', 'claude')
    monkeypatch.setattr(llm, 'ClaudeChat', lambda model: SimpleNamespace(model=model))

    def broken(model):
        raise ValueError('GEMINI_MODEL missing')

    monkeypatch.setattr(llm, 'make_gemini_llm', broken)
    assert llm.make_llm(None).primary is None


def test_fallback_setting_changes_cache_fingerprint(monkeypatch, database):
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-test')
    monkeypatch.delenv('LLM_FALLBACK', raising=False)
    off = runtime_fingerprint(database)
    monkeypatch.setenv('LLM_FALLBACK', 'claude')
    assert runtime_fingerprint(database) != off


def test_runner_rejects_unknown_provider():
    from src.evaluation import runner
    with pytest.raises(SystemExit):
        runner.main(['--mode', 'live', '--provider', 'openai', '--max-calls', '1'])


def test_real_sdk_serializes_request_offline():
    """The installed anthropic SDK against a local mock transport: no network."""
    anthropic = pytest.importorskip('anthropic')
    httpx2 = pytest.importorskip('httpx2')
    seen = {}

    def handler(request):
        seen['beta'] = request.headers.get('anthropic-beta')
        seen['body'] = json.loads(request.content)
        return httpx2.Response(200, json={
            'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5',
            'content': [{'type': 'text', 'text': json.dumps(ANSWER)}], 'stop_reason': 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': 11, 'output_tokens': 7}})

    client = anthropic.Anthropic(api_key='sk-test', max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    ClaudeChat('claude-haiku-4-5', client=client).invoke([msg('system', 'SYS'), msg('human', 'Q')])
    assert seen['body']['temperature'] == 0 and 'fallbacks' not in seen['body']
    out = ClaudeChat('claude-opus-5', client=client).invoke([msg('system', 'SYS'), msg('human', 'Q')])
    assert seen['beta'] == 'server-side-fallback-2026-07-01' and 'temperature' not in seen['body']
    assert seen['body']['fallbacks'] == 'default' and seen['body']['system'] == 'SYS'
    assert seen['body']['output_config']['format']['schema'] == RESPONSE_SCHEMA
    assert json.loads(out.content) == ANSWER and out.usage_metadata['output_tokens'] == 7


def test_workspace_header_from_env(monkeypatch):
    pytest.importorskip('anthropic')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-test')
    monkeypatch.setenv('ANTHROPIC_WORKSPACE_ID', 'wrkspc_test')
    assert ClaudeChat('claude-haiku-4-5').client.default_headers.get('anthropic-workspace-id') == 'wrkspc_test'
    monkeypatch.delenv('ANTHROPIC_WORKSPACE_ID')
    assert 'anthropic-workspace-id' not in ClaudeChat('claude-haiku-4-5').client.default_headers


# ---------------------------------------------------------------- Gemini model chain
class Overloaded(Exception):
    code = 503


class Timeout504(Exception):
    code = 504


class BadKey(Exception):
    code = 400


def _gemini_factory(behaviour, calls):
    def factory(model):
        def invoke(messages):
            calls.append(model)
            outcome = behaviour[model]
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(content=json.dumps(ANSWER), usage_metadata={'input_tokens': 1, 'output_tokens': 1},
                                   response_metadata={})
        return SimpleNamespace(invoke=invoke)
    return factory


def test_gemini_chain_moves_past_overloaded_models_and_remembers():
    now, calls = [0.0], []
    chain = llm.GeminiChain(['g-38', 'g-37', 'g-36'], clock=lambda: now[0], cooldown=60,
                            factory=_gemini_factory({'g-38': Overloaded(), 'g-37': Timeout504(), 'g-36': 'ok'}, calls))
    out = chain.invoke([msg('human', 'q')])
    assert out.response_metadata['model_name'] == 'g-36' and 'g-38 unavailable' in out.response_metadata['fallback_reason']
    assert calls == ['g-38', 'g-37', 'g-36']
    chain.invoke([msg('human', 'q')])
    assert calls[3:] == ['g-36']  # failed models skipped during cooldown
    now[0] = 61
    chain.invoke([msg('human', 'q')])
    assert calls[4:] == ['g-38', 'g-37', 'g-36']  # retried after cooldown


def test_gemini_chain_does_not_mask_real_errors_and_raises_when_all_down():
    chain = llm.GeminiChain(['a', 'b'], factory=_gemini_factory({'a': BadKey(), 'b': 'ok'}, []))
    with pytest.raises(BadKey):
        chain.invoke([msg('human', 'q')])
    chain = llm.GeminiChain(['a', 'b'], factory=_gemini_factory({'a': Overloaded(), 'b': Overloaded()}, []))
    with pytest.raises(Overloaded):  # transient: the agent's own retry/backoff still applies
        chain.invoke([msg('human', 'q')])


def test_gemini_chain_configuration(monkeypatch):
    monkeypatch.delenv('LLM_FALLBACK', raising=False)
    monkeypatch.setenv('GEMINI_MODEL', 'g-38')
    monkeypatch.setenv('GEMINI_FALLBACK_MODELS', ' g-36, g-38 ,g-35 ')
    assert llm.gemini_models() == ['g-38', 'g-36', 'g-35']
    assert isinstance(llm.make_llm('g-38'), llm.GeminiChain)
    monkeypatch.delenv('GEMINI_FALLBACK_MODELS')
    monkeypatch.setattr(llm, 'make_gemini_llm', lambda model: 'single')
    assert llm.make_llm('g-38') == 'single'
