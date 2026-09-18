"""Each chain link exports its configuration from the source the chain names.

K1 and K2 used explicit weights, K3 the declared `impact_rc3` comparator, K4 the
frozen manifest and K5 the checkout's active manifest (ledger, 2026-09-16). The
chain only proves something if those sources really are what each export read,
so the exporter resolves them itself and records which one it used, along with
the commit it ran from.
"""

import json

import pytest

from app.scoring import impact_runtime
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match
from app.scoring.impact_manifest import RC3, ManifestMismatchError, lf_sha256
from scripts import export_impact_artifact as export
from scripts import freeze_impact_candidate as freezer
from tests.buy_disruption_fixtures import build_match, session

LOCKED = FormulaWeights(damage=1.0, leverage=2.5, econ=2.5, assists=100.0, trade_credit_scale=1.0)


def test_the_rc3_comparator_is_the_explicit_locked_configuration():
    kwargs, source = export.resolve_configuration(comparator=RC3)
    assert kwargs == export.build_kwargs_for(weights=LOCKED, credit_on=True)
    assert source == {"kind": "comparator", "name": RC3}


def test_credit_off_changes_only_the_credit_flag():
    kwargs, source = export.resolve_configuration(comparator=RC3, credit="off")
    assert kwargs == export.build_kwargs_for(weights=LOCKED, credit_on=False)
    assert source["credit_override"] == "off"


def test_comparator_and_explicit_configurations_score_identically(tmp_path):
    """K3 = K1 in miniature: the same rows, so the same artifact bytes."""
    db = session(all_tables=True)
    match, _, _ = build_match(db, "sources", kills={3: [("B1", "A1", 10.0), ("A2", "B1", 10.5)]},
                              count_stats=True)
    explicit = export.build_kwargs_for(weights=LOCKED, credit_on=True)
    declared, _ = export.resolve_configuration(comparator=RC3)

    a = export.write_artifact(build_impact_rows_for_match(db, match.id, **explicit), tmp_path / "explicit.csv")
    b = export.write_artifact(build_impact_rows_for_match(db, match.id, **declared), tmp_path / "declared.csv")
    assert a["sha256"] == b["sha256"]


def test_a_frozen_manifest_resolves_to_its_release_comparator(tmp_path):
    db = session(all_tables=True)
    match, _, _ = build_match(db, "manifest-source", kills={3: [("A1", "B1", 10.0)]})
    out = tmp_path / "candidate-manifest.json"
    freezer.freeze(db, candidate_id="impact-rc3-test", release_comparator=RC3, activation_version=3,
                   match_ids=[match.id], out_path=out, scorer_revision="f" * 40, packages=[])

    kwargs, source = export.resolve_configuration(manifest_path=str(out))

    assert kwargs == export.resolve_configuration(comparator=RC3)[0]
    assert source == {"kind": "manifest", "file": "candidate-manifest.json", "lf_sha256": lf_sha256(out),
                      "comparator": RC3}


def test_active_needs_an_active_manifest(monkeypatch):
    monkeypatch.setattr(impact_runtime, "ACTIVE_MANIFEST", None)
    with pytest.raises(export.ExportRefused, match="ACTIVE_MANIFEST is None"):
        export.resolve_configuration(active=True)


def test_one_source_at_a_time():
    with pytest.raises(export.ExportRefused, match="choose one configuration source"):
        export.resolve_configuration(comparator=RC3, active=True)


def test_explicit_weights_cannot_ride_along_with_another_source():
    with pytest.raises(export.ExportRefused, match="--weights"):
        export.resolve_configuration(comparator=RC3, weights=LOCKED)


def test_an_explicit_export_must_say_whether_credit_is_on():
    with pytest.raises(export.ExportRefused, match="--credit"):
        export.resolve_configuration()


def test_an_explicit_export_defaults_to_the_locked_weights():
    kwargs, source = export.resolve_configuration(credit="on")
    assert kwargs == export.build_kwargs_for(weights=LOCKED, credit_on=True)
    assert source == {"kind": "explicit"}


def test_code_identity_names_head_and_everything_that_differs_from_it():
    def fake_git(*args):
        if args[0] == "status":
            return " M webapp/app/scoring/impact.py\n?? webapp/scripts/scratch.py\n"
        return "0123abcd\n"

    assert export.code_identity(fake_git) == {
        "revision": "0123abcd",
        "dirty": [" M webapp/app/scoring/impact.py", "?? webapp/scripts/scratch.py"],
    }


def test_a_manifest_that_does_not_match_this_checkout_is_refused(tmp_path):
    """K4 must score with the frozen configuration, so a manifest this code
    does not reproduce stops the export rather than scoring something else."""
    db = session(all_tables=True)
    match, _, _ = build_match(db, "tampered", kills={3: [("A1", "B1", 10.0)]})
    out = tmp_path / "candidate-manifest.json"
    freezer.freeze(db, candidate_id="impact-rc3-test", release_comparator=RC3, activation_version=3,
                   match_ids=[match.id], out_path=out, scorer_revision="f" * 40, packages=[])
    manifest = json.loads(out.read_text(encoding="utf-8"))
    manifest["econ_scale"] = "tampered"
    out.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestMismatchError):
        export.resolve_configuration(manifest_path=str(out))


def test_the_cli_refuses_a_checkout_that_is_not_exactly_a_commit(monkeypatch, tmp_path, capsys):
    import app.db

    def no_database():
        raise AssertionError("the export reached the database instead of refusing")

    monkeypatch.setattr(app.db, "SessionLocal", no_database)
    monkeypatch.setattr(export, "code_identity", lambda: {"revision": "abc", "dirty": ["?? webapp/scratch.py"]})
    assert export.main(["--out", str(tmp_path), "--label", "t", "--comparator", RC3]) == 3
    assert "REFUSED: webapp/ is not exactly a commit" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [], "nothing is written"
