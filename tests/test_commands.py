import pytest

from core.commands import (
    CommandError,
    parse_command,
    parse_draw,
    parse_get,
    parse_pick,
    parse_redraw,
    parse_trial,
    parse_use,
)


def test_draw_parses_selectors_and_preserves_natural_language():
    draw = parse_draw(
        "一只小猫\n在旧城旅行。 Don't forget the moon. "
        '--tpl="Nocturne Book" --style “纸上油画” --candidates ２ --series 4 --instance backup'
    )
    assert draw.idea == "一只小猫\n在旧城旅行。 Don't forget the moon."
    assert (draw.layout, draw.style, draw.candidates, draw.series) == (
        "Nocturne Book",
        "纸上油画",
        2,
        "4",
    )
    assert draw.instance_id == "backup"


def test_draw_allows_literal_flags_in_the_prompt():
    draw = parse_command("画", '--tpl "Paper Lab" -- C:\\work  --paper --max')
    assert draw.command == "draw"
    assert draw.layout == "Paper Lab"
    assert draw.idea == "C:\\work  --paper --max"


def test_trial_has_position_for_workflow_or_explicit_option():
    trial = parse_trial("午后的铁轨 ＃２")
    assert (trial.workflow, trial.prompt) == ("2", "午后的铁轨")
    explicit = parse_trial('--workflow="Pencil lab" 巴黎 2026')
    assert (explicit.workflow, explicit.prompt) == ("Pencil lab", "巴黎 2026")
    quoted = parse_command("试画", "“巴黎 2026”")
    assert quoted.workflow is None
    assert quoted.prompt == "巴黎 2026"


def test_use_preserves_legacy_and_named_refs():
    only_story = parse_use("３")
    assert only_story.storyboard == "3" and only_story.workflow is None
    shorthand = parse_use("1，2、3")
    assert (shorthand.workflow, shorthand.storyboard, shorthand.presets) == ("1", "2", ("3",))
    selected = parse_use('0 "Moon Village" 5，6，5 1')
    assert (selected.workflow, selected.storyboard, selected.presets, selected.channel) == (
        "0",
        "Moon Village",
        ("5", "6"),
        "1",
    )
    assert parse_command("preview", "1 2 0").presets == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", (None, None, None)),
        ("图片", (None, "img", None)),
        ("#12 HTML 3", ("12", "html", "3")),
        ("PDF ＃９ --tpl Nocturne", ("9", "pdf", "Nocturne")),
        ("--format strip --tpl 1", (None, "strip", "1")),
        ("8 2", ("8", None, "2")),
        ("assembly-abc html", ("assembly-abc", "html", None)),
    ],
)
def test_get_compatibility(text, expected):
    parsed = parse_get(text)
    assert (parsed.task, parsed.format, parsed.layout) == expected


def test_export_and_control_ref_arguments():
    export = parse_command("export", '"First Print" zip 2 --instance=backup')
    assert (export.work, export.format, export.layout, export.instance_id) == (
        "First Print",
        "zip",
        "2",
        "backup",
    )
    redraw = parse_redraw("#22 ＃２ 一辆绿车  高对比")
    assert (redraw.task, redraw.panel, redraw.prompt) == ("22", 2, "一辆绿车  高对比")
    pick = parse_pick("22 1 ２")
    assert (pick.task, pick.panel, pick.candidate) == ("22", 1, 2)
    assert parse_command("pause", "#22").task == "22"
    assert parse_command("qa", "22").task == "22"
    assert parse_command("status").task is None


def test_read_and_session_commands():
    assert parse_command("ls", "工作流 --instance backup").target == "workflows"
    assert parse_command("ls", "kit").target == "kits"
    assert parse_command("作品", "河边\n水彩").keyword == "河边\n水彩"
    assert parse_command("向导", "--instance=backup").instance_id == "backup"
    assert parse_command("ping", "--instance=primary").instance_id == "primary"
    assert parse_command("mode", "QA_then_SEND").mode == "qa_then_send"
    assert parse_command("quota").command == "quota"


@pytest.mark.parametrize(
    ("command", "text", "code"),
    [
        ("draw", "--tpl Folio", "invalid_arguments"),
        ("draw", "idea --style", "invalid_arguments"),
        ("draw", "idea --tpl=", "invalid_arguments"),
        ("draw", "idea --candidates 9999999999999", "invalid_arguments"),
        ("draw", "idea --candidates 0", "invalid_arguments"),
        ("draw", "idea --candidates 17", "invalid_arguments"),
        ("draw", 'idea --tpl "Unclosed Title', "invalid_arguments"),
        ("draw", "idea --tpl A --tpl=B", "duplicate_option"),
        ("draw", "idea --invalid unknown-secret", "unknown_option"),
        ("trial", "--workflow test", "invalid_arguments"),
        ("trial", "prompt --instance=../staging", "invalid_arguments"),
        ("use", "1 2 0,3", "invalid_arguments"),
        ("use", "1 2 3,", "invalid_arguments"),
        ("preview", "1 2", "invalid_arguments"),
        ("get", "1 docx", "invalid_format"),
        ("get", "docx", "invalid_format"),
        ("get", "1 html pdf", "invalid_arguments"),
        ("export", "1 img", "invalid_format"),
        ("export", "", "invalid_arguments"),
        ("redraw", "1 -2 prompt", "invalid_arguments"),
        ("pick", "1 2 0", "invalid_arguments"),
        ("pick", "1 2 3 4", "invalid_arguments"),
        ("pause", "1 --instance backup", "unknown_option"),
        ("mode", "random", "invalid_arguments"),
        ("quota", "extra", "invalid_arguments"),
        ("ls", "unknown", "invalid_arguments"),
    ],
)
def test_argument_errors_are_friendly_and_do_not_echo_untrusted_input(command, text, code):
    with pytest.raises(CommandError) as exc:
        parse_command(command, text)
    assert exc.value.code == code
    assert f"/mio {command}" in exc.value.message
    assert "unknown-secret" not in exc.value.message
