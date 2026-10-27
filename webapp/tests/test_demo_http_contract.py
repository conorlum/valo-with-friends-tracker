"""T09: real requests cannot mutate demo data or expose disabled route families."""
import pytest
import os
import json
import subprocess
import sys
from pathlib import Path
from app.config import settings
from app.db import Base
from app.models import Player, Friendship, AccountGroup
from app.models.replay import Replay, ReplayDeletion
from tests.support.site import site  # noqa: F401
from tests.support.replays import stored_replay


@pytest.mark.parametrize('method,path,data,status', [
    ('POST','/friends/add',{'display_name':'Other'},403), ('POST','/friends/remove',{'display_name':'Other'},403),
    ('GET','/accounts',None,403), ('POST','/accounts/add',{'display_name':'Other'},403),
    ('POST','/accounts/remove',{'display_name':'Other'},403), ('POST','/accounts/make-main',{'display_name':'Other'},403),
    ('POST','/accounts/label',{'display_name':'Other','label':'renamed'},403),
    ('GET','/replays',None,404), ('GET','/replays/upload',None,404),
    ('POST','/replays/upload/code',{'code':'test-code'},404),
    ('GET','/admin/replays/archive',None,404),
    ('GET','/gaps',None,404),
])
def test_demo_route_families_refuse_without_mutation(site, monkeypatch, method, path, data, status):
    client, factory = site
    with factory() as db: db.add_all([Player(display_name='Viewer'),Player(display_name='Other')]); db.commit()
    client.post('/login', data={'display_name':'Viewer'})
    monkeypatch.setattr(settings,'demo_mode',True)
    monkeypatch.setattr(settings,'replay_worker_url','http://must-not-contact.invalid')
    monkeypatch.setattr(settings,'replay_upload_code','test-code')
    monkeypatch.setattr(settings,'replay_admin_token','test-admin')
    response = client.request(method,path,data=data,headers={'authorization':'Bearer test-admin'})
    assert response.status_code == status
    with factory() as fresh:
        assert fresh.query(Player).count() == 2
        assert fresh.query(Friendship).count() == 0 and fresh.query(AccountGroup).count() == 0


def database_snapshot(factory):
    with factory() as db:
        return {table.name: db.execute(table.select().order_by(*table.primary_key.columns)).all()
                for table in Base.metadata.sorted_tables}


@pytest.mark.parametrize('suffix,status', [('',200), ('/coach',200), ('/coach/prompt',200),
    ('/moments.json',200), ('/1.json',200), ('/1/gaps.json',200), ('/1/control.bin',202),
    ('/control/players.json',200)])
def test_demo_refuses_the_same_accessible_linked_replay(site, monkeypatch, tmp_path, suffix, status):
    client, factory = site
    uuid, _, _, _ = stored_replay(factory, tmp_path)
    path = f'/replays/{uuid}{suffix}'
    # Prove resource existence, validity, linking, and map prerequisites first.
    assert client.get(path).status_code == status
    before = database_snapshot(factory)
    monkeypatch.setattr(settings,'demo_mode',True)
    assert client.get(path).status_code == 404
    assert database_snapshot(factory) == before


def test_demo_admin_delete_preserves_an_existing_replay_and_no_tombstone(site, monkeypatch, tmp_path):
    client, factory = site
    uuid, rid, _, _ = stored_replay(factory, tmp_path)
    monkeypatch.setattr(settings,'replay_admin_token','test-admin')
    headers = {'authorization':'Bearer test-admin'}
    before = database_snapshot(factory)
    monkeypatch.setattr(settings,'demo_mode',True)
    assert client.post('/admin/replays/delete',json={'match_uuid':uuid},headers=headers).status_code == 404
    assert database_snapshot(factory) == before
    # The identical authenticated request is valid and destructive outside demo
    # mode. Run it last so refusal must have retained the original resource.
    monkeypatch.setattr(settings,'demo_mode',False)
    response = client.post('/admin/replays/delete',json={'match_uuid':uuid},headers=headers)
    assert response.status_code == 200 and response.json()['replay_deleted'] is True
    with factory() as db:
        assert db.get(Replay,rid) is None and db.get(ReplayDeletion,uuid) is not None


@pytest.mark.parametrize('demo,name', [('true','ValoMaths'), ('false','ValoWithFriendsTracker')])
def test_branding_is_initialized_from_configuration_in_a_fresh_process(demo, name):
    code = ("import json; from app.main import app; from app.templates import templates; "
            "from tests.support.http import Client; "
            "print(json.dumps({'title':app.title,'name':templates.env.globals['site_name'],"
            "'demo':templates.env.globals['demo_mode'],'html':Client(app).get('/').text}))")
    result = subprocess.run([sys.executable,'-c',code], cwd=Path(__file__).parents[1],
        env={**os.environ,'DATABASE_URL':'sqlite://','DEMO_MODE':demo,'SITE_NAME':name,'MAINTENANCE_MODE':'0'},
        capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['title'] == data['name'] == name
    assert data['demo'] is (demo == 'true')
    assert ('Demo &ndash; sample data' in data['html']) is (demo == 'true')
