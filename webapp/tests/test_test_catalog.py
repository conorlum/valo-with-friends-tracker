"""The suite catalog (tests/suites.toml, scripts/test_catalog.py): every test file in exactly one suite, every file
under app/ and ../replay_worker/ guarded, and the selection and timing rules `plan` and `run` rely on."""

import pytest

from scripts import test_catalog as tc

CATALOG = {
    "engine": {"about": "e", "tests": ["tests/replays/test_engine*.py"], "guards": ["app/control/engine.py"]},
    "geometry": {"about": "g", "tests": ["tests/replays/test_geo.py"],
                 "guards": ["app/control/geometry.py", "app/control/engine.py"]},
    "pages": {"about": "p", "tests": ["tests/test_page.py"], "guards": ["app/templates/**"]},
}
TESTS = ["tests/replays/test_engine.py", "tests/replays/test_engine_more.py", "tests/replays/test_geo.py",
         "tests/test_page.py"]


def _select(changed):
    return tc.select(CATALOG, tc.suite_of(CATALOG, TESTS), changed)


def test_the_committed_catalog_has_no_problems():
    assert tc.check(tc.load_catalog(), tc.all_files()) == []


def test_double_star_crosses_zero_or_more_folders():
    assert tc.match("tests/browser/test_x.py", "tests/browser/**/test_*.py")
    assert tc.match("app/scoring/a/b.py", "app/scoring/**")
    assert not tc.match("app/x/y.py", "app/*")


def test_a_guarded_file_picks_every_suite_that_guards_it():
    picked, unguarded, broad = _select(["app/control/engine.py"])
    assert sorted(picked) == ["engine", "geometry"] and unguarded == [] and not broad


def test_a_changed_test_picks_its_own_suite_only():
    picked, _, _ = _select(["tests/replays/test_geo.py"])
    assert list(picked) == ["geometry"]


def test_a_helper_picks_the_suites_with_tests_in_its_folder():
    picked, unguarded, _ = _select(["tests/replays/toys.py"])
    assert sorted(picked) == ["engine", "geometry"] and unguarded == []


def test_an_unguarded_file_is_reported_not_dropped():
    picked, unguarded, _ = _select(["scripts/one_off.py"])
    assert picked == {} and unguarded == ["scripts/one_off.py"]


def test_a_broad_file_picks_every_suite():
    picked, _, broad = _select(["tests/conftest.py"])
    assert broad and sorted(picked) == ["engine", "geometry", "pages"]


def test_check_finds_unowned_doubly_owned_and_unguarded_files():
    catalog = {"a": {"about": "a", "tests": ["tests/test_*.py"], "guards": ["app/x.py"]},
               "b": {"about": "b", "tests": ["tests/test_two.py"], "guards": ["app/nothing_here.py"]}}
    files = ["tests/test_one.py", "tests/test_two.py", "tests/sub/test_three.py", "app/x.py", "app/y.py"]
    problems = tc.check(catalog, files)
    assert "tests/test_two.py: in 2 suites (a, b)" in problems
    assert "tests/sub/test_three.py: in no suite" in problems
    assert "app/y.py: guarded by no suite" in problems
    assert "[b] guards: 'app/nothing_here.py' matches nothing" in problems
    assert not any(p.startswith("app/x.py") for p in problems)


def test_an_unlisted_test_is_timed_at_its_files_mean_quick_test():
    times = {"tests/test_a.py": {"tests": {"test_slow": 12.0}, "rest": [4, 1.0]}}
    assert tc.test_time(times, "tests/test_a.py::test_slow") == 12.0
    assert tc.test_time(times, "tests/test_a.py::test_other[x]") == pytest.approx(0.25)
    assert tc.test_time(times, "tests/test_b.py::test_c") is None


@pytest.mark.parametrize("given, expected", [
    ("webapp/app/control/engine.py", "app/control/engine.py"),
    ("replay_worker/server.py", "../replay_worker/server.py"),
    ("app/main.py", "app/main.py"),
])
def test_paths_are_read_from_git_or_from_webapp(given, expected):
    assert tc.normalise(given) == expected


def test_lanes_follow_the_markers_most_restrictive_first():
    assert tc.lane_of({"slow", "postgres"}) == "postgres"
    assert tc.lane_of({"browser", "slow"}) == "browser"
    assert tc.lane_of({"geometry", "slow"}) == "slow"
    assert tc.lane_of({"geometry"}) == "default"
