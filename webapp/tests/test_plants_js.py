"""The plant page's table sort (app/static/js/plants.js sortRows), run in node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "plants.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _order(values, type_, direction):
    rows = [{"value": v, "index": i} for i, v in enumerate(values)]
    script = f"""
const p = require({json.dumps(str(JS))});
console.log(JSON.stringify(p.sortRows({json.dumps(rows)}, {json.dumps(type_)}, {json.dumps(direction)})));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def _picked(values, type_, direction):
    return [values[i] for i in _order(values, type_, direction)]


def test_numbers_descending_with_empty_last():
    assert _picked([67.2, "", 82.3, 68.9], "number", "desc") == [82.3, 68.9, 67.2, ""]


def test_numbers_ascending_with_empty_still_last():
    assert _picked([67.2, "", 82.3, 68.9], "number", "asc") == [67.2, 68.9, 82.3, ""]


def test_signed_text_values_parse_as_numbers():
    assert _picked(["-1.5", "+6.1", "-5.0"], "number", "desc") == ["+6.1", "-1.5", "-5.0"]


def test_text_ascending():
    assert _picked(["Safe", "Default", "Open"], "text", "asc") == ["Default", "Open", "Safe"]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_ties_keep_original_order(direction):
    expected = [0, 1, 2, 3] if direction == "asc" else [3, 0, 1, 2]
    assert _order([5, 5, 5, 9], "number", direction) == expected
    assert _order(["a", "a"], "text", direction) == [0, 1]


def test_muted_rows_get_no_special_place():
    # 30 and 45 stand for muted rows: the sort never looks at that.
    values = [30, 120, 45, 80]
    assert _picked(values, "number", "desc") == [120, 80, 45, 30]
    assert _picked(values, "number", "asc") == [30, 45, 80, 120]
