from app.templates import templates


def point(**over):
    base = {"id": 7, "theme": "trading", "title": "Died alone on B", "body": "You were 30 m from help.",
            "disputed": False, "feedback": [],
            "instances": [{"round": 4, "t": 41.5, "t1": None, "caption": "first death"},
                          {"round": 9, "t": 12.0, "t1": 20.0, "caption": "again"}]}
    return {**base, **over}


def coach(**over):
    report = {"id": 3, "version": 2, "versions": [1, 2], "playbook_version": "5", "model": "m", "created": "2026-10-12",
              "player_id": 11, "player_name": "Owner#1", "data_gaps": {"6": ["control_failed"]}, "dropped_points": 1,
              "good": [point(id=1, title="Good trade")], "improve": [point()]}
    base = {"match_uuid": "abc", "team_coming_soon": True,
            "players": [{"player_id": 11, "name": "Owner#1", "selected": True},
                        {"player_id": 12, "name": "Friend#2", "selected": False}], "report": report}
    return {**base, **over}


def html(data):
    """The strip above the replay, then (when there is a report) its points as the page's two tab panels."""
    render = lambda name: templates.env.get_template(name).render(coach=data)
    return render("replays/_coach.html") + (render("replays/_coach_points.html") if data["report"] else "")


def player(data):
    """The replay viewer as the coaching page (`data`) or the plain replay page (None) renders it."""
    return templates.env.get_template("replays/_player.html").render(
        replay={"map_name": "Ascent", "round_numbers": [1, 2]}, linked=True, control=None, coach=data)


def test_every_instance_is_a_link_carrying_its_round_and_time():
    out = html(coach())
    assert out.count('data-coach-round="4" data-coach-t="41.5"') == 2      # the good point and the improve point
    assert 'data-coach-round="9" data-coach-t="12.0"' in out
    assert "R4 0:41" in out and "R9 0:12" in out and "first death" in out


def test_the_report_states_its_version_playbook_and_gaps():
    out = html(coach())
    assert "playbook 5" in out and "Round 6" in out and "control_failed" in out
    assert "1 point was dropped" in out
    assert 'href="/replays/abc/coach?player=11&amp;version=1"' in out


def test_a_side_with_fewer_than_three_points_says_so():
    out = html(coach())                                  # one good point, one to improve, none dropped on purpose
    assert out.count("Only 1 in this version") == 2
    full = coach()
    full["report"]["good"] = [point(id=i) for i in (1, 2, 3)]
    full["report"]["improve"] = []
    out = html(full)
    assert "Only" not in out and out.count("None in this version") == 1


def test_the_team_toggle_is_disabled_and_says_coming_soon():
    out = html(coach())
    assert "Coming soon" in out and "data-coach-team disabled" in out


def test_a_disputed_point_stays_and_is_marked():
    data = coach()
    data["report"]["improve"] = [point(disputed=True, feedback=[{"author": "Friend#2", "verdict": "wrong",
                                                                 "note": "not so", "handled": False}])]
    out = html(data)
    assert "Disputed" in out and "Died alone on B" in out and "not so" in out


def test_report_text_is_escaped():
    data = coach()
    data["report"]["improve"] = [point(title="<script>alert(1)</script>", body="a & b <b>x</b>")]
    out = html(data)
    assert "<script>alert(1)</script>" not in out and "&lt;script&gt;" in out and "a &amp; b" in out


def test_a_point_without_instances_and_an_instance_without_a_time_still_render():
    data = coach()
    data["report"]["good"] = [point(instances=[])]
    data["report"]["improve"] = [point(instances=[{"round": 3, "t": None, "t1": None, "caption": ""}])]
    out = html(data)
    assert 'data-coach-round="3" data-coach-t=""' in out and "R3" in out


def test_no_report_shows_the_empty_state():
    out = html(coach(report=None, players=[]))
    assert "No coaching report for this match yet" in out


def test_the_points_are_two_tabs_beside_the_map_with_did_well_open():
    out = player(coach())
    side = out.split('<aside class="replay-side">', 1)[1].split("</aside>", 1)[0]
    tabs = side.split('role="tablist"', 1)[1].split("</div>", 1)[0]
    assert tabs.index("Did well") < tabs.index("To improve") < tabs.index("Kill feed")
    assert 'class="replay-tab is-active" data-replay-tab="coach-good" aria-selected="true"' in tabs
    assert tabs.count("is-active") == 1 and tabs.count('aria-selected="true"') == 1
    good = side.split('data-replay-panel="coach-good" role="tabpanel">', 1)[1].split('data-replay-panel="coach-improve"', 1)[0]
    improve = side.split('data-replay-panel="coach-improve" role="tabpanel" hidden>', 1)[1].split('data-replay-panel="feed"', 1)[0]
    assert "Good trade" in good and "Died alone on B" not in good
    assert "Died alone on B" in improve and "Good trade" not in improve
    assert 'data-replay-panel="feed" role="tabpanel" hidden>' in side          # one panel shows at a time
    assert "has-coach" in out.split("data-replay", 1)[0]


def test_the_replay_page_and_a_match_without_a_report_keep_the_kill_feed_first():
    for out in (player(None), player(coach(report=None, players=[]))):
        assert "coach-good" not in out and "Did well" not in out and "has-coach" not in out
        assert 'class="replay-tab is-active" data-replay-tab="feed" aria-selected="true"' in out
        assert 'data-replay-panel="feed" role="tabpanel">' in out


def test_a_match_with_several_coached_players_offers_one_prompt_for_all_of_them():
    whole_match = 'href="/replays/abc/coach/prompt" '
    assert whole_match in html(coach()) and 'href="/replays/abc/coach/prompt?report=3"' in html(coach())
    alone = coach(players=[{"player_id": 11, "name": "Owner#1", "selected": True}])
    assert whole_match not in html(alone) and 'href="/replays/abc/coach/prompt?report=3"' in html(alone)
