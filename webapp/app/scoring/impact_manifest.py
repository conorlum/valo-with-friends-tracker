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
    "app/scoring/weapon_prices.py",
    "app/scoring/round_rewards.py",
    "app/scoring/agent_economy.py",
    "app/scoring/plant_window.py",
    "app/scoring/preplant_empirical_factor.py",
    # The ORM definitions decide what the scorer READS -- a mapped column, a
    # type conversion or the Team enum can change scores without any file in
    # app/scoring changing, and the raw-SQL fingerprint would not see it.
    "app/models/match.py",
    "app/models/round.py",
    "app/models/kill_event.py",
    "app/models/impact_score.py",
    # Impact v4: remove_post_decided_assists maps assistants to players by
    # Player.display_name, so the Player mapping is now scoring input too.
    "app/models/player.py",
)
_MASKED_ASSIGNMENTS = frozenset({"IMPACT_CALCULATION_VERSION"})

CALCULATOR_CONSTANT_NAMES = (
    "AUDIT_VERSION", "TARGET_KIT", "TEAM_REFERENCE", "BACKGROUND", "DISRUPTION",
    "ACTIVATION_GAP", "CARRYOVER_FLOOR", "ABSORBED_RATE", "DISRUPTED_RATE",
    "WEALTH_ZERO_AT", "WEALTH_CEILING", "ROSTER_SIZE",
    "BONUS_AUDIT_VERSION", "BONUS_DENIAL_THRESHOLD", "SWING_VALUE_PER_CREDIT",
    "BONUS_WON_FACTOR", "BONUS_LOST_FACTOR", "SURVIVED_LOSS_REWARD",
)

LIVE_LEGACY = "live_legacy"
SEPARATE_ECON_LEGACY = bd.MODEL_SEPARATE_ECON_LEGACY
V2_WEALTH = bd.MODEL_V2_WEALTH
V2_30_80 = bd.MODEL_V2_30_80
V2_30_80_BONUS = bd.MODEL_V2_30_80_BONUS_DENIAL
RC3 = "impact_rc3"
V4 = "impact_v4"
V4_N = "impact_v4_n"

# The two Impact v4 flags. config_to_dict writes each ONLY WHEN TRUE, so every
# comparator frozen before they existed (rc3's included) serialises to exactly
# the dict it was frozen as, and config_from_dict reads an absent key as False.
# A declared comparator that sets one is still protected: verify_manifest
# compares its whole dict, so a manifest that drops the key fails. That
# protection covers DECLARED comparators only (plan R5.2).
_V4_FLAGS = ("enable_decided_only_time", "remove_post_decided_assists")

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
    # The round 2/14 bonus-round denial candidate (spec 2026-09-12): identical to 30/80
    # outside half-round 2 and for victims on the pistol-losing team.
    V2_30_80_BONUS: ImpactScoringConfig(V2_30_80_BONUS, enable_econ_component=True,
                                        econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL),
    # The rc3 release candidate: the owner's locked weights with the trade credit
    # ON, declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md.
    # Declaring it HERE, and not only inside the frozen file, is what lets
    # verify_manifest reject a manifest whose release configuration has drifted
    # from the code -- including one that merely omits enable_trade_credit, which
    # config_from_dict would otherwise read as False and ship the wrong scoring.
    RC3: ImpactScoringConfig(RC3, enable_econ_component=True,
                             econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL,
                             weights=impact.FormulaWeights(damage=1.0, leverage=2.5, econ=2.5,
                                                           assists=100.0, trade_credit_scale=1.0),
                             enable_trade_credit=True),
    # Impact v4 (declaration 12; plan 2026-09-21-impact-v4): rc3 exactly, plus
    # no time factor (T = 1, 0 once the round is decided) and no assists
    # credit on kills made after the round was decided.
    V4: ImpactScoringConfig(V4, enable_econ_component=True,
                            econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL,
                            weights=impact.FormulaWeights(damage=1.0, leverage=2.5, econ=2.5,
                                                          assists=100.0, trade_credit_scale=1.0),
                            enable_trade_credit=True,
                            enable_decided_only_time=True, remove_post_decided_assists=True),
    # Diagnostic: rc3 plus the time change alone (arm N), so a review can show
    # how v4's movement splits between its two changes.
    V4_N: ImpactScoringConfig(V4_N, enable_econ_component=True,
                              econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL,
                              weights=impact.FormulaWeights(damage=1.0, leverage=2.5, econ=2.5,
                                                            assists=100.0, trade_credit_scale=1.0),
                              enable_trade_credit=True, enable_decided_only_time=True),
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
        # ONE lone target only: `IMPACT_CALCULATION_VERSION = RATE = 2` would
        # otherwise mask RATE too, hiding a behavioral change behind the bump.
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in _MASKED_ASSIGNMENTS):
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
                  "window_seconds": impact.TRADE_WINDOW_SECONDS,
                  # The credit schedule is a declared value, so it belongs in the
                  # identity in readable form. Until now only impact.py's source
                  # digest covered it, which says "something changed" rather than
                  # what the frozen shares actually were.
                  "credit_schedule": [list(step) for step in impact.TRADE_CREDIT_SCHEDULE]},
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
                    "econ": config.weights.econ, "assists": config.weights.assists,
                    "trade_credit_scale": config.weights.trade_credit_scale},
        "enable_postplant_leverage": config.enable_postplant_leverage,
        "enable_preplant_empirical": config.enable_preplant_empirical,
        "enable_trade_credit": config.enable_trade_credit,
        # Only when True (see _V4_FLAGS): rc3's frozen dict must not change.
        **{flag: True for flag in _V4_FLAGS if getattr(config, flag)},
    }


