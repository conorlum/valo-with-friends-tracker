from app.coach import prompt
from app.coach.playbook import Playbook

BOOK = Playbook("4", ("trading", "rotation"), "# Principles\n- Trade your teammate.")
BRIEF = {"v": 1, "subject": {"name": "Owner#1"}, "match": {"map": "Ascent"}, "rounds": [], "ranked": [],
         "areas": ["A Main"], "data_gaps": {}, "players": []}


def test_the_run_prompt_carries_the_playbook_voice_brief_and_the_command():
    text = prompt.run_prompt(BOOK, ["Stop peeking that dry."], BRIEF)
    assert "Trade your teammate." in text and "Stop peeking that dry." in text and '"Owner#1"' in text
    assert "./q where" in text and "./q knew" in text and "trading, rotation" in text
    assert '"points"' in text and '"evidence"' in text


def test_the_run_prompt_without_voice_samples_says_so():
    assert "No voice samples" in prompt.run_prompt(BOOK, [], BRIEF)


def test_the_repair_prompt_lists_each_failed_point_with_its_problems():
    text = prompt.repair_prompt([(2, {"title": "Died alone"}, ["the number 35 is not in a cited answer"])])
    assert "Died alone" in text and "the number 35" in text and '"index": 2' in text
