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


@pytest.mark.parametrize("text", [
    "---\nversion: 1\nthemes: a\n---\n# Principles\n\n# Rules\n1. Say only what the data shows.\n",     # none written
    "---\nversion: 1\nthemes: a\n---\n# Rules\n1. Say only what the data shows.\n",                    # no section
    "---\nversion: 0\nthemes: a\n---\n# Principles\n- Trade your teammate.\n",                         # still version 0
    "---\nversion: 00\nthemes: a\n---\n# Principles\n- Trade your teammate.\n",                        # zero, spelled twice
])
def test_a_playbook_without_the_owners_principles_is_refused(tmp_path, text):
    with pytest.raises(playbook.PlaybookError, match="no principles yet"):
        playbook.load(write(tmp_path, text))


def test_the_committed_playbook_names_no_player_and_loads_once_it_has_principles(tmp_path):
    raw = playbook.PATH.read_text(encoding="utf-8")
    assert not re.search(r"[^\s#]#\S", raw.split("---", 2)[2])        # no Name#TAG; headings are fine
    if re.search(r"^version: 0$", raw, re.MULTILINE):                 # as shipped: the coach refuses to run
        with pytest.raises(playbook.PlaybookError, match="no principles yet"):
            playbook.load()
        raw = raw.replace("version: 0", "version: 1", 1).replace("# Principles\n", "# Principles\n\n- Trade.\n", 1)
    book = playbook.load(write(tmp_path, raw))        # the rest of the file (themes, rules) is sound
    assert len(book.themes) >= 4 and "# Rules" in book.text


def test_the_name_check_allows_headings_and_catches_a_riot_id():
    pattern = r"[^\s#]#\S"
    assert not re.search(pattern, "# Principles\n\n## Rules\n\n### Detail\n")
    assert re.search(pattern, "As Someone#NA1 showed")