def config_from_dict(data: dict) -> ImpactScoringConfig:
    return ImpactScoringConfig(
        config_id=data["config_id"], use_realized_swing=data["use_realized_swing"],
        enable_econ_component=data["enable_econ_component"], econ_model=data["econ_model"],
        weights=FormulaWeights(**data["weights"]),
        enable_postplant_leverage=data["enable_postplant_leverage"],
        enable_preplant_empirical=data["enable_preplant_empirical"],
        # Manifests frozen before the switch existed scored without it.
        enable_trade_credit=data.get("enable_trade_credit", False),
        # Written only when True, so absent means False (_V4_FLAGS).
        enable_decided_only_time=data.get("enable_decided_only_time", False),
        remove_post_decided_assists=data.get("remove_post_decided_assists", False),
    )


def config_from_manifest(manifest: dict, comparator: str | None = None) -> ImpactScoringConfig:
    name = comparator or manifest["release_comparator"]
    if name not in manifest["comparators"]:
        raise ManifestMismatchError([f"comparator {name!r} is not in the manifest"])
    return config_from_dict(manifest["comparators"][name])


# ---- source snapshots ------------------------------------------------------------------

# The source-fingerprint CONTRACT: which rows, which columns, which projection.
# Fingerprints from different contracts are never comparable, so the version is
# recorded beside every set of them (a manifest's source_snapshots, an export's
# inputs) and a comparison across versions is refused. A missing version is 1.
#   1  rc3: match_players, rounds, stats, events (declared 2026-09-16).
#   2  Impact v4 (declaration 13, plan R1): events gain the assistants payload
#      of kill_events.source_meta, and `players` adds each match player's
#      player_id and Player.display_name -- remove_post_decided_assists reads
#      both. Nothing in v1 is dropped.
SOURCE_FINGERPRINT_VERSION = 2
_UNVERSIONED_FINGERPRINTS = 1

_FINGERPRINT_QUERIES = {
    "match_players": "SELECT id, team, agent FROM match_players WHERE match_id = :m ORDER BY id",
    "players": ("SELECT mp.id, mp.player_id, p.display_name FROM match_players mp "
                "JOIN players p ON p.id = mp.player_id WHERE mp.match_id = :m ORDER BY mp.id"),
    "rounds": ("SELECT id, round_number, outcome, planted, plant_time, exploded, defused, defuse_time "
               "FROM rounds WHERE match_id = :m ORDER BY round_number"),
    "stats": ("SELECT s.round_id, s.match_player_id, s.score, s.kills, s.deaths, s.assists, "
              "s.loadout, s.remaining FROM round_player_stats s JOIN rounds r ON r.id = s.round_id "
              "WHERE r.match_id = :m ORDER BY s.round_id, s.match_player_id"),
    "events": ("SELECT k.id, k.round_id, k.killer_match_player_id, k.death_match_player_id, "
               "k.event_time_seconds, k.weapon, k.source_meta FROM kill_events k "
               "JOIN rounds r ON r.id = k.round_id WHERE r.match_id = :m ORDER BY k.id"),
}


def _assistants_projection(source_meta):
    """The one part of kill_events.source_meta the scorer reads, canonically.

    Computed here rather than in SQL so the same contract holds on every
    dialect the fingerprint runs on (PostgreSQL returns a json column parsed,
    sqlite as text). None when there is no assistants key at all, which keeps
    "no assistants recorded" distinct from an empty list."""
    if isinstance(source_meta, str):
        source_meta = json.loads(source_meta)
    if not isinstance(source_meta, dict) or "assistants" not in source_meta:
        return None
    return json.dumps(source_meta["assistants"], sort_keys=True, separators=(",", ":"))


def _fingerprint_rows(name: str, rows):
    if name == "events":
        rows = [(*row[:-1], _assistants_projection(row[-1])) for row in rows]
    return [[str(value) if value is not None else None for value in row] for row in rows]


def match_source_fingerprint(db, match_id: int) -> str:
    """Content hash of exactly the rows build_impact_rows_for_match reads for
    one match, under contract SOURCE_FINGERPRINT_VERSION. Scores depend on
    nothing else, so an unchanged fingerprint under an unchanged manifest
    reproduces the approved values even after unrelated matches are ingested."""
    content = {
        name: _fingerprint_rows(name, db.execute(text(sql), {"m": match_id}).all())
        for name, sql in _FINGERPRINT_QUERIES.items()
    }
    if not content["match_players"]:
        raise ManifestMismatchError([f"match {match_id} has no source rows"])
    return _canonical_sha256(content)


