"""Named operations pinned to Mio's published OpenAPI snapshot."""

import json
import string
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote

_manifest = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
REVISION: str = _manifest["revision"]
OPS = MappingProxyType({name: tuple(route) for name, route in _manifest["operations"].items()})


def operation_path(operation_id: str, path_params: Mapping[str, str]) -> str:
    template = OPS[operation_id][1]
    fields = {key for _, key, _, _ in string.Formatter().parse(template) if key is not None}
    if fields != path_params.keys():
        expected = ", ".join(sorted(fields)) or "(none)"
        raise ValueError(f"Expected path params for {operation_id}: {expected}")
    segments = {}
    for field, value in path_params.items():
        if (
            not isinstance(value, str)
            or value in {"", ".", ".."}
            or any(c in "/\\%" or ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError(f"Invalid path segment for {field}")
        segments[field] = quote(value, safe="")
    return template.format_map(segments)
