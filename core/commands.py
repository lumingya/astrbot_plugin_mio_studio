"""Parse Mio chat commands without depending on AstrBot or contacting Mio."""

from __future__ import annotations

import re
from dataclasses import dataclass

FORMATS = ("html", "pdf", "zip", "strip", "slices", "motion")
NOTIFY_MODES = ("images", "strip", "export", "summary", "qa_then_send", "silent")
_FORMAT_ALIASES = {"image": "img", "images": "img", "图片": "img"}
_COMMAND_ALIASES = {"画": "draw", "试画": "trial", "作品": "works", "向导": "wizard"}
_CATALOG_ALIASES = {
    "workflows": ("wf", "workflow", "workflows", "工作流"),
    "storyboards": ("sb", "storyboard", "storyboards", "分幕", "分镜"),
    "presets": ("ps", "preset", "presets", "预设"),
    "channels": ("ch", "channel", "channels", "通道"),
    "layouts": ("tpl", "layout", "layouts", "template", "templates", "模板"),
    "kits": ("kit", "kits", "套件"),
}
_LEX = re.compile(
    r"""(?:(--[a-z][\w-]*=)?("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|“[^”]*”|‘[^’]*’)(?=\s|$)|\S+)"""
)
_INSTANCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}\Z")
_NUMBER = re.compile(r"[0-9]+\Z")
_INDEX_TRANSLATION = str.maketrans("＃０１２３４５６７８９", "#0123456789")
_EXPORT_TASK = re.compile(r"(?:assembly-[\w-]+|[a-fA-F0-9]{32}|[a-fA-F0-9-]{36})\Z")

USAGE = {
    "use": "/mio use <工作流#> <分镜#> [预设#,预设#] [通道#]，或 /mio use <分镜#>",
    "preview": "/mio preview <工作流#> <分镜#> <预设#>",
    "draw": "/mio draw <一句话> [--tpl 模板] [--style 预设] [--candidates 2]",
    "trial": "/mio trial <提示词> [工作流#]",
    "works": "/mio works [关键词]",
    "get": "/mio get [任务#] [html|pdf|zip|strip|slices|motion|img] [模板#]",
    "export": "/mio export <作品#> [html|pdf|zip|strip|slices|motion] [模板#]",
    "redraw": "/mio redraw <任务#> <第几幕> [新提示词]",
    "pick": "/mio pick <任务#> <第几幕> <候选#>",
    "mode": "/mio mode <images|strip|export|summary|qa_then_send|silent>",
    "ls": "/mio ls [wf|sb|ps|ch|tpl|kit]",
    **{
        name: f"/mio {name} <任务#>" for name in ("status", "pause", "resume", "cancel", "rm", "qa")
    },
    **{name: f"/mio {name}" for name in ("help", "ping", "jobs", "quota", "wizard")},
}

HELP_WAVE3 = """创作与取件
/mio draw <一句话> [--tpl 模板] [--style 预设] [--candidates 2] — 一句话出漫（/mio 画）
/mio trial <提示词> [工作流#] — 快速试画（/mio 试画）
/mio works [关键词] — 浏览作品（/mio 作品）
/mio export <作品#> [html|pdf|zip|strip|slices|motion] [模板#] — 导出历史作品
/mio preview <工作流#> <分镜#> <预设#> — 预览装配
/mio redraw <任务#> <第几幕> [新提示词] — 重画单幕
/mio pick <任务#> <第几幕> <候选#> — 采用候选图
/mio qa <任务#> — 运行质检
/mio wizard — 三步创作向导（/mio 向导；回复“取消”可退出）
/mio quota — 查看用量、冷却与队列
/mio mode <images|strip|export|summary|qa_then_send|silent> — 更改当前会话交付方式
支持按次指定实例：例如 /mio ls wf --instance backup。名称有空格时请加引号。"""


