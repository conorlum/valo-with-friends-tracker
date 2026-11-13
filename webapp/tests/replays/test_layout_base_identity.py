"""The base map assets keep every control/gaps/heights identity frozen."""

import json
import warnings

import pytest

from app.replays import layouts
from tests.replays import layout_golden as golden


@pytest.fixture(scope="module")
def expected():
    return json.loads(golden.GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        figures = golden.build_figures()
    return figures, [str(w.message) for w in caught if "recorded null" in str(w.message)]


@pytest.fixture(scope="module")
def actual(built):
    return built[0]


@pytest.fixture(scope="module")
def built_by_layout():
    asset_keys = {}
    for name in golden.map_names():
        selection = layouts.layout_for(name, "++Ares-Core+release-13.08")
        assert selection.state == "ok"
        assert selection.layout.asset_key == name
        asset_keys[name] = selection.layout.asset_key
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        figures = golden.build_figures(asset_keys=asset_keys)
    assert [str(w.message) for w in caught if "recorded null" in str(w.message)] == []
    return figures


def test_no_figure_raised(built):
    # A figure that raises is recorded as null; a null golden value would then still match.
    assert built[1] == []


def test_golden_map_set_matches_index(expected):
    assert set(expected["maps"]) == set(golden.map_names())


@pytest.mark.parametrize("map_name", golden.map_names())
def test_base_layout_figures(map_name, expected, actual):
    assert actual["maps"][map_name] == expected["maps"][map_name]


@pytest.mark.parametrize("map_name", golden.map_names())
def test_base_layout_figures_through_selection(map_name, expected, built_by_layout):
    assert built_by_layout["maps"][map_name] == expected["maps"][map_name]


@pytest.mark.parametrize("figure", ["hearing_hash", "must_block_sha_lf", "assets_revision"])
def test_global_asset_figures(figure, expected, actual):
    assert actual[figure] == expected[figure]


@pytest.mark.parametrize("map_name", ["Abyss", "Ascent", "Lotus", "Split", "Summit", "Sunset"])
def test_golden_keeps_real_feature_bundles(map_name, expected):
    context = expected["maps"][map_name]["current_geometry"]
    assert context["state"] == "ready"
    assert context["feature_key"] is not None
    assert context["geometry"] is not None
