"""Behavioral tests for bespoke discovery and database synchronization paths."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from open_notebook.ai import model_discovery as discovery


def _response(url: str, payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", url),
    )


def _client_for(handler):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, url, **kwargs):
            return handler(url, kwargs)

    return FakeClient


def _pin_to_input(monkeypatch):
    async def fake_pin(url, provider):
        return SimpleNamespace(
            url=url,
            headers={"Host": f"{provider}.example"},
            extensions={"sni_hostname": f"{provider}.example"},
        )

    monkeypatch.setattr(discovery, "prepare_pinned_http_target", fake_pin)


def _credential_lookup(monkeypatch, values=None, error: Exception | None = None):
    async def fake_get(provider):
        if error:
            raise error
        if values is None:
            return []
        return [SimpleNamespace(to_esperanto_config=lambda: values)]

    monkeypatch.setattr(
        discovery.Credential,
        "get_by_provider",
        staticmethod(fake_get),
    )


def test_models_endpoint_and_model_type_classification():
    assert discovery._models_endpoint("https://api.example/v1/") == (
        "https://api.example/v1/models"
    )
    assert discovery._models_endpoint("https://api.example/v1/models/") == (
        "https://api.example/v1/models"
    )
    cases = [
        ("whisper-1", "openai", "speech_to_text"),
        ("tts-1", "openai", "text_to_speech"),
        ("text-embedding-3", "openai", "embedding"),
        ("gpt-4", "openai", "language"),
        ("gemini-tts-preview", "google", "text_to_speech"),
        ("nomic-embed-text", "ollama", "embedding"),
        ("voxtral-mini-tts", "mistral", "text_to_speech"),
        ("voxtral-small-latest", "mistral", "speech_to_text"),
        ("unknown", "unknown", "language"),
    ]
    assert [
        discovery.classify_model_type(name, provider)
        for name, provider, _ in cases
    ] == [expected for _, _, expected in cases]


@pytest.mark.asyncio
async def test_generated_compat_discoverer_delegates(monkeypatch):
    seen = []

    async def fake_discover(provider):
        seen.append(provider)
        return [
            discovery.DiscoveredModel(
                name="model", provider=provider, model_type="language"
            )
        ]

    monkeypatch.setattr(discovery, "discover_openai_compatible_provider", fake_discover)
    discoverer = discovery._make_openai_compat_discoverer("groq")

    models = await discoverer()

    assert discoverer.__name__ == "discover_groq_models"
    assert seen == ["groq"]
    assert models[0].provider == "groq"


@pytest.mark.asyncio
async def test_google_discovery_honors_generation_capabilities(monkeypatch):
    calls = []

    def handler(url, kwargs):
        calls.append((url, kwargs))
        return _response(
            url,
            {
                "models": [
                    {
                        "name": "models/gemini-embed",
                        "displayName": "Gemini Embed",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {
                        "name": "models/gemini-flash",
                        "displayName": "Gemini Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {"name": ""},
                ]
            },
        )

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))

    models = await discovery.discover_google_models()

    assert [(model.name, model.model_type) for model in models] == [
        ("gemini-embed", "embedding"),
        ("gemini-flash", "language"),
    ]
    assert calls[0][1]["headers"] == {"X-Goog-Api-Key": "gemini-key"}


@pytest.mark.asyncio
async def test_google_discovery_missing_key_and_http_failure_return_empty(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert await discovery.discover_google_models() == []

    monkeypatch.setenv("GOOGLE_API_KEY", "key")

    def handler(url, kwargs):
        return _response(url, {"error": "unavailable"}, 503)

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))
    assert await discovery.discover_google_models() == []


@pytest.mark.asyncio
async def test_ollama_discovery_uses_pinned_target_and_classifies(monkeypatch):
    calls = []
    _pin_to_input(monkeypatch)

    def handler(url, kwargs):
        calls.append((url, kwargs))
        return _response(
            url,
            {"models": [{"name": "qwen3:9b"}, {"name": "nomic-embed-text"}, {}]},
        )

    monkeypatch.setenv("OLLAMA_API_BASE", "https://ollama.example/")
    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))

    models = await discovery.discover_ollama_models()

    assert [(model.name, model.model_type) for model in models] == [
        ("qwen3:9b", "language"),
        ("nomic-embed-text", "embedding"),
    ]
    assert calls[0][0] == "https://ollama.example/api/tags"
    assert calls[0][1]["extensions"] == {"sni_hostname": "ollama.example"}


@pytest.mark.asyncio
async def test_ollama_discovery_handles_invalid_or_unreachable_target(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "")
    assert await discovery.discover_ollama_models() == []

    async def fail_pin(url, provider):
        raise ValueError("blocked local endpoint")

    monkeypatch.setenv("OLLAMA_API_BASE", "https://ollama.example")
    monkeypatch.setattr(discovery, "prepare_pinned_http_target", fail_pin)
    assert await discovery.discover_ollama_models() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function,key,expected_types",
    [
        (discovery.discover_voyage_models, "VOYAGE_API_KEY", {"embedding"}),
        (
            discovery.discover_elevenlabs_models,
            "ELEVENLABS_API_KEY",
            {"text_to_speech", "speech_to_text"},
        ),
        (
            discovery.discover_deepgram_models,
            "DEEPGRAM_API_KEY",
            {"text_to_speech", "speech_to_text"},
        ),
    ],
)
async def test_static_provider_discovery_requires_key_and_sets_modalities(
    monkeypatch, function, key, expected_types
):
    monkeypatch.delenv(key, raising=False)
    assert await function() == []

    monkeypatch.setenv(key, "configured")
    models = await function()
    assert models
    assert {model.model_type for model in models} == expected_types


@pytest.mark.asyncio
async def test_cohere_discovery_returns_empty_when_factory_fails(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "configured")

    async def fail_thread(*args, **kwargs):
        raise RuntimeError("factory unavailable")

    monkeypatch.setattr(discovery.asyncio, "to_thread", fail_thread)
    assert await discovery.discover_cohere_models() == []


@pytest.mark.asyncio
async def test_openai_compatible_discovery_prefers_credential_config(monkeypatch):
    _credential_lookup(
        monkeypatch,
        {"api_key": "stored-key", "base_url": "https://compat.example/v1/"},
    )
    _pin_to_input(monkeypatch)
    calls = []

    def handler(url, kwargs):
        calls.append((url, kwargs))
        return _response(
            url,
            {"data": [{"id": "gpt-4o"}, {"id": "whisper-1"}, {}]},
        )

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))

    models = await discovery.discover_openai_compatible_models()

    assert [(model.name, model.model_type) for model in models] == [
        ("gpt-4o", "language"),
        ("whisper-1", "speech_to_text"),
    ]
    assert calls[0][0] == "https://compat.example/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer stored-key"


@pytest.mark.asyncio
async def test_openai_compatible_discovery_falls_back_to_environment(monkeypatch):
    _credential_lookup(monkeypatch, error=RuntimeError("database offline"))
    _pin_to_input(monkeypatch)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://env.example/v1")

    def handler(url, kwargs):
        return _response(url, {"data": [{"id": "text-embedding-3-small"}]})

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))
    models = await discovery.discover_openai_compatible_models()
    assert [(model.name, model.model_type) for model in models] == [
        ("text-embedding-3-small", "embedding")
    ]


@pytest.mark.asyncio
async def test_openai_compatible_discovery_requires_base_and_handles_errors(
    monkeypatch,
):
    _credential_lookup(monkeypatch)
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    assert await discovery.discover_openai_compatible_models() == []

    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://compat.example/v1")
    _pin_to_input(monkeypatch)

    def status_error(url, kwargs):
        return _response(url, {"error": "no"}, 401)

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(status_error))
    assert await discovery.discover_openai_compatible_models() == []

    async def fail_pin(url, provider):
        raise RuntimeError("pin failed")

    monkeypatch.setattr(discovery, "prepare_pinned_http_target", fail_pin)
    assert await discovery.discover_openai_compatible_models() == []


@pytest.mark.asyncio
async def test_anthropic_compatible_discovery_normalizes_and_authenticates(
    monkeypatch,
):
    _credential_lookup(monkeypatch)
    _pin_to_input(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_API_KEY", "anthropic-key")
    monkeypatch.setenv(
        "ANTHROPIC_COMPATIBLE_BASE_URL", "https://anthropic.example/v1/models"
    )
    calls = []

    def handler(url, kwargs):
        calls.append((url, kwargs))
        return _response(url, {"data": [{"id": "claude-local"}, {}]})

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))
    models = await discovery.discover_anthropic_compatible_models()

    assert [(model.name, model.provider) for model in models] == [
        ("claude-local", "anthropic_compatible")
    ]
    assert calls[0][0] == "https://anthropic.example/v1/models"
    assert calls[0][1]["headers"]["x-api-key"] == "anthropic-key"
    assert calls[0][1]["headers"]["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_anthropic_compatible_discovery_requires_base_and_handles_errors(
    monkeypatch,
):
    _credential_lookup(monkeypatch, error=RuntimeError("database offline"))
    monkeypatch.delenv("ANTHROPIC_COMPATIBLE_BASE_URL", raising=False)
    assert await discovery.discover_anthropic_compatible_models() == []

    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_BASE_URL", "https://a.example/v1")
    _pin_to_input(monkeypatch)

    def status_error(url, kwargs):
        return _response(url, {"error": "no"}, 500)

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(status_error))
    assert await discovery.discover_anthropic_compatible_models() == []

    async def fail_pin(url, provider):
        raise RuntimeError("pin failed")

    monkeypatch.setattr(discovery, "prepare_pinned_http_target", fail_pin)
    assert await discovery.discover_anthropic_compatible_models() == []


@pytest.mark.asyncio
async def test_omlx_discovery_uses_defaults_and_handles_http_errors(monkeypatch):
    _credential_lookup(monkeypatch)
    _pin_to_input(monkeypatch)
    monkeypatch.delenv("OMLX_API_BASE", raising=False)
    monkeypatch.setenv("OMLX_API_KEY", "local-key")
    calls = []

    def handler(url, kwargs):
        calls.append((url, kwargs))
        return _response(url, {"data": [{"id": "local-model"}, {}]})

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))
    models = await discovery.discover_omlx_models()
    assert [model.name for model in models] == ["local-model"]
    assert calls[0][0] == "http://localhost:11435/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer local-key"

    def status_error(url, kwargs):
        return _response(url, {}, 503)

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(status_error))
    assert await discovery.discover_omlx_models() == []

    async def fail_pin(url, provider):
        raise RuntimeError("pin failed")

    monkeypatch.setattr(discovery, "prepare_pinned_http_target", fail_pin)
    assert await discovery.discover_omlx_models() == []


@pytest.mark.asyncio
async def test_omlx_discovery_prefers_stored_configuration(monkeypatch):
    _credential_lookup(
        monkeypatch,
        {"api_key": "stored", "base_url": "https://stored.example/v1/"},
    )
    _pin_to_input(monkeypatch)
    calls = []

    def handler(url, kwargs):
        calls.append(url)
        return _response(url, {"data": []})

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _client_for(handler))
    assert await discovery.discover_omlx_models() == []
    assert calls == ["https://stored.example/v1/models"]


@pytest.mark.asyncio
async def test_discover_provider_dispatches_and_rejects_unknown(monkeypatch):
    called = []

    async def fake_discover():
        called.append(True)
        return [discovery.DiscoveredModel("m", "test", "language")]

    monkeypatch.setattr(
        discovery,
        "PROVIDER_DISCOVERY_FUNCTIONS",
        {"test": fake_discover, "credential-only": None},
    )

    assert len(await discovery.discover_provider_models("test")) == 1
    assert await discovery.discover_provider_models("credential-only") == []
    assert await discovery.discover_provider_models("unknown") == []
    assert called == [True]


@pytest.mark.asyncio
async def test_sync_provider_counts_existing_new_and_failed_models(monkeypatch):
    discovered = [
        discovery.DiscoveredModel("Existing", "demo", "language"),
        discovery.DiscoveredModel("New", "demo", "embedding"),
        discovery.DiscoveredModel("Broken", "demo", "language"),
    ]

    async def fake_discover(provider):
        return discovered

    async def fake_query(query, params):
        assert params == {"provider": "demo"}
        return [{"name": "existing", "type": "language"}]

    saved = []

    class FakeModel:
        def __init__(self, name, provider, type):
            self.name = name

        async def save(self):
            if self.name == "Broken":
                raise RuntimeError("cannot save")
            saved.append(self.name)

    monkeypatch.setattr(discovery, "discover_provider_models", fake_discover)
    monkeypatch.setattr(discovery, "repo_query", fake_query)
    monkeypatch.setattr(discovery, "Model", FakeModel)

    assert await discovery.sync_provider_models("demo", auto_register=False) == (
        3,
        0,
        0,
    )
    assert await discovery.sync_provider_models("demo") == (3, 1, 1)
    assert saved == ["New"]


@pytest.mark.asyncio
async def test_sync_provider_handles_empty_discovery_and_query_failure(monkeypatch):
    async def no_models(provider):
        return []

    monkeypatch.setattr(discovery, "discover_provider_models", no_models)
    assert await discovery.sync_provider_models("demo") == (0, 0, 0)

    async def one_model(provider):
        return [discovery.DiscoveredModel("New", "demo", "language")]

    async def failed_query(query, params):
        raise RuntimeError("database unavailable")

    saved = []

    class FakeModel:
        def __init__(self, **kwargs):
            saved.append(kwargs)

        async def save(self):
            return None

    monkeypatch.setattr(discovery, "discover_provider_models", one_model)
    monkeypatch.setattr(discovery, "repo_query", failed_query)
    monkeypatch.setattr(discovery, "Model", FakeModel)
    assert await discovery.sync_provider_models("demo") == (1, 1, 0)
    assert saved[0]["name"] == "New"


@pytest.mark.asyncio
async def test_sync_all_providers_isolates_failures(monkeypatch):
    async def fake_sync(provider, auto_register=True):
        if provider == "broken":
            raise RuntimeError("failed")
        return (1, 1, 0)

    monkeypatch.setattr(
        discovery,
        "PROVIDER_DISCOVERY_FUNCTIONS",
        {"working": object(), "broken": object()},
    )
    monkeypatch.setattr(discovery, "sync_provider_models", fake_sync)

    assert await discovery.sync_all_providers() == {
        "working": (1, 1, 0),
        "broken": (0, 0, 0),
    }


@pytest.mark.asyncio
async def test_provider_model_counts_ignore_unknown_modalities(monkeypatch):
    async def fake_query(query, params):
        assert params == {"provider": "OpenAI"}
        return [
            {"type": "language", "count": 3},
            {"type": "embedding", "count": 2},
            {"type": "reranker", "count": 99},
            {"type": "speech_to_text"},
        ]

    monkeypatch.setattr(discovery, "repo_query", fake_query)

    assert await discovery.get_provider_model_count("OpenAI") == {
        "language": 3,
        "embedding": 2,
        "speech_to_text": 0,
        "text_to_speech": 0,
    }
