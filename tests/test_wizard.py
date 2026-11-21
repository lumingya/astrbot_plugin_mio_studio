import asyncio

import pytest

from core.wizard import WizardManager

ALICE = "qq:alice"
BOB = "qq:bob"
ROOM = "qq:GroupMessage:10"
OTHER = "qq:GroupMessage:20"


@pytest.fixture
def catalog():
    return {
        "storyboards": [
            {"id": "story-a", "title": "水边的灯"},
            {"id": "story-b", "title": "午夜书店"},
            {"id": "story-c", "title": "城里的雪"},
        ],
        "workflows": [{"id": "workflow-one", "title": "手工印刷"}],
        "presets": [
            {"id": "preset-ink", "title": "墨色"},
            {"id": "preset-night", "title": "午夜街角"},
        ],
    }


def test_wizard_is_three_steps_and_uses_stable_resources_after_catalog_change(catalog):
    wizard = WizardManager()
    assert "1/3" in wizard.start(ALICE, ROOM, catalog, instance_id="backup", now=0).text
    assert "2/3" in wizard.consume(ALICE, ROOM, "午夜书店", now=10).text
    catalog["workflows"][0]["id"] = "changed-by-refresh"
    assert "3/3" in wizard.consume(ALICE, ROOM, "＃１", now=11).text
    reply = wizard.consume(ALICE, ROOM, "1、2,1", now=12)
    assert reply.completed and not reply.cancelled
    selection = reply.selection
    assert selection.storyboard == "story-b"
    assert selection.workflow == "workflow-one"
    assert selection.presets == ("preset-ink", "preset-night")
    assert selection.instance_id == "backup"
    assert wizard.consume(ALICE, ROOM, "1", now=13) is None


def test_wizard_cancel_and_isolation(catalog):
    wizard = WizardManager()
    wizard.start(ALICE, ROOM, catalog, now=10)
    wizard.start(BOB, ROOM, catalog, now=10)
    wizard.start(ALICE, OTHER, catalog, now=10)
    assert wizard.consume(ALICE, ROOM, "聊天时顺便说一句", now=11) is None
    assert wizard.consume(ALICE, ROOM, "/mio status 1", now=11) is None
    cancelled = wizard.consume(ALICE, ROOM, "取消", now=11)
    assert cancelled.cancelled and not cancelled.completed
    assert not wizard.active(ALICE, ROOM, now=12)
    assert wizard.active(BOB, ROOM, now=12)
    assert wizard.active(ALICE, OTHER, now=12)
    assert wizard.cancel(BOB, ROOM)


def test_paging_search_and_defaults(catalog):
    wizard = WizardManager(page_size=1)
    initial = wizard.start(ALICE, ROOM, catalog, now=0)
    assert "1. 水边的灯" in initial.text and "午夜书店" not in initial.text
    assert "2. 午夜书店" in wizard.consume(ALICE, ROOM, "更多", now=1).text
    assert "1. 水边的灯" in wizard.consume(ALICE, ROOM, "上一页", now=2).text
    assert "没有匹配" in wizard.consume(ALICE, ROOM, "搜索 不存在", now=3).text
    assert "3. 城里的雪" in wizard.consume(ALICE, ROOM, "搜索 雪", now=4).text
    assert "没有这个编号" in wizard.consume(ALICE, ROOM, "999", now=5).text
    assert "2/3" in wizard.consume(ALICE, ROOM, "3", now=6).text
    assert "3/3" in wizard.consume(ALICE, ROOM, "默认", now=7).text
    assert "单独回复 0" in wizard.consume(ALICE, ROOM, "0,1", now=8).text
    selection = wizard.consume(ALICE, ROOM, "0", now=9).selection
    assert (selection.storyboard, selection.workflow, selection.presets) == ("story-c", None, ())


def test_expiration_and_capacity(catalog):
    wizard = WizardManager(ttl=10, max_sessions=1)
    wizard.start(ALICE, ROOM, catalog, now=0)
    assert "稍后再试" in wizard.start(BOB, ROOM, catalog, now=1).text
    assert "2/3" in wizard.consume(ALICE, ROOM, "1", now=9).text
    assert wizard.prune(now=10) == 0
    assert wizard.prune(now=19) == 1
    assert wizard.consume(ALICE, ROOM, "1", now=19) is None
    assert "1/3" in wizard.start(BOB, ROOM, catalog, now=20).text


def test_empty_storyboards_disallow_a_dead_end():
    wizard = WizardManager()
    assert "还没有分镜" in wizard.start(ALICE, ROOM, {}, now=0).text
    assert not wizard.active(ALICE, ROOM, now=0)


async def test_inactive_wizard_cleans_itself_from_event_loop(catalog):
    wizard = WizardManager(ttl=0.01)
    wizard.start(ALICE, ROOM, catalog)
    await asyncio.sleep(0.05)
    assert wizard.prune() == 0  # Timer already removed the draft.
    assert not wizard.active(ALICE, ROOM)
    wizard.close()
