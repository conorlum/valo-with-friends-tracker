import re

import pytest

from app.coach import playbook

GOOD = "---\nversion: 3\nthemes: trading, rotation , utility\n---\n# Principles\nTrade your teammate.\n"


def write(tmp_path, text):
    path = tmp_path / "playbook.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_playbook_has_a_version_themes_and_its_text(tmp_path):
    book = playbook.load(write(tmp_path, GOOD))
    assert book.version == "3" and book.themes == ("trading", "rotation", "utility")
    assert book.text.startswith("# Principles") and "version:" not in book.text


@pytest.mark.parametrize("text, message", [
    ("# no front block\n", "front block"),
    ("---\nthemes: a\n---\nx\n", "version"),
    ("---\nversion: 1\nthemes:  \n---\nx\n", "themes"),
    ("---\nversion: 1\nthemes: a\n---\n   \n", "empty"),
])
def test_a_malformed_playbook_is_refused(tmp_path, text, message):
    with pytest.raises(playbook.PlaybookError, match=message):
        playbook.load(write(tmp_path, text))


def test_the_committed_playbook_loads_and_names_no_player():
    book = playbook.load()
    assert book.version and len(book.themes) >= 4
    assert not re.search(r"[^\s#]#\S", book.text)        # no Name#TAG; "# " and "## " headings are fine


def test_the_name_check_allows_headings_and_catches_a_riot_id():
    pattern = r"[^\s#]#\S"
    assert not re.search(pattern, "# Principles\n\n## Rules\n\n### Detail\n")
    assert re.search(pattern, "As Someone#NA1 showed")
