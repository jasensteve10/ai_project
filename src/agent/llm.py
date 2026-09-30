"""Model providers: Gemini (primary) and an opt-in, capped Claude fallback.

The fallback is OFF unless ``LLM_FALLBACK=claude``. The Anthropic API has no free tier:
every Claude call is billed, so ``CLAUDE_FALLBACK_MAX_CALLS`` caps fallback calls per
process. Experiments never use the fallback (one model per run); the runner selects a
single provider explicitly instead.
"""
import os
import time
from types import SimpleNamespace

DEFAULT_CLAUDE_MODEL = 'claude-opus-5'
DEFAULT_FALLBACK_MAX_CALLS = 20
# Server-side refusal fallback ("default" routing) — Anthropic's recommended default for Opus 5 / Fable.
REFUSAL_FALLBACK_BETA = 'server-side-fallback-2026-07-01'
_REFUSAL_FALLBACK_PREFIXES = ('claude-opus-5', 'claude-fable-5')
# Models that reject sampling parameters (Opus 4.7+ / Opus 5 / Fable / Sonnet 5); others run at temperature 0.
_NO_SAMPLING_PREFIXES = ('claude-opus-5', 'claude-fable-5', 'claude-opus-4-7', 'claude-opus-4-8', 'claude-sonnet-5')

# Same contract as src/prompts/sql_system.md (+ agent_retrieval.md); enforced by structured outputs.
_NULLABLE_STRING = {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'status': {'type': 'string',
                   'enum': ['answerable', 'unsupported', 'needs_clarification', 'needs_context']},
        'sql': _NULLABLE_STRING,
        'response': _NULLABLE_STRING,
        'search': _NULLABLE_STRING,
    },
    'required': ['status', 'sql', 'response', 'search'],
    'additionalProperties': False,
}

_TRANSIENT_NAMES = {
    # Google / LangChain
    'ResourceExhausted', 'TooManyRequests', 'GoogleRateLimitError', 'ServiceUnavailable', 'DeadlineExceeded',
    'TimeoutError', 'ConnectError', 'ReadTimeout',
    # Anthropic SDK
    'RateLimitError', 'InternalServerError', 'OverloadedError', 'ServiceUnavailableError', 'APIConnectionError',
    'APITimeoutError', 'DeadlineExceededError',
}


def is_transient(exc):
    """Retryable provider failure (rate limit, overload, 5xx, network)."""
    code = getattr(exc, 'code', None)
    status = getattr(exc, 'status_code', None)
    return (code in (429, 500, 502, 503, 504) or status in (408, 409, 429, 500, 502, 503, 504, 529)
            or type(exc).__name__ in _TRANSIENT_NAMES)


class ClaudeRefusal(Exception):
    """Claude (and its server-side fallback chain) declined the request."""


class FallbackBudgetExceeded(Exception):
    """The per-process cap on paid Claude fallback calls is spent."""


def make_gemini_llm(model):
    if not model:
        raise ValueError('Set GEMINI_MODEL in .env to a model available in your account (see .env.example).')
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=model, temperature=0, max_retries=0,
                                  timeout=30, vertexai=False, response_mime_type='application/json')


def _role(message):
    kind = getattr(message, 'type', None) or getattr(message, 'role', None)
    return 'system' if kind == 'system' else 'user'


