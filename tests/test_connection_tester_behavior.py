"""End-to-end behavior tests for provider and registered-model probes.

The network boundary is replaced with deterministic responses; the production
URL pinning, status classification, modality dispatch, and user-facing results
are still exercised.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from google.auth.exceptions import GoogleAuthError, TransportError

from open_notebook.ai import connection_tester as tester
from open_notebook.ai import models as models_module


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _install_http(monkeypatch, effect, requests: list[dict] | None = None):
    calls = requests if requests is not None else []

    async def fake_target(url, provider):
        calls.append({"pin_url": url, "provider": provider})
        if isinstance(effect, ValueError):
            raise effect
        return SimpleNamespace(
            url=url,
            headers={"Host": "provider.example"},
            extensions={"sni_hostname": "provider.example"},
        )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append({"client": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, url, **kwargs):
            calls.append({"get_url": url, **kwargs})
            if isinstance(effect, BaseException):
                raise effect
            return effect

    monkeypatch.setattr(tester, "prepare_pinned_http_target", fake_target)
    monkeypatch.setattr(tester.httpx, "AsyncClient", FakeClient)
    return calls


async def _call_probe(name: str):
    if name == "azure":
        return await tester._test_azure_connection(
            endpoint="https://azure.example/",
            api_key="azure-key",
            api_version="2026-01-01",
        )
    if name == "ollama":
        return await tester._test_ollama_connection("https://ollama.example/")
    if name == "openai_compatible":
        return await tester._test_openai_compatible_connection(
            "https://openai.example/v1", "openai-key"
        )
    return await tester._test_anthropic_compatible_connection(
        "https://anthropic.example/v1/models", "anthropic-key"
    )


def _payload(name: str, *, empty: bool = False) -> dict:
    models = [] if empty else [
        {"name": "one", "id": "one"},
        {"name": "two", "id": "two"},
        {"name": "three", "id": "three"},
        {"name": "four", "id": "four"},
    ]
    return {"models" if name == "ollama" else "data": models}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["azure", "ollama", "openai_compatible", "anthropic_compatible"]
)
async def test_provider_probes_pin_urls_and_report_model_lists(monkeypatch, name):
    requests: list[dict] = []
    _install_http(monkeypatch, _Response(200, _payload(name)), requests)

    success, message = await _call_probe(name)

    assert success is True
    assert "4 models" in message
    assert "+1 more" in message
    get_call = next(item for item in requests if "get_url" in item)
    assert get_call["extensions"] == {"sni_hostname": "provider.example"}
    assert get_call["headers"]["Host"] == "provider.example"
    if name == "azure":
        assert get_call["headers"]["api-key"] == "azure-key"
        assert "api-version=2026-01-01" in get_call["get_url"]
    elif name == "openai_compatible":
        assert get_call["headers"]["Authorization"] == "Bearer openai-key"
        assert get_call["get_url"].endswith("/models")
    elif name == "anthropic_compatible":
        assert get_call["headers"]["x-api-key"] == "anthropic-key"
        assert get_call["get_url"] == "https://anthropic.example/v1/models"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["azure", "ollama", "openai_compatible", "anthropic_compatible"]
)
async def test_provider_probes_accept_empty_model_lists(monkeypatch, name):
    _install_http(monkeypatch, _Response(200, _payload(name, empty=True)))

    success, message = await _call_probe(name)

    assert success is True
    assert "no models" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,status,fragment",
    [
        (name, status, fragment)
        for name in (
            "azure",
            "ollama",
            "openai_compatible",
            "anthropic_compatible",
        )
        for status, fragment in (
            (401, "Invalid API key"),
            (403, "lacks required permissions"),
            (500, "status 500"),
        )
    ],
)
async def test_provider_probe_http_statuses_are_actionable(
    monkeypatch, name, status, fragment
):
    _install_http(monkeypatch, _Response(status))

    success, message = await _call_probe(name)

    assert success is False
    assert fragment in message


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 405])
async def test_anthropic_model_listing_can_be_unsupported(monkeypatch, status):
    _install_http(monkeypatch, _Response(status))

    success, message = await _call_probe("anthropic_compatible")

    assert success is True
    assert "listing is unsupported" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,effect,fragment",
    [
        (name, effect, fragment)
        for name in (
            "azure",
            "ollama",
            "openai_compatible",
            "anthropic_compatible",
        )
        for effect, fragment in (
            (ValueError("blocked target"), "blocked target"),
            (httpx.ConnectError("offline"), "Cannot connect"),
            (httpx.ReadTimeout("late"), "timed out"),
            (RuntimeError("unexpected detail"), "Connection error"),
        )
    ],
)
async def test_provider_probe_failures_do_not_leak_tracebacks(
    monkeypatch, name, effect, fragment
):
    _install_http(monkeypatch, effect)

    success, message = await _call_probe(name)

    assert success is False
    assert fragment in message
    assert "Traceback" not in message


@pytest.mark.asyncio
async def test_azure_probe_uses_environment_and_requires_endpoint_and_key(
    monkeypatch,
):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    assert await tester._test_azure_connection() == (
        False,
        "No Azure endpoint configured",
    )

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.example")
    assert await tester._test_azure_connection() == (
        False,
        "No Azure API key configured",
    )


def test_anthropic_url_and_vertex_file_error_classification():
    assert tester.normalize_anthropic_compatible_base_url(" https://a.test ") == (
        "https://a.test/v1"
    )
    assert tester.normalize_anthropic_compatible_base_url(
        "https://a.test/v1/models/"
    ) == "https://a.test/v1"
    assert tester._is_vertex_credentials_file_error(FileNotFoundError("missing"))
    assert tester._is_vertex_credentials_file_error(GoogleAuthError("bad json"))
    assert not tester._is_vertex_credentials_file_error(ConnectionError("offline"))
    assert not tester._is_vertex_credentials_file_error(TimeoutError("slow"))
    assert not tester._is_vertex_credentials_file_error(TransportError("offline"))


def test_generated_audio_is_valid_wav_and_bundled_audio_has_fallback(
    monkeypatch, tmp_path: Path
):
    wav = tester._generate_test_wav()
    assert wav.name == "test.wav"
    assert wav.read(12).startswith(b"RIFF")

    speech = tmp_path / "speech.mp3"
    speech.write_bytes(b"ID3-fake-speech")
    monkeypatch.setattr(tester, "_TEST_SPEECH_PATH", str(speech))
    assert tester._get_test_audio().read() == b"ID3-fake-speech"

    monkeypatch.setattr(tester, "_TEST_SPEECH_PATH", str(tmp_path / "missing"))
    assert tester._get_test_audio().read(4) == b"RIFF"


class _Manager:
    model: object | None = None
    error: Exception | None = None

    async def get_model(self, model_id):
        if self.error:
            raise self.error
        return self.model


class _Language:
    response: object | None = None

    async def achat_complete(self, messages):
        assert messages[0]["content"] == "Hi!"
        return self.response


class _Embedding:
    vectors = [[1.0, 2.0, 3.0]]

    async def aembed(self, texts):
        assert texts == ["This is a test."]
        return self.vectors


class _Speech:
    voices = {"voice-id": "Voice"}

    @property
    def available_voices(self):
        return self.voices

    async def agenerate_speech(self, text, voice):
        return SimpleNamespace(content=b"audio")


class _Transcriber:
    result: object = SimpleNamespace(text="hello there")

    async def atranscribe(self, audio_file: io.BytesIO, language: str):
        assert language == "en"
        return self.result


@pytest.fixture
def fake_model_manager(monkeypatch):
    _Manager.model = None
    _Manager.error = None
    monkeypatch.setattr(models_module, "ModelManager", _Manager)
    monkeypatch.setattr(tester, "LanguageModel", _Language)
    monkeypatch.setattr(tester, "EmbeddingModel", _Embedding)
    monkeypatch.setattr(tester, "TextToSpeechModel", _Speech)
    monkeypatch.setattr(tester, "SpeechToTextModel", _Transcriber)
    return _Manager


@pytest.mark.asyncio
async def test_registered_language_and_embedding_model_probes(
    fake_model_manager, monkeypatch
):
    class Completion:
        content = "a valid response"

    monkeypatch.setattr(tester, "ChatCompletion", Completion)
    language = _Language()
    language.response = Completion()
    fake_model_manager.model = language
    success, message = await tester.test_individual_model(
        SimpleNamespace(id="model:language", type="language", provider="openai")
    )
    assert (success, message) == (True, "Response: a valid response")

    language.response = SimpleNamespace()
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:stream", type="language", provider="openai")
    ) == (True, "Connection successful (streaming response)")

    fake_model_manager.model = _Embedding()
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:embedding", type="embedding", provider="openai")
    ) == (True, "Embedding dimensions: 3")
    fake_model_manager.model.vectors = []
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:empty", type="embedding", provider="openai")
    ) == (True, "Embedding successful")


@pytest.mark.asyncio
async def test_registered_audio_model_probes(fake_model_manager, monkeypatch):
    speech = _Speech()
    fake_model_manager.model = speech
    success, message = await tester.test_individual_model(
        SimpleNamespace(
            id="model:speech", type="text_to_speech", provider="elevenlabs"
        )
    )
    assert (success, message) == (True, "Audio generated: 5 bytes")

    class EmptySpeech(_Speech):
        voices = {}

        async def agenerate_speech(self, text, voice):
            assert voice == "alloy"
            return object()

    fake_model_manager.model = EmptySpeech()
    assert await tester.test_individual_model(
        SimpleNamespace(
            id="model:speech-empty", type="text_to_speech", provider="custom"
        )
    ) == (True, "Speech generation successful")

    transcriber = _Transcriber()
    fake_model_manager.model = transcriber
    monkeypatch.setattr(tester, "_get_test_audio", lambda: io.BytesIO(b"audio"))
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:stt", type="speech_to_text", provider="openai")
    ) == (True, "Transcription: hello there")
    transcriber.result = "plain transcription"
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:stt-text", type="speech_to_text", provider="openai")
    ) == (True, "Transcription: plain transcription")
    transcriber.result = SimpleNamespace(text="  ")
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:stt-empty", type="speech_to_text", provider="openai")
    ) == (True, "Connection successful (test clip produced no transcription)")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_type,instance,expected",
    [
        ("language", object(), "expected a language model"),
        ("embedding", object(), "expected an embedding model"),
        ("text_to_speech", object(), "expected a text-to-speech model"),
        ("speech_to_text", object(), "expected a speech-to-text model"),
        ("image", object(), "Unsupported model type"),
    ],
)
async def test_registered_model_type_mismatches_are_explicit(
    fake_model_manager, model_type, instance, expected
):
    fake_model_manager.model = instance
    success, message = await tester.test_individual_model(
        SimpleNamespace(id="model:wrong", type=model_type, provider="openai")
    )
    assert success is False
    assert expected in message


@pytest.mark.asyncio
async def test_registered_model_creation_and_error_results(fake_model_manager):
    fake_model_manager.model = None
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:none", type="language", provider="openai")
    ) == (False, "Could not create model instance")

    fake_model_manager.error = RuntimeError("429 quota exceeded")
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:quota", type="language", provider="openai")
    ) == (True, "Rate limited - but connection works")

    fake_model_manager.error = FileNotFoundError("/private/secret/path")
    assert await tester.test_individual_model(
        SimpleNamespace(id="model:vertex", type="language", provider="vertex")
    ) == (False, "Invalid or inaccessible credentials file")

    fake_model_manager.error = RuntimeError("bad construction")
    success, message = await tester.test_individual_model(
        SimpleNamespace(id="model:bad", type="language", provider="openai")
    )
    assert success is False
    assert message == "bad construction"
