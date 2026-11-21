"""Regenerate the supported operation allowlist from a local upstream OpenAPI snapshot."""

import argparse
import json
from pathlib import Path

DISCOVERY = {
    "/meta",
    "/whoami",
    "/operations",
    "/settings",
    "/settings/models",
    "/image-adapters",
    "/image-channels",
    "/comfy-adapters",
    "/assets/{asset_id}",
}


def supported(path: str) -> bool:
    return path in DISCOVERY or path.split("/")[1] in {
        "album-templates",
        "workflows",
        "workshop",
        "series",
        "episodes",
        "export",
        "creation-kits",
        "webhooks",
        "jobs",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("revision")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    operations = {}
    for path, methods in spec["paths"].items():
        path = path.removeprefix("/api/v2")
        if supported(path):
            for method, op in methods.items():
                if method in {"get", "post", "put", "patch", "delete"}:
                    name = op["operationId"]
                    if name in operations:
                        raise ValueError(f"Duplicate operation ID: {name}")
                    operations[name] = (method.upper(), path)
    result = {
        "revision": args.revision,
        "operations": dict(sorted(operations.items())),
    }
    destination = Path(__file__).resolve().parents[1] / "core" / "v2_operations.json"
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
