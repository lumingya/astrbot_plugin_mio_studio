"""Print-inspired chat cards with optional Pillow and local fonts."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .textfmt import KIND_LABEL, STATUS_LABEL

RENDER_MODES = ("auto", "image", "text")
_WIDTH = 880
_MARGIN = 54
_LIMIT = 64
_SWATCHES = ("#87695a", "#8e7763", "#7d8570", "#737684", "#a0776a")

_THEMES = {
    "paper": {
        "sheet": "#f3ede1",
        "panel": "#ece2d1",
        "line": "#cfc0aa",
        "ink": "#302822",
        "muted": "#635348",
        "accent": "#8e4737",
    },
    "ink": {
        "sheet": "#252521",
        "panel": "#30302a",
        "line": "#4d493d",
        "ink": "#f3e8d5",
        "muted": "#cbbba1",
        "accent": "#e4a387",
    },
}


@dataclass(frozen=True, slots=True)
class _Row:
    title: str
    detail: str = ""
    tag: str = ""
    progress: float | None = None
    swatch: tuple[str, ...] = ()


def _font_candidates(font_path: Path | str | None):
    if font_path:
        yield Path(font_path)
    if value := os.environ.get("MIO_CARD_FONT"):
        yield Path(value)
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    yield from (windows / name for name in ("msyh.ttc", "simsun.ttc", "simhei.ttf"))
    for value in (
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    ):
        yield Path(value)


def _find_font(image_font, configured):
    for path in _font_candidates(configured):
        try:
            if not path.is_file():
                continue
            font = image_font.truetype(str(path), 24)
            missing = bytes(font.getmask(chr(0x10FFFF)))
            if all(bytes(font.getmask(glyph)) != missing for glyph in "中文"):
                return str(path)
        except (OSError, ValueError):
            continue
    return None


class CardRenderer:
    """Return a PNG path, or None when the caller should send text."""

    def __init__(self, cache_dir: Path, font_path: Path | str | None = None):
        self.cache_dir = Path(cache_dir)
        self._font_path = font_path
        self._font: str | None = None
        self._searched = False
        self._lock = threading.Lock()

    def catalog(
        self,
        catalog: Mapping[str, Sequence[Mapping[str, Any]]],
        kinds: Sequence[str],
        *,
        mode: str = "auto",
        theme: str = "paper",
    ) -> Path | None:
        rows: list[_Row] = []
        multiple = len(kinds) > 1
        for kind in kinds:
            items = catalog.get(kind) or []
            label = KIND_LABEL.get(kind, "创作资源")
            limit = 3 if multiple else 10
            if not items:
                rows.append(_Row(label, "暂未配置", "00"))
                continue
            if len(items) > limit:
                return None
            for i, item in enumerate(items, 1):
                title = str(item.get("title") or item.get("name") or item.get("id") or "未命名")
                note = item.get("description") or item.get("provider") or item.get("layout")
                detail = f"{label} · {note}" if note else label
                swatch = ()
                if kind == "layouts":
                    options = item.get("options") or {}
                    swatch = tuple(
                        val
                        if isinstance(val, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", val)
                        else fallback
                        for val, fallback in zip(
                            (options.get("paper"), options.get("accent"), options.get("text")),
                            _SWATCHES,
                        )
                    )
                rows.append(_Row(title, detail, f"{i:02}", swatch=swatch))
        return self._try_rows(
            "RESOURCE INDEX",
            "创作资源",
            "工作流 · 分镜 · 预设 · 画册",
            rows[:18],
            "编号对应 /mio ls · 使用 /mio use 开始创作",
            mode,
            theme,
        )

    def tasks(
        self,
        views: Sequence[Mapping[str, Any]],
        *,
        previews: Sequence[Path] = (),
        mode: str = "auto",
        theme: str = "paper",
    ) -> Path | None:
        rows: list[_Row] = []
        for task in views[:8]:
            status = str(task.get("statusKey") or "unknown")
            total = int(task.get("total") or 0)
            done = max(0, int(task.get("done") or 0))
            details = [STATUS_LABEL.get(status, "处理中")]
            if total:
                details.append(f"{done}/{total} 幕")
            if queue := task.get("queuePosition"):
                details.append(f"队列第 {queue} 位")
            if failed := task.get("failed"):
                details.append(f"{failed} 幕失败")
            rows.append(
                _Row(
                    str(task.get("title") or "未命名任务"),
                    "  ·  ".join(details),
                    f"#{task.get('num') or '—'}",
                    min(1.0, done / total) if total > 0 else None,
                )
            )
        return self._try_rows(
            "PRODUCTION LOG",
            "任务进度",
            f"记录 {len(views)} 项",
            rows or [_Row("暂无任务", "发送 /mio draw 写下一个创意")],
            "查看 /mio status #  ·  取件 /mio get #",
            mode,
            theme,
            previews=previews,
        )

    def help(self, *, mode: str = "auto", theme: str = "paper") -> Path | None:
        rows = [
            _Row("/mio draw 一句话", "从灵感生成漫画", "01"),
            _Row("/mio wizard", "跟随向导逐步创作", "02"),
            _Row("/mio ls · /mio use 1 1 1", "浏览资源并装配工坊任务", "03"),
            _Row("/mio trial 提示词", "试画一张图", "04"),
            _Row("/mio jobs · /mio status #", "查看创作进度", "05"),
            _Row("/mio get # · /mio works", "领取成品或浏览已有作品", "06"),
            _Row("/mio redraw # 1 · /mio qa #", "返工单幕或查看质检", "07"),
        ]
        return self._try_rows(
            "FIELD GUIDE",
            "绘页速查",
            "给漫画创作者的聊天手册",
            rows,
            "完整命令和参数请查看 Mio 绘页控制面板",
            mode,
            theme,
        )

    def gallery(
        self,
        works: Sequence[Mapping[str, Any]],
        *,
        mode: str = "auto",
        theme: str = "paper",
    ) -> Path | None:
        return self._render("gallery", mode, theme, tuple(works[:6]), len(works))

    def _try_rows(
        self,
        kicker: str,
        title: str,
        intro: str,
        rows: Sequence[_Row],
        footer: str,
        mode: str,
        theme: str,
        *,
        previews: Sequence[Path] = (),
    ) -> Path | None:
        return self._render("rows", mode, theme, kicker, title, intro, rows, footer, previews[:5])

    def _render(self, renderer: str, mode: str, theme: str, *args) -> Path | None:
        if mode == "text":
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None
        with self._lock:
            if not self._searched:
                self._font = _find_font(ImageFont, self._font_path)
                self._searched = True
            if self._font is None:
                return None
            try:
                palette = _THEMES.get(theme, _THEMES["paper"])
                create = _rows_image if renderer == "rows" else _gallery_image
                image = create(Image, ImageDraw, ImageFont, self._font, palette, *args)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    prefix="mio-card-", suffix=".png", dir=self.cache_dir, delete=False
                ) as fp:
                    path = Path(fp.name)
                    try:
                        image.save(fp, format="PNG", optimize=True)
                    except (OSError, ValueError):
                        fp.close()
                        path.unlink(missing_ok=True)
                        return None
                stale = sorted(
                    self.cache_dir.glob("mio-card-*.png"), key=lambda p: p.stat().st_mtime_ns
                )[:-_LIMIT]
                for old in stale:
                    try:
                        old.unlink(missing_ok=True)
                    except OSError:
                        pass
                return path
            except (OSError, ValueError):
                return None


class _Canvas:
    def __init__(self, image, image_draw, image_font, font: str, colors, height: int):
        self._image_type = image
        self.image = image.new("RGB", (_WIDTH, height), colors["sheet"])
        self.draw = image_draw.Draw(self.image)
        self.colors = colors
        self._font_file = font
        self._image_font = image_font
        self._fonts = {}

    def label(self, x, y, text, size, color="ink", *, max_width=None, anchor="lt", lines=1):
        font = self._fonts.get(size)
        if font is None:
            font = self._fonts[size] = self._image_font.truetype(self._font_file, size)
        content = " ".join(str(text or "").split())[:600]
        width = max_width or _WIDTH - 2 * _MARGIN
        for index in range(lines):
            pos = (x, y + index * (size + 5))
            if self.draw.textlength(content, font=font) <= width:
                self.draw.text(pos, content, fill=self.colors[color], font=font, anchor=anchor)
                break
            tail = "…" if index == lines - 1 else ""
            low, high = 0, len(content)
            while low < high:
                middle = (low + high + 1) // 2
                if self.draw.textlength(content[:middle] + tail, font=font) <= width:
                    low = middle
                else:
                    high = middle - 1
            if not tail and (space := content.rfind(" ", low // 2, low)) != -1:
                low = space
            self.draw.text(
                pos, content[:low] + tail, fill=self.colors[color], font=font, anchor=anchor
            )
            content = content[max(1, low) :].lstrip()

    def header(self, kicker, title, intro):
        self.draw.line((_MARGIN, 43, _WIDTH - _MARGIN, 43), fill=self.colors["line"], width=1)
        self.label(_MARGIN, 62, "MIO  /  绘页", 19, "accent")
        self.label(_WIDTH - _MARGIN, 62, kicker, 17, "muted", anchor="rt")
        self.label(_MARGIN, 111, title, 54)
        self.label(_MARGIN, 181, intro, 22, "muted")
        self.draw.line((_MARGIN, 224, _WIDTH - _MARGIN, 224), fill=self.colors["line"], width=1)

    def footer(self, text):
        bottom = self.image.height
        self.draw.line(
            (_MARGIN, bottom - 69, _WIDTH - _MARGIN, bottom - 69), fill=self.colors["line"], width=1
        )
        self.label(_MARGIN, bottom - 48, text, 19, "muted")

    def photo(self, path, box) -> bool:
        if not isinstance(path, (str, Path)):
            return False
        from PIL import ImageOps

        try:
            with self._image_type.open(path) as image:
                if image.width * image.height > 25_000_000:
                    return False
                x0, y0, x1, y1 = box
                cropped = ImageOps.fit(
                    image.convert("RGB"),
                    (x1 - x0, y1 - y0),
                    method=self._image_type.Resampling.LANCZOS,
                )
                self.image.paste(cropped, (x0, y0))
        except (OSError, ValueError, self._image_type.DecompressionBombError):
            return False
        return True


def _rows_image(
    image, image_draw, image_font, font, colors, kicker, title, intro, rows, footer, previews
):
    preview_height = 138 if previews else 0
    row_height = 88
    canvas = _Canvas(
        image, image_draw, image_font, font, colors, 320 + row_height * len(rows) + preview_height
    )
    canvas.header(kicker, title, intro)
    for index, row in enumerate(rows):
        top = 237 + row_height * index
        if index:
            canvas.draw.line(
                (_MARGIN, top - 7, _WIDTH - _MARGIN, top - 7), fill=colors["line"], width=1
            )
        canvas.label(_MARGIN, top + 4, row.tag or "·", 22, "accent", max_width=72)
        canvas.label(_MARGIN + 78, top + 1, row.title, 27, max_width=646)
        canvas.label(
            _MARGIN + 78,
            top + 37,
            row.detail,
            19,
            "muted",
            max_width=610,
            lines=1 if row.progress is not None else 2,
        )
        if row.progress is not None:
            x = _MARGIN + 78
            canvas.draw.rounded_rectangle((x, top + 66, x + 610, top + 72), 3, fill=colors["line"])
            if row.progress:
                canvas.draw.rounded_rectangle(
                    (x, top + 66, x + int(610 * row.progress), top + 72), 3, fill=colors["accent"]
                )
        for i, swatch in enumerate(row.swatch):
            canvas.draw.rectangle((758 + i * 20, top + 36, 773 + i * 20, top + 51), fill=swatch)
    y = 237 + row_height * len(rows)
    for i, preview in enumerate(previews):
        x = _MARGIN + i * 155
        canvas.draw.rectangle((x, y + 3, x + 140, y + 110), fill=colors["panel"])
        canvas.photo(preview, (x, y + 3, x + 141, y + 111))
    canvas.footer(footer)
    return canvas.image


def _gallery_image(image, image_draw, image_font, font, colors, works, count):
    canvas = _Canvas(
        image, image_draw, image_font, font, colors, 355 + 285 * ((len(works) + 2) // 3)
    )
    canvas.header("PUBLISHED ESSAYS", "作品画廊", f"精选预览 · 共 {count} 部作品")
    if not works:
        canvas.label(_MARGIN, 255, "还没有可浏览的作品。", 26, "muted")
    for index, item in enumerate(works):
        x = _MARGIN + index % 3 * 263
        y = 247 + index // 3 * 285
        title = item.get("title") or item.get("name") or "未命名"
        if not canvas.photo(item.get("cover_path"), (x, y, x + 243, y + 208)):
            swatch = _SWATCHES[index % len(_SWATCHES)]
            canvas.draw.rectangle((x, y, x + 243, y + 207), fill=swatch)
            canvas.draw.rectangle((x + 30, y + 22, x + 213, y + 185), outline="#eee3d0", width=1)
            canvas.draw.arc((x + 50, y + 60, x + 190, y + 198), 180, 344, fill="#eee3d0", width=2)
            canvas.draw.ellipse((x + 144, y + 50, x + 166, y + 72), fill="#eee3d0")
            canvas.draw.line((x + 58, y + 165, x + 185, y + 165), fill="#eee3d0", width=1)
        number = item.get("num") or f"{index + 1:02}"
        canvas.label(x, y + 224, f"{number}  {title}", 23, max_width=243)
        canvas.label(x, y + 255, item.get("subtitle") or "Mio · 绘页", 16, "muted", max_width=243)
    canvas.footer("使用 /mio export 作品编号 领取画册")
    return canvas.image
