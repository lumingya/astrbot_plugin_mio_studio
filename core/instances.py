"""Route Mio requests by a stable instance ID."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .v2_client import MioApiError, MioV2Client, normalize_base_url

PRIMARY_INSTANCE_ID = "primary"
_INSTANCE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")


@dataclass(frozen=True, slots=True)
class InstanceConfig:
    id: str
    label: str
    base_url: str = "http://127.0.0.1:8788"
    token: str = field(default="", repr=False)
    timeout: float = 60.0
    cloud: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or _INSTANCE_ID.fullmatch(self.id) is None:
            raise ValueError("实例 ID 需为 1–48 位小写字母、数字、下划线或连字符。")
        if not isinstance(self.label, str) or not 1 <= len(self.label.strip()) <= 80:
            raise ValueError("实例名称需为 1–80 个字符。")
        if not isinstance(self.token, str):
            raise ValueError("Mio 令牌应为文本。")
        token = self.token.strip()
        if any(char.isspace() or not 32 < ord(char) < 127 for char in token):
            raise ValueError("Mio 令牌格式有误，请重新复制完整令牌。")
        try:
            timeout = float(self.timeout)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("实例超时需为 0 到 600 秒之间的数字。") from None
        if isinstance(self.timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise ValueError("实例超时需大于 0 秒，且不得超过 600 秒。")
        if not isinstance(self.cloud, bool):
            raise ValueError("云端实例标记应为布尔值。")
        if not isinstance(self.base_url, str):
            raise ValueError("实例地址应为 HTTP 或 HTTPS URL。")

        object.__setattr__(self, "label", self.label.strip())
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        object.__setattr__(self, "timeout", timeout)

    def public(self) -> dict[str, str | bool | float]:
        return {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "has_token": bool(self.token),
            "timeout": self.timeout,
            "cloud": self.cloud,
        }


class InstanceRegistry:
    """Own lazy clients for one plugin lifetime.

    Explicit and session selections fail closed when removed, and tasks should
    pass their stored instance ID when getting a client.
    """

    def __init__(self, primary: InstanceConfig) -> None:
        if primary.id != PRIMARY_INSTANCE_ID:
            raise ValueError("主实例 ID 必须为 primary。")
        self._default_id = PRIMARY_INSTANCE_ID
        self._configs = {primary.id: primary}
        self._clients: dict[str, MioV2Client] = {}
        self._retired: list[MioV2Client] = []
        self._closed = False

    @property
    def default_id(self) -> str:
        return self._default_id

    def _require_open(self) -> None:
        if self._closed:
            raise MioApiError("Mio 插件正在关闭，请稍后重试。", code="registry_closed", status=503)

    async def configure(
        self, configs: Iterable[InstanceConfig], default_id: str = PRIMARY_INSTANCE_ID
    ) -> None:
        """Replace the complete topology after validation, including primary."""
        self._require_open()
        new: dict[str, InstanceConfig] = {}
        for config in configs:
            if config.id in new:
                raise ValueError("实例 ID 重复，请为每个实例使用不同的 ID。")
            new[config.id] = config
        if PRIMARY_INSTANCE_ID not in new:
            raise ValueError("配置中必须保留 primary 主实例。")
        if not isinstance(default_id, str) or default_id not in new:
            raise ValueError("默认实例不存在，请先添加该实例。")

        # The snapshot switch has no await points. Existing operations can keep
        # using the client they received, while new lookups get the new client.
        for instance_id, old in self._configs.items():
            replacement = new.get(instance_id)
            if replacement is None or (
                old.base_url,
                old.token,
                old.timeout,
            ) != (replacement.base_url, replacement.token, replacement.timeout):
                client = self._clients.pop(instance_id, None)
                if client is not None:
                    self._retired.append(client)
        self._configs = new
        self._default_id = default_id

    def resolve(
        self, requested: str | None = None, session_default: str | None = None
    ) -> InstanceConfig:
        self._require_open()
        instance_id = requested or session_default or self._default_id
        if not isinstance(instance_id, str) or instance_id not in self._configs:
            raise MioApiError(
                "Mio 实例不存在或已移除，请检查实例配置。",
                code="instance_not_found",
                status=404,
            )
        return self._configs[instance_id]

    def get(self, instance_id: str | None = None) -> MioV2Client:
        config = self.resolve(instance_id)
        client = self._clients.get(config.id)
        if client is None:
            client = MioV2Client(config.base_url, config.token, timeout=config.timeout)
            self._clients[config.id] = client
        return client

    def public(self) -> list[dict[str, str | bool | float]]:
        return [config.public() for config in self._configs.values()]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        clients = [*self._clients.values(), *self._retired]
        self._clients.clear()
        self._retired.clear()
        await asyncio.gather(*(client.close() for client in clients))