class ClaudeChat:
    """Minimal adapter giving Claude the ``invoke(messages)`` interface the agent uses.

    Returns an object with ``content`` (JSON text), ``usage_metadata`` and
    ``response_metadata`` like LangChain's Gemini messages. SDK retries are disabled;
    the agent owns retry/backoff so every attempt is counted in its trace.
    """
    provider = 'anthropic'

    def __init__(self, model=None, *, client=None, timeout=60.0, max_tokens=16000):
        self.model = model or os.getenv('ANTHROPIC_MODEL') or DEFAULT_CLAUDE_MODEL
        self.max_tokens = max_tokens
        self.temperature = None if self.model.startswith(_NO_SAMPLING_PREFIXES) else 0
        if client is None:
            import anthropic
            # Keys not scoped to a workspace must name one on every request.
            workspace = (os.getenv('ANTHROPIC_WORKSPACE_ID') or '').strip()
            headers = {'anthropic-workspace-id': workspace} if workspace else None
            client = anthropic.Anthropic(max_retries=0, timeout=timeout, default_headers=headers)
        self.client = client

    def invoke(self, messages):
        system = '\n\n'.join(m.content for m in messages if _role(m) == 'system')
        turns = [{'role': 'user', 'content': m.content} for m in messages if _role(m) != 'system']
        kwargs = {}
        if self.model.startswith(_REFUSAL_FALLBACK_PREFIXES):
            kwargs.update(betas=[REFUSAL_FALLBACK_BETA], fallbacks='default')
        if self.temperature is not None:
            # anthropic 1.x dropped sampling kwargs from create(); models that still accept them
            # (e.g. Haiku 4.5) take them via extra_body. Temperature 0 is this project's fixed policy.
            kwargs['extra_body'] = {'temperature': self.temperature}
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=turns,
            output_config={'format': {'type': 'json_schema', 'schema': RESPONSE_SCHEMA}},
            **kwargs,
        )
        if response.stop_reason == 'refusal':
            details = getattr(response, 'stop_details', None)
            raise ClaudeRefusal(f'Claude declined the request (category: {getattr(details, "category", None)})')
        text = ''.join(block.text for block in response.content if block.type == 'text')
        usage = response.usage
        return SimpleNamespace(
            content=text,
            usage_metadata={'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens,
                            'total_tokens': usage.input_tokens + usage.output_tokens,
                            'cache_read_input_tokens': getattr(usage, 'cache_read_input_tokens', None) or 0},
            response_metadata={'provider': self.provider, 'model_name': response.model,
                               'temperature': self.temperature,
                               'finish_reason': response.stop_reason, 'request_id': getattr(response, '_request_id', None)},
        )


def _tag(response, provider, model):
    """Uniform response carrying which provider/model actually answered."""
    metadata = dict(getattr(response, 'response_metadata', None) or {})
    metadata.setdefault('provider', provider)
    metadata.setdefault('model_name', model)
    return SimpleNamespace(content=response.content, usage_metadata=getattr(response, 'usage_metadata', None),
                           response_metadata=metadata)


class FallbackLLM:
    """Gemini first; Claude only when Gemini is failing.

    - Non-transient Gemini failures (bad/revoked key, retired model, permission) switch
      to Claude immediately.
    - Transient failures (429/5xx/timeouts) are re-raised so the agent retries Gemini
      with backoff; after ``transient_threshold`` consecutive ones, Claude answers.
    - Once tripped, Gemini is skipped for ``cooldown`` seconds (circuit breaker).
    - Claude calls stop at ``max_fallback_calls``; call-cap errors from an outer meter
      (``BudgetExceeded``) and Claude refusals are never rerouted.
    """

    def __init__(self, primary, secondary, *, primary_model, secondary_model,
                 max_fallback_calls=DEFAULT_FALLBACK_MAX_CALLS, transient_threshold=2, cooldown=300.0,
                 clock=time.monotonic):
        self.primary, self.secondary = primary, secondary
        self.primary_model, self.secondary_model = primary_model, secondary_model
        self.max_fallback_calls, self.transient_threshold = max_fallback_calls, transient_threshold
        self.cooldown, self.clock = cooldown, clock
        self.fallback_calls, self.consecutive_transient = 0, 0
        self.primary_down_until, self.last_primary_error = None, None

    def _primary_available(self):
        return self.primary is not None and (self.primary_down_until is None or self.clock() >= self.primary_down_until)

    def invoke(self, messages):
        reason = 'primary unavailable'
        if self._primary_available():
            try:
                response = self.primary.invoke(messages)
                self.consecutive_transient = 0
                return _tag(response, 'google', self.primary_model)
            except Exception as exc:
                if type(exc).__name__ == 'BudgetExceeded':
                    raise
                self.last_primary_error = f'{type(exc).__name__}: {exc}'
                if is_transient(exc):
                    self.consecutive_transient += 1
                    if self.consecutive_transient < self.transient_threshold:
                        raise
                self.primary_down_until = self.clock() + self.cooldown
                reason = self.last_primary_error
        if self.fallback_calls >= self.max_fallback_calls:
            raise FallbackBudgetExceeded(
                f'Gemini unavailable ({reason}) and the Claude fallback cap '
                f'({self.max_fallback_calls} calls) is spent; raise CLAUDE_FALLBACK_MAX_CALLS to allow more paid calls.')
        self.fallback_calls += 1
        response = _tag(self.secondary.invoke(messages), 'anthropic', self.secondary_model)
        response.response_metadata['fallback_reason'] = reason
        return response


