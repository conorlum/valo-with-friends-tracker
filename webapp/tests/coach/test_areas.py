from app.coach import areas
from scripts import fetch_callouts

TABLE = {"Toy": [{"name": "A Site", "super": "A", "u": 1000, "v": 1000},
                 {"name": "B Site", "super": "B", "u": 9000, "v": 9000}]}


def test_area_is_the_nearest_callout():
    assert areas.area_at("Toy", 1200, 900, TABLE) == {"name": "A Site", "super": "A"}
    assert areas.area_at("Toy", 8000, 8500, TABLE) == {"name": "B Site", "super": "B"}


def test_a_map_without_callouts_has_no_areas():
    assert areas.area_at("Unknown", 5000, 5000, TABLE) is None
    assert areas.callouts("Unknown", TABLE) == []


class FakeMap:
    def to_uv(self, x, y):
        return int(y), int(x)


def test_convert_names_callouts_and_matches_maps_by_uuid():
    api = [{"uuid": "u-1", "displayName": "Ignored name",
            "callouts": [{"regionName": "Main", "superRegionName": "A", "location": {"x": 10.0, "y": 20.0}}]},
           {"uuid": "u-2", "displayName": "No callouts", "callouts": None},
           {"uuid": "u-9", "displayName": "Not ours", "callouts": []}]
    out = fetch_callouts.convert(api, {"Toy": FakeMap(), "Bare": FakeMap()}, {"Toy": "u-1", "Bare": "u-2"})
    assert out == {"Toy": [{"name": "A Main", "super": "A", "u": 20, "v": 10}], "Bare": []}