class CommandError(ValueError):
    def __init__(self, message: str, *, command: str, code: str = "invalid_arguments") -> None:
        usage = USAGE.get(command)
        self.message = f"{message}\n用法：{usage}" if usage else message
        self.code = code
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class CommandArgs:
    command: str
    instance_id: str | None = None
    target: str | None = None
    idea: str = ""
    prompt: str = ""
    workflow: str | None = None
    storyboard: str | None = None
    presets: tuple[str, ...] = ()
    channel: str | None = None
    series: str | None = None
    style: str | None = None
    task: str | None = None
    work: str | None = None
    panel: int | None = None
    candidate: int | None = None
    candidates: int = 1
    format: str | None = None
    layout: str | None = None
    keyword: str = ""
    mode: str | None = None


@dataclass(frozen=True, slots=True)
class _Word:
    raw: str
    value: str
    start: int
    end: int
    quoted: bool


def _words(text: str) -> list[_Word]:
    result = []
    for match in _LEX.finditer(text):
        raw = match[0]
        prefix, quoted = match.groups()
        if quoted:
            value = quoted[1:-1]
            if quoted[0] in ('"', "'"):
                value = value.replace("\\" + quoted[0], quoted[0]).replace("\\\\", "\\")
            value = (prefix or "") + value
        else:
            value = raw
        result.append(_Word(raw, value, match.start(), match.end(), bool(quoted) and not prefix))
    return result


def _free_text(source: str, words: list[_Word]) -> str:
    if len(words) == 1 and words[0].quoted:
        return words[0].value.strip()
    chunks = []
    for previous, current in zip([None, *words], words, strict=False):
        if previous:
            between = source[previous.end : current.start]
            chunks.append(between if between.isspace() else " ")
        chunks.append(current.raw)
    return "".join(chunks).strip()


_OPTIONS = {
    "draw": {"tpl", "style", "candidates", "series"},
    "trial": {"workflow"},
    "get": {"tpl", "format"},
    "export": {"tpl", "format"},
}
_INSTANCE_COMMANDS = {"ls", "use", "preview", "draw", "trial", "works", "export", "ping", "wizard"}


def _split_options(command: str, text: str) -> tuple[list[_Word], dict[str, str]]:
    allowed = set(_OPTIONS.get(command, ()))
    if command in _INSTANCE_COMMANDS:
        allowed.add("instance")
    args, options = [], {}
    words = iter(_words(text))
    positional_only = False
    for word in words:
        if not positional_only and not word.quoted and word.value.startswith("--"):
            if word.value == "--":
                positional_only = True
                continue
            key, sep, value = word.value[2:].partition("=")
            if key not in allowed:
                raise CommandError("有不支持的选项。", command=command, code="unknown_option")
            if key in options:
                raise CommandError(
                    "同一选项只能设置一次。", command=command, code="duplicate_option"
                )
            if not sep:
                token = next(words, None)
                if token is None or (not token.quoted and token.value.startswith("--")):
                    raise CommandError(f"--{key} 缺少参数。", command=command)
                value = token.value
            if not value.strip() or len(value) > 256:
                raise CommandError(f"--{key} 参数为空或太长。", command=command)
            options[key] = value.strip()
        else:
            args.append(word)
    return args, options


def _index(value: str, *, command: str, label: str, limit: int = 99999) -> int:
    normalized = value.translate(_INDEX_TRANSLATION).removeprefix("#")
    if not _NUMBER.fullmatch(normalized) or len(normalized) > 9:
        raise CommandError(f"{label}应为正整数。", command=command)
    ordinal = int(normalized)
    if not 1 <= ordinal <= limit:
        raise CommandError(f"{label}应为 1 到 {limit} 的整数。", command=command)
    return ordinal


def _ref(value: str | None, *, command: str) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not 1 <= len(value) <= 256 or any(ord(ch) < 32 for ch in value):
        raise CommandError("资源编号或名称为空或太长。", command=command)
    if value.startswith(('"', "'", "“", "‘")):
        raise CommandError("请补全资源名称两侧的引号。", command=command)
    normalized = value.translate(_INDEX_TRANSLATION).removeprefix("#")
    return normalized if _NUMBER.fullmatch(normalized) else value


