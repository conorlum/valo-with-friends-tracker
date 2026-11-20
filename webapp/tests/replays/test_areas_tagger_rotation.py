"""Drive the Areas panel's actual map and reference pointer handlers (R12)."""
import copy
import json

import pytest

from tests.replays.test_map_feature_tagger import page_data, run_page


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
