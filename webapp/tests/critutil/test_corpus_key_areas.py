from app.coach import areas
from app.services import replay_util
from tests.coach.test_areas import painted_object, write_paint


def test_corpus_key_keeps_its_original_value_until_areas_are_reviewed(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
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
