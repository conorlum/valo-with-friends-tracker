import json
from pathlib import Path

from app.coach import areas


def test_owner_confirmed_area_cases():
    cases = json.loads((Path(__file__).parent / "data" / "area_cases.json").read_text(encoding="utf-8"))
    for case in cases:
        assert set(case) == {"map", "u", "v", "z", "area"}
        if areas.is_painted(case["map"]):
            result = areas.area_at(case["map"], case["u"], case["v"], case["z"])
            assert result is not None and case["area"] in result["path"], case