def fingerprint_contract_problem(recorded_version, where: str) -> str | None:
    """A difference line when fingerprints recorded under `recorded_version`
    (None: recorded before versions existed, i.e. 1) cannot be compared with
    this checkout's, else None."""
    version = _UNVERSIONED_FINGERPRINTS if recorded_version is None else recorded_version
    if version != SOURCE_FINGERPRINT_VERSION:
        return (f"{where}: source fingerprints use fingerprint contract v{version}, this checkout "
                f"computes v{SOURCE_FINGERPRINT_VERSION}; they are not comparable")
    return None


def verify_source_snapshots(db, manifest: dict, match_ids=None) -> None:
    problem = fingerprint_contract_problem(
        manifest.get("source_snapshots", {}).get("fingerprint_version"), "manifest")
    if problem:
        raise ManifestMismatchError([problem])
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

# Appended to the formula description when the bonus-denial model is the release comparator,
# so a frozen manifest never describes a different formula than the one it scores.
_BONUS_FORMULA_CHANGE = (
    "economy: round 2/14 bonus-round denial -- a pistol winner's first death with paid kit net of agent "
    "utility above 1,500 pays V*1.10*net denied/19,500 to the killer (V 0.8 won / 1.0 lost) and 80% of that "
    "as the victim's debit, net of inferred pickups (spec 2026-09-12)",
)


def _weights_and_credit_changes(release_comparator: str) -> tuple[str, ...]:
    """The weights and trade-credit lines of `formula_changes_vs_live_legacy`,
    derived from the comparator actually being released.

    They used to be hard-coded as "A(damage)=1.25, B=1.0, C=1.0" and "trade
    discount: unchanged", which was true for rc2 and silently false for anything
    else. A frozen manifest that misdescribes what it freezes is worse than one
    that says nothing, because the review reads it as the change summary.
    """
    config = COMPARATORS.get(release_comparator)
    if config is None:
        return (f"weights: release comparator {release_comparator!r} is not declared in code",)
    weights = config.weights
    lines = [
        f"weights: A(damage)={weights.damage}, B(leverage)={weights.leverage}, "
        f"C(econ)={weights.econ}, D(assists)={weights.assists}; ECON_SCALE unchanged",
    ]
    if config.enable_trade_credit:
        lines.append(
            f"trade credit: ON at scale {weights.trade_credit_scale} -- a player who is traded is "
            "credited a share of the trade kill's leverage on the declared schedule, added on top "
            "of the trader; the trade discount itself is unchanged")
    else:
        lines.append("trade discount: unchanged (the declared cost schedule and 6s window); "
                     "trade credit OFF")
    return tuple(lines)


def _timing_and_assists_changes(release_comparator: str) -> tuple[str, ...]:
    """The timing line (and, for v4, the assists line) of
    `formula_changes_vs_live_legacy`, generated from the release comparator's
    flags (plan R11) -- a hard-coded "unchanged legacy time factor" would be
    frozen into a v4 manifest that changes exactly that."""
    config = COMPARATORS.get(release_comparator)
    if config is None or not config.enable_decided_only_time:
        lines = ["timing: unchanged legacy time factor (post-plant table and pre-plant curve OFF)"]
    else:
        lines = ["timing: NO time factor -- T = 1 for every kill and death before and after the plant "
                 "(no ramp, no plant+38..45 override), and T = 0 once the round is decided: defused "
                 "and at/after the defuse, a real plant at/after plant+45, or an unplanted Time Win "
                 "after 100s (declaration 12); post-plant table and pre-plant curve OFF"]
    if config is not None and config.remove_post_decided_assists:
        lines.append("assists: an assist on a kill made after the round was decided is not paid "
                     "(D per assist), clamped to the scoreboard count; damage keeps its "
                     "combat-score assist points")
    return tuple(lines)


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
            *(_BONUS_FORMULA_CHANGE if release_comparator == V2_30_80_BONUS else ()),
            "columns: econ_impact and swing_impact are written 0; econ_component is signed",
            *_timing_and_assists_changes(release_comparator),
            *_weights_and_credit_changes(release_comparator),
        ],
        "timing": {"postplant_leverage": False, "preplant_empirical": False,
                   "postplant_table_sha256": None, "preplant_centering_c": None},
        "impact_calculation_version_at_freeze": impact.IMPACT_CALCULATION_VERSION,
        "activation_impact_calculation_version": activation_impact_calculation_version,
        "scorer_revision": scorer_revision,
        # The snapshots were taken by this checkout, so under its contract.
        "source_snapshots": {**source_snapshots, "fingerprint_version": SOURCE_FINGERPRINT_VERSION},
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
    problem = fingerprint_contract_problem(
        (manifest.get("source_snapshots") or {}).get("fingerprint_version"), "source_snapshots")
    if problem:
        diffs.append(problem)
    if diffs:
        raise ManifestMismatchError(diffs)
