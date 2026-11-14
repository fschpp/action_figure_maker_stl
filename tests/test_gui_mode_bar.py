from afm.gui import mode_bar as MB
from afm.gui.controller import Mode


def test_every_tool_mode_has_a_bar_and_view_has_none():
    assert MB.help_for(Mode.VIEW) is None
    missing = [m for m in Mode if m is not Mode.VIEW and MB.help_for(m) is None]
    assert missing == []


def test_hints_are_one_short_line_and_actions_exist():
    for mode, (hint, acts) in MB.MODE_HELP.items():
        assert 0 < len(hint) <= 200 and "\n" not in hint, mode
        assert acts and set(acts) <= set(MB.ACTION_LABELS), mode
        assert "Esc" in hint, mode


def test_polygon_bar_offers_close_and_undo():
    _, acts = MB.help_for(Mode.POLYGON)
    assert {"close", "undo_point", "cancel"} <= set(acts)
