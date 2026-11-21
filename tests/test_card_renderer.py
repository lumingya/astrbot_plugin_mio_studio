from pathlib import Path

import pytest

from core import card_renderer
from core.card_renderer import CardRenderer


@pytest.fixture
def pillow_font(monkeypatch):
    """Portable test font; production still verifies a real Chinese system font."""
    pillow = pytest.importorskip("PIL.Image")
    from PIL import ImageFont

    original = ImageFont.truetype

    def truetype(font, size=10, *args, **kwargs):
        if font == "__fixture_font__":
            return ImageFont.load_default(size=size)
        return original(font, size, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", truetype)
    monkeypatch.setattr(card_renderer, "_find_font", lambda *_: "__fixture_font__")
    return pillow


def test_cards_are_valid_pngs_in_both_themes(tmp_path, pillow_font):
    cards = CardRenderer(tmp_path)
    path = cards.help(theme="paper")
    with pillow_font.open(path) as image:
        assert image.format == "PNG"
        assert image.width == 880 and image.height > 650
        image.verify()
    with pillow_font.open(path) as image:
        assert image.getpixel((0, 0)) == (243, 237, 225)
    path = cards.tasks(
        [{"num": 12, "title": "Atlas", "statusKey": "running", "done": 1, "total": 2}], theme="ink"
    )
    with pillow_font.open(path) as image:
        assert image.getpixel((0, 0)) == (37, 37, 33)
        # The first half of the track is filled; the second half remains muted.
        assert image.getpixel((150, 306)) == (228, 163, 135)
        assert image.getpixel((500, 306)) == (77, 73, 61)


def test_template_swatch_reflects_mio_options(tmp_path, pillow_font):
    cards = CardRenderer(tmp_path)
    path = cards.catalog(
        {
            "layouts": [
                {
                    "id": "folio",
                    "title": "Folio",
                    "options": {"paper": "#c0bdac", "accent": "#234567", "text": "invalid"},
                    "description": "长句需要按字体宽度进行换行。" * 10,
                }
            ]
        },
        ("layouts",),
    )
    with pillow_font.open(path) as image:
        assert image.getpixel((765, 278)) == (192, 189, 172)
        assert image.getpixel((785, 278)) == (35, 69, 103)


def test_gallery_uses_actual_cover_and_accepts_missing_images(tmp_path, pillow_font):
    path = tmp_path / "cover.png"
    pillow_font.new("RGB", (30, 86), (70, 80, 30)).save(path)
    cards = CardRenderer(tmp_path / "cards")
    gallery = cards.gallery(
        [
            {"title": "一部漫画", "cover_path": path},
            {"title": "第二部", "cover_path": Path("/missing-preview.png")},
        ]
    )
    with pillow_font.open(gallery) as image:
        assert image.getpixel((60, 255)) == (70, 80, 30)
        assert image.getpixel((325, 255)) == (142, 119, 99)


def test_text_mode_and_missing_font_fall_back_without_creating_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(card_renderer, "_font_candidates", lambda _: iter(()))
    cards = CardRenderer(tmp_path)
    assert cards.help(mode="text") is None
    assert cards.help(mode="image") is None
    assert not list(tmp_path.iterdir())


def test_cards_leave_a_long_numbered_catalog_to_plain_text(tmp_path):
    cards = CardRenderer(tmp_path)
    catalog = {"workflows": [{"id": f"wf{i}"} for i in range(11)]}
    assert cards.catalog(catalog, ("workflows",)) is None
    assert not list(tmp_path.iterdir())
