"""Freeze a complete Impact scoring candidate BEFORE it is reviewed, and refuse
to score under it if anything it depends on has moved.

Plan-review finding P2: freezing only the econ constants cannot make a full
Impact review reproducible. The manifest therefore pins everything a score
depends on -- comparator configurations and weights, the econ scale, the
calculator constants, the trade schedule, the agent allowance table, timing
flags, a behavioral digest of every scoring source file, the Python version
that digest was taken under, and a content fingerprint of each reviewed
match's source rows. Review, recompute/backfill and ingestion load the SAME
manifest; verify_manifest fails visibly on any difference, so an approved
report cannot silently drift from what is activated.

The source digest is taken over the parsed AST with docstrings removed and
the IMPACT_CALCULATION_VERSION assignment masked: comments, docstrings and
the one coordinated version bump at activation do not invalidate approval,
while ANY change to executable scoring code does, and must be re-reviewed.
"""
import ast
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import text

from app.scoring import agent_economy
from app.scoring import econ_buy_disruption as bd
from app.scoring import econ_component
from app.scoring import impact
from app.scoring.impact_config import ImpactScoringConfig
from app.scoring.impact import FormulaWeights

MANIFEST_VERSION = 1
WEBAPP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = WEBAPP_ROOT.parent

HASHED_SOURCES = (
    "app/scoring/impact.py",
    "app/scoring/impact_config.py",
    "app/scoring/econ_buy_disruption.py",
    "app/scoring/econ_component.py",
    "app/scoring/agent_economy.py",
    "app/scoring/plant_window.py",
    "app/scoring/preplant_empirical_factor.py",
)
_MASKED_ASSIGNMENTS = frozenset({"IMPACT_CALCULATION_VERSION"})

CALCULATOR_CONSTANT_NAMES = (
    "AUDIT_VERSION", "TARGET_KIT", "TEAM_REFERENCE", "BACKGROUND", "DISRUPTION",
    "ACTIVATION_GAP", "CARRYOVER_FLOOR", "ABSORBED_RATE", "DISRUPTED_RATE",
    "WEALTH_ZERO_AT", "WEALTH_CEILING", "ROSTER_SIZE",
)

LIVE_LEGACY = "live_legacy"
SEPARATE_ECON_LEGACY = bd.MODEL_SEPARATE_ECON_LEGACY
V2_WEALTH = bd.MODEL_V2_WEALTH
V2_30_80 = bd.MODEL_V2_30_80

COMPARATORS = {
    # The current runtime formula. Its REPLAY and the PERSISTED site values are
    # reported separately by the review tool; neither stands in for the other.
    LIVE_LEGACY: ImpactScoringConfig(LIVE_LEGACY),
    # The older pooled/zero-sum allocator: optional diagnostic only.
    SEPARATE_ECON_LEGACY: ImpactScoringConfig(
        SEPARATE_ECON_LEGACY, enable_econ_component=True, econ_model=bd.MODEL_SEPARATE_ECON_LEGACY),
    # V2 kill credit + the independent wealth debit: the Abyss -100..-928 column.
    V2_WEALTH: ImpactScoringConfig(V2_WEALTH, enable_econ_component=True, econ_model=bd.MODEL_V2_WEALTH),
    # Identical V2 kill credit + the owner's 30%/80% debit: the release candidate.
    V2_30_80: ImpactScoringConfig(V2_30_80, enable_econ_component=True, econ_model=bd.MODEL_V2_30_80),
}


class ManifestMismatchError(RuntimeError):
    def __init__(self, differences):
        self.differences = tuple(differences)
        super().__init__("Frozen candidate manifest does not match this checkout:\n  "
                         + "\n  ".join(self.differences))


# ---- identities --------------------------------------------------------------------

