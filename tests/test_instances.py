from dataclasses import replace

import pytest

from core.instances import InstanceConfig, InstanceRegistry
from core.v2_client import MioApiError


def test_public_config_excludes_token_and_normalizes_address():
    config = InstanceConfig(
        "primary", "Main desk", "http://localhost:8788/mio/api/v2/", token="mio_fixture"
    )
    assert config.base_url == "http://localhost:8788/mio"
    assert "mio_fixture" not in repr(config)
    assert "token" not in config.public()
    assert config.public()["has_token"] is True


@pytest.mark.parametrize(
    "value",
    [
        "ftp://localhost:8788",
        "https://user:pass@studio.example.invalid",
        "https://studio.example.invalid?key=ignored",
        "https://studio.example.invalid/#secret",
        "http://localhost:99999",
    ],
)
def test_rejects_invalid_or_credential_bearing_urls(value):
    with pytest.raises(ValueError) as exc:
        InstanceConfig("primary", "Main desk", value)
    assert value not in str(exc.value)


async def test_instance_routing_has_explicit_precedence_and_no_fallback():
    primary = InstanceConfig("primary", "Main desk")
    cloud = InstanceConfig("cloud", "Cloud", "https://studio.example.invalid", cloud=True)
    registry = InstanceRegistry(primary)
    try:
        await registry.configure([primary, cloud], default_id="cloud")
        assert registry.resolve() is cloud
        assert registry.resolve(session_default="primary") is primary
        assert registry.resolve("cloud", "primary") is cloud
        assert registry.get("cloud") is registry.get()
        assert registry.get("primary") is not registry.get()

        await registry.configure([primary])
        for requested, session_default in (("cloud", None), (None, "cloud")):
            with pytest.raises(MioApiError) as exc:
                registry.resolve(requested, session_default)
            assert exc.value.code == "instance_not_found"
    finally:
        await registry.close()


async def test_invalid_update_preserves_topology():
    primary = InstanceConfig("primary", "Main desk")
    backup = InstanceConfig("backup", "Backup")
    registry = InstanceRegistry(primary)
    await registry.configure([primary, backup], default_id="backup")
    try:
        for configs, default_id in (([backup], "backup"), ([primary, primary], "primary")):
            with pytest.raises(ValueError):
                await registry.configure(configs, default_id=default_id)
            assert registry.resolve() is backup
    finally:
        await registry.close()


async def test_connection_edits_rotate_client_and_shutdown_closes_all(monkeypatch):
    class FakeClient:
        def __init__(self, base_url, token, *, timeout):
            self.closed = False
            self.credentials = (base_url, token, timeout)

        async def close(self):
            self.closed = True

    monkeypatch.setattr("core.instances.MioV2Client", FakeClient)
    primary = InstanceConfig("primary", "Main desk", token="mio_fixture")
    registry = InstanceRegistry(primary)
    initial = registry.get()
    await registry.configure([replace(primary, label="Main room")])
    assert registry.get() is initial

    await registry.configure([replace(primary, token="mio_fixture_replacement")])
    replacement = registry.get()
    assert replacement.credentials[1] == "mio_fixture_replacement"
    assert replacement is not initial
    assert not initial.closed

    await registry.close()
    assert initial.closed and replacement.closed
    with pytest.raises(MioApiError) as exc:
        registry.get()
    assert exc.value.code == "registry_closed"