def _presets(value: str, *, command: str) -> tuple[str, ...]:
    parts = [part.strip() for part in re.split(r"[,，、]", value)]
    if len(parts) > 128 or any(not part for part in parts):
        raise CommandError("预设请用逗号分隔，最多 128 个。", command=command)
    refs = tuple(dict.fromkeys(_ref(part, command=command) for part in parts))
    if "0" in refs and len(refs) > 1:
        raise CommandError("使用默认预设（0）时不能同时选择其他预设。", command=command)
    return () if refs == ("0",) else refs


def _format(value: str, *, command: str) -> str:
    fmt = value.casefold()
    fmt = _FORMAT_ALIASES.get(fmt, fmt)
    if fmt in FORMATS or (command == "get" and fmt == "img"):
        return fmt
    raise CommandError("不支持这个导出格式。", command=command, code="invalid_format")


def _export(command: str, args: list[_Word], options: dict[str, str], data: dict) -> None:
    values = [arg.value for arg in args]
    fmt = _format(options["format"], command=command) if "format" in options else None
    layout = _ref(options.get("tpl"), command=command)
    if command == "export":
        if not values:
            raise CommandError("请提供作品编号，可先用 /mio works 查看。", command=command)
        data["work"] = _ref(values.pop(0), command=command)
        if values and fmt is None:
            fmt = _format(values.pop(0), command=command)
    else:
        # Preserve /mio get img, /mio get #12 img and /mio get html #12.
        positional = []
        for value in values:
            low = value.casefold()
            if low in FORMATS or low == "img" or low in _FORMAT_ALIASES:
                if fmt is not None:
                    raise CommandError("每次取件只能选择一种格式。", command=command)
                fmt = _format(value, command=command)
            else:
                positional.append(value)
        values = positional
        if values:
            ref = _ref(values[0], command=command)
            if ref and (_NUMBER.fullmatch(ref) or _EXPORT_TASK.fullmatch(ref)):
                data["task"] = _ref(values.pop(0), command=command)
        if values and not _looks_like_layout(values[0]) and (fmt is None or not data.get("task")):
            raise CommandError("不支持这个导出格式。", command=command, code="invalid_format")
    if values:
        if layout is not None or len(values) > 1:
            raise CommandError("请只选择一个导出模板。", command=command)
        layout = _ref(values[0], command=command)
    data.update(format=fmt, layout=layout)


def _looks_like_layout(value: str) -> bool:
    return bool(
        _NUMBER.fullmatch(value.translate(_INDEX_TRANSLATION).removeprefix("#"))
        or "-" in value
        or not value.isascii()
        or " " in value
        or value.casefold() in {"monograph", "nocturne", "folio"}
    )


