"""Authorize chat and panel operations before doing any work in Mio."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["admin", "creator", "viewer"]
Capability = Literal["read", "create", "control", "remove", "export", "admin", "session_config"]
_MESSAGE_TYPES = {"GroupMessage", "FriendMessage", "OtherMessage"}


class GovernanceError(Exception):
    """Permission or policy failure with user-facing text safe to show in chat."""

    def __init__(self, message: str, *, code: str, status: int = 403) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class Actor:
    """Identity supplied by a trusted AstrBot event adapter or panel route."""

    id: str
    name: str = ""
    session: str = ""
    platform: str = ""
    is_admin: bool = False
    is_session_admin: bool = False

    @classmethod
    def panel(cls) -> Actor:
        """Use only in a plugin route already authenticated by AstrBot."""
        return cls("panel", "控制面板", platform="panel", is_admin=True)

    @property
    def scoped_id(self) -> str:
        # UMO starts with AstrBot's unique adapter ID, unlike get_platform_name().
        adapter = self.session.split(":", 1)[0] if self.session else self.platform
        return f"{adapter}:{self.id}"


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """Quota limits to enforce in the store transaction when admitting work."""

    user_active_limit: int
    daily_limit: int
    cooldown_seconds: int
    cloud_daily_limit: int
    cloud: bool

    def store_kwargs(self, actor: Actor) -> dict[str, Any]:
        """Pass the authorized limits and scoped caller to TaskStore.admit."""
        return {
            "actor_key": actor.scoped_id,
            "daily_limit": self.daily_limit,
            "user_limit": self.user_active_limit,
            "cooldown": self.cooldown_seconds,
            "cloud": self.cloud,
            "cloud_limit": self.cloud_daily_limit,
        }


def _section(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key)
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(item.strip() for item in value if isinstance(item, str) and item.strip())


def _limit(config: Mapping[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        return min(hi, max(lo, int(value)))
    except ValueError:
        return default


def _session_parts(umo: str) -> tuple[str, str, str] | None:
    parts = umo.split(":", 2)
    if len(parts) != 3 or not parts[0] or parts[1] not in _MESSAGE_TYPES or not parts[2]:
        return None
    return parts[0], parts[1], parts[2]


def _allowed_session(umo: str, allowed: frozenset[str]) -> bool:
    # Platform / message type are explicit prefixes. Complete UMO values match
    # exactly, since a platform may put separators in its actual session ID.
    parts = _session_parts(umo)
    return parts is not None and (
        not allowed
        or umo in allowed
        or parts[0] in allowed
        or f"{parts[0]}:" in allowed
        or f"{parts[0]}:{parts[1]}" in allowed
        or f"{parts[0]}:{parts[1]}:" in allowed
    )


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


class Governance:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config

    @property
    def permissions(self) -> Mapping[str, Any]:
        return _section(self.config, "permissions")

    @property
    def limits(self) -> Mapping[str, Any]:
        return _section(self.config, "governance")

    @property
    def global_limit(self) -> int:
        return _limit(self.limits, "global_limit", 2, 1, 50)

    def role(self, actor: Actor) -> Role | None:
        if actor.is_admin:
            return "admin"
        if (
            self.permissions.get("admin_only") is True
            or not actor.id
            or not _allowed_session(actor.session, _items(self.permissions.get("allowed_sessions")))
        ):
            return None
        viewers = _items(self.permissions.get("viewer_ids"))
        creators = _items(self.permissions.get("creator_ids"))
        actor_ids = {actor.scoped_id}
        if ":" not in actor.id:
            actor_ids.add(actor.id)
        if actor_ids & viewers:
            return "viewer"
        return "creator" if not creators or actor_ids & creators else "viewer"

    def require(self, actor: Actor, capability: Capability) -> Role:
        role = self.role(actor)
        if role is None:
            raise GovernanceError("当前会话没有使用 Mio 绘页的权限。", code="session_denied")
        if capability not in {
            "read",
            "create",
            "control",
            "remove",
            "export",
            "admin",
            "session_config",
        }:
            raise ValueError("Unknown governance capability")
        if role == "admin" or capability == "read":
            return role
        if capability == "session_config":
            parts = _session_parts(actor.session)
            if actor.is_session_admin and parts and parts[1] == "GroupMessage":
                return role
            raise GovernanceError("只有管理员或群主可以更改交付策略。", code="role_denied")
        if capability == "admin":
            raise GovernanceError("此操作仅限管理员。", code="role_denied")
        if role == "creator":
            return role
        raise GovernanceError("访客可以浏览与取件；创作和管理需要创作者权限。", code="role_denied")

    def authorize_session_config(self, actor: Actor, session: str) -> None:
        role = self.require(actor, "session_config")
        if role != "admin" and actor.session != session:
            raise GovernanceError("只能更改当前群的交付策略。", code="session_denied")

    def authorize_task(
        self, actor: Actor, record: Mapping[str, Any], *, capability: Capability = "control"
    ) -> None:
        role = self.require(actor, capability)
        if role != "admin" and (
            record.get("sender_id") != actor.id or record.get("umo") != actor.session
        ):
            raise GovernanceError("这个任务不属于你，或来自其他会话。", code="not_owner")
        if capability not in {"read", "remove"} and (
            record.get("status") in {"legacy", "archived"} or record.get("legacy") is True
        ):
            raise GovernanceError(
                "这是旧版任务记录。请在 Mio 画廊核对作品，或创建新的任务。",
                code="legacy_task",
                status=409,
            )

    def policy(self, actor: Actor, *, cloud: bool = False, text: str = "") -> AdmissionPolicy:
        role = self.require(actor, "create")
        if cloud and role != "admin":
            # Cloud privileges can cost money and use adapter-qualified IDs.
            allowed = _items(self.permissions.get("cloud_allowed_ids"))
            if actor.scoped_id not in allowed:
                raise GovernanceError("当前账号没有使用云端出图通道的权限。", code="cloud_denied")
        prompt = _normalized(text)
        if any(_normalized(word) in prompt for word in _items(self.limits.get("blocked_keywords"))):
            raise GovernanceError(
                "内容包含此工作室限制的关键词，请修改后再试。", code="blocked_keyword"
            )
        return AdmissionPolicy(
            user_active_limit=_limit(self.limits, "user_active_limit", 1, 1, 50),
            daily_limit=_limit(self.limits, "daily_limit", 8, 0, 10000),
            cooldown_seconds=_limit(self.limits, "cooldown_seconds", 10, 0, 86400),
            cloud_daily_limit=_limit(self.limits, "cloud_daily_limit", 3, 0, 10000),
            cloud=cloud,
        )
