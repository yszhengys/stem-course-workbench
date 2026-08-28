"""Behavior tests for credential environment migration and status helpers."""

import pytest
from pydantic import SecretStr

from api import credentials_service as service
from open_notebook.domain.credential import Credential


def test_encryption_key_gate_accepts_configured_secret_and_rejects_missing(
    monkeypatch,
):
    monkeypatch.setattr(service, "get_secret_from_env", lambda name: None)
    with pytest.raises(ValueError, match="Encryption key not configured"):
        service.require_encryption_key()

    monkeypatch.setattr(service, "get_secret_from_env", lambda name: "configured")
    service.require_encryption_key()


def test_credential_response_exposes_metadata_without_secret():
    credential = Credential(
        name="Primary",
        provider="openai",
        modalities=["language"],
        api_key=SecretStr("secret"),
        base_url="https://api.example/v1",
        num_ctx=8192,
    )
    object.__setattr__(credential, "id", "credential:primary")
    object.__setattr__(credential, "created", "2026-01-01")
    object.__setattr__(credential, "updated", "2026-01-02")

    response = service.credential_to_response(credential, model_count=4)

    assert response.id == "credential:primary"
    assert response.has_api_key is True
    assert response.model_count == 4
    assert response.base_url == "https://api.example/v1"
    assert not hasattr(response, "api_key")


def test_environment_configuration_rules_and_default_modalities(monkeypatch):
    monkeypatch.setattr(
        service,
        "PROVIDER_ENV_CONFIG",
        {
            "all": {"required": ["ALL_A", "ALL_B"]},
            "any": {"required_any": ["ANY_A", "ANY_B"]},
            "empty": {},
        },
    )
    monkeypatch.setenv("ALL_A", "value")
    monkeypatch.setenv("ALL_B", " ")
    monkeypatch.setenv("ANY_B", "value")

    assert service.check_env_configured("all") is False
    assert service.check_env_configured("any") is True
    assert service.check_env_configured("empty") is False
    assert service.check_env_configured("unknown") is False
    assert "language" in service.get_default_modalities("OPENAI")
    assert service.get_default_modalities("unknown") == ["language"]


def test_provider_required_field_validation_uses_decrypted_api_key(monkeypatch):
    seen = []

    def fake_validate(provider, base_url, api_key):
        seen.append((provider, base_url, api_key))

    monkeypatch.setattr(
        service, "validate_url_key_provider_required_fields", fake_validate
    )
    credential = Credential(
        name="Compatible",
        provider="openai_compatible",
        modalities=["language"],
        api_key=SecretStr("secret"),
        base_url="https://api.example/v1",
    )

    service.ensure_provider_required_fields(credential)

    assert seen == [("openai_compatible", "https://api.example/v1", "secret")]


@pytest.mark.parametrize(
    "provider,environment,expected",
    [
        (
            "ollama",
            {"OLLAMA_API_BASE": "http://localhost:11434"},
            {"base_url": "http://localhost:11434", "api_key": None},
        ),
        (
            "omlx",
            {"OMLX_API_BASE": "http://localhost:11435/v1", "OMLX_API_KEY": "k"},
            {"base_url": "http://localhost:11435/v1", "api_key": "k"},
        ),
        (
            "vertex",
            {
                "VERTEX_PROJECT": "project",
                "VERTEX_LOCATION": "us-central1",
                "GOOGLE_APPLICATION_CREDENTIALS": "/credentials.json",
            },
            {"project": "project", "location": "us-central1"},
        ),
        (
            "azure",
            {
                "AZURE_OPENAI_API_KEY": "azure-key",
                "AZURE_OPENAI_ENDPOINT": "https://azure.example",
                "AZURE_OPENAI_API_VERSION": "2026-01-01",
                "AZURE_OPENAI_ENDPOINT_LLM": "https://azure.example/llm",
                "AZURE_OPENAI_ENDPOINT_EMBEDDING": "https://azure.example/embed",
                "AZURE_OPENAI_ENDPOINT_STT": "https://azure.example/stt",
                "AZURE_OPENAI_ENDPOINT_TTS": "https://azure.example/tts",
            },
            {"endpoint": "https://azure.example", "api_key": "azure-key"},
        ),
        (
            "openai_compatible",
            {
                "OPENAI_COMPATIBLE_API_KEY": "compat-key",
                "OPENAI_COMPATIBLE_BASE_URL": "https://compat.example/v1",
            },
            {"base_url": "https://compat.example/v1", "api_key": "compat-key"},
        ),
        (
            "anthropic_compatible",
            {
                "ANTHROPIC_COMPATIBLE_API_KEY": "compat-key",
                "ANTHROPIC_COMPATIBLE_BASE_URL": "https://compat.example/v1",
            },
            {"base_url": "https://compat.example/v1", "api_key": "compat-key"},
        ),
        (
            "google",
            {"GEMINI_API_KEY": "gemini-key"},
            {"api_key": "gemini-key"},
        ),
        (
            "openai",
            {"OPENAI_API_KEY": "openai-key"},
            {"api_key": "openai-key"},
        ),
    ],
)
def test_create_credential_from_each_environment_shape(
    monkeypatch, provider, environment, expected
):
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    credential = service.create_credential_from_env(provider)

    assert credential.provider == provider
    assert credential.name == "Default (Migrated from env)"
    assert credential.modalities == service.get_default_modalities(provider)
    for field, value in expected.items():
        actual = getattr(credential, field)
        if isinstance(actual, SecretStr):
            actual = actual.get_secret_value()
        assert actual == value


@pytest.mark.asyncio
async def test_provider_status_distinguishes_database_environment_and_none(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "PROVIDER_ENV_CONFIG",
        {
            "database": {"required": ["DB_KEY"]},
            "environment": {"required": ["ENV_KEY"]},
            "none": {"required": ["NONE_KEY"]},
            "database_error": {"required": ["ERROR_KEY"]},
        },
    )
    monkeypatch.setenv("ENV_KEY", "configured")
    monkeypatch.setenv("ERROR_KEY", "configured")
    monkeypatch.setattr(
        service,
        "get_secret_from_env",
        lambda name: "encryption-key",
    )

    async def fake_get(provider):
        if provider == "database":
            return [object()]
        if provider == "database_error":
            raise RuntimeError("database offline")
        return []

    monkeypatch.setattr(
        service.Credential,
        "get_by_provider",
        staticmethod(fake_get),
    )

    status = await service.get_provider_status()

    assert status == {
        "configured": {
            "database": True,
            "environment": True,
            "none": False,
            "database_error": True,
        },
        "source": {
            "database": "database",
            "environment": "environment",
            "none": "none",
            "database_error": "environment",
        },
        "encryption_configured": True,
    }
    assert await service.get_env_status() == {
        "database": False,
        "environment": True,
        "none": False,
        "database_error": True,
    }
