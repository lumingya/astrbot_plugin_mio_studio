import json

from core.resources import Resources


class Upstream:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def call(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        return self.reply(operation, **kwargs)


async def test_catalog_uses_published_summaries_and_isolates_client_caches():
    def reply(operation, **kwargs):
        if operation == "list_workflows":
            return [{"id": "wf", "name": "Ink", "nodes": 32, "config": {"api_key": "private"}}]
        if operation == "list_storyboards":
            offset = kwargs["params"]["offset"]
            return {
                "items": [
                    {"id": f"story-{n}", "title": f"分镜 {n}", "panel_count": 4}
                    for n in range(offset, min(103, offset + 100))
                ],
                "total": 103,
            }
        if operation == "list_image_channels":
            return [
                {"id": "", "label": "默认图像服务", "kind": "chat_image", "api_key": "private"},
                {"id": "local", "label": "本地", "kind": "comfyui"},
            ]
        if operation == "list_album_templates":
            return [
                {"id": "folio", "layout": "folio", "options": {"accent": "#123abc", "text": "evil"}}
            ]
        return []

    resources = Resources()
    first = Upstream(reply)
    second = Upstream(lambda *_args, **_kwargs: [])
    result = await resources.catalog(first)
    assert len(result["storyboards"]) == 103
    assert result["storyboards"][101]["frameCount"] == 4
    assert result["workflows"][0]["nodeCount"] == 32
    assert [(row["id"], row["cloud"]) for row in result["channels"]] == [
        ("", True),
        ("local", False),
    ]
    assert result["layouts"][0]["options"] == {"accent": "#123abc"}
    assert "private" not in json.dumps(result)
    result["workflows"].clear()
    assert (await resources.catalog(first))["workflows"]
    assert not (await resources.catalog(second))["workflows"]
    assert len(first.calls) == 7
    await resources.catalog(first, force=True)
    assert len(first.calls) == 14


async def test_details_allow_only_user_facing_fields():
    def reply(operation, **_kwargs):
        if operation == "get_preset":
            return {
                "id": "preset-1",
                "title": "Forest guide",
                "entries": [
                    {"key": "character", "value": "Messenger"},
                    {"key": "api_key", "value": "private"},
                ],
                "bindings": {"secret": "private"},
            }
        return {
            "id": "board",
            "title": "Story",
            "base_prompt": "in this {scene}",
            "panels": [
                {
                    "id": "p1",
                    "order": 0,
                    "overrides": {
                        "raw_prompt": "{character} {{escaped}} mio_test_placeholder_value",
                        "values": {"token": "private"},
                    },
                }
            ],
        }

    resources = Resources()
    upstream = Upstream(reply)
    preset = await resources.detail(upstream, "presets", "preset-1")
    assert preset["category"] == "characters"
    assert [entry["key"] for entry in preset["entries"]] == ["character"]
    board = await resources.detail(upstream, "storyboards", "board")
    assert board["panels"][0]["variables"] == ["character", "scene"]
    assert "private" not in json.dumps(board)
    assert "mio_test_placeholder_value" not in json.dumps(board)


async def test_works_searches_episode_titles_and_keeps_export_identifiers():
    def reply(operation, **kwargs):
        if operation == "list_series":
            return [{"id": "series-1", "title": "星图"}, {"id": "series-2", "title": "短篇"}]
        series_id = kwargs["path_params"]["series_id"]
        return {
            "items": [
                {"id": "ep-2", "title": "星图续篇", "cover_asset_id": "cover"}
                if series_id == "series-2"
                else {"id": "ep-1", "title": "第一话"}
            ],
            "total": 1,
        }

    works = await Resources().works(Upstream(reply), query="星图", limit=2)
    assert [(row["series_id"], row["episode_id"], row["series_title"]) for row in works] == [
        ("series-1", "ep-1", "星图"),
        ("series-2", "ep-2", "短篇"),
    ]
    assert works[1]["cover_asset_id"] == "cover"
