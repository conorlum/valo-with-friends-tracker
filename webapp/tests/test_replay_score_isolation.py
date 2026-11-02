"""T11: replay replacement leaves every persisted Impact field untouched."""
from dataclasses import replace
from app.models import ImpactScore, Round, Match
from app.scoring.impact import PERSISTED_FIELDS
from app.replays.store import store_replay
from tests.support.site import site  # noqa: F401
from tests.support.replays import stored_replay


def test_replacement_never_rewrites_scores(site, tmp_path):
    _, factory = site
    uuid, rid, ids, condensed = stored_replay(factory, tmp_path)
    with factory() as db:
        from app.models.replay import ReplayPlayer
        mpid = db.query(ReplayPlayer).filter_by(replay_id=rid,slot=0).one().match_player_id
        rnd = db.query(Round).join(Match).filter(Match.external_id == str(uuid)).first()
        db.add(ImpactScore(round_id=rnd.id,match_player_id=mpid,kill_impact=123,death_impact=45,impact=78))
        db.commit()
        before = [tuple(getattr(row,k) for k in ('round_id','match_player_id',*PERSISTED_FIELDS))
                  for row in db.query(ImpactScore)]
        newer = replace(condensed,source_sha256='f'*64)
        result = store_replay(db,newer,source='local',replace=True)
        assert result.action == 'replaced'
    with factory() as fresh:
        assert [tuple(getattr(row,k) for k in ('round_id','match_player_id',*PERSISTED_FIELDS))
                for row in fresh.query(ImpactScore)] == before
