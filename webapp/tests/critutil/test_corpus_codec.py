"""The portable build's wire format preserves input types, order and the original cache key."""

import copy
import hashlib
import io
import json
import re
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from app.critutil import abilities, constants, corpus
from app.services import replay_util, util_model, util_values
from tests.critutil.test_build import FRIEND, fake_gate, toy_corpus
from tests.critutil.test_util_model_refit import db  # noqa: F401
from tests.critutil.test_values import MODEL


AT = datetime(2026, 11, 11, 12, 13, 14, 123457, tzinfo=timezone(timedelta(hours=-8)))


def parts_for(ctx, friends, rows):
    return {"values_version": util_values.VERSION, "page_version": corpus.PAGE_VERSION,
            "abilities_hash": abilities.table_hash(), "constants_version": constants.VERSION,
            "friends_hash": hashlib.sha256(",".join(map(str, sorted(friends))).encode()).hexdigest(),
            "rows": [[r, n, fp, str(at)] for r, n, fp, at, *_ in rows],
            "replays": [[rid, c["match_id"], c["offset"]] for rid, c in ctx.items()]}


def original_inputs(shuffled=False):
    stored, ctx = toy_corpus()
    for rid, c in ctx.items():
        c["loadout"] = {(1, "Red"): 4300.5, (1, "Blue"): 3300.0}
        c["rounds"][1]["id"] = rid * 100 + 1
        stored[rid][2] = copy.deepcopy(stored[rid][1])
        c["rounds"][2] = {"winner": "defense", "id": rid * 100 + 2}
        c["loadout"][(2, "Red")] = 2800.0
    rows = [(rid, n, f"fp-{rid}-{n}", AT, data)
            for rid, rounds in stored.items() for n, data in rounds.items()]
    if shuffled:
        # Interleave replays, and put round 2 before round 1 within each replay.
        rows = rows[1::2][::-1] + rows[::2][::-1]
        stored = replay_util._stored_rounds(None, rows=rows)
        ctx = dict(reversed(list(ctx.items())))
    return stored, ctx, rows


