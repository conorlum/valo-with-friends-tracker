"""Offline probe contracts: private outputs, bounded fetching and match-cluster rates."""

import gzip
import io
import json
import re
import shutil
import subprocess
import sys
import threading
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import peek_probe as probe

from app.control import engine, geometry
from app.moments import peeks
from tests.moments.helpers import damage, kill
from tests.replays import control_toys as toys

UUIDS = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 14)]


class Response(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network access")
    monkeypatch.setattr(probe, "_urlopen", forbidden)


def roster(tmp_path, names=("Invented#A",)):
    path = tmp_path / "friends.json"
    path.write_text(json.dumps(list(names)), encoding="utf-8")
    return path


def context(index=0):
    return {
        "match": {"map": "Toy", "asset": "Toy", "match_id": f"match-{index}",
                  "side_to_team": {"A": "red", "B": "blue"}, "clock_offset": 0},
        "players": {
            "0": {"player_id": "friend-id", "name": "Invented#A", "agent": "Jett", "team": "red", "side": "A"},
            "1": {"player_id": "stranger-id", "name": "Stranger#1", "agent": "Sage", "team": "red", "side": "A"},
            "5": {"player_id": "enemy-id", "name": "Opponent#Z", "agent": "Omen", "team": "blue", "side": "B"},
        },
        "rounds": {"1": {"db_deaths": [{"slot": 5, "t_db": 12.5}]}},
    }


def html(ctx):
    return ('<!doctype html><script id="replay-data" type="application/json">'
            + json.dumps(ctx) + '</script>').encode()


def toy_blob():
    players = {
        0: ("A", [(0, 230, 280, 0)]),
        1: ("A", [(0, 230, 280, 0)]),
        5: ("B", [(0, 330, 120, 180), (11.992, 330, 120, 180), (12, 330, 280, 180)]),
    }
    blob = toys.blob(players, t_end=16, util=[damage(12.25, 0, 5), damage(12.25, 1, 5)], deaths={5: 12.5})
    for slot, (_, points) in players.items():
        blob["tracks"][str(slot)] = toys.track(points, 0, 16, hz=125)
    blob["hz"], blob["kills"] = 125, [kill(12.5, 0, 5)]
    return blob


def serve(monkeypatch, responses):
    calls = []
    def opener(request, **kwargs):
        assert request.get_method() == "GET"
        calls.append(request.full_url)
        data = responses[request.full_url.removeprefix(probe.SITE)]
        if isinstance(data, int):
            raise HTTPError(request.full_url, data, "unavailable", {}, io.BytesIO(b"unavailable"))
        return Response(data)
    monkeypatch.setattr(probe, "_urlopen", opener)
    return calls


def patch_geometry(monkeypatch):
    directories = []
    loads = []
    def load(name):
        loads.append(name)
        return toys.midwall_hall()
    def visibility(geo, directory):
        directories.append(Path(directory))
        return geo
    monkeypatch.setattr(geometry, "load_geometry", load)
    monkeypatch.setattr(geometry, "visibility", visibility)
    return directories, loads


def read_rows(out):
    return [json.loads(line) for line in (out / "openings.jsonl").read_text().splitlines()]


def test_fetch_get_cache_and_non_200(monkeypatch, tmp_path):
    calls = serve(monkeypatch, {"/ok": b"cached", "/absent": 404})
    for _ in range(2):
        assert probe.fetch(probe.SITE + "/ok", tmp_path) == b"cached"
        with pytest.raises(probe.FetchError) as exc:
            probe.fetch(probe.SITE + "/absent", tmp_path)
        assert exc.value.status == 404
    assert calls == [probe.SITE + "/ok", probe.SITE + "/absent"]
    monkeypatch.setattr(probe, "_urlopen", lambda *a, **k: Response(b"denied", 403))
    with pytest.raises(probe.FetchError) as exc:
        probe.fetch(probe.SITE + "/forbidden", tmp_path)
    assert exc.value.status == 403


def test_fetch_requests_stored_gzip_with_get(monkeypatch, tmp_path):
    def opener(request, **kwargs):
        assert request.get_method() == "GET"
        assert request.get_header("Accept-encoding") == "gzip"
        return Response(b"stored gzip")
    monkeypatch.setattr(probe, "_urlopen", opener)
    assert probe.fetch(probe.SITE + "/round.json", tmp_path) == b"stored gzip"


@pytest.mark.parametrize("status", [408, 429, 500, 503, 599])
@pytest.mark.parametrize("http_error", [False, True])
def test_retryable_fetch_is_not_cached(monkeypatch, tmp_path, status, http_error):
    calls = []
    def opener(request, **kwargs):
        calls.append(request.full_url)
        if len(calls) == 1:
            if http_error:
                raise HTTPError(request.full_url, status, "temporary", {}, io.BytesIO(b"temporary"))
            return Response(b"temporary", status)
        return Response(b"healthy")
    monkeypatch.setattr(probe, "_urlopen", opener)
    url = probe.SITE + "/recover"
    with pytest.raises(probe.FetchError) as exc:
        probe.fetch(url, tmp_path)
    assert exc.value.status == status
    assert list(tmp_path.rglob("*")) == []
    assert probe.fetch(url, tmp_path) == b"healthy"
    assert calls == [url, url]
    assert exc.value.retryable is True
    assert probe.fetch(url, tmp_path) == b"healthy"
    assert len(calls) == 2


@pytest.mark.parametrize("status,retryable", [(200, False), (400, False), (403, False), (404, False),
                                            (408, True), (429, True), (500, True), (503, True), (599, True)])
def test_fetch_error_retryable_rule(status, retryable):
    assert probe.FetchError(status).retryable is retryable


def test_old_retryable_cache_does_not_prevent_recovery(monkeypatch, tmp_path):
    url = probe.SITE + "/recover"
    path = probe._cache_path(url, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old failure")
    path.with_suffix(".status").write_text("503")
    calls = serve(monkeypatch, {"/recover": b"healthy"})
    assert probe.fetch(url, tmp_path) == b"healthy"
    assert calls == [url]


def test_moments_retryable_failure_recovers_on_rerun(monkeypatch, tmp_path):
    patch_geometry(monkeypatch)
    uuid = UUIDS[0]
    responses = {f"/replays/{uuid}": html(context()), f"/replays/{uuid}/moments.json": 503,
                 f"/replays/{uuid}/1.json": json.dumps(toy_blob()).encode(), "/static/img/maps/Toy.png": b"png"}
    calls = serve(monkeypatch, responses)
    out = tmp_path / "out"
    argv = ["run", "--uuids", uuid, "--jobs", "1", "--friends", str(roster(tmp_path)), "--out", str(out)]
    assert probe.main(argv) == 1
    coverage = json.loads((out / "coverage.json").read_text())
    assert coverage["replays"]["failed"] == [{"uuid": uuid, "error": "HTTP 503"}]
    assert coverage["rounds"]["failed"] == [{"uuid": uuid, "round": 1, "error": "HTTP 503"}]
    assert coverage["replays"]["skipped"]["no_moments"] == []
    assert coverage["rounds"]["skipped"]["no_moments"] == []
    assert not read_rows(out)
    responses[f"/replays/{uuid}/moments.json"] = b"{}"
    assert probe.main(argv) == 0
    coverage = json.loads((out / "coverage.json").read_text())
    assert coverage["replays"]["completed"] == [uuid]
    assert coverage["rounds"]["completed"] == [{"uuid": uuid, "round": 1}]
    assert not coverage["replays"]["failed"] and not coverage["rounds"]["failed"]
    assert read_rows(out)
    assert calls.count(probe.SITE + f"/replays/{uuid}") == 1
    assert calls.count(probe.SITE + f"/replays/{uuid}/moments.json") == 2


def test_fetch_concurrency_and_phase_boundary(monkeypatch, tmp_path):
    lock, gate = threading.Lock(), threading.Event()
    active = peak = 0
    def opener(request, **kwargs):
        nonlocal active, peak
        assert request.get_method() == "GET"
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 4:
                gate.set()
        gate.wait(.04)
        with lock:
            active -= 1
        return Response(html(context()))
    monkeypatch.setattr(probe, "_urlopen", opener)
    # Stop after phase 1: a stand-in pool checks that workers cannot fetch.
    class Pool:
        def __init__(self, jobs):
            assert jobs == 8
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def map(self, fn, tasks, chunksize=None):
            assert chunksize == 1
            assert active == 0
            monkeypatch.setattr(probe, "fetch", lambda *a: pytest.fail("phase 2 fetched"))
            # Invalid round bytes cause compute failures without a network call.
            return [fn(task) for task in tasks]
    monkeypatch.setattr(probe.multiprocessing, "Pool", Pool)
    patch_geometry(monkeypatch)
    assert probe.main(["run", "--uuids", *UUIDS[:12], "--jobs", "8", "--friends",
                       str(roster(tmp_path)), "--out", str(tmp_path / "out")]) == 1
    assert 1 < peak <= 4


def test_run_offline_coverage_cache_and_private_outputs(monkeypatch, tmp_path):
    dirs, loads = patch_geometry(monkeypatch)
    blob = toy_blob()
    expected = peeks.round_openings(engine.RoundInputs(blob, toys.midwall_hall(),
                                   engine.ControlLink(db_deaths=((5, 12.5),))), blob)
    assert {r["slot"] for r in expected} >= {0, 1, 5}
    good, unlinked, unsupported, malformed = UUIDS[:4]
    responses = {"/replays": " ".join(f'/replays/{u}' for u in reversed(UUIDS[:4] * 2)).encode(),
                 "/static/img/maps/Toy.png": b"png"}
    for i, uuid in enumerate(UUIDS[:4]):
        ctx = context(i)
        if uuid == unlinked:
            ctx["players"] = {}
        responses[f"/replays/{uuid}"] = html(ctx)
        # Names here deliberately differ: moments names must not be used.
        responses[f"/replays/{uuid}/moments.json"] = 404 if uuid == unsupported else b'{"name":"Wrong#1"}'
        responses[f"/replays/{uuid}/1.json"] = b"malformed" if uuid == malformed else json.dumps(blob).encode()
    calls = serve(monkeypatch, responses)
    out = tmp_path / "out"
    argv = ["run", "--jobs", "1", "--friends", str(roster(tmp_path, ["invented#a"])), "--out", str(out)]
    assert probe.main(argv) == 1
    rows = read_rows(out)
    assert len(rows) == len(expected)
    for row, original in zip(rows, expected):
        assert all(row[key] == value for key, value in original.items())
        assert row["uuid"] == good and row["round"] == 1
        assert row["match_id"] == "match-0" and row["asset"] == "Toy"
    by_slot = {row["slot"]: row for row in rows}
    assert by_slot[0]["player_label"] == "Invented#A"
    assert by_slot[1]["player_label"] == "Sage"
    assert by_slot[5]["player_label"] == "the enemy Omen"
    assert by_slot[0]["opponent_label"] == "the enemy Omen"
    assert by_slot[5]["opponent_label"] == "Invented#A"
    assert [s for s, row in by_slot.items() if row["friend"]] == [0]
    assert json.loads((out / "players.json").read_text()) == {"friend-id": "Invented#A"}
    summary = json.loads((out / "summary.json").read_text())
    assert set(summary["players"]) == {"friend-id"}
    for name in ("openings.jsonl", "players.json", "summary.json", "coverage.json"):
        text = (out / name).read_text()
        assert all(private not in text for private in ("Stranger#1", "Opponent#Z", "Wrong#1"))
    coverage = json.loads((out / "coverage.json").read_text())
    assert coverage["replays"]["discovered"] == 4
    assert coverage["replays"]["completed"] == [good]
    assert coverage["replays"]["skipped"] == {"unlinked": [unlinked], "no_moments": [unsupported]}
    assert coverage["rounds"]["failed"][0]["uuid"] == malformed
    assert coverage["rounds"]["failed"][0]["round"] == 1
    assert coverage["rounds"]["failed"][0]["error"]
    assert dirs and all(d.is_relative_to(out) for d in dirs)
    assert loads == [blob["map"]]
    assert calls.count(probe.SITE + "/static/img/maps/Toy.png") == 1
    assert (out / "maps" / "Toy.png").read_bytes() == b"png"
    before = (out / "openings.jsonl").read_bytes()
    monkeypatch.setattr(probe, "_urlopen", lambda *a, **k: pytest.fail("cache miss on rerun"))
    assert probe.main(argv) == 1
    assert (out / "openings.jsonl").read_bytes() == before


def test_one_friend_across_replays_and_roster_override(monkeypatch, tmp_path):
    patch_geometry(monkeypatch)
    blob = json.dumps(toy_blob()).encode()
    responses = {"/static/img/maps/Toy.png": b"png"}
    for i, uuid in enumerate(UUIDS[:2]):
        ctx = context(i)
        if i:
            ctx["players"]["0"].update(agent="Breach", team="blue")
            ctx["players"]["1"]["team"] = "blue"
            ctx["players"]["5"]["team"] = "red"
        responses.update({f"/replays/{uuid}": html(ctx), f"/replays/{uuid}/moments.json": b"{}",
                          f"/replays/{uuid}/1.json": gzip.compress(blob)})
    serve(monkeypatch, responses)
    out = tmp_path / "out"
    argv = ["run", "--uuids", *UUIDS[:2], "--jobs", "1", "--friends", str(roster(tmp_path)), "--out", str(out)]
    assert probe.main(argv) == 0
    rows = read_rows(out)
    friends = [r for r in rows if r["friend"]]
    assert {r["agent"] for r in friends} == {"Jett", "Breach"}
    assert {r["player_label"] for r in friends} == {"Invented#A"}
    assert {r["opponent_label"] for r in friends} == {"the enemy Omen"}
    summary = json.loads((out / "summary.json").read_text())
    assert list(summary["players"]) == ["friend-id"]
    roster(tmp_path, [])
    assert probe.main(argv) == 0
    assert not any(r["friend"] for r in read_rows(out))
    assert json.loads((out / "players.json").read_text()) == {}


@pytest.mark.parametrize("command", [["run"], ["report"], ["score", "answers.json"]])
def test_outside_temp_refused(monkeypatch, tmp_path, command):
    monkeypatch.setattr(probe.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    with pytest.raises(SystemExit) as exc:
        probe.main([*command, "--out", str(tmp_path / "outside")])
    assert exc.value.code == 2
    assert not (tmp_path / "outside").exists()


def row(index, **kwargs):
    return {"uuid": UUIDS[0], "round": 1, "slot": 0, "i": index, "match_id": f"m{index % 3}",
            "player_id": "friend-id", "player_label": "Invented#A", "friend": True,
            "type": "wide_swing", "opener": "peeker", "result": "won", "band": "mid",
            "jiggles": 0, "reason": None, **kwargs}


@pytest.mark.parametrize("reason", ["no_gun", "no_line", "no_onset"])
def test_null_openers_have_separate_none_column(tmp_path, capsys, reason):
    rows = [row(0, type="other", opener=None, reason=reason),
            row(1, type="other", opener="unclear", reason="both_moving")]
    summary = probe.report(rows, tmp_path)
    assert summary["counts"]["other"]["none"] == 1
    assert summary["counts"]["other"]["unclear"] == 1
    assert rows[0]["opener"] is None
    printed = capsys.readouterr().out
    other_line = next(line for line in printed.splitlines() if line.startswith("other:"))
    assert "none=1" in other_line and "unclear=1" in other_line
    assert "none" not in probe.pc.OPENERS


def test_aggregate_pooled_shrink_other_jiggles_and_no_strangers():
    rows = [row(i, result="won" if i < 2 else "lost", jiggles=int(i == 0)) for i in range(3)]
    rows += [row(i + 3, friend=False, player_id="stranger", result="won" if i == 0 else "lost") for i in range(3)]
    rows += [row(6, type="other", result="lost", reason="caught_moving"), row(7, type="other", reason="caught_moving"),
             row(8, type="held", opener="holder"), row(9, type="held", opener="holder", result="lost"),
             row(10, type="wide_swing", result="unresolved"), row(11, type="other", result="unresolved", reason="no_gun")]
    summary = probe.aggregate(rows)
    assert summary["counts"]["wide_swing"]["peeker"] == 7
    assert summary["pooled"]["wide_swing"]["rate"] == .5
    assert summary["pooled"]["other"]["rate"] == .5
    assert summary["other_reasons"] == {"caught_moving": 2, "no_gun": 1}
    assert set(summary["players"]) == {"friend-id"}
    player = summary["players"]["friend-id"]["types"]["wide_swing"]
    assert (player["n"], player["wins"], player["rate"], player["interval"]) == (3, 2, 2/3, None)
    assert player["shrunk"] == pytest.approx((2 + 10 * .5) / 13)
    assert player["bands"]["mid"]["n"] == 3
    assert player["jiggled"]["yes"]["n"] == 1
    assert player["jiggled"]["no"]["n"] == 2
    assert summary["sanity"]["peeker"]["n"] == 8
    assert summary["sanity"]["holder"]["n"] == 2


def test_cluster_interval_resamples_matches():
    clustered = [row(i, match_id=f"m{i//2}", result="lost" if i < 2 else "won") for i in range(8)]
    spread = [dict(r, match_id=f"m{i % 4}") for i, r in enumerate(clustered)]
    def interval(rows):
        return probe.aggregate(rows)["players"]["friend-id"]["types"]["wide_swing"]["interval"]
    lo, hi = interval(clustered)
    a, b = interval(spread)
    assert hi - lo > b - a
    assert probe.aggregate(clustered) == probe.aggregate(clustered)


def test_score_and_report_commands(tmp_path, capsys):
    rows = [row(0), row(1), row(2, type="held"), row(3, type="other")]
    answers = {f'{r["uuid"]}|{r["round"]}|{r["slot"]}|{r["i"]}': r["type"] for r in rows}
    answers[next(iter(answers))] = "held"
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps(answers))
    (tmp_path / "openings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    score = probe.score(rows, answers)
    assert score["overall"] == {"n": 4, "right": 3, "rate": .75}
    assert score["by_type"]["wide_swing"] == {"n": 2, "right": 1, "rate": .5}
    assert score["confusion"]["wide_swing"] == {"held": 1, "wide_swing": 1}
    assert probe.main(["score", str(answers_path), "--out", str(tmp_path)]) == 0
    assert "75.0%" in capsys.readouterr().out
    assert probe.main(["report", "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "peeker won" in printed and "holder" in printed and "Invented#A" in printed
    assert json.loads((tmp_path / "summary.json").read_text()) == probe.aggregate(rows)


def test_fetch_failures_and_limit(monkeypatch, tmp_path):
    responses = {"/replays": " ".join(f"/replays/{u}" for u in reversed(UUIDS[:3])).encode(),
                 f"/replays/{UUIDS[0]}": 503}
    calls = serve(monkeypatch, responses)
    out = tmp_path / "out"
    assert probe.main(["run", "--limit", "1", "--jobs", "1", "--friends", str(roster(tmp_path)),
                       "--out", str(out)]) == 1
    coverage = json.loads((out / "coverage.json").read_text())
    assert coverage["replays"]["discovered"] == 1
    assert coverage["replays"]["failed"][0]["uuid"] == UUIDS[0]
    assert len(calls) == 2


def test_map_fetch_failure_is_reported(monkeypatch, tmp_path):
    uuid = UUIDS[0]
    serve(monkeypatch, {f"/replays/{uuid}": html(context()), f"/replays/{uuid}/moments.json": b"{}",
                        f"/replays/{uuid}/1.json": json.dumps(toy_blob()).encode(),
                        "/static/img/maps/Toy.png": 503})
    out = tmp_path / "out"
    assert probe.main(["run", "--uuids", uuid, "--jobs", "1", "--friends", str(roster(tmp_path)),
                       "--out", str(out)]) == 1
    coverage = json.loads((out / "coverage.json").read_text())
    assert coverage["replays"]["failed"][0]["uuid"] == uuid
    assert not read_rows(out)


def test_ability_only_opening_preserves_null_opponent_and_counts(monkeypatch, tmp_path):
    patch_geometry(monkeypatch)
    uuid = UUIDS[0]
    blob = toy_blob()
    blob["kills"] = []
    blob["util"] = [damage(12.25, 0, 5, src="ability")]
    serve(monkeypatch, {f"/replays/{uuid}": html(context()), f"/replays/{uuid}/moments.json": b"{}",
                        f"/replays/{uuid}/1.json": json.dumps(blob).encode(), "/static/img/maps/Toy.png": b"png"})
    out = tmp_path / "out"
    assert probe.main(["run", "--uuids", uuid, "--jobs", "1", "--friends", str(roster(tmp_path)),
                       "--out", str(out)]) == 0
    rows = read_rows(out)
    assert rows and all(r["opponent"] is None and r["opponent_label"] is None for r in rows)
    assert all(r["type"] == "other" and r["reason"] == "no_gun" for r in rows)
    summary = json.loads((out / "summary.json").read_text())
    assert sum(sum(cs.values()) for cs in summary["counts"].values()) == len(rows)


@pytest.mark.parametrize("failure", ["fetch", "compute", "setup"])
def test_partial_round_and_setup_failures(monkeypatch, tmp_path, capsys, failure):
    patch_geometry(monkeypatch)
    uuid = UUIDS[0]
    ctx = context()
    ctx["rounds"]["2"] = {"db_deaths": []}
    if failure == "setup":
        del ctx["match"]["match_id"]
    good = json.dumps(toy_blob()).encode()
    serve(monkeypatch, {f"/replays/{uuid}": html(ctx), f"/replays/{uuid}/moments.json": b"{}",
                        f"/replays/{uuid}/1.json": good,
                        f"/replays/{uuid}/2.json": 503 if failure == "fetch" else good,
                        "/static/img/maps/Toy.png": b"png"})
    if failure == "compute":
        link = probe.link_for
        def broken_link(ctx, n):
            if n == 2:
                raise ValueError("Stranger#1")
            return link(ctx, n)
        monkeypatch.setattr(probe, "link_for", broken_link)
    out = tmp_path / "out"
    assert probe.main(["run", "--uuids", uuid, "--jobs", "1", "--friends", str(roster(tmp_path)),
                       "--out", str(out)]) == 1
    coverage = json.loads((out / "coverage.json").read_text())
    assert coverage["rounds"]["discovered"] == 2
    assert len(coverage["replays"]["failed"]) == 1
    assert len(coverage["rounds"]["failed"]) == (2 if failure == "setup" else 1)
    assert len(coverage["rounds"]["completed"]) == (0 if failure == "setup" else 1)
    assert all(r["round"] == 1 for r in read_rows(out))
    if failure != "fetch":
        assert "Traceback" in capsys.readouterr().err
    assert "Stranger#1" not in (out / "coverage.json").read_text()


class PeekHTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.sections, self.cards, self.links, self.scripts = [], [], [], []
        self.payload = ""
        self.in_data = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and "data-type" in attrs:
            self.sections.append(attrs)
        if tag == "article":
            self.cards.append(attrs)
        if tag == "a":
            self.links.append(attrs)
        if tag == "script":
            self.scripts.append(attrs)
            self.in_data = attrs.get("type") == "application/json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.payload += data


def page_row(index, **kwargs):
    # The complete opening shape, including the nullable unclassified fields.
    return row(index, opponent=5, t0=10.0, t1=12.0, t_event=11.5, t_first_sight=10.5,
               t_onset=10.5, speed_mps=5.0, sideways_m=2.0, exposure_s=.4,
               repeek_gap_s=None, repeek_spot_m=None, distance_m=15.0, gun_class="rifle",
               agent="Jett", asset="Toy", opponent_label="the enemy Omen") | kwargs


def answer_key(r):
    return f"{r['uuid']}|{r['round']}|{r['slot']}|{r['i']}"


def test_build_page_sampling_safety_and_answer_keys(tmp_path):
    players = {"friend-id": "Invented#A", "second-id": "Fiction#B"}
    rows = []
    for i in range(3 * len(probe.pc.TYPES)):
        named = i % 4 != 3
        pid = "friend-id" if i % 2 == 0 else "second-id"
        rows.append(page_row(i, type=probe.pc.TYPES[i % len(probe.pc.TYPES)], friend=named,
                             player_id=pid if named else "stranger-id",
                             player_label=players[pid] if named else "Sage", agent="Sage",
                             name="Stranger#1", reason="unmeasured" if i % len(probe.pc.TYPES) == len(probe.pc.TYPES) - 1 else None,
                             jiggles=2, result="lost" if i % 2 else "won"))
    rows[0]["opponent_label"] = "the enemy </script><img src=x onerror=alert(1)>"
    page = probe.build_page(rows, players, tmp_path, per_type=2, seed=19)
    text = page.read_text(encoding="utf-8")
    parsed = PeekHTML(text)
    payload = json.loads(parsed.payload)
    assert page == tmp_path / "peeks.html"
    assert [s["data-type"] for s in parsed.sections] == list(probe.pc.TYPES)
    assert [int(s["data-total"]) for s in parsed.sections] == [3] * len(probe.pc.TYPES)
    assert all(sum(c["data-type"] == t for c in parsed.cards) == 2 for t in probe.pc.TYPES)
    cards = payload["cards"]
    assert {c["key"] for c in cards} == {c["data-key"] for c in parsed.cards}
    assert all(c["key"] == answer_key(c["row"]) for c in cards)
    assert all(c["row"]["friend"] for c in cards if c["row"]["type"] in ("held", "wide_swing", "slow_peek", "re_peek"))
    assert "Stranger#1" not in text and "stranger-id" not in parsed.payload
    assert "</script><img" not in text and "\\u003c" in parsed.payload
    assert all("src" not in script for script in parsed.scripts)
    assert all(re.fullmatch(re.escape(probe.SITE) + r"/replays/[0-9a-f-]{36}\?round=1&t=8\.50", a["href"])
               and a["target"] == "_blank" for a in parsed.links)
    assert "after 2 jiggles" in text and "Everyone's average" in text
    assert "jiggled yes" in text and "jiggled no" not in text
    assert "walls and smokes only" in text and "thresholds are a draft" in text
    assert probe.build_page(rows, players, tmp_path, per_type=2, seed=19).read_text(encoding="utf-8") == text
    # With room for all examples, preserve safe enemy labels and strip raw names.
    rows[-1]["player_label"] = "Stranger#1"
    rows[3]["player_label"] = "the enemy Sage"
    all_text = probe.build_page(rows, players, tmp_path, per_type=20).read_text(encoding="utf-8")
    assert "Stranger#1" not in all_text
    assert "the enemy Sage vs the enemy Omen" in all_text


def test_build_page_samples_each_other_reason_and_preserves_card_order(tmp_path):
    players = {"friend-id": "Invented#A"}
    rows = [page_row(i, type="other", reason="no_line", friend=i >= 18,
                     player_id="friend-id" if i >= 18 else "stranger-id",
                     player_label="Invented#A" if i >= 18 else "Jett") for i in range(20)]
    rows.append(page_row(20, type="other", reason="unmeasured"))
    page = probe.build_page(rows, players, tmp_path, per_type=2, seed=0)
    text = page.read_text(encoding="utf-8")
    parsed = PeekHTML(text)
    cards = json.loads(parsed.payload)["cards"]
    assert [c["row"]["reason"] for c in cards] == ["no_line", "no_line", "unmeasured"]
    assert all(c["row"]["friend"] for c in cards)
    assert {c["row"]["i"] for c in cards[:2]} == {18, 19}
    assert '<h3>no line (20)</h3>' in text
    assert '<h3>unmeasured (1)</h3>' in text
    assert text.index('<h3>no line (20)</h3>') < text.index('<h3>unmeasured (1)</h3>')
    assert parsed.sections[-1] == {"data-type": "other", "data-total": "21"}
    assert [c["key"] for c in cards] == [c["data-key"] for c in parsed.cards]
    assert probe.build_page(rows, players, tmp_path, per_type=2, seed=0).read_text(encoding="utf-8") == text


def test_build_page_omits_empty_player_types_and_splits(tmp_path):
    players = {"friend-id": "Invented#A"}
    page = probe.build_page([page_row(i) for i in range(3)], players, tmp_path)
    text = page.read_text(encoding="utf-8")
    table = text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "slow peek" not in table
    assert "jiggled yes" not in table
    assert "<td>0</td>" not in table
    assert "<td>wide swing</td>" in table and "<td>wide swing / jiggled no</td>" in table
    assert table.count("<tr>") == 2


def test_run_page_paths_and_offline_rebuild(monkeypatch, tmp_path):
    patch_geometry(monkeypatch)
    uuid = UUIDS[0]
    blob = toy_blob()
    openings = [page_row(i, type="other", reason=reason, t_onset=None if i < 3 else 10.25,
                         t_event=None if i == 0 else 11.5, t0=1.0 if i == 0 else 10.0,
                         opponent=None if i == 0 else 5,
                         speed_mps=None, sideways_m=None, exposure_s=None)
                for i, reason in enumerate(("no_gun", "no_line", "no_onset", "unmeasured"))]
    monkeypatch.setattr(peeks, "round_openings", lambda rnd, blob: openings)
    serve(monkeypatch, {f"/replays/{uuid}": html(context()), f"/replays/{uuid}/moments.json": b"{}",
                        f"/replays/{uuid}/1.json": json.dumps(blob).encode(), "/static/img/maps/Toy.png": b"png"})
    out = tmp_path / "out"
    assert probe.main(["run", "--uuids", uuid, "--jobs", "1", "--friends", str(roster(tmp_path)),
                       "--out", str(out)]) == 0
    before = (out / "peeks.html").read_text(encoding="utf-8")
    parsed = PeekHTML(before)
    cards = json.loads(parsed.payload)["cards"]
    assert len(cards) == 4
    sidecar = {p["key"]: p["paths"] for p in
               map(json.loads, (out / "paths.jsonl").read_text().splitlines())}
    assert set(sidecar) == {answer_key(r) for r in read_rows(out)}
    rnd = engine.RoundInputs(blob, toys.midwall_hall(), engine.ControlLink(db_deaths=((5, 12.5),)))
    for card in cards:
        r, paths = card["row"], card["paths"]
        assert paths == sidecar[card["key"]]
        ref = 1.0 if r["reason"] == "no_gun" else 10.25 if r["reason"] == "unmeasured" else 11.5
        assert card["t_ref"] == ref
        assert card["viewer_url"].endswith(f"&t={max(0, ref - 2):.2f}")
        assert paths["sight_line"] == (r["reason"] == "unmeasured")
        assert paths["px_to_image"] == 1 / geometry.PX
        assert paths["m_per_px"] == rnd.geo.m_per_px
        assert len(paths["player"]) == 49
        if r["opponent"] is None:
            assert paths["opponent"] == []
        else:
            assert len(paths["opponent"]) == 49
        for label, slot in (("player", 0), ("opponent", r["opponent"])):
            for t, x, y, segment in paths[label]:
                assert (t - (ref - 2)) * 16 == pytest.approx(round((t - (ref - 2)) * 16))
                expected = rnd.pos(slot, t) if rnd.alive(slot, t) else None
                assert [x, y] == (list(expected[:2]) if expected else [None, None])
                sample = rnd._sample(slot, t) if expected else None
                assert segment == (int(rnd._segment[slot][sample]) if sample is not None else None)
    monkeypatch.setattr(probe, "_urlopen", lambda *a, **k: pytest.fail("page must never fetch"))
    assert probe.main(["page", "--out", str(out)]) == 0
    assert (out / "peeks.html").read_text(encoding="utf-8") == before


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_page_answer_state_scores_and_storage_failures():
    js = Path(probe.__file__).with_name("peek_page.js")
    rows = [page_row(0), page_row(1)]
    script = f"""
const p = require({json.dumps(str(js))});
const state = {{}};
p.recordRight(state, {json.dumps(answer_key(rows[0]))}, 'wide_swing');
p.recordCorrected(state, {json.dumps(answer_key(rows[1]))}, 'held');
const broken = {{getItem() {{ throw Error('private'); }}, setItem() {{ throw Error('full'); }}}};
p.saveAnswers(broken, 'answers', state);
if (Object.keys(p.loadAnswers(broken, 'answers')).length) throw Error('bad load');
const storage = {{value: null, setItem(k,v) {{this.value=v;}}, getItem() {{return this.value;}}}};
p.saveAnswers(storage, 'answers', state);
const restored = p.loadAnswers(storage, 'answers');
if (JSON.stringify(restored) !== JSON.stringify(state)) throw Error('lost answers');
storage.value = 'not json';
if (Object.keys(p.loadAnswers(storage, 'answers')).length) throw Error('bad JSON');
console.log(p.exportAnswers(restored));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    answers = json.loads(result.stdout)
    scored = probe.score(rows, answers)
    assert scored["overall"] == {"n": 2, "right": 1, "rate": .5}
    assert scored["confusion"] == {"wide_swing": {"held": 1, "wide_swing": 1}}


def test_page_script_guarded():
    import tomllib
    suites = tomllib.loads((Path(probe.WEBAPP_ROOT) / "tests/suites.toml").read_text(encoding="utf-8"))
    assert "scripts/peek_page.js" in suites["critical-moments"]["guards"]


@pytest.mark.parametrize("slot,label", [(0, "player"), (5, "opponent")])
def test_paths_preserve_short_segment_break_on_the_playback_grid(slot, label):
    blob = toy_blob()
    before, after = [(0, 120, 200, 0)], [(0, 800, 200, 0)]
    blob["tracks"][str(slot)] = (toys.track(before, 0, .992, hz=125)
                                 + toys.track(after, 1.008, 16, hz=125))
    rnd = engine.RoundInputs(blob, toys.open_hall(), engine.ControlLink(db_deaths=()))
    paths = probe._paths(page_row(0, t_onset=2), rnd)
    track = paths[label]
    assert len(track) == 49
    assert [p[0] for p in track] == [k / 16 for k in range(49)]
    assert all(len(p) == 4 for p in track)
    assert track[15][1:3] == pytest.approx([120, 200], abs=.1)
    assert track[17][1:3] == pytest.approx([800, 200], abs=.1)
    assert track[15][3] == 0 and track[17][3] == 1
    assert track[16][1] is not None  # No null grid point hides this segment boundary.


@pytest.mark.parametrize("exact,rounded,event,t0,expected", [
    (6.734, 6.73, 6.984, 6.984, 6.734),
    (1.234, 1.23, 1.484, 1.484, 1.234),
    (0.0, 0.0, .25, .25, 0.0),
    (None, None, 2.345, 2, 2.345),
    (None, None, None, 1.234, 1.234),
    ("absent", 6.73, 6.984, 6.984, 6.73),
])
def test_page_and_paths_use_exact_onset_with_legacy_fallback(
        tmp_path, exact, rounded, event, t0, expected):
    from tests.moments.test_peek_labels import round_of, steps

    rnd, _ = round_of(steps((6.734, None)))
    r = page_row(0, t_onset=rounded, t_event=event, t0=t0)
    if exact != "absent":
        r["t_onset_exact"] = exact
    paths = probe._paths(r, rnd)
    assert probe._moment(r) == expected
    assert paths["at_ref"][0] == list(rnd.pos(0, expected)[:2])
    assert paths["player"][0][0] == expected - 2
    assert paths["sight_line"] is (rounded is not None)
    if exact == 6.734:
        assert peeks.line_clear(rnd, 0, 5, expected) is True
        assert peeks.line_clear(rnd, 0, 5, rounded) is False
        assert paths["at_ref"][0] != list(rnd.pos(0, rounded)[:2])
    (tmp_path / "paths.jsonl").write_text(
        json.dumps({"key": answer_key(r), "paths": paths}) + "\n", encoding="utf-8")
    text = probe.build_page([r], {"friend-id": "Invented#A"}, tmp_path).read_text(encoding="utf-8")
    card = json.loads(PeekHTML(text).payload)["cards"][0]
    assert card["t_ref"] == expected
    assert card["paths"] == paths
    assert card["viewer_url"].endswith(f"&t={max(0, expected - 2):.2f}")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_page_path_commands_break_segments_and_keep_playback_index():
    js = Path(probe.__file__).with_name("peek_page.js")
    script = f"""
const p = require({json.dumps(str(js))});
const track = [[0, 1, 2, 0], [1/16, 2, 3, 0], [2/16, 800, 4, 1],
               [3/16, 801, 5, 1], [4/16, null, null, null], [5/16, 802, 6, 1]];
const legacy = track.map(s => s.slice(0, 3));
console.log(JSON.stringify({{
    commands: p.pathCommands(track), legacy: p.pathCommands(legacy),
    points: track.map(s => p.pointAt(track, s[0])),
    outside: p.pointAt(track, 10), empty: p.pathCommands([])
}}));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    actual = json.loads(result.stdout)
    assert actual["commands"] == [["moveTo", 1, 2], ["lineTo", 2, 3], ["moveTo", 800, 4],
                                   ["lineTo", 801, 5], ["moveTo", 802, 6]]
    assert actual["legacy"] == [["moveTo", 1, 2], ["lineTo", 2, 3], ["lineTo", 800, 4],
                                 ["lineTo", 801, 5], ["moveTo", 802, 6]]
    assert actual["points"] == [[1, 2], [2, 3], [800, 4], [801, 5], [None, None], [802, 6]]
    assert actual["outside"] is None and actual["empty"] == []


def test_page_types_equal_plan_types():
    js = Path(probe.__file__).with_name("peek_page.js").read_text(encoding="utf-8")
    types = json.loads(re.search(r"const PEEK_TYPES = (\[[^;]+\]);", js)[1])
    assert tuple(types) == probe.pc.TYPES
