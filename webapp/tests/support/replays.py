"""Use the existing synthetic link fixture through the production condenser/store."""
import copy
from tests.replays.replay_synthetic import SyntheticMatch
from tests.replays.test_replay_store import add_match, LINK_KILLS, ROUNDS, WINNERS
from app.replays.condense import condense_export_dir
from app.replays.store import store_replay
from app.models.replay import Replay


def stored_replay(factory, tmp_path):
    synthetic = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(LINK_KILLS), winners=dict(WINNERS))
    condensed = condense_export_dir(synthetic.write(tmp_path/'export'), source_sha256=synthetic.source_sha256)
    with factory() as db:
        match, ids = add_match(db)
        result = store_replay(db, condensed, source='local')
        replay = db.get(Replay, result.replay_id)
        assert replay.link_status == 'linked'
        return replay.match_uuid, replay.id, ids, condensed
