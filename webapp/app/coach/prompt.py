"""The text the coach model is given: the run prompt, the repair prompt, and the no-report prompt."""

from __future__ import annotations

import json

COMMANDS = """\
./q where <round> <t>                      every player's position, area, facing, role, health, and whether alive
./q knew <round> <t> <A|B|player>          what that side knew by then: enemies seen (with a place), other signals (no place)
./q distance <round> <t> <player> <player> metres between two living players
./q control <round> <t0> <t1> "<area>"     who held a named area over a span (t0 = t1 for one moment)
./q timeline <round> [<t0> <t1>]           kills, damage, utility, plant and defuse in order
./q impact <round> <player>                the player's stored Impact row for the round"""

SHAPE = """\
{"points": [
  {"polarity": "good" | "improve",
   "theme": "<one theme from the list>",
   "title": "<a few words>",
   "body": "<two to four sentences in the owner's voice>",
   "instances": [{"round": 7, "t": 41.5, "t1": 48.0, "caption": "<one line>", "evidence": ["q3", "q9"]}]}
]}"""


def run_prompt(book, voice: list[str], brief: dict) -> str:
    samples = "\n".join(f"- {line}" for line in voice) if voice else "No voice samples yet: write plainly and directly."
    return f"""{book.text}

# Themes

A point's theme must be one of: {", ".join(book.themes)}

# The owner's voice

{samples}

# The query command

Run it from the current directory, one call per command, with no pipes or redirection. Times are seconds into
the round (41.5, not 0:41) and rounds are whole numbers. A player is a name, a name without its tag, or a slot
number. Every answer is JSON with an "id"; that id is what you cite as evidence. An answer with "error" is not
evidence. An answer with "available": false means the data is missing for that round.

{COMMANDS}

Each flag in the brief already has an "id" you may cite. Evidence for an instance must be about that instance's
round. Every answer is recomputed when your report is checked, so only real answers count. Use no other tool
and read no file.

# The brief

{json.dumps(brief, separators=(",", ":"))}

# What to return

When you have checked your points, reply with one JSON object and nothing else, in this shape. "t" is the moment
in the round; "t1" is optional and ends a span. Up to three "good" and up to three "improve".

{SHAPE}
"""


def repair_prompt(failed: list[tuple[int, dict, list[str]]]) -> str:
    blocks = []
    for index, point, problems in failed:
        blocks.append(json.dumps({"index": index, "title": point.get("title"), "problems": problems}, indent=1))
    return ("These points did not pass the evidence check. For each, either fix it using query answers (ask more "
            "if you need to) or leave it out. Reply with one JSON object {\"points\": [...]} holding only the "
            "fixed points, each with its original \"index\" added, and nothing else.\n\n" + "\n".join(blocks))


def json_only_prompt() -> str:
    return ("Your reply had no report. Reply now with one JSON object in the shape you were given and nothing else."
            f"\n\n{SHAPE}")