@pytest.mark.parametrize("shuffled", [False, True])
def test_full_build_round_trip_preserves_types_order_and_values(tmp_path, monkeypatch, shuffled):
    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(True))
    stored, ctx, rows = original_inputs(shuffled)
    model = {**MODEL, "key": "original-win"}
    parts = parts_for(ctx, {FRIEND}, rows)
    encoded = corpus.encode_context(ctx, {FRIEND}, model, rows, {9, 3}, False, parts)
    wire = json.loads(encoded)
    assert wire["rows"] == [[rid, n, fp, 1794427994123457] for rid, n, fp, at, *_ in rows]
    decoded = corpus.decode_context(encoded)
    assert decoded["ctx"] == ctx
    assert decoded["friends"] == {FRIEND} and isinstance(decoded["friends"], set)
    assert decoded["excluded"] == {3, 9} and isinstance(decoded["excluded"], set)
    assert list(decoded["ctx"]) == list(ctx)
    for rid, c in decoded["ctx"].items():
        assert isinstance(rid, int)
        assert all(isinstance(k, int) for k in c["players"])
        assert all(isinstance(k, int) for k in c["rounds"])
        assert all(isinstance(k, tuple) and isinstance(k[0], int) for k in c["loadout"])
    assert [r[:3] for r in decoded["rows"]] == [r[:3] for r in rows]
    assert all(r[3] == AT for r in decoded["rows"])
    blobs = {corpus.row_key(*r[:4]): corpus.encode_round(r[4]) for r in rows}
    assert all(re.fullmatch(corpus.KEY_RE, key) for key in blobs)
    path = tmp_path / "inputs.tar"
    corpus.pack_inputs(encoded, dict(reversed(list(blobs.items()))), {}, path)
    with tarfile.open(path) as archive:
        assert set(archive.getnames()) == {"context.json", *(f"rounds/{key}.json" for key in blobs)}
    inputs = corpus.unpack_inputs(path)
    expected = corpus.corpus_from_inputs((stored, ctx, {FRIEND}), rows, model, painted_table={})
    result = corpus.compute(inputs)
    assert result["values"] == expected
    assert json.dumps(result["values"], sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert result["win"] == model
    assert result["values_key"] == corpus.values_key(parts, model["key"])
    # Check accumulation order directly, independently of numerical coincidence.
    calls = []
    monkeypatch.setattr(corpus, "corpus_from_inputs", lambda *args, **kw: calls.append(args) or {})
    corpus.compute(inputs)
    rebuilt = calls[0][0][0]
    assert list(rebuilt) == list(stored)
    assert [list(v) for v in rebuilt.values()] == [list(v) for v in stored.values()]


def test_published_paint_travels_in_the_context_and_keys_the_build(tmp_path, monkeypatch):
    """The worker has no git checkout and no paint file: the site's paint reaches it in context.json, places every
    cast, and is in the values_key exactly as the site's corpus_key puts it."""
    from app.coach import areas
    from tests.coach.test_areas import painted_object, write_paint

    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(True))
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path / "paint")
    monkeypatch.setattr(areas, "_git_root", lambda: None)
    (tmp_path / "paint").mkdir()
    path = write_paint(tmp_path / "paint", painted_object({0: 3}))      # cell 0 is "A Site" (super "A")
    table = areas.load_table(published_only=True)
    path.unlink()                                                     # nothing on the worker's disk
    assert set(table) == {"Sunset"}

    stored, ctx, rows = original_inputs()
    for rid, c in ctx.items():
        c["map"] = "Sunset"
        for data in stored[rid].values():
            for cast in data["casts"]:
                cast.update(u=0, v=0)
    rows = [(rid, n, fp, at, stored[rid][n]) for rid, n, fp, at, _ in rows]
    model = {**MODEL, "key": "original-win"}
    monkeypatch.setattr(replay_util, "friend_players", lambda db: {FRIEND})
    site_hash = replay_util.corpus_key_parts(None, [], painted_table=table)["areas_hash"]
    assert site_hash == areas.table_hash(painted_table=table) is not None
    parts = parts_for(ctx, {FRIEND}, rows) | {"areas_hash": site_hash}

    encoded = corpus.encode_context(ctx, {FRIEND}, model, rows, set(), False, parts, table)
    decoded = corpus.decode_context(encoded)
    assert set(decoded["areas"]) == {"Sunset"} and decoded["areas"]["Sunset"].raw == table["Sunset"].raw
    archive = tmp_path / "inputs.tar"
    corpus.pack_inputs(encoded, {corpus.row_key(*r[:4]): corpus.encode_round(r[4]) for r in rows}, {}, archive)
    result = corpus.compute(corpus.unpack_inputs(archive))

    casts = [row for row in result["values"]["rows"] if row["kind"] != "heal"]
    assert casts and {row["area"] for row in casts} == {"A"}
    assert result["values_key"] == corpus.values_key(parts, model["key"])
    assert result["values_key"] != corpus.values_key({**parts, "areas_hash": None}, model["key"])
    # No paint at all keeps the pre-paint key, and a context with no paint places casts by callouts alone.
    assert corpus.values_key({**parts, "areas_hash": None}, model["key"]) == \
        corpus.values_key({k: v for k, v in parts.items() if k != "areas_hash"}, model["key"])
    assert corpus.decode_context(corpus.encode_context(ctx, {FRIEND}, model, rows, set(), False, parts))["areas"] == {}


def test_a_corrupt_paint_member_refuses_the_build(monkeypatch):
    stored, ctx, rows = original_inputs()
    wire = json.loads(corpus.encode_context(ctx, {FRIEND}, MODEL, rows, set(), False, parts_for(ctx, {FRIEND}, rows)))
    wire["areas"] = [["Sunset", "{not json"]]
    with pytest.raises(ValueError, match="invalid painted areas for 'Sunset'"):
        corpus.decode_context(json.dumps(wire).encode())


def test_row_keys_normalize_utc_without_losing_microseconds():
    expected = "7-2-fp-1794427994123457"
    assert corpus.row_key(7, 2, "fp", AT) == expected
    assert corpus.row_key(7, 2, "fp", AT.astimezone(timezone.utc)) == expected
    assert corpus.row_key(7, 2, "fp", AT.astimezone(timezone.utc).replace(tzinfo=None)) == expected
    assert corpus.row_key(7, 2, "fp", AT + timedelta(microseconds=1)) != expected