def parse_command(command: str, text: str | None = None) -> CommandArgs:
    """Parse a subcommand name and the raw GreedyStr captured by AstrBot."""
    command = command.strip().casefold()
    command = _COMMAND_ALIASES.get(command, command)
    if command not in USAGE:
        raise CommandError("未知 Mio 指令。用 /mio help 查看帮助。", command=command)
    text = (text or "").strip()
    if len(text) > 16000 or "\0" in text:
        raise CommandError("输入过长或含不可用字符，请缩短后重试。", command=command)
    args, options = _split_options(command, text)
    instance_id = options.get("instance")
    if instance_id is not None and not _INSTANCE.fullmatch(instance_id):
        raise CommandError("实例 ID 只能包含小写字母、数字、下划线和连字符。", command=command)
    data = {"command": command, "instance_id": instance_id}

    def ref(value: str | None) -> str | None:
        return _ref(value, command=command)

    def count(minimum: int, maximum: int) -> None:
        if not minimum <= len(args) <= maximum:
            raise CommandError("参数数量不正确。", command=command)

    if command in {"use", "preview"}:
        if len(args) == 1 and not args[0].quoted:
            # Legacy chat syntax accepts full-width commas between positional refs.
            legacy = re.split(r"[，、]", args[0].value.translate(_INDEX_TRANSLATION))
            if len(legacy) > 1 and all(value.removeprefix("#").isdigit() for value in legacy):
                args = _words(" ".join(legacy))
        count(1 if command == "use" else 3, 4 if command == "use" else 3)
        values = [word.value for word in args]
        if len(values) == 1:
            data["storyboard"] = ref(values[0])
        else:
            data.update(workflow=ref(values[0]), storyboard=ref(values[1]))
            if len(values) >= 3:
                data["presets"] = _presets(values[2], command=command)
            if len(values) == 4:
                data["channel"] = ref(values[3])
    elif command == "draw":
        idea = _free_text(text, args)
        if not 1 <= len(idea) <= 4000:
            raise CommandError("请输入 1 到 4000 字的创作灵感。", command=command)
        data.update(
            idea=idea,
            layout=ref(options.get("tpl")),
            style=ref(options.get("style")),
            series=ref(options.get("series")),
            candidates=_index(
                options.get("candidates", "1"), command=command, label="候选数", limit=16
            ),
        )
    elif command == "trial":
        workflow = options.get("workflow")
        if workflow is None and len(args) > 1 and not args[-1].quoted:
            last = args[-1].value.translate(_INDEX_TRANSLATION).removeprefix("#")
            if _NUMBER.fullmatch(last):
                workflow = args.pop().value
        prompt = _free_text(text, args)
        if not 1 <= len(prompt) <= 8000:
            raise CommandError("请输入 1 到 8000 字的试画提示词。", command=command)
        data.update(prompt=prompt, workflow=ref(workflow))
    elif command == "works":
        keyword = _free_text(text, args)
        if len(keyword) > 256:
            raise CommandError("搜索词最多 256 字。", command=command)
        data["keyword"] = keyword
    elif command in {"get", "export"}:
        _export(command, args, options, data)
    elif command in {"redraw", "pick"}:
        count(2 if command == "redraw" else 3, 8000 if command == "redraw" else 3)
        data["task"] = ref(args[0].value)
        data["panel"] = _index(args[1].value, command=command, label="分幕编号")
        if command == "pick":
            data["candidate"] = _index(args[2].value, command=command, label="候选编号")
        else:
            prompt = _free_text(text, args[2:])
            if len(prompt) > 8000:
                raise CommandError("重画提示词最多 8000 字。", command=command)
            data["prompt"] = prompt
    elif command in {"pause", "resume", "cancel", "rm", "qa", "status"}:
        count(0 if command == "status" else 1, 1)
        if args:
            data["task"] = ref(args[0].value)
    elif command == "mode":
        count(1, 1)
        mode = args[0].value.casefold()
        if mode not in NOTIFY_MODES:
            raise CommandError("这种交付方式不可用。", command=command)
        data["mode"] = mode
    elif command == "ls":
        count(0, 1)
        if args:
            name = args[0].value.casefold()
            data["target"] = next((k for k, vals in _CATALOG_ALIASES.items() if name in vals), None)
            if data["target"] is None:
                raise CommandError("没有这个资源分类。", command=command)
    else:
        count(0, 0)
    return CommandArgs(**data)


def parse_draw(text: str) -> CommandArgs:
    return parse_command("draw", text)


def parse_trial(text: str) -> CommandArgs:
    return parse_command("trial", text)


def parse_use(text: str) -> CommandArgs:
    return parse_command("use", text)


def parse_get(text: str) -> CommandArgs:
    return parse_command("get", text)


def parse_redraw(text: str) -> CommandArgs:
    return parse_command("redraw", text)


def parse_pick(text: str) -> CommandArgs:
    return parse_command("pick", text)
