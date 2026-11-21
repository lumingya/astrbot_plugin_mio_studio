"""Bounded projections used by the authenticated plugin dashboard."""

from __future__ import annotations

import asyncio
import base64
import math
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .service import ServiceError
from .store import PENDING_STATES
from .v2_client import DownloadedFile

_CREDENTIAL = re.compile(
    r"\bmio_[A-Za-z0-9_-]{12,}\b|\bsk-[A-Za-z0-9_-]{12,}\b|(?i:\bbearer\s+)[A-Za-z0-9._~-]{8,}"
)
_SECRET_FIELD = re.compile(r"secret|token|password|api.?key|authorization|密钥|令牌|凭据", re.I)
_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_PREVIEW = 6 * 1024**2


def display(value: Any, limit: int = 256) -> str:
    return _CREDENTIAL.sub("[已隐藏]", value)[:limit] if isinstance(value, str) else ""


def rows(value: Any, limit: int = 400) -> list[dict]:
    return (
        [row for row in value[:limit] if isinstance(row, dict)] if isinstance(value, list) else []
    )


def integer(value: Any, *, default=None, minimum=0, maximum=400) -> int | None:
    if value is None or value == "":
        return default
    if type(value) is int:
        number = value
    elif isinstance(value, str) and len(value) <= 10 and value.isascii() and value.isdecimal():
        number = int(value)
    else:
        raise ServiceError("请输入有效的整数。", code="invalid_number")
    if not minimum <= number <= maximum:
        raise ServiceError(f"数值应在 {minimum} 到 {maximum} 之间。", code="invalid_number")
    return number