def test_values_key_matches_original_database_key(db):
    from app.models.friendship import Friendship
    db.add(Friendship(owner_player_id=11, friend_player_id=12))
    db.commit()
    rows = [(6, 2, "fp-b", AT), (6, 1, "fp-a", AT.replace(tzinfo=None))]
    model = {"key": "old-win"}
    friends = hashlib.sha256(b"11,12").hexdigest()
    old = hashlib.sha256(f"{util_values.VERSION}:{corpus.PAGE_VERSION}:old-win:{abilities.table_hash()}:"
                         f"{constants.VERSION}:{friends}".encode())
    for rid, n, fp, at in sorted(rows, key=lambda r: (r[0], r[1])):
        old.update(f"|{rid}:{n}:{fp}:{at}".encode())
    old.update(b"|r6:6:1.25")
    parts = replay_util.corpus_key_parts(db, rows)
    assert corpus.values_key(parts, model["key"]) == replay_util.corpus_key(db, rows, model) == old.hexdigest()[:32]
    decoded = corpus.decode_context(corpus.encode_context({}, set(), model, rows, set(), False, parts))
    assert corpus.values_key(decoded["key_parts"], model["key"]) == old.hexdigest()[:32]


def test_dispatch_identity_includes_refit_exclusions_observations_and_digest():
    key = corpus.dispatch_key("values", False, {3, 2}, ["obs-b", "obs-a"], "digest")
    assert key == corpus.dispatch_key("values", False, {2, 3}, ["obs-a", "obs-b"], "digest")
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert key != corpus.dispatch_key("values", True, {2, 3}, ["obs-a", "obs-b"], "digest")
    assert key != corpus.dispatch_key("values", False, {2}, ["obs-a", "obs-b"], "digest")
    assert key != corpus.dispatch_key("values", False, {2, 3}, ["obs-a"], "digest")
    assert key != corpus.dispatch_key("values", False, {2, 3}, ["obs-a", "obs-b"], "new-digest")


@pytest.mark.parametrize("refit", [False, True])
def test_compute_refit_uses_payload_match_ids_and_the_resulting_estimator_key(tmp_path, monkeypatch, refit):
    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(True))
    stored, ctx, rows = original_inputs()
    obs = {31: [(5, 5, False, 0.0, None, True)], 32: [(4, 5, False, 8.0, None, False)],
           99: [(1, 1, True, 30.0, 20.0, True)]}
    excluded = {99}
    model = None
    parts = parts_for(ctx, {FRIEND}, rows)
    encoded = corpus.encode_context(ctx, {FRIEND}, model, rows, excluded, refit, parts)
    # Observation shards carry [match_id, observations] entries; their filenames are opaque.
    observations = {"opaque-alpha": corpus.encode_obs([[31, obs[31]], [99, obs[99]]]),
                    "opaque-beta": corpus.encode_obs([[32, obs[32]]])}
    path = tmp_path / "inputs.tar"
    corpus.pack_inputs(encoded, {corpus.row_key(*r[:4]): corpus.encode_round(r[4]) for r in rows}, observations, path)
    result = corpus.compute(corpus.unpack_inputs(path))
    win = util_model.refit_from(obs, excluded) if refit else None
    assert result["win"] == win
    assert result["values"] == corpus.corpus_from_inputs((stored, ctx, {FRIEND}), rows, win, painted_table={})
    assert result["values_key"] == corpus.values_key(parts, (win or {}).get("key"))
    if refit:
        assert result["values_key"] != corpus.values_key(parts, None)


def test_output_rejects_nan():
    with pytest.raises(ValueError):
        corpus.encode_output({"x": float("nan")})
    assert corpus.encode_output({"x": 1}) == b'{"x":1}'


def test_unpack_rejects_unsafe_archive_members(tmp_path):
    path = tmp_path / "bad.tar"
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo("../outside.json")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"{}"))
    with pytest.raises(ValueError):
        corpus.unpack_inputs(path)
