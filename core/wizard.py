"""Three-step, per-user workshop wizard for AstrBot's plain messages."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

_STAGES = ("storyboards", "workflows", "presets")
_LABELS = ("分镜", "工作流", "预设")
_CANCEL = {"取消", "退出", "cancel"}
_DEFAULT = {"默认", "跳过", "0"}
_NEXT = {"更多", "下一页"}
_PREVIOUS = {"上一页"}
_INDEX_TRANSLATION = str.maketrans("＃０１２３４５６７８９", "#0123456789")
_INDEX = re.compile(r"#?[0-9]+\Z")


@dataclass(frozen=True, slots=True)
class WizardSelection:
    workflow: str | None
    storyboard: str
    presets: tuple[str, ...]
    instance_id: str | None


@dataclass(frozen=True, slots=True)
class WizardReply:
    text: str
    selection: WizardSelection | None = None
    cancelled: bool = False

    @property
    def completed(self) -> bool:
        return self.selection is not None


@dataclass(frozen=True, slots=True)
class _Resource:
    id: str
    title: str


@dataclass(slots=True)
class _Session:
    catalog: tuple[tuple[_Resource, ...], ...]
    instance_id: str | None
    expires: float
    stage: int = 0
    page: int = 0
    query: str = ""
    storyboard: str = ""
    workflow: str | None = None


def _resources(items: Any) -> tuple[_Resource, ...]:
    if not isinstance(items, (list, tuple)):
        return ()
    resources: dict[str, _Resource] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip() or len(item_id) > 256:
            continue
        title = item.get("title") or item.get("name") or item_id
        title = str(title).strip()[:256]
        resources.setdefault(item_id, _Resource(item_id, title))
        if len(resources) == 1000:
            break
    return tuple(resources.values())


class WizardManager:
    """Keep short-lived drafts in memory; only completion yields a selection."""

    def __init__(
        self,
        *,
        ttl: float = 180,
        page_size: int = 7,
        max_sessions: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl <= 0 or page_size < 1 or max_sessions < 1:
            raise ValueError("Wizard limits must be positive")
        self.ttl = ttl
        self.page_size = min(page_size, 12)
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[tuple[str, str], _Session] = {}
        self._timer: asyncio.TimerHandle | None = None
        self._deadline: float | None = None

    def _schedule(self) -> None:
        deadline = min((state.expires for state in self._sessions.values()), default=None)
        if deadline == self._deadline:
            return
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
            self._deadline = None
        if deadline is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._deadline = deadline
        self._timer = loop.call_later(max(0, deadline - self._clock()), self._expire)

    def _expire(self) -> None:
        self._timer = None
        self._deadline = None
        self.prune()

    def prune(self, now: float | None = None) -> int:
        current = self._clock() if now is None else now
        expired = [key for key, state in self._sessions.items() if state.expires <= current]
        for key in expired:
            self._sessions.pop(key, None)
        if now is None:
            self._schedule()
        return len(expired)

    def close(self) -> None:
        self._sessions.clear()
        self._schedule()

    def active(self, actor_key: str, session: str, *, now: float | None = None) -> bool:
        self.prune(now)
        return (actor_key, session) in self._sessions

    def start(
        self,
        actor_key: str,
        session: str,
        catalog: Mapping[str, Any],
        *,
        instance_id: str | None = None,
        now: float | None = None,
    ) -> WizardReply:
        self.prune(now)
        resources = tuple(_resources(catalog.get(kind)) for kind in _STAGES)
        if not actor_key or not session:
            return WizardReply("无法识别当前会话，请重新发送 /mio wizard。")
        if not resources[0]:
            return WizardReply("这个实例还没有分镜。请在 Mio 创建分镜，再打开向导。")
        key = (actor_key, session)
        if key not in self._sessions and len(self._sessions) >= self.max_sessions:
            return WizardReply("当前正在使用向导的人较多，请稍后再试。")
        current = self._clock() if now is None else now
        state = _Session(resources, instance_id, current + self.ttl)
        self._sessions[key] = state
        if now is None:
            self._schedule()
        return self._show(state)

    def cancel(self, actor_key: str, session: str) -> bool:
        found = self._sessions.pop((actor_key, session), None) is not None
        if found:
            self._schedule()
        return found

    @staticmethod
    def _matches(state: _Session) -> list[tuple[int, _Resource]]:
        items = list(enumerate(state.catalog[state.stage], start=1))
        if state.query:
            key = state.query.casefold()
            return [(i, r) for i, r in items if key in r.title.casefold() or key in r.id.casefold()]
        return items

    def _show(self, state: _Session) -> WizardReply:
        items = self._matches(state)
        start = state.page * self.page_size
        page_items = items[start : start + self.page_size]
        lines = [f"Mio 创作向导 · {state.stage + 1}/3  选择{_LABELS[state.stage]}"]
        if state.query:
            lines.append(f"搜索结果：{len(items)} 项。回复“查看全部”清除筛选。")
        for index, resource in page_items:
            name = " ".join(resource.title.split())[:72]
            lines.append(f"{index}. {name}")
        if not page_items:
            lines.append("没有匹配的资源。" if state.query else "此实例暂未配置此类资源。")
        if state.stage > 0:
            lines.append("0. 使用已设置的默认值")
        if state.stage == 2:
            lines.append("回复多个预设编号可叠加，例如 1,3。提交后开始排队。")
        if len(items) > self.page_size:
            total_pages = (len(items) + self.page_size - 1) // self.page_size
            lines.append(f"第 {state.page + 1}/{total_pages} 页 · 回复“更多”或“上一页”。")
        lines.append("回复编号或名称；也可回复“搜索 关键词”或“取消”。")
        return WizardReply("\n".join(lines))

    @staticmethod
    def _choose(state: _Session, token: str) -> tuple[bool, str | None]:
        items = state.catalog[state.stage]
        normalized = token.casefold().translate(_INDEX_TRANSLATION)
        if normalized in _DEFAULT and state.stage > 0:
            return True, None
        if _INDEX.fullmatch(normalized):
            raw = normalized.removeprefix("#")
            ordinal = int(raw) if len(raw) <= 9 else 0
            return True, items[ordinal - 1].id if 1 <= ordinal <= len(items) else ""
        matches = [item for item in items if token in (item.id, item.title)]
        if len(matches) == 1:
            return True, matches[0].id
        return bool(matches), ""

    def consume(
        self, actor_key: str, session: str, text: str, *, now: float | None = None
    ) -> WizardReply | None:
        current = self._clock() if now is None else now
        self.prune(now)
        key = (actor_key, session)
        state = self._sessions.get(key)
        token = text.strip()
        if not state or not token or len(token) > 1024 or token.startswith("/"):
            return None
        if token.casefold() in _CANCEL:
            self._sessions.pop(key)
            if now is None:
                self._schedule()
            return WizardReply("已退出创作向导。你可以用 /mio wizard 重新开始。", cancelled=True)
        if token in _NEXT:
            if (state.page + 1) * self.page_size < len(self._matches(state)):
                state.page += 1
        elif token in _PREVIOUS:
            state.page = max(0, state.page - 1)
        elif token == "查看全部" or token.startswith(("搜索 ", "查 ")):
            state.query = token.partition(" ")[2].strip()[:80]
            state.page = 0
        else:
            # Try exact names first; resource titles themselves may contain commas.
            matched, chosen = self._choose(state, token)
            if not matched and state.stage == 2 and re.search(r"[,，、]", token):
                parts = [p.strip() for p in re.split(r"[,，、]", token)]
                picks = [self._choose(state, part) for part in parts]
                if not all(known for known, _ in picks):
                    return None
                if len(parts) > 128 or any(not item_id for _, item_id in picks):
                    return WizardReply("请检查预设编号。使用默认值时请单独回复 0。")
                return self._complete(
                    key, state, tuple(dict.fromkeys(id_ for _, id_ in picks)), now
                )
            if not matched:
                return None
            if chosen == "":
                return WizardReply("没有这个编号，或名称不唯一。请按列表编号选择。")
            if state.stage == 0:
                state.storyboard = chosen or ""
            elif state.stage == 1:
                state.workflow = chosen
            else:
                return self._complete(key, state, (chosen,) if chosen else (), now)
            state.stage += 1
            state.page = 0
            state.query = ""
        state.expires = current + self.ttl
        if now is None:
            self._schedule()
        return self._show(state)

    def _complete(
        self, key: tuple[str, str], state: _Session, presets: tuple[str, ...], now: float | None
    ) -> WizardReply:
        self._sessions.pop(key)
        if now is None:
            self._schedule()
        return WizardReply(
            "已选好分镜、工作流与预设，正在准备提交。",
            selection=WizardSelection(state.workflow, state.storyboard, presets, state.instance_id),
        )
