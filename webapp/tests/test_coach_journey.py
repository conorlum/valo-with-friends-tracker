"""T13: deterministic snapshot -> checked report -> storage -> real page -> form feedback."""
import json
from app.coach import snapshot, runner
from app.services import coach, replays
from app.models import Player
from app.models.coach import CoachReport, CoachPoint, CoachFeedback
from app.models.replay import Replay
from tests.coach.test_runner import Model, BOOK, good_point
from tests.support.site import site  # noqa: F401
from tests.support.replays import stored_replay


def test_offline_report_and_feedback_round_trip_through_the_app(site, tmp_path):
    client, factory = site
    uuid, rid, ids, condensed = stored_replay(factory, tmp_path)
    with factory() as db:
        context = replays.page_context(db, db.get(Replay, rid))
    data = snapshot.assemble(context, condensed.rounds, {rn: ('not_ready', False, None) for rn in condensed.rounds})
    folder = tmp_path/'snapshot'; snapshot.save(data, folder)
    snap = snapshot.load(folder)
    def reply(evidence):
        point = good_point([evidence]); point['title'] = 'Held position <script>bad()</script>'
        return json.dumps({'points': [point]})
    model = Model(snap, folder, [reply])
    payload = runner.generate(snap, 0, folder, BOOK, model, 'offline-test')
    with factory() as db:
        report = coach.store_report(db, db.get(Replay, rid).match_id, ids[0], payload)
        report_id, version = report.id, report.version
        point_id = coach.points(db, report_id)[0].id
        name = db.get(Player, ids[0]).display_name
    response = client.get(f'/replays/{uuid}/coach')
    assert response.status_code == 200
    assert '&lt;script&gt;bad()&lt;/script&gt;' in response.text
    assert 'Held position <script>' not in response.text
    assert 'control' in response.text.lower()  # missing-source disclosure survives the snapshot/store seam
    assert client.post('/login', data={'display_name':name}).status_code == 303
    response = client.post(f'/replays/{uuid}/coach/points/{point_id}/feedback',
                           data={'verdict_0':'wrong', 'note_0':'Moment needs review', 'note':'Overall note'})
    assert response.status_code == 303 and f'version={version}' in response.headers['location']
    fresh_page = client.get(response.headers['location'])
    assert fresh_page.status_code == 200 and 'Moment needs review' in fresh_page.text
    with factory() as fresh:
        assert fresh.query(CoachReport).count() == 1
        feedback = fresh.query(CoachFeedback).order_by(CoachFeedback.id).all()
        assert {(f.instance_index,f.note) for f in feedback} == {(0,'Moment needs review'),(None,'Overall note')}


def test_unknown_report_version_falls_back_and_foreign_feedback_is_refused(site, tmp_path):
    from tests.coach.test_coach_store import payload
    client, factory = site
    uuid, rid, ids, _ = stored_replay(factory, tmp_path)
    with factory() as db:
        replay = db.get(Replay, rid)
        coach.store_report(db, replay.match_id, ids[0], payload())
        coach.store_report(db, replay.match_id, ids[0], payload(playbook_version='new'))
    page = client.get(f'/replays/{uuid}/coach?version=999')
    assert page.status_code == 200 and 'new' in page.text
    assert client.post(f'/replays/{uuid}/coach/points/999999/feedback', data={'note':'no'}).status_code == 403
    with factory() as db: name = db.get(Player, ids[0]).display_name
    client.post('/login', data={'display_name':name})
    assert client.post(f'/replays/{uuid}/coach/points/999999/feedback', data={'note':'no'}).status_code == 404