def assembly_preview(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ServiceError("Mio 没有返回装配预览。", status=502)
    panels = []
    missing = set()
    for item in rows(data.get("panels"), 200):
        variables = []
        hidden_values = set()
        for entry in rows(item.get("variables"), 128):
            key = display(entry.get("key"), 128)
            if not key:
                continue
            status = display(entry.get("status"), 32)
            secret = _SECRET_FIELD.search(key)
            raw = entry.get("value")
            if secret and isinstance(raw, str) and raw:
                hidden_values.add(raw)
            variables.append(
                {
                    "name": key,
                    "key": key,
                    "value": "" if secret else display(raw, 2048),
                    "field": display(entry.get("field"), 48),
                    "source": display(entry.get("source"), 48),
                    "status": status,
                    "missing": status not in {"resolved", "empty"},
                }
            )
        absent = list(dict.fromkeys(v["key"] for v in variables if v["missing"]))
        missing.update(absent)
        panel_id = display(item.get("panel_id"))

        def prompt(value, limit):
            clean = value if isinstance(value, str) else ""
            for hidden in sorted(hidden_values, key=len, reverse=True):
                clean = clean.replace(hidden, "[已隐藏]")
            return display(clean, limit)

        positive = prompt(item.get("positive"), 8000)
        panels.append(
            {
                "id": panel_id,
                "panel_id": panel_id,
                "title": display(item.get("title")),
                "order": item.get("order") if type(item.get("order")) is int else len(panels),
                "positive": positive,
                "prompt": positive,
                "negative": prompt(item.get("negative"), 8000),
                "dialogues": [prompt(line, 400) for line in item["dialogues"][:40]]
                if isinstance(item.get("dialogues"), list)
                else [],
                "variables": variables,
                "missing": absent,
                "missing_variables": absent,
            }
        )
    return {"title": display(data.get("title")), "panels": panels, "missing": sorted(missing)}


def _score(value: object) -> float | None:
    return float(value) if type(value) in (float, int) and 0 <= value <= 1 else None


def episode_view(episode: dict) -> dict:
    panel_rows = rows(episode.get("panels"), 200)
    take_rows = rows(episode.get("takes"), 4000)
    panel_rows.sort(key=lambda row: row["order"] if type(row.get("order")) is int else 0)
    output = []
    for number, panel in enumerate(panel_rows, 1):
        panel_id = display(panel.get("id"))
        if not panel_id:
            continue
        take_list = []
        for take in take_rows:
            if take.get("panel_id") != panel_id or take.get("status") not in {
                "adopted",
                "candidate",
            }:
                continue
            if len(take_list) >= 32:
                break
            take_id = display(take.get("id"))
            if not take_id:
                continue
            qa = take.get("qa") if isinstance(take.get("qa"), dict) else None
            issues = qa.get("issues") if qa else None
            take_list.append(
                {
                    "id": take_id,
                    "number": len(take_list) + 1,
                    "status": take["status"],
                    "score": _score(take.get("score")),
                    "qa": {
                        "score": _score(qa.get("score")),
                        "passed": qa.get("passed") is True,
                        "issues": [display(issue, 200) for issue in issues[:6]]
                        if isinstance(issues, list)
                        else [],
                    }
                    if qa
                    else None,
                }
            )
        description = display(panel.get("description"), 600)
        output.append(
            {
                "id": panel_id,
                "title": display(panel.get("title")) or f"第 {number} 幕",
                "description": description,
                "number": number,
                "status": "adopted"
                if any(take["status"] == "adopted" for take in take_list)
                else ("candidate" if take_list else "pending"),
                "takes": take_list,
            }
        )
    return {
        "episode_id": display(episode.get("id")),
        "id": display(episode.get("id")),
        "series_id": display(episode.get("series_id")),
        "title": display(episode.get("title")),
        "description": display(episode.get("synopsis"), 600),
        "panels": output,
    }


async def image_source(file: DownloadedFile) -> str:
    if file.content_type not in _IMAGE_MIME:
        raise ServiceError("Mio 没有返回可预览的图片。", code="image_format", status=415)
    if file.size > _MAX_PREVIEW:
        raise ServiceError(
            "图片超过面板预览上限，请下载原始成品。", code="image_too_large", status=413
        )

    def encode():
        try:
            with file.path.open("rb") as stream:
                raw = stream.read(_MAX_PREVIEW + 1)
        except OSError:
            raise ServiceError(
                "预览文件已过期，请重新加载。", code="image_expired", status=404
            ) from None
        if not raw or len(raw) > _MAX_PREVIEW:
            raise ServiceError("预览文件不可用。", code="image_too_large", status=413)
        return f"data:{file.content_type};base64,{base64.b64encode(raw).decode('ascii')}"

    return await asyncio.to_thread(encode)


def _time(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else 0


def overview_metrics(tasks, catalog, *, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    completed = [t for t in tasks if t.get("status") == "complete"]
    durations = [
        _time(t.get("finished_at")) - start
        for t in completed
        if (start := _time(t.get("started_at") or t.get("created_at"))) > 0
        and _time(t.get("finished_at")) >= start
    ]
    return {
        "images": sum(
            max(0, task["done"])
            for task in completed
            if _time(task.get("finished_at")) >= midnight
            and task.get("kind") != "qa"
            and type(task.get("done")) is int
        ),
        "active": sum(task.get("status") in PENDING_STATES for task in tasks),
        "average": round(sum(durations) / len(durations), 1) if durations else 0,
        "resources": sum(len(group) for group in catalog.values() if isinstance(group, list)),
    }


def export_options(payload: Mapping) -> dict:
    nested = payload.get("options")
    options = dict(nested) if isinstance(nested, dict) else {}
    options.update(
        {
            key: payload[key]
            for key in (
                "theme_color",
                "signature",
                "show_captions",
                "show_prompts",
                "image_profile",
                "lettered",
                "voice",
            )
            if key in payload
        }
    )
    cleaned = {}
    for key in ("show_captions", "show_prompts", "lettered", "voice"):
        value = options.get(key)
        if value is None:
            continue
        if type(value) is bool:
            cleaned[key] = value
        elif isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
            cleaned[key] = value.lower() in {"true", "1"}
        else:
            raise ServiceError("导出选项必须为布尔值。", code="invalid_export")
    for key in ("theme_color", "signature", "image_profile"):
        value = options.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > 120:
            raise ServiceError("导出选项格式不正确。", code="invalid_export")
        if key == "theme_color" and value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ServiceError("请输入 6 位十六进制主题色。", code="invalid_export")
        if key == "image_profile" and value not in {
            "auto",
            "archive",
            "clean",
            "publish",
            "preview",
        }:
            raise ServiceError("请选择可用的图片档位。", code="invalid_export")
        cleaned[key] = value
    return cleaned