def lf_sha256(path) -> str:
    """SHA-256 over LF-normalized bytes, stable across CRLF checkouts."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def behavioral_source_digest(path) -> str:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in _MASKED_ASSIGNMENTS for t in node.targets):
            node.value = ast.Constant(value="<masked>")
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def _canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def current_code_identity() -> dict:
    allowances = dict(sorted(agent_economy.AGENT_FREE_ABILITY_CREDITS.items()))
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "econ_scale": econ_component.ECON_SCALE,
        "calculator_constants": {name: getattr(bd, name) for name in CALCULATOR_CONSTANT_NAMES},
        "trade": {"cost_schedule": [list(step) for step in impact.TRADE_COST_SCHEDULE],
                  "window_seconds": impact.TRADE_WINDOW_SECONDS},
        "agent_free_ability_credits": allowances,
        "agent_free_ability_credits_sha256": _canonical_sha256(allowances),
        "source_digests": {p: behavioral_source_digest(WEBAPP_ROOT / p) for p in HASHED_SOURCES},
    }


def config_to_dict(config: ImpactScoringConfig) -> dict:
    return {
        "config_id": config.config_id,
        "use_realized_swing": config.use_realized_swing,
        "enable_econ_component": config.enable_econ_component,
        "econ_model": config.econ_model,
        "weights": {"damage": config.weights.damage, "leverage": config.weights.leverage,
                    "econ": config.weights.econ},
        "enable_postplant_leverage": config.enable_postplant_leverage,
        "enable_preplant_empirical": config.enable_preplant_empirical,
    }


def config_from_dict(data: dict) -> ImpactScoringConfig:
    return ImpactScoringConfig(
        config_id=data["config_id"], use_realized_swing=data["use_realized_swing"],
        enable_econ_component=data["enable_econ_component"], econ_model=data["econ_model"],
        weights=FormulaWeights(**data["weights"]),
        enable_postplant_leverage=data["enable_postplant_leverage"],
        enable_preplant_empirical=data["enable_preplant_empirical"],
    )


def config_from_manifest(manifest: dict, comparator: str | None = None) -> ImpactScoringConfig:
    name = comparator or manifest["release_comparator"]
    if name not in manifest["comparators"]:
        raise ManifestMismatchError([f"comparator {name!r} is not in the manifest"])
    return config_from_dict(manifest["comparators"][name])


# ---- source snapshots ------------------------------------------------------------------

_FINGERPRINT_QUERIES = {
    "match_players": "SELECT id, team, agent FROM match_players WHERE match_id = :m ORDER BY id",
    "rounds": ("SELECT id, round_number, outcome, planted, plant_time, exploded, defused, defuse_time "
               "FROM rounds WHERE match_id = :m ORDER BY round_number"),
    "stats": ("SELECT s.round_id, s.match_player_id, s.score, s.kills, s.deaths, s.assists, "
              "s.loadout, s.remaining FROM round_player_stats s JOIN rounds r ON r.id = s.round_id "
              "WHERE r.match_id = :m ORDER BY s.round_id, s.match_player_id"),
    "events": ("SELECT k.id, k.round_id, k.killer_match_player_id, k.death_match_player_id, "
               "k.event_time_seconds FROM kill_events k JOIN rounds r ON r.id = k.round_id "
               "WHERE r.match_id = :m ORDER BY k.id"),
}


def match_source_fingerprint(db, match_id: int) -> str:
    """Content hash of exactly the rows build_impact_rows_for_match reads for
    one match. Scores depend on nothing else, so an unchanged fingerprint
    under an unchanged manifest reproduces the approved values even after
    unrelated matches are ingested."""
    content = {
        name: [[str(value) if value is not None else None for value in row]
               for row in db.execute(text(sql), {"m": match_id}).all()]
        for name, sql in _FINGERPRINT_QUERIES.items()
    }
    if not content["match_players"]:
        raise ManifestMismatchError([f"match {match_id} has no source rows"])
    return _canonical_sha256(content)


def verify_source_snapshots(db, manifest: dict, match_ids=None) -> None:
    frozen = manifest.get("source_snapshots", {}).get("matches", {})
    wanted = [str(m) for m in match_ids] if match_ids is not None else list(frozen)
    diffs = []
    for match_id in wanted:
        if match_id not in frozen:
            diffs.append(f"match {match_id} has no frozen source fingerprint")
            continue
        current = match_source_fingerprint(db, int(match_id))
        if current != frozen[match_id]:
            diffs.append(f"match {match_id} source rows changed since the freeze")
    if diffs:
        raise ManifestMismatchError(diffs)


# ---- build / load / verify ----------------------------------------------------------------

def build_manifest(*, candidate_id: str, created: str, scorer_revision: str,
                   activation_impact_calculation_version: int, source_snapshots: dict,
                   release_comparator: str = V2_30_80, notes=()) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "candidate_id": candidate_id,
        "created": created,
        "status": "frozen_for_review",
        "release_comparator": release_comparator,
        "site_comparison": [LIVE_LEGACY, release_comparator],
        "penalty_comparison": [V2_WEALTH, V2_30_80],
        "diagnostic_comparators": [SEPARATE_ECON_LEGACY],
        "comparators": {name: config_to_dict(config) for name, config in COMPARATORS.items()},
        "formula_changes_vs_live_legacy": [
            "structure: impact = A*damage + B*leverage + C*econ_component replaces "
            "damage + mean(econ, time, swing) kill-order products",
            "economy: the legacy econ-differential and swing factors leave leverage; "
            "econ is the separate buy-disruption component with 30%/80% death debits",
            "columns: econ_impact and swing_impact are written 0; econ_component is signed",
            "timing: unchanged legacy time factor (post-plant table and pre-plant curve OFF)",
            "trade discount: unchanged (the declared cost schedule and 6s window)",
            "weights: A(damage)=1.25, B(leverage)=1.0, C(econ)=1.0; ECON_SCALE unchanged",
        ],
        "timing": {"postplant_leverage": False, "preplant_empirical": False,
                   "postplant_table_sha256": None, "preplant_centering_c": None},
        "impact_calculation_version_at_freeze": impact.IMPACT_CALCULATION_VERSION,
        "activation_impact_calculation_version": activation_impact_calculation_version,
        "scorer_revision": scorer_revision,
        "source_snapshots": source_snapshots,
        "notes": list(notes),
        **current_code_identity(),
    }


def write_manifest(path, manifest: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def load_manifest(path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ManifestMismatchError([f"manifest file is missing: {path}"])
    return json.loads(path.read_text(encoding="utf-8"))


def verify_manifest(manifest: dict) -> None:
    """Raise ManifestMismatchError listing EVERY difference, or return."""
    diffs = []
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        diffs.append(f"manifest_version {manifest.get('manifest_version')!r} != {MANIFEST_VERSION}")
    current = current_code_identity()
    for key in ("python", "econ_scale", "calculator_constants", "trade",
                "agent_free_ability_credits", "agent_free_ability_credits_sha256"):
        if manifest.get(key) != current[key]:
            diffs.append(f"{key}: frozen {manifest.get(key)!r} != current {current[key]!r}")
    frozen_digests = manifest.get("source_digests") or {}
    for path in sorted(set(frozen_digests) | set(current["source_digests"])):
        if frozen_digests.get(path) != current["source_digests"].get(path):
            diffs.append(f"scoring source {path} differs from the frozen digest -- re-run the review")
    timing = manifest.get("timing") or {}
    if any(timing.get(k) for k in ("postplant_leverage", "preplant_empirical",
                                   "postplant_table_sha256", "preplant_centering_c")):
        diffs.append("timing candidates need frozen table artifacts, which manifest v1 does not support")
    comparators = manifest.get("comparators") or {}
    for name in (LIVE_LEGACY, V2_WEALTH, V2_30_80):
        if name not in comparators:
            diffs.append(f"required comparator {name!r} is missing")
    for name, data in comparators.items():
        try:
            config_from_dict(data).build_kwargs()
        except (KeyError, TypeError, ValueError) as exc:
            diffs.append(f"comparator {name!r} is invalid: {exc}")
        else:
            if name in COMPARATORS and config_to_dict(COMPARATORS[name]) != data:
                diffs.append(f"comparator {name!r} does not match its declared identity")
    if manifest.get("release_comparator") not in comparators:
        diffs.append(f"release comparator {manifest.get('release_comparator')!r} is missing")
    if diffs:
        raise ManifestMismatchError(diffs)
