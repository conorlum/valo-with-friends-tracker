"""Step 3a/3d: scripts/recompute_impact.py rescoring EVERY match must
invalidate the whole player_view_cache table BEFORE recomputing any
ImpactScore rows (and commit that invalidation before the recompute loop
starts) -- a mid-run failure must leave the cache EMPTY, never serving
profile/econ numbers derived from the previous scoring run. This is real
orchestration risk with no DB fixture available, so per docs/
player_page_render_speed.txt Step 3d option (ii): a mocked session + mocked
cache functions asserting call ORDER.
"""

from unittest.mock import MagicMock, patch

import scripts.recompute_impact as recompute_impact_script


def test_invalidate_and_commit_happen_before_any_match_is_recomputed():
    call_order: list[str] = []

    fake_db = MagicMock()
    fake_match_1 = MagicMock(id=1, external_id="m1")
    fake_match_2 = MagicMock(id=2, external_id="m2")
    fake_db.query.return_value.all.return_value = [fake_match_1, fake_match_2]
    fake_db.commit.side_effect = lambda: call_order.append("commit")

    def fake_invalidate(db):
        assert db is fake_db
        call_order.append("invalidate")

    def fake_compute(db, match_id):
        assert db is fake_db
        call_order.append(f"compute:{match_id}")

    with (
        patch.object(recompute_impact_script, "SessionLocal", return_value=fake_db),
        patch.object(recompute_impact_script, "invalidate_all_player_caches", side_effect=fake_invalidate),
        patch.object(recompute_impact_script, "compute_impact_for_match", side_effect=fake_compute),
    ):
        recompute_impact_script.main()

    assert call_order == ["invalidate", "commit", "compute:1", "compute:2"]


def test_invalidate_runs_even_when_a_later_match_fails_to_recompute():
    """The invalidate+commit must already be durable before the loop even
    starts touching matches -- so a failure partway through the recompute
    loop still leaves the cache correctly empty, not stale."""
    call_order: list[str] = []

    fake_db = MagicMock()
    fake_match_1 = MagicMock(id=1, external_id="m1")
    fake_match_2 = MagicMock(id=2, external_id="m2")
    fake_db.query.return_value.all.return_value = [fake_match_1, fake_match_2]
    fake_db.commit.side_effect = lambda: call_order.append("commit")

    def fake_invalidate(db):
        call_order.append("invalidate")

    def fake_compute(db, match_id):
        call_order.append(f"compute:{match_id}")
        if match_id == 2:
            raise RuntimeError("boom")

    with (
        patch.object(recompute_impact_script, "SessionLocal", return_value=fake_db),
        patch.object(recompute_impact_script, "invalidate_all_player_caches", side_effect=fake_invalidate),
        patch.object(recompute_impact_script, "compute_impact_for_match", side_effect=fake_compute),
    ):
        try:
            recompute_impact_script.main()
        except RuntimeError:
            pass

    assert call_order[:2] == ["invalidate", "commit"]
