"""Drive the Areas panel's actual map and reference pointer handlers (R12)."""
import base64
import copy
import json
import re
from pathlib import Path

import pytest

from tests.replays.test_map_feature_tagger import page_data, run_page


PANEL_HELPERS = """
const key='control-tagger-areas-draft-v1:Ascent';
function enter(p, opts) {const page=openPage(p.data,opts); page.TG.setMode('areas'); return page;}
function exported(page) {page.click('areasExport'); return page.downloads.at(-1).text;}
function paste(page,text) {page.el('areasPaste').value=text; page.click('areasPasteAdd');}
function point(page,kind,x,y) {
  page.el('areasCanvas').dispatch(kind,{button:0,pointerId:1,clientX:x/1024*800,clientY:y/1024*800});
}
function paint(page) {
  page.el('areasTree').children[1].click(); page.click('areaTool-brush');
  point(page,'pointerdown',128,384); point(page,'pointerup',128,384);
}
"""


def run_areas(body, data, **payload):
    return run_page(PANEL_HELPERS + body, {"data": data, **payload})


@pytest.fixture
def height_data(data):
    seeded = copy.deepcopy(data)
    packed = bytearray(256 * 256 // 8)
    for cell in (17, 500):
        packed[cell // 8] |= 1 << (cell % 8)
    seeded["height_paint"] = {"maps": {"Ascent": {"ground": base64.b64encode(packed).decode("ascii")}}}
    return seeded


@pytest.mark.parametrize("action", ["paste", "place", "shrink", "delete", "parent", "extra parents", "undo"])
def test_areas_actions_preserve_control_and_seeded_heights(height_data, action):
    got = run_areas("""function run(p) {
      const page=enter(p), T=page.T, TG=page.TG;
      // openPage does not load height paint: use the real template's loading path.
      for(const name of Object.keys(p.data.maps)) {
        const heightEntry=(p.data.height_paint.maps||{})[name];
        TG.edits[name]=T.editsFrom((p.data.tags.maps||{})[name],heightEntry);
        TG.loaded[name]=T.editsFrom((p.data.tags.maps||{})[name],heightEntry);
      }
      if(p.action==='place') paste(page,'Alcove');
      if(p.action==='extra parents') {
        page.el('areasNewName').value='Bridge'; page.el('areasAddChild').checked=true; page.click('areasAdd');
      }
      if(p.action==='undo') {page.el('areasTree').children[1].click(); paintPoint('brush');}
      function paintPoint(tool) {
        page.click('areaTool-'+tool); point(page,'pointerdown',128,384); point(page,'pointerup',128,384);
      }
      function snapshot() {
        return {tags:JSON.stringify(T.exportTags(p.data.tags,TG.maps,TG.edits)),
                heights:JSON.stringify(T.heightPaintExport({edits:TG.edits,height_paint:p.data.height_paint})),
                areas:exported(page)+'|'+page.el('areasQueue').textContent};
      }
      const before=snapshot();
      if(p.action==='paste') paste(page,'Alcove');
      if(p.action==='place') {page.click('areaTool-place'); point(page,'pointerdown',128,384);}
      if(p.action==='shrink') paintPoint('shrink');
      if(p.action==='delete') page.click('areasDelete');
      if(p.action==='parent') {
        page.el('areasTree').children[1].click(); page.el('areasParent').value='1';
        page.el('areasParent').dispatch('change');
      }
      if(p.action==='extra parents') {
        for(const option of page.el('areasAlsoParents').children) option.selected=+option.value===2;
        page.el('areasAlsoParents').dispatch('change');
      }
      if(p.action==='undo') page.click('areasUndo');
      return {before,after:snapshot(),seed:p.data.height_paint,loaded:T.packPaint(TG.edits.Ascent.heightPaints.ground)};
    }""", height_data, action=action)
    assert got["before"]["areas"] != got["after"]["areas"], action
    assert got["before"]["tags"] == got["after"]["tags"]
    assert got["before"]["heights"] == got["after"]["heights"]
    assert json.loads(got["before"]["heights"]) == got["seed"]
    assert got["loaded"] == got["seed"]["maps"]["Ascent"]["ground"]


@pytest.fixture(scope="module")
def data():
    data = page_data(future=False)
    for name, m in data["maps"].items():
        # Four cells across: the left column is the region being filled; the rest is a different name.
        cells = [[1, 1], [2, 3]] * 4
        m["areas"] = {"map": name, "grid": 4, "walk": [[1, 16]], "floors": {},
                       "draft": {"names": [{"id": i, "name": label, "parent": None,
                                            "super": "Mid", "super_source": "set"}
                                           for i, label in [(1, "Gate"), (2, "Court")]],
                                 "cells": cells, "bands": {}}, "existing": None}
    return data


@pytest.mark.parametrize("tool", ["brush", "fill"])
@pytest.mark.parametrize("turns", [1, 3])
def test_rotated_map_pointer_exports_identical_areas(data, tool, turns):
    got = run_page("""function run(p) {
      function act(k) {
        const page = openPage(p.data,{turns:k}), canvas=page.el('areasCanvas');
        canvas.getBoundingClientRect=()=>({left:40,top:30,width:2048,height:2048});
        page.TG.setMode('areas'); page.flushFrames();
        // Select Court, then paint/fill the left column at stored pixel (128,384).
        page.el('areasTree').children[1].click(); page.click('areaTool-'+p.tool);
        const display = [[128,384],[640,128],[896,640],[384,896]][k];
        const e={button:0,pointerId:1,clientX:40+display[0]*2,clientY:30+display[1]*2};
        canvas.dispatch('pointerdown',e); canvas.dispatch('pointermove',e); canvas.dispatch('pointerup',e);
        page.flushFrames(); page.click('areasExport');
        const texts=()=>canvas.getContext('2d').textCalls;
        const before=texts().length;
        page.TG.setMode('features'); page.TG.setMode('areas'); page.flushFrames();
        const after=texts().length;
        page.TG.selectMap('Bind'); page.flushFrames();
        return {text:page.downloads.at(-1).text, before,after,last:texts().slice(after)};
      }
      return {plain:act(0),rotated:act(p.turns)};
    }""", {"data": data, "tool": tool, "turns": turns})
    assert got["plain"]["text"] == got["rotated"]["text"]
    exported = json.loads(got["rotated"]["text"])
    cells = [value for value, count in exported["cells"] for _ in range(count)]
    assert cells[4] == 2  # the intended stored cell actually changed
    assert cells.count(2) == (16 if tool == "fill" else 13)
    rotated = got["rotated"]
    assert rotated["before"] > 0 and rotated["after"] > rotated["before"]
    assert {call["text"] for call in rotated["last"]} == {"Gate", "Court"}
    # Wrapper rotation plus each text's local transform is identity, including after switching maps.
    for call in rotated["last"]:
        a, b, c, d, _, _ = call["matrix"]
        expected = {1: (0, -1, 1, 0), 3: (0, 1, -1, 0)}[turns]
        assert (a, b, c, d) == pytest.approx(expected, abs=1e-12)


def test_reference_preview_keeps_image_coordinates_during_rotated_alignment(data):
    got = run_page("""function run(p) {
      function align(k) {
        const storage=memoryStorage();
        storage.setItem('control-tagger-area-reference-v1:Ascent',JSON.stringify({url:'data:image/png;base64,',matrix:[[1,0,0],[0,1,0]]}));
        const page=openPage(p.data,{turns:k,storage}); page.TG.setMode('areas'); page.flushFrames();
        page.click('areasAlign');
        const preview=page.el('areasReferencePreview'), canvas=page.el('areasCanvas');
        preview.getBoundingClientRect=()=>({left:10,top:20,width:512,height:512});
        canvas.getBoundingClientRect=()=>({left:40,top:30,width:2048,height:2048});
        for (const [x,y] of [[100,200],[500,600]]) {
          preview.dispatch('click',{clientX:10+x/2,clientY:20+y/2});
          const d=k===0?[x+50,y+30]:[1024-y-30,x+50];
          canvas.dispatch('pointerdown',{button:0,pointerId:1,clientX:40+d[0]*2,clientY:30+d[1]*2});
        }
        return JSON.parse(storage.getItem('control-tagger-area-reference-v1:Ascent')).matrix;
      }
      return [align(0),align(1)];
    }""", {"data": copy.deepcopy(data)})
    assert got == [[[1, 0, 50], [0, 1, 30]]] * 2


@pytest.mark.parametrize("turns", [1, 3])
def test_place_rotated_queue_and_parent_modes(data, turns):
    got = run_areas("""function run(p) {
      function act(turns) {
        const page=enter(p,{turns});
        // Pick is the default; a click selects Court without changing cells.
        const original=exported(page);
        point(page,'pointerdown',...[[384,128],[896,384],[640,896],[128,640]][turns]);
        const picked=page.el('areasName').value, unchanged=exported(page)===original;
        paste(page,'Alcove\\nBalcony'); const unlisted=page.el('areasMessage').textContent;
        const queued=page.el('areasQueue').textContent, before=exported(page);
        page.el('areasPlaceChild').checked=true; page.el('areasPlaceNew').checked=false;
        page.click('areaTool-place');
        const coords=[[128,384],[640,128],[896,640],[384,896]][turns];
        point(page,'pointerdown',...coords);
        const first=JSON.parse(exported(page)), advanced=page.el('areasQueue').textContent;
        page.el('areasPlaceChild').checked=false; page.el('areasPlaceNew').checked=true;
        const next=[[896,896],[128,896],[128,128],[896,128]][turns];
        point(page,'pointerdown',...next);
        const last=exported(page), empty=page.el('areasQueue').textContent;
        point(page,'pointerdown',...next);
        return {picked,unchanged,unlisted,queued,before,first,advanced,last,empty,
                message:page.el('areasMessage').textContent};
      }
      return {plain:act(0),rotated:act(p.turns)};
    }""", data, turns=turns)
    a, b = got["plain"], got["rotated"]
    assert a["picked"] == b["picked"] == "Court" and a["unchanged"] and b["unchanged"]
    assert a["unlisted"] and "Gate" in a["unlisted"] and "Court" in a["unlisted"]
    assert "Alcove" in a["queued"] and "Balcony" in a["queued"]
    assert len(json.loads(a["before"])["names"]) == 2
    assert a["first"]["names"][-1]["parent"] == 2
    assert "Alcove" not in a["advanced"] and "Balcony" in a["advanced"]
    assert json.loads(a["last"])["names"][-1]["parent"] is None
    assert "queue" in a["message"].lower()
    assert a["first"] == b["first"] and a["last"] == b["last"]


@pytest.mark.parametrize("action", ["delete", "shrink", "brush", "fill"])
def test_resize_delete_and_multimove_stroke_undo_exactly(data, action):
    got = run_areas("""function run(p) {
      const page=enter(p), original=exported(page);
      if(p.action==='delete') page.click('areasDelete');
      else {
        page.click('areaTool-'+p.action);
        if(p.action==='brush'||p.action==='fill') page.el('areasTree').children[1].click();
        point(page,'pointerdown',128,128);
        point(page,'pointermove',128,384); point(page,'pointermove',128,640);
        point(page,'pointerup',128,640);
      }
      const edited=exported(page); page.click('areasUndo');
      const undone=exported(page), saved=page.storage.getItem(key);
      page.window.taggerAreas.undo();
      return {original,edited,undone,saved,empty:exported(page)};
    }""", data, action=action)
    assert got["edited"] != got["original"]
    assert got["undone"] == got["original"] == got["empty"]
    assert json.loads(got["saved"])["cells"] == json.loads(got["original"])["cells"]


def test_restore_is_explicit_and_discard_removes_draft(data):
    got = run_areas("""function run(p) {
      let page=enter(p); const original=exported(page); paint(page);
      const edited=exported(page), saved=page.storage.getItem(key);
      page=page.reopen(); page.TG.setMode('areas');
      const pending=!page.el('areasRestoreRow').hidden, before=exported(page);
      page.click('areasRestore'); const restored=exported(page), hidden=page.el('areasRestoreRow').hidden;
      const restoredSaved=page.storage.getItem(key);
      page=page.reopen(); page.TG.setMode('areas'); page.click('areasDiscard');
      return {original,edited,saved,pending,before,restored,restoredSaved,hidden,removed:page.storage.getItem(key)};
    }""", data)
    assert got["pending"] and got["before"] == got["original"]
    assert got["restored"] == got["edited"] != got["original"] and got["hidden"]
    assert got["restoredSaved"] == got["saved"]
    assert got["removed"] is None


@pytest.mark.parametrize("choice", ["areasRestore", "areasDiscard"])
def test_pending_draft_survives_edits_and_reopen(data, choice):
    got = run_areas("""function run(p) {
      let page=enter(p); paint(page); const first=page.storage.getItem(key), firstText=exported(page);
      page=page.reopen(); page.TG.setMode('areas'); paste(page,'Alcove');
      const newer=page.storage.getItem(key+':newer'), preserved=page.storage.getItem(key);
      page=page.reopen(); page.TG.setMode('areas');
      const pending=!page.el('areasRestoreRow').hidden; page.click(p.choice);
      return {first,firstText,newer,preserved,pending,stored:page.storage.getItem(key),
              second:page.storage.getItem(key+':newer'),text:exported(page)};
    }""", data, choice=choice)
    assert got["pending"] and got["preserved"] == got["first"] and got["newer"]
    assert got["second"] is None
    if choice == "areasRestore":
        assert got["text"] == got["firstText"]
    else:
        assert got["stored"] == got["newer"]


@pytest.mark.parametrize("same_session", [False, True])
@pytest.mark.parametrize("action", ["place", "cells", "bands", "names", "parents", "hand", "queue"])
def test_discard_promoted_draft_stays_protected_until_model_matches(data, action, same_session):
    seeded = copy.deepcopy(data)
    seeded["maps"]["Ascent"]["areas"]["floors"] = {"0": [[0, 10], [20, 30]]}
    got = run_areas("""function run(p) {
      let page=enter(p); paint(page); const first=page.storage.getItem(key);
      page=page.reopen(); page.TG.setMode('areas');
      if(p.action==='place') {
        paste(page,'Alcove'); page.click('areaTool-place'); point(page,'pointerdown',128,384);
      } else if(p.action==='cells') paint(page);
      else if(p.action==='bands'||p.action==='hand') {
        if(p.action==='bands') {
          page.el('areasTree').children[1].click();
          page.el('areasFloor').value='1'; page.el('areasFloor').dispatch('change');
        }
        page.click('areaTool-brush'); point(page,'pointerdown',128,128); point(page,'pointerup',128,128);
      } else if(p.action==='names') {
        page.el('areasName').value='Arch'; page.click('areasRename');
      } else if(p.action==='parents') {
        page.el('areasTree').children[1].click();
        page.el('areasParent').value='1'; page.el('areasParent').dispatch('change');
      } else paste(page,'Alcove');
      const newer=page.storage.getItem(key+':newer'), preserved=page.storage.getItem(key);
      if(!p.same_session) {page=page.reopen(); page.TG.setMode('areas');}
      page.click('areasDiscard');
      const promoted=page.storage.getItem(key), removed=page.storage.getItem(key+':newer'),
            pending=!page.el('areasRestoreRow').hidden, message=page.el('areasMessage').textContent;
      paste(page,'Tunnel');
      const afterEdit=page.storage.getItem(key), second=page.storage.getItem(key+':newer');
      page=page.reopen(); page.TG.setMode('areas'); page.click('areasRestore');
      return {first,newer,preserved,promoted,removed,pending,message,afterEdit,second,
              restored:page.storage.getItem(key),hidden:page.el('areasRestoreRow').hidden,
              cleared:page.storage.getItem(key+':newer')};
    }""", seeded, action=action, same_session=same_session)
    assert got["preserved"] == got["first"] and got["newer"]
    assert got["promoted"] == got["newer"] and got["removed"] is None
    assert got["pending"] is not same_session
    assert got["hidden"] and got["cleared"] is None
    newer = json.loads(got["newer"])
    if action == "place":
        placed = next(row for row in newer["names"] if row["name"] == "Alcove")
        assert any(owner == placed["id"] for owner, _ in newer["cells"])
    if same_session:
        assert got["second"] is None and got["afterEdit"] != got["promoted"]
        expected = {**newer, "queue": newer["queue"] + ["Tunnel"]}
        assert json.loads(got["afterEdit"]) == expected == json.loads(got["restored"])
    else:
        assert "restore" in got["message"].lower() and "saved draft" in got["message"].lower()
        assert got["second"] and got["afterEdit"] == got["promoted"]
        assert json.loads(got["restored"]) == newer


@pytest.mark.parametrize("failure", ["failWrites", "failReads", "badJSON", "incompatible"])
def test_storage_failure_does_not_interrupt_painting(data, failure):
    got = run_areas("""function run(p) {
      const storage=memoryStorage();
      if(p.failure==='badJSON') storage.setItem(key,'{broken');
      if(p.failure==='incompatible') storage.setItem(key,JSON.stringify({v:1,map:'Other',grid:99}));
      storage.opts[p.failure]=true;
      const page=enter(p,{storage}), initial=page.el('areasMessage').textContent;
      const pending=!page.el('areasRestoreRow').hidden, original=exported(page);
      if(p.failure==='incompatible') page.click('areasRestore');
      const refusal=page.el('areasMessage').textContent;
      paint(page);
      return {initial,pending,refusal,original,edited:exported(page),message:page.el('areasMessage').textContent,
              first:storage.m.get(key),newer:storage.m.get(key+':newer')};
    }""", data, failure=failure)
    assert got["edited"] != got["original"]
    if failure == "failWrites":
        assert "storage" in got["message"].lower()
    elif failure == "failReads":
        assert got["initial"] and got["pending"] and got["newer"]
    elif failure == "badJSON":
        assert got["initial"] and got["pending"] and got["first"] == "{broken"
    else:
        assert got["pending"] and "restore" in got["refusal"].lower()
        assert "grid" in got["refusal"].lower() and json.loads(got["first"])["map"] == "Other"


def test_failed_reads_preserve_saved_bytes_and_failed_promotion_preserves_newer(data):
    got = run_areas("""function run(p) {
      let page=enter(p); paste(page,'Alcove'); const storage=page.storage, first=storage.getItem(key);
      storage.opts.failReads=true; page=page.reopen(); page.TG.setMode('areas'); paint(page);
      const preserved=storage.m.get(key), newer=storage.m.get(key+':newer');
      page.click('areasDiscard'); const unread=storage.m.get(key);
      storage.opts.failReads=false; storage.opts.failWrites=true; page.click('areasDiscard');
      const failed={first:storage.m.get(key),newer:storage.m.get(key+':newer'),pending:!page.el('areasRestoreRow').hidden};
      storage.opts.failWrites=false; page.click('areasDiscard');
      return {first,preserved,newer,unread,failed,promoted:storage.getItem(key),second:storage.getItem(key+':newer')};
    }""", data)
    assert got["first"] == got["preserved"] == got["unread"] == got["failed"]["first"]
    assert got["newer"] and got["failed"]["newer"] == got["newer"] and got["failed"]["pending"]
    assert got["promoted"] == got["newer"] and got["second"] is None


def test_export_queue_extra_parent_set_and_refusal(data):
    got = run_areas("""function run(p) {
      const page=enter(p); paste(page,'Alcove'); exported(page);
      const queueMessage=page.el('areasMessage').textContent;
      page.el('areasNewName').value='Bridge'; page.el('areasAddChild').checked=true; page.click('areasAdd');
      const select=page.el('areasAlsoParents');
      for(const opt of select.children) opt.selected=+opt.value===2;
      select.dispatch('change'); const linked=exported(page);
      for(const opt of select.children) opt.selected=+opt.value===1;
      select.dispatch('change');
      const refused=page.el('areasMessage').textContent, rerendered=select.children.map(o=>[+o.value,!!o.selected]);
      page.click('areasUndo');
      return {queueMessage,linked,refused,rerendered,undone:exported(page)};
    }""", data)
    assert "Alcove" in got["queueMessage"]
    assert json.loads(got["linked"])["names"][-1]["also_parents"] == [2]
    assert got["refused"] and got["rerendered"] == [[1, False], [2, True]]
    assert "also_parents" not in json.loads(got["undone"])["names"][-1]


def test_refused_place_keeps_queue_and_cells(data):
    got = run_areas("""function run(p) {
      const page=enter(p); paint(page); paste(page,'Alcove');
      const before=exported(page); page.click('areaTool-place'); point(page,'pointerdown',128,384);
      const refused=page.el('areasMessage').textContent;
      return {before,after:exported(page),refused,queue:page.el('areasQueue').textContent};
    }""", data)
    assert got["before"] == got["after"]
    assert "hand-painted" in got["refused"] and "Alcove" in got["queue"]


@pytest.mark.parametrize("mismatch", ["grid", "walk_hash"])
def test_incompatible_restore_keeps_both_slots_and_current_model(data, mismatch):
    got = run_areas("""function run(p) {
      let page=enter(p); paint(page); const storage=page.storage, saved=JSON.parse(storage.getItem(key));
      saved[p.mismatch]=p.mismatch==='grid'?8:'changed-mask'; storage.setItem(key,JSON.stringify(saved));
      const first=storage.getItem(key);
      page=page.reopen(); page.TG.setMode('areas'); paste(page,'Alcove');
      const second=storage.getItem(key+':newer'), before=exported(page);
      page.click('areasRestore'); const refusal=page.el('areasMessage').textContent;
      return {first,second,before,after:exported(page),refusal,
              keptFirst:storage.getItem(key),keptSecond:storage.getItem(key+':newer'),
              pending:!page.el('areasRestoreRow').hidden};
    }""", data, mismatch=mismatch)
    assert got["first"] == got["keptFirst"] and got["second"] == got["keptSecond"]
    assert got["before"] == got["after"] and got["pending"] and "incompatible" in got["refusal"]


def test_empty_draft_is_not_pending_and_autosave_is_per_map(data):
    got = run_areas("""function run(p) {
      const storage=memoryStorage(); storage.setItem(key,''); const page=enter(p,{storage});
      const pending=!page.el('areasRestoreRow').hidden; paste(page,'Alcove'); const first=storage.getItem(key);
      page.TG.selectMap('Bind'); const otherPending=!page.el('areasRestoreRow').hidden;
      paste(page,'Tunnel'); const other=storage.getItem('control-tagger-areas-draft-v1:Bind');
      return {pending,otherPending,first,other,kept:storage.getItem(key),second:storage.getItem(key+':newer')};
    }""", data)
    assert not got["pending"] and not got["otherPending"]
    assert got["first"] == got["kept"] and got["second"] is None
    assert json.loads(got["first"])["queue"] == ["Alcove"]
    assert json.loads(got["other"])["queue"] == ["Tunnel"]


@pytest.mark.parametrize("transition", ["Escape", "s", "e", "p", "c", "w", "u", "b", "k", "a", "f", "m", "o", "h", "t", "map"])
@pytest.mark.parametrize("tool", ["brush", "erase", "shrink"])
@pytest.mark.parametrize("ending", ["pointerup", "pointercancel"])
def test_transition_finishes_stroke_on_owning_map_and_restores(data, transition, tool, ending):
    template = (Path(__file__).resolve().parents[2] / "scripts/control_tagger.template.html").read_text(encoding="utf-8")
    match = re.search(r'  document.addEventListener\("keydown", function \(e\) \{(.*?)^  \}\);', template, re.M | re.S)
    assert match
    got = run_areas("""function run(p) {
      const page=enter(p), canvas=page.el('areasCanvas'), original=exported(page);
      if(p.tool==='brush') page.el('areasTree').children[1].click();
      page.click('areaTool-'+p.tool); point(page,'pointerdown',128,128);
      point(page,'pointermove',128,384);
      const edited=exported(page), active=canvas.hasPointerCapture(1);
      if(p.transition==='map') page.TG.selectMap('Bind');
      else {
        const handler=new Function('e','window','state','DATA','setMode',p.branch);
        handler({key:p.transition,preventDefault(){}},page.window,page.TG.state,p.data,m=>page.TG.setMode(m));
      }
      const saved=page.storage.getItem(key), captured=canvas.hasPointerCapture(1),
            released=canvas.releasedPointers.slice(), destination=exported(page);
      point(page,'pointermove',128,640); point(page,p.ending,128,640);
      const afterLate=exported(page), other=page.storage.getItem('control-tagger-areas-draft-v1:Bind');
      page.TG.selectMap('Ascent'); page.TG.setMode('areas'); page.flushFrames();
      const returned=exported(page), reopened=page.reopen(); reopened.TG.setMode('areas');
      const pending=!reopened.el('areasRestoreRow').hidden, beforeRestore=exported(reopened);
      reopened.click('areasRestore'); const restored=exported(reopened);
      page.click('areasUndo'); const undone=exported(page); page.click('areasUndo');
      return {original,edited,active,saved,captured,released,destination,afterLate,other,returned,
              pending,beforeRestore,restored,undone,emptyUndo:exported(page)};
    }""", data, transition=transition, tool=tool, ending=ending, branch=match.group(1))
    assert got["active"] and got["edited"] != got["original"]
    assert got["saved"] is not None
    assert json.loads(got["saved"])["map"] == "Ascent"
    assert json.loads(got["saved"])["cells"] == json.loads(got["edited"])["cells"]
    assert not got["captured"] and got["released"] == [1]
    assert got["destination"] == got["afterLate"] and got["other"] is None
    assert got["returned"] == got["restored"] == got["edited"]
    assert got["pending"] and got["beforeRestore"] == got["original"]
    assert got["undone"] == got["emptyUndo"] == got["original"]


@pytest.mark.parametrize("transition", ["mode", "map"])
@pytest.mark.parametrize("storage_case", ["pending", "failWrites", "failReads"])
def test_transition_autosave_respects_storage_failure_and_pending_draft(data, transition, storage_case):
    got = run_areas("""function run(p) {
      let page=enter(p); paint(page); const first=page.storage.getItem(key), storage=page.storage;
      if(p.storage_case==='pending') {page=page.reopen(); page.TG.setMode('areas');}
      else storage.opts[p.storage_case]=true;
      const original=exported(page); page.el('areasTree').children[1].click(); page.click('areaTool-brush');
      point(page,'pointerdown',128,128); const edited=exported(page);
      if(p.transition==='map') page.TG.selectMap('Bind'); else page.TG.setMode('select');
      point(page,'pointerup',128,128);
      page.TG.selectMap('Ascent'); page.TG.setMode('areas');
      const returned=exported(page), note=page.el('areasMessage').textContent;
      storage.opts.failWrites=false; storage.opts.failReads=false;
      const kept=storage.getItem(key), newer=storage.getItem(key+':newer');
      page=page.reopen(); page.TG.setMode('areas'); page.click('areasRestore');
      return {first,original,edited,returned,note,kept,newer,restored:exported(page)};
    }""", data, transition=transition, storage_case=storage_case)
    assert got["returned"] == got["edited"] != got["original"]
    if storage_case == "pending":
        assert got["kept"] == got["first"] and got["newer"] is not None
        assert json.loads(got["newer"])["cells"] == json.loads(got["edited"])["cells"]
        assert json.loads(got["restored"])["cells"] == json.loads(got["first"])["cells"]
    elif storage_case == "failWrites":
        assert "storage" in got["note"].lower() and got["kept"] == got["first"]
    else:
        assert json.loads(got["kept"])["cells"] == json.loads(got["edited"])["cells"]
        assert got["restored"] == got["edited"]


@pytest.mark.parametrize("modifier", ["ctrlKey", "metaKey"])
@pytest.mark.parametrize("tool", ["brush", "erase", "shrink"])
@pytest.mark.parametrize("ending", ["pointerup", "pointercancel"])
def test_keyboard_undo_cancels_active_stroke_and_next_stroke_has_checkpoint(data, modifier, tool, ending):
    template = (Path(__file__).resolve().parents[2] / "scripts/control_tagger.template.html").read_text(encoding="utf-8")
    match = re.search(r'  document.addEventListener\("keydown", function \(e\) \{(.*?)^  \}\);', template, re.M | re.S)
    assert match
    got = run_areas("""function run(p) {
      const page=enter(p), canvas=page.el('areasCanvas'), original=exported(page);
      if(p.tool==='brush') page.el('areasTree').children[1].click();
      page.click('areaTool-'+p.tool); point(page,'pointerdown',128,128);
      const active=canvas.hasPointerCapture(1), painted=exported(page);
      const handler=new Function('e','window','state','DATA','setMode',p.branch);
      const e={key:'z',ctrlKey:false,metaKey:false,preventDefault(){}}; e[p.modifier]=true;
      handler(e,page.window,page.TG.state,p.data,m=>page.TG.setMode(m));
      const undone=exported(page), captured=canvas.hasPointerCapture(1), released=canvas.releasedPointers.slice();
      point(page,'pointermove',128,384); point(page,p.ending,128,640);
      point(page,'pointermove',128,640);
      const afterMoves=exported(page), saved=page.storage.getItem(key);
      page.click('areasUndo'); const emptyUndo=exported(page);
      point(page,'pointerdown',128,128); point(page,'pointermove',128,384);
      point(page,'pointerup',128,384);
      const fresh=exported(page); page.click('areasUndo');
      return {original,active,painted,undone,captured,released,afterMoves,saved,emptyUndo,fresh,
              freshUndone:exported(page),freshSaved:page.storage.getItem(key)};
    }""", data, modifier=modifier, tool=tool, ending=ending, branch=match.group(1))
    assert got["active"] and got["painted"] != got["original"]
    assert got["undone"] == got["original"]
    assert not got["captured"] and got["released"] == [1]
    assert got["afterMoves"] == got["emptyUndo"] == got["original"]
    assert json.loads(got["saved"])["cells"] == json.loads(got["original"])["cells"]
    assert got["fresh"] != got["original"] == got["freshUndone"]
    assert json.loads(got["freshSaved"])["cells"] == json.loads(got["original"])["cells"]


@pytest.mark.parametrize("modifier", ["ctrlKey", "metaKey"])
def test_template_keyboard_undo_in_areas_keeps_mode(data, modifier):
    template = (Path(__file__).resolve().parents[2] / "scripts/control_tagger.template.html").read_text(encoding="utf-8")
    match = re.search(r'  document.addEventListener\("keydown", function \(e\) \{(.*?)^  \}\);', template, re.M | re.S)
    assert match
    got = run_areas("""function run(p) {
      const page=enter(p); paint(page); let calls=0, prevented=0;
      const originalUndo=page.window.taggerAreas.undo;
      const window={taggerAreas:{undo(){calls++; originalUndo();}}};
      const state={mode:'areas'}, DATA=p.data, setMode=m=>state.mode=m;
      const handler=new Function('e','window','state','DATA','setMode',p.branch);
      const e={key:'z',ctrlKey:false,metaKey:false,preventDefault(){prevented++;}}; e[p.modifier]=true;
      handler(e,window,state,DATA,setMode);
      return {calls,prevented,mode:state.mode,text:exported(page),saved:page.storage.getItem(key)};
    }""", data, branch=match.group(1), modifier=modifier)
    assert got["calls"] == got["prevented"] == 1 and got["mode"] == "areas"
    assert json.loads(got["text"])["cells"] == data["maps"]["Ascent"]["areas"]["draft"]["cells"]
    assert json.loads(got["saved"])["cells"] == json.loads(got["text"])["cells"]
