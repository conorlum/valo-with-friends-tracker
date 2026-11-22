"""R2/R11/R12: one geometry contract across the three map surfaces."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


def node(script):
    assert NODE, "node is required for rotation agreement"
    result = subprocess.run([NODE, "-e", script], cwd=WEBAPP, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def reference(x, y, k, size=1024):
    return [(x, y), (size-y, x), (size-x, size-y), (y, size-x)][k % 4]


def test_all_surfaces_agree_with_quarter_turn_reference():
    got = node("""
      const surfaces = [require('./app/static/js/replay.js'), require('./app/static/js/gap_patterns.js'),
                        require('./scripts/control_tagger_core.js')];
      const rows = [];
      for (const k of [-5,-1,0,1,2,3,4,7]) for (const [x,y] of [[0,0],[1023,0],[10,700],[512,512]]) {
        rows.push({k,x,y, values:surfaces.map(s => ({r:s.rotatePoint(x,y,1024,k),
          u:s.unrotatePoint(x,y,1024,k), back:s.unrotatePoint(...Object.values(s.rotatePoint(x,y,1024,k)),1024,k)}))});
      }
      console.log(JSON.stringify(rows));
    """)
    for row in got:
        for value in row["values"]:
            assert value["r"] == dict(zip(("x", "y"), reference(row["x"], row["y"], row["k"])))
            assert value["u"] == dict(zip(("x", "y"), reference(row["x"], row["y"], -row["k"])))
            assert value["back"] == {"x": row["x"], "y": row["y"]}


@pytest.mark.parametrize("width", [2048, 512])
def test_pointer_inverse_includes_zoom_and_pan(width):
    for k in range(4):
        x, y = reference(300, 200, k)
        client_x, client_y = 40 + x / 1024 * width, 30 + y / 1024 * width
        got = node(f"""
          const T = require('./scripts/control_tagger_core.js');
          console.log(JSON.stringify(T.pointerToMap({{left:40,top:30,width:{width},height:{width}}},
            {client_x},{client_y},1024,{k})));
        """)
        assert got == pytest.approx({"x": 300, "y": 200}, abs=.5)
        if k == 0:
            assert got == {"x": (client_x-40)/width*1024, "y": (client_y-30)/width*1024}


def test_gap_routes_rotate_but_choke_labels_are_upright_above_dot():
    got = node("""
      const G = require('./app/static/js/gap_patterns.js');
      let matrix = [1,0,0,1,0,0], click;
      const calls = [], point = (x,y) => [matrix[0]*x+matrix[2]*y+matrix[4],matrix[1]*x+matrix[3]*y+matrix[5]];
      const ctx = new Proxy({setTransform(...m){matrix=m;}, moveTo(x,y){calls.push(['route',point(x,y)]);},
        arc(x,y){calls.push(['dot',point(x,y)]);},
        fillText(t,x,y){calls.push(['text',t,point(x,y),matrix.slice()]);},
        drawImage(){calls.push(['image',point(0,0)]);}}, {get(t,p){return p in t?t[p]:()=>{};}});
      const drawing = {map:'Ascent',asset:'Ascent',rotation:1,params:{},chokes:{1:{name:'Gate',x:100,y:50}}};
      const doc = {getElementById(){return {textContent:JSON.stringify(drawing)};},
        querySelector(s){return s==='[data-gaps-canvas]'?{width:1024,getContext:()=>ctx}:{appendChild(){}};},
        createElement(){return {appendChild(){}};},addEventListener(k,fn){click=fn;}};
      global.Image = class {constructor(){this.complete=true;this.naturalWidth=1024;}};
      global.fetch = () => Promise.resolve({ok:true,json:()=>Promise.resolve({rows:[{kind:'predicted',route:[[[0,100,50],[1,110,60]]]}]})});
      G.wire(doc);
      click({target:{closest:()=>({hasAttribute:()=>false,getAttribute:k=>k==='data-seq'?'1':'Gate'})}});
      setImmediate(()=>console.log(JSON.stringify(calls)));
    """)
    assert ["route", [974, 100]] in got
    assert ["image", [1024, 0]] in got
    assert ["dot", [974, 100]] in got
    assert ["text", "Gate", [974, 94], [1, 0, 0, 1, 0, 0]] in got
