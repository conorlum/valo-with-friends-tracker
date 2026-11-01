"""The freshness check's memos (figures_hash, the per-map feature identity) are speed-ups only: same answers,
re-read when an input changes."""

import json
import os
import time

from app.replays import control_format as cf
from app.replays import map_feature_inputs as fi
from app.services import replay_control as rc


def _old(path):
    """Back-date a file past the racy window, as a deployed asset is."""
    past = time.time() - 60
    os.utime(path, (past, past))


def test_figures_hash_is_remembered_and_follows_an_edit(tmp_path, monkeypatch):
    utility = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))
    path = tmp_path / "utility.json"
    path.write_text(json.dumps(utility), encoding="utf-8")
    _old(path)
    first = cf.figures_hash(utility=path)
    reads = []
    real = cf._figures_hash
    monkeypatch.setattr(cf, "_figures_hash", lambda *a: reads.append(1) or real(*a))
    assert cf.figures_hash(utility=path) == first and reads == [], "an unchanged file is not read again"
    key = next(iter(utility["molly"]))
    utility["molly"][key]["seconds"] = 123.25
    path.write_text(json.dumps(utility), encoding="utf-8")
    assert cf.figures_hash(utility=path) != first and reads == [1], "a just-edited file is read (racy window)"
    _old(path)
    assert cf.figures_hash(utility=path) != first


def test_figures_hash_never_trusts_a_file_changed_within_the_racy_window(tmp_path):
    utility = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))
    path = tmp_path / "utility.json"
    path.write_text(json.dumps(utility), encoding="utf-8")
    before = cf.figures_hash(utility=path)
    key = next(iter(utility["molly"]))
    utility["molly"][key]["seconds"] = 9.5
    path.write_text(json.dumps(utility), encoding="utf-8")      # same instant, perhaps the same size and mtime
    assert cf.figures_hash(utility=path) != before
    assert cf.figures_hash(utility=path) == cf._figures_hash(utility=path)


def test_feature_identity_is_remembered_on_its_inputs(monkeypatch):
    calls = []
    monkeypatch.setattr(fi, "identify_features", lambda *a: calls.append(a) or ("input", a[1]))
    monkeypatch.setattr(rc, "_features_memo", {})

    class Source:
        raw_sha256 = "s1"

    base = {"sight": {"data": "AA=="}, "legacy": {}}
    assert rc._identify_features("Map", "h1", Source, base) == ("input", "h1")
    assert rc._identify_features("Map", "h1", Source, dict(base)) == ("input", "h1") and len(calls) == 1
    assert rc._identify_features("Map", "h2", Source, base) == ("input", "h2") and len(calls) == 2
    assert rc._identify_features("Map", "h1", Source, {**base, "sight": {"data": "AQ=="}}) and len(calls) == 3
    Source.raw_sha256 = "s2"
    rc._identify_features("Map", "h1", Source, base)
    monkeypatch.setattr(fi, "CONSUMER_VERSIONS", {"other": 1})
    rc._identify_features("Map", "h1", Source, base)
    assert len(calls) == 5, "a new source or consumer table is identified afresh"


def test_feature_identity_errors_are_not_remembered(monkeypatch):
    calls = []

    def fail(*a):
        calls.append(1)
        raise fi.FeatureInputsError("bad")

    monkeypatch.setattr(fi, "identify_features", fail)
    monkeypatch.setattr(rc, "_features_memo", {})

    class Source:
        raw_sha256 = "s"

    for _ in range(2):
        try:
            rc._identify_features("Map", None, Source, {})
        except fi.FeatureInputsError:
            pass
    assert calls == [1, 1]
