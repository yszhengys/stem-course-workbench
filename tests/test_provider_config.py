"""Behavioral coverage for the legacy ProviderConfig migration source.

ProviderConfig is still read by the explicit credentials migration endpoint, so
it remains part of the supported upgrade path even though new credentials use
individual records.
"""

from typing import Any

import pytest
from pydantic import SecretStr

from open_notebook.domain import provider_config as provider_config_module
from open_notebook.domain.provider_config import ProviderConfig, ProviderCredential


def _credential(
    credential_id: str,
    *,
    provider: str = "openai",
    is_default: bool = False,
) -> ProviderCredential:
    return ProviderCredential(
        id=credential_id,
        name=f"Credential {credential_id}",
        provider=provider,
        is_default=is_default,
        api_key=SecretStr(f"secret-{credential_id}"),
        base_url="https://provider.example/v1",
        model="model-a",
        created="2026-01-01 00:00:00",
        updated="2026-01-01 00:00:00",
    )


def test_provider_credential_round_trip_keeps_secret_masked(monkeypatch):
    monkeypatch.setattr(
        provider_config_module,
        "encrypt_value",
        lambda value: f"encrypted:{value}",
    )
    credential = _credential("primary")

    plain = credential.to_dict()
    encrypted = credential.to_dict(encrypted=True)
    restored = ProviderCredential.from_dict(plain, decrypted=True)
    already_secret = ProviderCredential.from_dict(
        {**plain, "api_key": SecretStr("ready")}
    )
    without_key = ProviderCredential.from_dict(
        {key: value for key, value in plain.items() if key != "api_key"}
    )

    assert plain["api_key"] == "secret-primary"
    assert encrypted["api_key"] == "encrypted:secret-primary"
    assert restored.api_key is not None
    assert already_secret.api_key is not None
    assert restored.api_key.get_secret_value() == "secret-primary"
    assert already_secret.api_key.get_secret_value() == "ready"
    assert without_key.api_key is None
    assert "secret-primary" not in repr(restored.api_key)


def test_provider_config_default_and_delete_rules_are_deterministic():
    config = ProviderConfig.model_validate({"credentials": {}})
    first = _credential("first", provider="OPENAI")
    second = _credential("second")

    config.add_config("OPENAI", first)
    config.add_config("openai", second)

    assert first.provider == "openai"
    assert first.is_default is False
    assert second.is_default is True
    assert config.get_default_config("OPENAI") is second
    assert config.get_config("openai", "first") is first
    assert config.get_config("openai", "missing") is None
    assert config.get_default_config("missing") is None
    assert config.delete_config("openai", "second") is False

    assert config.set_default_config("openai", "first") is True
    assert config.delete_config("openai", "second") is True
    assert config.delete_config("openai", "missing") is False
    assert config.set_default_config("openai", "missing") is False
    assert config.get_default_config("openai") is first


@pytest.mark.asyncio
async def test_provider_config_loads_and_decrypts_valid_legacy_records(monkeypatch):
    rows = [
        {
            "credentials": {
                "openai": [
                    {
                        "id": "legacy",
                        "name": "Legacy key",
                        "provider": "openai",
                        "is_default": True,
                        "api_key": "ciphertext",
                        "base_url": "https://api.example/v1",
                    },
                    None,
                ],
                "invalid": "not-a-list",
            }
        }
    ]

    async def fake_query(query, params):
        assert "SELECT * FROM ONLY" in query
        assert str(params["record_id"]) == ProviderConfig.record_id
        return rows

    monkeypatch.setattr(provider_config_module, "repo_query", fake_query)
    monkeypatch.setattr(
        provider_config_module,
        "decrypt_value",
        lambda value: "decrypted-secret" if value == "ciphertext" else value,
    )

    config = await ProviderConfig.get_instance()

    loaded = config.get_default_config("openai")
    assert loaded is not None
    assert loaded.api_key is not None
    assert loaded.id == "legacy"
    assert loaded.api_key.get_secret_value() == "decrypted-secret"
    assert config.credentials["openai"] == [loaded]
    assert getattr(config, "_db_loaded") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [{}, [], "unexpected"])
async def test_provider_config_empty_or_legacy_shapes_load_as_empty(
    monkeypatch, stored
):
    async def fake_query(query, params):
        return stored

    monkeypatch.setattr(provider_config_module, "repo_query", fake_query)

    config = await ProviderConfig.get_instance()

    assert config.credentials == {}


@pytest.mark.asyncio
async def test_provider_config_save_encrypts_each_secret(monkeypatch):
    saved: dict[str, Any] = {}

    async def fake_upsert(table, record_id, data):
        saved.update(table=table, record_id=record_id, data=data)

    monkeypatch.setattr(provider_config_module, "repo_upsert", fake_upsert)
    monkeypatch.setattr(
        provider_config_module,
        "encrypt_value",
        lambda value: f"sealed:{value}",
    )
    config = ProviderConfig.model_validate({"credentials": {}})
    config.add_config("openai", _credential("save"))

    returned = await config.save()

    assert returned is config
    assert saved["table"] == "open_notebook"
    assert saved["record_id"] == ProviderConfig.record_id
    assert saved["data"]["credentials"]["openai"][0]["api_key"] == (
        "sealed:secret-save"
    )


def test_provider_config_test_cache_can_be_cleared():
    sentinel = ProviderConfig.model_validate({"credentials": {}})
    ProviderConfig._instances[ProviderConfig.record_id] = sentinel

    ProviderConfig._clear_for_test()

    assert ProviderConfig.record_id not in ProviderConfig._instances
