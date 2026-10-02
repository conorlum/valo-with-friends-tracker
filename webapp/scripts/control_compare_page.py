"""One local page with each rendered scene before and after, side by side, images embedded.

    .venv313\\Scripts\\python.exe scripts\\control_compare_page.py <scenes dir> <out.html> [--before before] [--after after] [--notes notes.json]

Reads the pictures and summary_<label>.json that scripts/render_control_scenes.py wrote for two labels.
`--notes` is an optional {scene stem: "what to look at"} JSON. The page is self-contained (one file),
never published: open it locally.
"""
import argparse
import base64
import html
import json
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scenes", type=Path)
ap.add_argument("out", type=Path)
ap.add_argument("--before", default="before")
ap.add_argument("--after", default="after")
ap.add_argument("--notes", type=Path)
args = ap.parse_args()
before = json.loads((args.scenes / f"summary_{args.before}.json").read_text(encoding="utf-8"))
after = json.loads((args.scenes / f"summary_{args.after}.json").read_text(encoding="utf-8"))
notes = json.loads(args.notes.read_text(encoding="utf-8")) if args.notes else {}


def img(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def nums(d: dict) -> str:
    return " · ".join(f"{k} {v:,} m²" for k, v in d.items())


rows = []
for stem in before:
    b, a = args.scenes / f"{stem}_{args.before}.png", args.scenes / f"{stem}_{args.after}.png"
    if not (b.exists() and a.exists()):
        continue
    mapname, uuid, rnd, t = stem.split("_")
    rows.append(f"""
<section>
  <h2>{html.escape(mapname)} {html.escape(rnd)} at {html.escape(t)} s <span class="id">{html.escape(uuid)}</span></h2>
  <p>{html.escape(notes.get(stem, ""))}</p>
  <div class="pair">
    <figure><figcaption>{html.escape(args.before)}: {html.escape(nums(before[stem]))}</figcaption><img src="{img(b)}" alt="{html.escape(args.before)}"></figure>
    <figure><figcaption>{html.escape(args.after)}: {html.escape(nums(after.get(stem, {})))}</figcaption><img src="{img(a)}" alt="{html.escape(args.after)}"></figure>
  </div>
</section>""")
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Map control compare</title>
<style>
:root {{ --bg:#15171b; --panel:#1f2228; --text:#e6e8eb; --muted:#9aa1ab; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,sans-serif; }}
main {{ max-width:1600px; margin:0 auto; padding:16px; }}
h1 {{ font-size:22px; margin:8px 0; }} h2 {{ font-size:18px; margin:24px 0 4px; }}
.id {{ color:var(--muted); font-size:13px; font-weight:normal; }}
.legend, p {{ color:var(--muted); margin:4px 0 8px; }}
.pair {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
@media (max-width:900px) {{ .pair {{ grid-template-columns:1fr; }} }}
figure {{ margin:0; background:var(--panel); padding:8px; border-radius:8px; }}
figcaption {{ font-size:13px; color:var(--muted); margin-bottom:6px; }}
img {{ width:100%; height:auto; display:block; border-radius:4px; }}
</style></head><body><main>
<h1>Map control: {html.escape(args.before)} and {html.escape(args.after)}</h1>
<p class="legend">Attack red, defense blue: light = passive, mid = safe, strong = active; purple = contested. Dots are players
(white line = facing), circles are smokes, the thick white line is Viper's wall while it is up. Ground totals are each
side's held cells at that moment.</p>
{''.join(rows)}
</main></body></html>"""
args.out.write_text(page, encoding="utf-8")
print(args.out, f"{args.out.stat().st_size / 1e6:.1f} MB")
