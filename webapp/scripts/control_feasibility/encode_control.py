"""Stage 0b (docs/replay-map-control-plan.md, Storage): real bytes for one round's control, and the
browser's decode time.

Feasibility tooling, not product code. Reads `<out>/engine/<stem>.npz` (engine_proto.py) and encodes:

- **states:** one code per walkable cell per 16 Hz tick. A checkpoint (4 bits per cell) every
  CHECKPOINT_S, and in between only the cells that changed (varint index gap, then the code).
- **coverage / control masks:** one bitmask per player per tick (control at 16 Hz or 2 Hz), a full
  bitmask at each checkpoint and the flipped cells' index gaps in between.

Each stream is gzipped (level 9) the way the endpoint would serve it. Then it builds a page that
decodes every tick in the browser (DecompressionStream plus a plain JS loop) and times it headless.

Writes `<out>/engine/<stem>.sizes.json` and `<stem>.decode.html`.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\encode_control.py <out_dir> <stem>
    .venv\\Scripts\\python.exe scripts\\control_feasibility\\encode_control.py <out_dir> <stem> --browser
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

HZ = 16
CHECKPOINTS_S = (1, 2, 4)


def varint(n: int, out: bytearray) -> None:
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)


def encode_states(states: np.ndarray, every: int) -> bytes:
    out = bytearray()
    prev = None
    for i, frame in enumerate(states):
        if i % every == 0:
            out.append(0)
            padded = np.append(frame, 0) if len(frame) % 2 else frame
            out += (padded[0::2] << 4 | padded[1::2]).astype(np.uint8).tobytes()
        else:
            out.append(1)
            changed = np.flatnonzero(frame != prev)
            varint(len(changed), out)
            last = -1
            for c in changed:
                varint(int(c - last - 1), out)
                out.append(int(frame[c]))
                last = c
        prev = frame
    return bytes(out)


def encode_masks(masks: np.ndarray, every: int) -> bytes:
    """masks: ticks x players x cells (bool)."""
    out = bytearray()
    prev = None
    for i, frame in enumerate(masks):
        for p, mask in enumerate(frame):
            if i % every == 0:
                out += np.packbits(mask).tobytes()
            else:
                flipped = np.flatnonzero(mask != prev[p])
                varint(len(flipped), out)
                last = -1
                for c in flipped:
                    varint(int(c - last - 1), out)
                    last = c
        prev = frame
    return bytes(out)


def gz(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def digest(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).astype(np.uint8).tobytes()).hexdigest()[:16]


PAGE = """<!doctype html><meta charset="utf-8"><title>Control decode</title><body><pre id="out">running</pre><script>
const DATA = %(data)s;
function b64(s) { const bin = atob(s), a = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; }
async function gunzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
function decodeStates(buf, ticks, cells) {
  const frames = new Array(ticks); let pos = 0, cur = new Uint8Array(cells);
  const vi = () => { let n = 0, s = 0, b; do { b = buf[pos++]; n |= (b & 0x7f) << s; s += 7; } while (b & 0x80); return n; };
  for (let i = 0; i < ticks; i++) {
    const kind = buf[pos++];
    if (kind === 0) { for (let c = 0; c < cells; c += 2) { const b = buf[pos++]; cur[c] = b >> 4; if (c + 1 < cells) cur[c + 1] = b & 15; } }
    else { const n = vi(); let c = -1; for (let k = 0; k < n; k++) { c += vi() + 1; cur[c] = buf[pos++]; } }
    frames[i] = cur.slice();
  }
  return frames;
}
function decodeMasks(buf, ticks, players, cells, every) {
  const frames = new Array(ticks), bytes = (cells + 7) >> 3; let pos = 0;
  const cur = []; for (let p = 0; p < players; p++) cur.push(new Uint8Array(cells));
  const vi = () => { let n = 0, s = 0, b; do { b = buf[pos++]; n |= (b & 0x7f) << s; s += 7; } while (b & 0x80); return n; };
  for (let i = 0; i < ticks; i++) {
    for (let p = 0; p < players; p++) {
      const m = cur[p];
      if (i %% every === 0) { for (let c = 0; c < cells; c++) m[c] = (buf[pos + (c >> 3)] >> (7 - (c & 7))) & 1; pos += bytes; }
      else { const n = vi(); let c = -1; for (let k = 0; k < n; k++) { c += vi() + 1; m[c] ^= 1; } }
    }
    frames[i] = cur.map(m => m.slice());
  }
  return frames;
}
async function sha(frames) {
  let total = 0; frames.forEach(f => total += Array.isArray(f) ? f.reduce((a, m) => a + m.length, 0) : f.length);
  const all = new Uint8Array(total); let o = 0;
  frames.forEach(f => (Array.isArray(f) ? f : [f]).forEach(m => { all.set(m, o); o += m.length; }));
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", all));
  return Array.from(h.slice(0, 8)).map(b => b.toString(16).padStart(2, "0")).join("");
}
(async () => {
  const res = {};
  for (const run of [1, 2, 3]) {
    const t0 = performance.now();
    const s = decodeStates(await gunzip(b64(DATA.states)), DATA.ticks, DATA.cells);
    const t1 = performance.now();
    const cov = decodeMasks(await gunzip(b64(DATA.coverage)), DATA.ticks, DATA.players, DATA.cells, DATA.every);
    const t2 = performance.now();
    const ctl = decodeMasks(await gunzip(b64(DATA.control)), DATA.ticks, DATA.players, DATA.cells, DATA.every);
    const t3 = performance.now();
    res["run" + run] = { states_ms: +(t1 - t0).toFixed(1), coverage_ms: +(t2 - t1).toFixed(1), control_ms: +(t3 - t2).toFixed(1) };
    if (run === 1) res.sha = { states: await sha(s), coverage: await sha(cov), control: await sha(ctl) };
  }
  if (performance.memory) res.js_heap_mb = +(performance.memory.usedJSHeapSize / 1e6).toFixed(1);
  document.getElementById("out").textContent = JSON.stringify(res);
})();
</script>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("stem")
    ap.add_argument("--browser", action="store_true", help="time the page in headless Chromium (needs Playwright)")
    args = ap.parse_args()
    eng = args.out / "engine"
    if args.browser:
        from playwright.sync_api import sync_playwright
        page_path = eng / f"{args.stem}.decode.html"
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--enable-precise-memory-info"])
            page = browser.new_page()
            page.goto(page_path.as_uri())
            page.wait_for_function("document.getElementById('out').textContent !== 'running'", timeout=120000)
            res = json.loads(page.inner_text("#out"))
            browser.close()
        sizes_path = eng / f"{args.stem}.sizes.json"
        sizes = json.loads(sizes_path.read_text())
        res["sha_matches"] = res["sha"] == sizes["decode_page"]["sha"]
        sizes["browser"] = res
        sizes_path.write_text(json.dumps(sizes, indent=1))
        print(json.dumps(res, indent=1))
        return

    z = np.load(eng / f"{args.stem}.npz")
    states, cov, ctl = z["states"], z["coverage"], z["control_masks"]
    ticks = len(states)
    result = {"stem": args.stem, "ticks": ticks, "cells": int(states.shape[1]), "players": int(cov.shape[1]),
              "seconds": round(ticks / HZ, 1), "by_checkpoint": {}}
    for cp in CHECKPOINTS_S:
        every = cp * HZ
        row = {"states": len(gz(encode_states(states, every))), "coverage_16hz": len(gz(encode_masks(cov, every)))}
        row["control_16hz"] = len(gz(encode_masks(ctl, every)))
        ctl2 = ctl[:: HZ // 2]
        row["control_2hz"] = len(gz(encode_masks(ctl2, max(1, cp * 2))))
        row["total_cf_16hz"] = row["states"] + row["coverage_16hz"] + row["control_16hz"]
        row["total_cf_2hz"] = row["states"] + row["coverage_16hz"] + row["control_2hz"]
        result["by_checkpoint"][f"{cp}s"] = row
        print(cp, row, flush=True)
    # each stream at lower rates (2 s checkpoints), KB per 100 s of round
    result["by_rate_kb_per_100s"] = {}
    for hz in (16, 8, 4, 2):
        step, every = HZ // hz, 2 * hz
        result["by_rate_kb_per_100s"][str(hz)] = {
            name: round(len(gz(enc(arr[::step], every))) / 1000 * 100 / result["seconds"])
            for name, enc, arr in (("states", encode_states, states), ("coverage", encode_masks, cov),
                                   ("control", encode_masks, ctl))}
        print(hz, result["by_rate_kb_per_100s"][str(hz)], flush=True)
    # per 100 s of round, so rounds of different lengths compare
    for key in ("total_cf_16hz", "total_cf_2hz"):
        result[f"{key}_per_100s_2s_checkpoints"] = round(result["by_checkpoint"]["2s"][key] * 100 / result["seconds"])
    every = 2 * HZ
    data = {"ticks": ticks, "cells": int(states.shape[1]), "players": int(cov.shape[1]), "every": every,
            "states": base64.b64encode(gz(encode_states(states, every))).decode(),
            "coverage": base64.b64encode(gz(encode_masks(cov, every))).decode(),
            "control": base64.b64encode(gz(encode_masks(ctl, every))).decode()}
    result["decode_page"] = {"checkpoint_s": 2, "sha": {
        "states": digest(states), "coverage": digest(cov), "control": digest(ctl)}}
    (eng / f"{args.stem}.decode.html").write_text(PAGE % {"data": json.dumps(data)}, encoding="utf-8")
    (eng / f"{args.stem}.sizes.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
