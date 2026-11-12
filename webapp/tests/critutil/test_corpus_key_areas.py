from app.coach import areas
from app.services import replay_util
from tests.coach.test_areas import painted_object, write_paint


def test_corpus_ignores_uncommitted_paint_and_snapshots_committed_bytes(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: tmp_path)
    monkeypatch.setattr(replay_util, "friend_players", lambda db: set())
    head = {}
    monkeypatch.setattr(areas, "_head_bytes", lambda root, path: head.get(path.name))
    original = replay_util.corpus_key(None, [], None)
    path = write_paint(tmp_path, painted_object({0: 3}))
    assert replay_util.corpus_key(None, [], None) == original
    assert replay_util.corpus_key(None, [], None) == original
    assert caplog.text.count(path.name) == 1
    assert areas.area_at("Sunset", 0, 0, painted_table=areas.load_table(published_only=True))["approximate"] is True
    assert areas.area_at("Sunset", 0, 0)["name"] == "A Site"
    head[path.name] = path.read_bytes()
    table = areas.load_table(published_only=True)
    published_key = replay_util.corpus_key(None, [], None, painted_table=table)
    assert published_key != original
    write_paint(tmp_path, painted_object({0: 1}))
    assert replay_util.corpus_key(None, [], None, painted_table=table) == published_key
    assert replay_util.area_of("Sunset", 0, 0, "attack", painted_table=table) == "A"
    assert replay_util.corpus_key(None, [], None) == original


def test_corpus_uses_reviewed_paint_without_git(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: None)
    monkeypatch.setattr(replay_util, "friend_players", lambda db: set())
    original = replay_util.corpus_key(None, [], None)
    write_paint(tmp_path, painted_object({0: 3}))
    assert replay_util.corpus_key(None, [], None) != original


def test_committed_crlf_paint_uses_the_same_corpus_key_as_lf(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: tmp_path)
    monkeypatch.setattr(replay_util, "friend_players", lambda db: set())
    head = areas.dumps(painted_object({0: 3})).encode("utf-8")
    monkeypatch.setattr(areas, "_head_bytes", lambda root, path: head)
    path = tmp_path / "Sunset.areas.json"
    path.write_bytes(head)
    lf_key = replay_util.corpus_key(None, [], None)
    path.write_bytes(head.replace(b"\n", b"\r\n"))
    table = areas.load_table(published_only=True)
    assert "Sunset" in table
    assert replay_util.area_of("Sunset", 0, 0, "attack", painted_table=table) == "A"
    assert replay_util.corpus_key(None, [], None) == lf_key


def test_missing_git_uses_all_reviewed_files_and_warns_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: tmp_path)
    write_paint(tmp_path, painted_object({0: 3}))
    write_paint(tmp_path, painted_object({0: 1}) | {"map": "Bind"}, "Bind")

    def missing_git(*args, **kwargs):
        raise OSError("git executable unavailable")

    monkeypatch.setattr(areas.subprocess, "run", missing_git)
    for _ in range(2):
        table = areas.load_table(published_only=True)
        assert set(table) == {"Bind", "Sunset"}
        assert replay_util.area_of("Sunset", 0, 0, "attack", painted_table=table) == "A"
    assert len(caplog.records) == 1
    assert "git" in caplog.text.lower()


def test_ensure_values_passes_the_key_snapshot_to_the_build(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: None)
    monkeypatch.setattr(replay_util, "friend_players", lambda db: set())
    monkeypatch.setattr(replay_util, "current_rows", lambda db, **kw: [])
    monkeypatch.setattr(replay_util, "_values_row", lambda db: None)
    monkeypatch.setattr(replay_util.util_model, "ensure", lambda db: {})
    monkeypatch.setattr(replay_util.util_model, "load", lambda db: {})
    monkeypatch.setattr(replay_util.util_model, "rebuild_lock", lambda *args: nullcontext())
    path = write_paint(tmp_path, painted_object({0: 3}))
    stored = []

    def build(db, *, rows, model, painted_table=None):
        assert painted_table is not None
        key = replay_util.corpus_key(db, rows, model, painted_table=painted_table)
        path.unlink(missing_ok=True)
        return {"key": key, "area": replay_util.area_of("Sunset", 0, 0, "attack", painted_table=painted_table)}

    monkeypatch.setattr(replay_util, "build_corpus", build)
    monkeypatch.setattr(replay_util, "_store_values", lambda db, key, data: stored.append((key, data)))
    replay_util.ensure_values(SimpleNamespace(expire_all=lambda: None))
    assert stored[0][0] == stored[0][1]["key"]
    assert stored[0][1]["area"] == "A"


def test_snapshot_reaches_cast_classification_through_the_corpus_builder(tmp_path, monkeypatch):
    from tests.critutil.test_build import toy_corpus
    from tests.critutil.test_values import MODEL

    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: None)
    path = write_paint(tmp_path, painted_object({0: 3}))
    table = areas.load_table(published_only=True)
    path.unlink()
    stored, ctx = toy_corpus()
    for replay_id, rounds in stored.items():
        ctx[replay_id]["map"] = "Sunset"
        for data in rounds.values():
            for cast in data["casts"]:
                cast.update(u=0, v=0)
    monkeypatch.setattr(replay_util, "_stored_rounds", lambda db, **kwargs: stored)
    monkeypatch.setattr(replay_util, "_replay_context", lambda db, ids: ctx)
    monkeypatch.setattr(replay_util, "friend_players", lambda db: set())
    built = replay_util.build_corpus(None, rows=[], model=MODEL, painted_table=table)
    assert built["rows"]
    assert {row["area"] for row in built["rows"]} == {"A"}


def test_corpus_key_keeps_its_original_value_until_areas_are_reviewed(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: None)
    monkeypatch.setattr(replay_util, "friend_players", lambda db: {7, 3})
    monkeypatch.setattr(replay_util.abilities, "table_hash", lambda: "toy-abilities")
    monkeypatch.setattr(replay_util.util_values, "VERSION", 10)
    monkeypatch.setattr(replay_util, "PAGE_VERSION", 20)
    monkeypatch.setattr(replay_util.constants, "VERSION", 30)
    model = {"key": "toy"}
    # The first 32 hex characters of sha256("10:20:toy:toy-abilities:30:" + hex sha256("3,7")).
    original = "18715beca3c693b427c38ac5fcc51b46"
    assert replay_util.corpus_key(None, [], model) == original

    paint = painted_object({0: 2})
    write_paint(tmp_path, paint | {"reviewed": False})
    assert replay_util.corpus_key(None, [], model) == original

    write_paint(tmp_path, paint)
    assert replay_util.corpus_key(None, [], model) != original
