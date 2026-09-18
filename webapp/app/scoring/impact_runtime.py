"""The ONE runtime switch for Impact scoring.

ingestion (compute_impact_for_match), scripts/backfill_impact_candidate.py
and the review tool all resolve their configuration here, so they cannot
disagree about which formula is live.

ACTIVE_MANIFEST is None: the live legacy formula, exactly as before this
module existed. Activation is a deliberate, reviewed change to this line --
a repo-relative path to an APPROVED frozen manifest -- coordinated with the
IMPACT_CALCULATION_VERSION bump the manifest names, inside a maintenance
window. See docs/superpowers/econ-buy-disruption-candidate/README.md.
"""
import functools

from app.scoring import impact
from app.scoring.impact_manifest import (
    REPO_ROOT,
    ManifestMismatchError,
    config_from_manifest,
    lf_sha256,
    load_manifest,
    verify_manifest,
)

ACTIVE_MANIFEST: str | None = "docs/superpowers/impact-rc3/candidate-manifest.json"


@functools.lru_cache(maxsize=None)
def _load_verified(manifest_path: str, file_digest: str):
    manifest = load_manifest(manifest_path)
    verify_manifest(manifest)
    expected = manifest["activation_impact_calculation_version"]
    if impact.IMPACT_CALCULATION_VERSION != expected:
        raise ManifestMismatchError([
            f"IMPACT_CALCULATION_VERSION is {impact.IMPACT_CALCULATION_VERSION}, but the manifest "
            f"activates at {expected}: bump it together with ACTIVE_MANIFEST"])
    return manifest, config_from_manifest(manifest)


def _verified():
    """Keyed on the file's CONTENT, so a manifest edited or replaced after a
    long-running worker started is re-verified rather than served from cache."""
    path = REPO_ROOT / ACTIVE_MANIFEST
    if not path.is_file():
        raise ManifestMismatchError([f"manifest file is missing: {path}"])
    return _load_verified(str(path), lf_sha256(path))


def active_manifest() -> dict | None:
    return None if ACTIVE_MANIFEST is None else _verified()[0]


def active_scoring_config():
    """The verified active ImpactScoringConfig, or None for the live legacy formula."""
    return None if ACTIVE_MANIFEST is None else _verified()[1]


def clear_cache() -> None:
    _load_verified.cache_clear()
