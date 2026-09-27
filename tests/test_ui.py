"""Terminal helpers: text width, truncation, tables and path completion."""

from scripts import ui


def test_wide_characters_count_double():
    assert ui.display_width("abc") == 3
    assert ui.display_width("고양이") == 6
    assert ui.display_width("\x1b[31mred\x1b[0m") == 3   # colour codes take no space


def test_truncating_paths_keeps_the_file_name():
    shortened = ui.truncate("Urlaub/Strand/beach_q80.jpg", 12, keep_end=True)
    assert shortened.startswith("…") and shortened.endswith("q80.jpg")
    assert ui.display_width(shortened) <= 12


def test_tables_have_no_trailing_spaces(capsys, monkeypatch):
    monkeypatch.setattr(ui, "terminal_width", lambda default=80: 80)
    ui.print_table([ui.Column(""), ui.Column("File", flex=True)],
                   [[("▶", ""), ("kept.png", ui.StyleUI.BOLD)], [" ", "other.png"]])
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line == line.rstrip() for line in lines)


def test_path_completion_handles_brackets(tmp_path):
    folder = tmp_path / "Photos [2024]"
    folder.mkdir()
    prefix = str(tmp_path / "Photos [")
    assert ui.complete_path(prefix, 0) == str(folder) + "/"
    assert ui.complete_path(prefix, 1) is None


def test_path_completion_lists_all_matches_once(tmp_path):
    for name in ("a1", "a2", "b"):
        (tmp_path / name).mkdir()
    prefix = str(tmp_path / "a")
    found = []
    state = 0
    while (match := ui.complete_path(prefix, state)) is not None:
        found.append(match)
        state += 1
    assert found == [str(tmp_path / "a1") + "/", str(tmp_path / "a2") + "/"]