_UNAVAILABLE_NAMES = {'GoogleModelNotFoundError', 'NotFound'}


def _model_unavailable(exc):
    """Overloaded, rate-limited, timed out or retired: worth trying the next Gemini model."""
    code = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
    return is_transient(exc) or code in (404, 504) or type(exc).__name__ in _UNAVAILABLE_NAMES


class GeminiChain:
    """Free-tier Gemini models tried in order (GEMINI_MODEL, then GEMINI_FALLBACK_MODELS).

    Google's 503 "model is experiencing high demand" can last for hours on one model
    while others answer; the chain moves on within the same request. A model that failed
    is skipped for ``cooldown`` seconds. Other errors (bad key, invalid request) propagate.
    """

    def __init__(self, models, *, factory=make_gemini_llm, cooldown=120.0, clock=time.monotonic):
        if not models:
            raise ValueError('Set GEMINI_MODEL in .env to a model available in your account.')
        self.models, self.factory, self.cooldown, self.clock = list(models), factory, cooldown, clock
        self._clients, self._down_until = {}, {}

    def _client(self, model):
        if model not in self._clients:
            self._clients[model] = self.factory(model)
        return self._clients[model]

    def invoke(self, messages):
        now = self.clock()
        ready = [m for m in self.models if self._down_until.get(m, 0) <= now]
        order = ready or self.models  # all cooling down: try them all again rather than fail
        last = None
        for model in order:
            try:
                response = self._client(model).invoke(messages)
            except Exception as exc:
                if type(exc).__name__ == 'BudgetExceeded' or not _model_unavailable(exc):
                    raise
                self._down_until[model] = self.clock() + self.cooldown
                last = exc
                continue
            tagged = _tag(response, 'google', model)
            if model != self.models[0]:
                tagged.response_metadata['fallback_reason'] = f'{self.models[0]} unavailable'
            return tagged
        raise last


def gemini_models(model=None):
    primary = model or os.getenv('GEMINI_MODEL')
    extra = [m.strip() for m in (os.getenv('GEMINI_FALLBACK_MODELS') or '').split(',') if m.strip()]
    return [m for m in dict.fromkeys([primary, *extra]) if m]


def make_gemini(model=None):
    """One Gemini model, or a GeminiChain when GEMINI_FALLBACK_MODELS is set."""
    models = gemini_models(model)
    return make_gemini_llm(models[0] if models else None) if len(models) <= 1 else GeminiChain(models)


def fallback_enabled():
    return (os.getenv('LLM_FALLBACK') or 'none').strip().lower() in {'claude', 'anthropic'}


def llm_descriptor(gemini_model=None):
    """What the app will call; part of the cache/runtime fingerprint."""
    return {'primary': gemini_model or os.getenv('GEMINI_MODEL'), 'gemini_chain': gemini_models(gemini_model),
            'fallback': (os.getenv('ANTHROPIC_MODEL') or DEFAULT_CLAUDE_MODEL) if fallback_enabled() else None}


def make_llm(model):
    """The application model: Gemini, wrapped with the Claude fallback only when enabled."""
    if not fallback_enabled():
        return make_gemini(model)
    claude_model = os.getenv('ANTHROPIC_MODEL') or DEFAULT_CLAUDE_MODEL
    try:
        cap = int(os.getenv('CLAUDE_FALLBACK_MAX_CALLS', DEFAULT_FALLBACK_MAX_CALLS))
    except ValueError as exc:
        raise ValueError('CLAUDE_FALLBACK_MAX_CALLS must be an integer') from exc
    try:
        primary = make_gemini(model)
    except Exception:  # Gemini unconfigured or SDK broken: serve from Claude, still capped
        primary = None
    return FallbackLLM(primary, ClaudeChat(claude_model), primary_model=model, secondary_model=claude_model,
                       max_fallback_calls=cap)


def make_provider_llm(provider, model=None):
    """A single fixed provider (experiments): never mixes models within a run."""
    if provider == 'gemini':
        return make_gemini_llm(model or os.getenv('GEMINI_MODEL'))
    if provider == 'claude':
        return ClaudeChat(model)
    raise ValueError(f'unknown provider {provider!r}')
