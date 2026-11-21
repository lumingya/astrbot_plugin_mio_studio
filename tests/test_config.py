import json
from pathlib import Path

import pytest

from core.config import ConfigError, Settings
from core.store import TaskStore

DEMO_TOKEN = "mio_tok_demo_" + "fake_test_only_0123456789"


class NativeConfig(dict):
    saved_count = 0

    def save_config(self):
        self.saved_count += 1


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_API_V2_TOKEN", raising=False)
    return Settings(NativeConfig(), TaskStore(tmp_path / "tasks.sqlite3"))


def test_native_config_matches_schema_defaults_and_uses_secret_reference(settings, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert "api_token" not in schema["mio"]["items"]
    assert settings.primary().base_url == "http://127.0.0.1:8788"
    assert settings.cfg("governance", "global_limit") == 2
    assert settings.cfg("webhook", "enabled") is False
    assert settings.view()["mio"]["api_token_set"] is False

    monkeypatch.setenv("MIO_API_V2_TOKEN", DEMO_TOKEN)
    assert settings.primary().token == DEMO_TOKEN
    assert settings.view()["mio"]["api_token_set"] is True
    assert DEMO_TOKEN not in str(settings.view())


def test_private_token_is_write_only_and_scoped_to_service_address(settings):
    configuration = settings.update(
        {"mio": {"api_token": DEMO_TOKEN, "base_url": "http://localhost:8788/api/v2"}}
    )
    assert settings.primary().token == DEMO_TOKEN
    assert settings.primary().base_url == "http://localhost:8788"
    assert DEMO_TOKEN not in str(settings.config)
    assert DEMO_TOKEN not in str(configuration)

    settings.update({"mio": {"api_token": ""}})
    assert settings.primary().token == DEMO_TOKEN
    settings.update({"mio": {"base_url": "https://second.example.org"}})
    assert settings.primary().token == ""


def test_invalid_patch_does_not_change_settings_or_save_token(settings):
    with pytest.raises(ConfigError, match="整数"):
        settings.update({"mio": {"api_token": DEMO_TOKEN}, "governance": {"global_limit": "n/a"}})
    assert settings.config == {}
    assert settings.primary().token == ""
    assert settings.config.saved_count == 0

    with pytest.raises(ConfigError) as exc:
        settings.update({"mio": {"api_token_env": DEMO_TOKEN}})
    assert DEMO_TOKEN not in str(exc.value)
    settings.config.update({"mio": {"base_url": "http://127.0.0.1:8777/api/v1"}})
    assert settings.primary().base_url == "http://127.0.0.1:8777"


def test_session_rules_support_local_overrides_and_reset(settings):
    session = "test:GroupMessage:42"
    assert settings.set_session_policy(session, {"notify_mode": "strip", "layout_id": "folio"}) == {
        "notify_mode": "strip",
        "layout_id": "folio",
    }
    assert settings.session_policy("test:GroupMessage:43") == {}
    assert settings.view()["delivery"]["notify_mode"] == "summary"
    with pytest.raises(ConfigError):
        settings.set_session_policy(session, {"notify_mode": "export", "api_token": DEMO_TOKEN})
    assert settings.session_policy(session)["notify_mode"] == "strip"
    assert settings.set_session_policy(session, {"notify_mode": None}) == {"layout_id": "folio"}
