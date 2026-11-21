"""Read native plugin settings and keep credentials outside the native dashboard payload."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from string import Formatter
from typing import TYPE_CHECKING, Any

from .instances import PRIMARY_INSTANCE_ID, InstanceConfig
from .v2_client import normalize_base_url
from .webhook import callback_url

if TYPE_CHECKING:
    from .store import TaskStore

_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
)
_FIELDS = {section: meta["items"] for section, meta in _SCHEMA.items()}
_BOUNDS = {
    "request_timeout": (5, 600),
    "seed": (0, 2**32 - 1),
    "concurrency": (0, 8),
    "max_images": (1, 100),
    "max_file_mb": (1, 200),
    "poll_interval": (2, 120),
    "global_limit": (1, 50),
    "user_active_limit": (1, 50),
    "daily_limit": (0, 10000),
    "cooldown_seconds": (0, 86400),
    "cloud_daily_limit": (0, 10000),
    "bind_port": (1, 65535),
}
_SESSION_FIELDS = {
    "preset_ids": ("defaults", "preset_ids"),
    "layout_id": ("export", "layout_id"),
    "format": ("export", "format"),
    "image_profile": ("export", "image_profile"),
    "notify_mode": ("delivery", "notify_mode"),
    "send_mode": ("delivery", "send_mode"),
    "include_captions": ("delivery", "include_captions"),
    "max_images": ("delivery", "max_images"),
    "max_file_mb": ("delivery", "max_file_mb"),
}
_TOKEN = re.compile(r"mio_tok_[A-Za-z0-9]+_[A-Za-z0-9_-]{20,150}")
_TOKEN_ENV = re.compile(r"MIO_[A-Z][A-Z0-9_]{0,63}")
_INSTANCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
_TITLE_FIELDS = {"story", "preset", "workflow", "channel", "date", "time"}
_UNCHANGED = object()


class ConfigError(ValueError):
    """Invalid settings suitable for display without echoing supplied values."""

    code = "invalid_config"
    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _bad(label: str, hint: str) -> ConfigError:
    return ConfigError(f"{label}：{hint}")


def _strings(value: Any, label: str, *, delimiters: str) -> list[str]:
    if isinstance(value, str):
        value = re.split(delimiters, value)
    if (
        not isinstance(value, list)
        or len(value) > 256
        or any(not isinstance(item, str) or len(item) > 256 for item in value)
    ):
        raise _bad(label, "请分行填写，每一项须为不超过 256 字符的文本。")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _normalize(section: str, key: str, value: Any) -> Any:
    meta = _FIELDS[section][key]
    label = meta["description"]
    kind = meta["type"]
    if kind == "bool":
        if type(value) is not bool:
            raise _bad(label, "请选择开启或关闭。")
    elif kind == "int":
        if isinstance(value, str):
            raw = value.strip()
            if re.fullmatch(r"[0-9]{1,12}", raw):
                value = int(raw)
        lo, hi = _BOUNDS[key]
        if type(value) is not int or not lo <= value <= hi:
            raise _bad(label, f"请输入 {lo} 到 {hi} 之间的整数。")
    elif kind == "list":
        value = _strings(value, label, delimiters=r"[\n,，]")
    else:
        if key == "preset_ids":
            return ",".join(_strings(value, label, delimiters=r"[\n,，]"))
        if not isinstance(value, str):
            raise _bad(label, "请输入文本。")
        value = value.strip()
        max_chars = 1800 if key in {"base_url", "public_url"} else 120
        if len(value) > max_chars or any(ord(char) < 32 for char in value):
            raise _bad(label, f"请输入不超过 {max_chars} 字符的单行文本。")
        if "options" in meta and value not in meta["options"]:
            raise _bad(label, "请选择一个有效选项。")
        if key == "base_url":
            value = normalize_base_url(value)
        elif key == "public_url" and value:
            callback_url(value, PRIMARY_INSTANCE_ID)
            value = value.rstrip("/")
        elif key == "api_token_env" and value and not _TOKEN_ENV.fullmatch(value):
            raise _bad(label, "请输入以 MIO_ 开头的环境变量名，请勿填写令牌本身。")
        elif key == "bind_host" and value != "localhost":
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise _bad(label, "请输入监听 IP 地址或 localhost。") from None
        elif key == "theme_color" and value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise _bad(label, "请使用 6 位十六进制颜色，例如 #A05C4D。")
        elif key == "title_template":
            try:
                valid = all(
                    field is None or field in _TITLE_FIELDS and not (spec or conv)
                    for _, field, spec, conv in Formatter().parse(value)
                )
            except ValueError:
                valid = False
            if not valid:
                raise _bad(label, "请检查花括号；只支持设置说明中列出的变量。")
    return value


def _submitted_token(value: Any) -> str | object:
    if value is None:
        return ""
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return _UNCHANGED
        if _TOKEN.fullmatch(token):
            return token
    raise ConfigError("请在 Mio 中复制开放 API v2 的完整 mio_ 令牌。")


class Settings:
    def __init__(self, config: dict[str, Any], store: TaskStore) -> None:
        self.config = config
        self.store = store

    def cfg(self, section: str, key: str, default: Any = None) -> Any:
        values = self.config.get(section)
        value = values.get(key) if isinstance(values, Mapping) else None
        meta = _FIELDS.get(section, {}).get(key)
        if meta is None:
            return default if value is None else value
        if value is not None:
            try:
                return _normalize(section, key, value)
            except ValueError:
                pass
        return deepcopy(meta["default"])

    def primary(self) -> InstanceConfig:
        address = self._primary_address(self.config.get("mio"))
        name = self.cfg("mio", "api_token_env")
        token = self.store.get_primary_token(address)
        if token is None:
            token = os.environ.get(name, "").strip() if name else ""
        return InstanceConfig(
            PRIMARY_INSTANCE_ID,
            "主实例",
            address,
            token if _TOKEN.fullmatch(token) else "",
            self.cfg("mio", "request_timeout"),
            cloud=self.cfg("mio", "cloud"),
        )

    @staticmethod
    def _primary_address(raw: Any) -> str:
        address = raw.get("base_url") if isinstance(raw, Mapping) else None
        if address in (None, ""):
            return _FIELDS["mio"]["base_url"]["default"]
        # Fail closed on invalid native input rather than silently using another server.
        try:
            return _normalize("mio", "base_url", address)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None

    def view(self) -> dict[str, Any]:
        result = {
            section: {key: self.cfg(section, key) for key in fields}
            for section, fields in _FIELDS.items()
        }
        try:
            has_token = bool(self.primary().token)
        except ConfigError:
            has_token = False
        result["mio"].update(api_token_set=has_token, api_token_masked="")
        result["defaults"]["preset_ids"] = [
            value for value in result["defaults"]["preset_ids"].split(",") if value
        ]
        return result

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ConfigError("配置应按设置分组提交。")
        patch: dict[str, dict[str, Any]] = {}
        token: str | object = _UNCHANGED
        for section, values in changes.items():
            if section not in _FIELDS or not isinstance(values, dict):
                raise ConfigError("包含未知设置分组，或设置分组的格式有误。")
            normalized = {}
            for key, value in values.items():
                if section == "mio" and key == "api_token":
                    token = _submitted_token(value)
                    continue
                if key not in _FIELDS[section]:
                    raise ConfigError("配置包含未知或只读字段。")
                try:
                    normalized[key] = _normalize(section, key, value)
                except ValueError as exc:
                    raise ConfigError(str(exc)) from None
            if normalized:
                original = self.config.get(section)
                patch[section] = {
                    **(
                        {key: value for key, value in original.items() if key in _FIELDS[section]}
                        if isinstance(original, Mapping)
                        else {}
                    ),
                    **normalized,
                }
        token_address = (
            self._primary_address(patch.get("mio", self.config.get("mio")))
            if isinstance(token, str)
            else None
        )
        snapshot = dict(self.config)
        save = getattr(self.config, "save_config", None)
        saved = False
        try:
            if patch:
                self.config.update(patch)
                if callable(save):
                    save()
                    saved = True
            if token_address is not None:
                self.store.set_primary_token(token_address, token)
        except (OSError, RuntimeError, sqlite3.Error):
            self.config.clear()
            self.config.update(snapshot)
            if saved:
                try:
                    save()
                except OSError:
                    pass
            raise ConfigError("无法保存设置，请检查插件数据目录权限。") from None
        return self.view()

    def session_policy(self, umo: str) -> dict[str, Any]:
        return self.store.session_overrides(umo) if umo else {}

    def set_session_policy(self, umo: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not umo or not isinstance(changes, dict):
            raise ConfigError("请选择会话并提交交付设置。")
        validated = {}
        for key, value in changes.items():
            if key == "instance_id":
                if value is not None and (
                    not isinstance(value, str) or not _INSTANCE.fullmatch(value)
                ):
                    raise ConfigError("所选实例 ID 无效。")
                validated[key] = value
                continue
            if key not in _SESSION_FIELDS:
                raise ConfigError("不支持在单个会话中覆盖这项设置。")
            try:
                validated[key] = (
                    _normalize(*_SESSION_FIELDS[key], value) if value is not None else None
                )
            except ValueError as exc:
                raise ConfigError(str(exc)) from None
        return self.store.set_session_overrides(umo, validated)
