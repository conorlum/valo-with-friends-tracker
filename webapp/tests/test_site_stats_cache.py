from app.services.site_stats_cache import (
    _validate_blob,
    _validate_eco_followup_variant,
    _validate_pistol_round_combos,
    _validate_pistol_win_followup_eco,
    _validate_round_combo_variant,
)

_VALID_PISTOL_MATCH_STATS = {
    "lost_both_total": 2, "lost_both_wins": 1,
    "won_one_total": 5, "won_one_wins": 3,
    "won_both_total": 1, "won_both_wins": 1,
}

_VALID_ECO_VARIANT = {"buckets": [[0, 10, 4, 12.5, 6.0, 8, 5], [1, 3, 1, 2.0, 1.5, 3, 2]]}
_VALID_ECO_FOLLOWUP = {"friends": _VALID_ECO_VARIANT, "all": _VALID_ECO_VARIANT}

_VALID_ROUND_COMBO_VARIANT = {
    "first_half": {"WW": {"total": 10, "win": 8}, "WL": {"total": 3, "win": 1}},
    "full": {"WWWW": {"total": 5, "win": 4}, "LLLL": {"total": 6, "win": 1}},
}
_VALID_ROUND_COMBOS = {"friends": _VALID_ROUND_COMBO_VARIANT, "all": _VALID_ROUND_COMBO_VARIANT}

_VALID_BLOB = {
    "pistol_match_stats": _VALID_PISTOL_MATCH_STATS,
    "pistol_win_followup_eco": _VALID_ECO_FOLLOWUP,
    "pistol_round_combos": _VALID_ROUND_COMBOS,
}


def test_happy_path_blob_validates():
    assert _validate_blob(_VALID_BLOB)


def test_validation_rejects_missing_top_level_key():
    assert not _validate_blob({})


def test_validation_rejects_extra_top_level_key():
    assert not _validate_blob({**_VALID_BLOB, "extra": {}})


def test_validation_rejects_missing_pistol_bucket_key():
    stats = {k: v for k, v in _VALID_PISTOL_MATCH_STATS.items() if k != "won_both_wins"}
    assert not _validate_blob({**_VALID_BLOB, "pistol_match_stats": stats})


def test_validation_rejects_wins_greater_than_total():
    stats = dict(_VALID_PISTOL_MATCH_STATS, lost_both_wins=99)
    assert not _validate_blob({**_VALID_BLOB, "pistol_match_stats": stats})


def test_validation_rejects_negative_count():
    stats = dict(_VALID_PISTOL_MATCH_STATS, won_one_total=-1)
    assert not _validate_blob({**_VALID_BLOB, "pistol_match_stats": stats})


def test_validation_rejects_non_dict():
    assert not _validate_blob("not a dict")
    assert not _validate_blob(None)


def test_eco_followup_variant_happy_path_validates():
    assert _validate_eco_followup_variant(_VALID_ECO_VARIANT)


def test_eco_followup_variant_rejects_wrong_row_length():
    assert not _validate_eco_followup_variant({"buckets": [[0, 10, 4, 12.5]]})


def test_eco_followup_variant_rejects_bucket_index_out_of_range():
    assert not _validate_eco_followup_variant({"buckets": [[999, 10, 4, 12.5, 6.0, 8, 5]]})


def test_eco_followup_variant_rejects_win_greater_than_total():
    assert not _validate_eco_followup_variant({"buckets": [[0, 5, 6, 12.5, 6.0, 5, 3]]})


def test_eco_followup_variant_rejects_negative_ratio_sum():
    assert not _validate_eco_followup_variant({"buckets": [[0, 10, 4, -1.0, 6.0, 8, 5]]})


def test_eco_followup_variant_rejects_match_win_greater_than_match_total():
    assert not _validate_eco_followup_variant({"buckets": [[0, 10, 4, 12.5, 6.0, 5, 6]]})


def test_eco_followup_variant_rejects_match_total_greater_than_total():
    assert not _validate_eco_followup_variant({"buckets": [[0, 10, 4, 12.5, 6.0, 11, 5]]})


def test_pistol_win_followup_eco_requires_both_friends_and_all_keys():
    assert _validate_pistol_win_followup_eco(_VALID_ECO_FOLLOWUP)
    assert not _validate_pistol_win_followup_eco({"friends": _VALID_ECO_VARIANT})
    assert not _validate_pistol_win_followup_eco({"all": _VALID_ECO_VARIANT})


def test_round_combo_variant_happy_path_validates():
    assert _validate_round_combo_variant(_VALID_ROUND_COMBO_VARIANT)


def test_round_combo_variant_allows_a_subset_of_combos():
    """Not every W/L combo need appear -- only ones actually observed."""
    assert _validate_round_combo_variant({"first_half": {"WW": {"total": 1, "win": 1}}, "full": {}})


def test_round_combo_variant_rejects_unknown_combo_key():
    assert not _validate_round_combo_variant({"first_half": {"XX": {"total": 1, "win": 1}}, "full": {}})


def test_round_combo_variant_rejects_wrong_length_combo_key_for_granularity():
    """A 4-char key doesn't belong in first_half (which only has 2 rounds)."""
    assert not _validate_round_combo_variant({"first_half": {"WWWW": {"total": 1, "win": 1}}, "full": {}})


def test_round_combo_variant_rejects_win_greater_than_total():
    assert not _validate_round_combo_variant({"first_half": {"WW": {"total": 1, "win": 2}}, "full": {}})


def test_round_combo_variant_rejects_missing_granularity_key():
    assert not _validate_round_combo_variant({"first_half": {}})


def test_pistol_round_combos_requires_both_friends_and_all_keys():
    assert _validate_pistol_round_combos(_VALID_ROUND_COMBOS)
    assert not _validate_pistol_round_combos({"friends": _VALID_ROUND_COMBO_VARIANT})
    assert not _validate_pistol_round_combos({"all": _VALID_ROUND_COMBO_VARIANT})
