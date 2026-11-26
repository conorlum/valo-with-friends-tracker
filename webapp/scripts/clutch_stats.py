"""Pure, DB-free clutch research statistics and temp-only Markdown/CSV output.

W5 row contract: one flat dict per player, with W1's situation_id, kind (1vN or
2vN), team, side (attack/defense), slot, n_enemies, own_alive, t_s,
t_end_window, won (in THIS player's side frame), alive_at_end, group and
n_at_start. Add uuid, round, match_id (the bootstrap/holdout cluster),
clock_offset, t_plant (replay seconds, None if no plant), label (ONLY the
display string returned by labels), agent, and these nested W1-W3 results:
clock, state, isolation (isolation_summary), engagement_rows (engagements with
in_view/clear_line counts), unknown, backshots (scalar), information, fakes,
util. Optional enemy_agents maps enemy slots to agent names for examples;
never put an enemy's display name there. No player_id/name is exported.
expected and fit_leave_one_match_out fill p_stored and p_fit in place and
return the rows. All times passed to util_model are converted to the DB clock.

MEASURES is a sequence of {name, unit, kind, from: callable(row)}. Missing
evidence stays None. Tier values from coach.fights._tier are already integers;
enemy summaries require every enemy's tier. Timely plant/defuse scalars mean
an attempt started with enough time, not whether it completed; defuse_half
means any attempt reached half. No attempts means False in an applicable
window; unknown timing stays None. fake_followed is null without fakes.

compare returns n, contributing matches (total and per side), outcome
counts/medians, difference and interval (won minus lost), status, and a
residual dict with n, split, lower_n/upper_n, lower_mean/
upper_mean, difference, interval and status. Residuals are outcome - p_stored;
the split is by bool value or the observed group median, ties in the lower
half. Rows missing predictions drop only from residual calculations. A constant
has no interval. results passed to candidates/write_report are lists of these
comparison dicts. write_report fills any missing measure/group comparisons.
Intervals require at least two contributing matches overall and on each side
of their contrast, after filtering missing measures or predictions.

labels(contexts, friends) takes replay page contexts and roster strings (case
insensitive), returning {uuid: {int_slot: display_label}}. Numbering uses sorted
player_id over the entire run, excluding roster members. Report/CSV callers
must use these labels; arbitrary raw player labels are not a supported input.
Coverage is a nested dict of aggregate counts; cutoffs are W1 cutoff rows.
Clear-line isolation (exactly one / nonzero clear-line engagements) is CSV-only.
"""

from __future__ import annotations

import csv
import math
import random
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import stats_math, util_model  # noqa: E402

GROUPS = ("1v1", "1v2", "1v3+", "2v2", "2v3")
SIGHT_NOTE = (
    "The sight lines use the map's walls and smokes, without box heights or doors "
    "(worker assets the public site does not serve). A line over a box or through "
    "a door uses this 2D approximation; every row shares that approximation."
)
NO_EXPORT = "The stored model was not exported; only the held-out fit calibration is available."


def _get(section, key):
    return lambda row: (row.get(section) or {}).get(key)


def _planted(row):
    plant = row.get("t_plant")
    return plant is not None and plant <= row["t_s"]


def _attempt(row, kind, key):
    applies = row["side"] == ("attack" if kind == "plant" else "defense")
    applies = applies and (_planted(row) == (kind == "defuse"))
    if not applies:
        return None
    attempts = (row.get("clock") or {}).get(kind + "_attempts")
    if attempts is None:
        return None
    values = [a.get(key) for a in attempts]
    return True if any(v is True for v in values) else (None if None in values else False)


def _enemy_tiers(row):
    tiers = list((row.get("state", {}).get("enemy_gun_tiers") or {}).values())
    return tiers if tiers and all(t is not None for t in tiers) else None


def _enemy_best(row):
    tiers = _enemy_tiers(row)
    return max(tiers) if tiers else None


def _tier_gap(row):
    own, enemies = _get("state", "gun_tier")(row), _enemy_tiers(row)
    return own - mean(enemies) if own is not None and enemies else None


def _loadout_gap(row):
    values = row.get("state", {}).get("team_loadouts") or {}
    own = values.get(row["team"])
    enemies = [v for k, v in values.items() if k != row["team"]]
    return own - enemies[0] if own is not None and len(enemies) == 1 and enemies[0] is not None else None


def _measure(name, kind, source, unit=None):
    return {"name": name, "unit": unit or kind, "kind": kind, "from": source}


MEASURES = [
    _measure("one_at_a_time", "share", _get("isolation", "one_at_a_time")),
    _measure("engagements", "count", _get("isolation", "engagements")),
    _measure("unseen_engagements", "count", _get("isolation", "unseen")),
    *[_measure(k, "seconds", _get("clock", k)) for k in ("time_left_start", "time_left_first_engagement")],
    _measure("still_share", "share", _get("clock", "still_share")),
    _measure("planted_in_time", "bool", lambda r: _attempt(r, "plant", "enough_time")),
    _measure("defuse_in_time", "bool", lambda r: _attempt(r, "defuse", "enough_time")),
    _measure("defuse_half", "bool", lambda r: _attempt(r, "defuse", "reached_half")),
    *[_measure(k, "share", _get("unknown", k)) for k in (
        "unknown_share_start", "unknown_share_end", "into_unknown_share")],
    _measure("backshots", "count", lambda r: r.get("backshots")),
    _measure("located_share", "share", _get("information", "located_share")),
    _measure("fakes", "count", _get("fakes", "fakes")),
    _measure("fake_followed", "count", lambda r: _get("fakes", "fake_followed")(r)
             if _get("fakes", "fakes")(r) else None),
    *[_measure(k, "count", _get("util", k)) for k in ("held_start", "used", "held_at_death")],
    _measure("ult_ready", "bool", _get("util", "ult_ready")),
    _measure("own_tier", "tier", _get("state", "gun_tier")),
    _measure("enemy_best_tier", "tier", _enemy_best),
    _measure("tier_gap", "tier", _tier_gap),
    _measure("hp_start", "hp", _get("state", "hp_shield")),
    _measure("loadout_gap", "credits", _loadout_gap),
]
_BY_NAME = {m["name"]: m for m in MEASURES}


def _frame(row):
    own, enemy = row["own_alive"], row["n_enemies"]
    a, d = (own, enemy) if row["side"] == "attack" else (enemy, own)
    offset, plant = row.get("clock_offset") or 0, row.get("t_plant")
    return a, d, _planted(row), row["t_s"] + offset, None if plant is None else plant + offset


def _observation(row):
    return (*_frame(row), bool(row["won"]) if row["side"] == "attack" else not row["won"])


def expected(rows, model):
    """Store the exported model's prediction in each row's own side frame."""
    for row in rows:
        row["p_stored"] = None if model is None else util_model.p_side(model, row["side"], *_frame(row))
    return rows


def fit_leave_one_match_out(rows):
    """Hold out the entire match; train only on the same kind (1vN/2vN)."""
    by_kind = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_kind[row["kind"]][row["match_id"]].append(_observation(row))
    models = {}
    for kind, matches in by_kind.items():
        for held_out in matches:
            observations = [o for match, obs in matches.items() if match != held_out for o in obs]
            models[kind, held_out] = util_model.fit(observations) if observations else None
    for row in rows:
        model = models[row["kind"], row["match_id"]]
        row["p_fit"] = None if model is None else util_model.p_side(model, row["side"], *_frame(row))
    return rows


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def calibration(rows, key):
    """Bin stored forecasts, never re-predict using one fold's fitted model."""
    bins = defaultdict(list)
    for row in rows:
        p = row.get(key)
        if _finite(p) and 0 <= p <= 1:
            bins[min(int(p * 10), 9)].append((p, int(row["won"])))
    return [{"bin": b, "n": len(values), "predicted": mean(p for p, _ in values),
             "actual": mean(y for _, y in values)} for b, values in sorted(bins.items())]


def _clusters(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["match_id"]].append(row)
    return dict(groups)


def _interval(a, b, rows):
    ci = stats_math.paired_bootstrap_delta(a, b, _clusters(rows), draws=1000, seed=0)
    return ci if all(math.isfinite(v) for v in ci) else None


def _sample_stat(clusters, value, predicate, fn):
    values = [value(r) for cluster in clusters for r in cluster if predicate(r)]
    return fn(values) if values else None


def _partition(rows, measure):
    value = measure["from"]
    split = False if measure["kind"] == "bool" else median(value(r) for r in rows)
    return split, lambda r: value(r) > split


def compare(rows, measure, group):
    """Outcome medians and residual mean split, with paired match-cluster CIs."""
    measure = _BY_NAME[measure] if isinstance(measure, str) else measure
    value = measure["from"]
    eligible = [r for r in rows if r["group"] == group and _finite(value(r))]
    won_rows = [r for r in eligible if r["won"]]
    lost_rows = [r for r in eligible if not r["won"]]
    won = [value(r) for r in won_rows]
    lost = [value(r) for r in lost_rows]
    constant = bool(eligible) and len({value(r) for r in eligible}) < 2
    result = {"measure": measure["name"], "group": group, "n": len(eligible),
              "matches": len(_clusters(eligible)),
              "won_matches": len(_clusters(won_rows)), "lost_matches": len(_clusters(lost_rows)),
              "n_won": len(won), "n_lost": len(lost),
              "won_median": median(won) if won else None, "lost_median": median(lost) if lost else None,
              "difference": median(won) - median(lost) if won and lost else None,
              "interval": None, "status": "constant" if constant else "not estimable"}
    if not constant and min(result["matches"], result["won_matches"], result["lost_matches"]) >= 2:
        result["interval"] = _interval(
            lambda cs: _sample_stat(cs, value, lambda r: r["won"], median),
            lambda cs: _sample_stat(cs, value, lambda r: not r["won"], median), eligible)
        if result["interval"] is not None:
            result["status"] = "estimated"
    predicted = [r for r in eligible if _finite(r.get("p_stored"))]
    residual = {"n": len(predicted), "lower_n": 0, "upper_n": 0, "split": None,
                "matches": len(_clusters(predicted)), "lower_matches": 0, "upper_matches": 0,
                "lower_mean": None, "upper_mean": None, "difference": None,
                "interval": None, "status": "constant" if constant else "not estimable"}
    if eligible:
        split, high = _partition(eligible, measure)
        residual["split"] = split
        score = lambda r: int(r["won"]) - r["p_stored"]
        for upper, key in ((False, "lower"), (True, "upper")):
            half = [r for r in predicted if high(r) == upper]
            values = [score(r) for r in half]
            residual[key + "_n"] = len(values)
            residual[key + "_matches"] = len(_clusters(half))
            residual[key + "_mean"] = mean(values) if values else None
        if not constant and residual["lower_n"] and residual["upper_n"]:
            residual["difference"] = residual["upper_mean"] - residual["lower_mean"]
        if not constant and min(residual["matches"], residual["lower_matches"], residual["upper_matches"]) >= 2:
            residual["interval"] = _interval(
                lambda cs: _sample_stat(cs, score, high, mean),
                lambda cs: _sample_stat(cs, score, lambda r: not high(r), mean), predicted)
            if residual["interval"] is not None:
                residual["status"] = "estimated"
    result["residual"] = residual
    return result


def _pick(rows, rng):
    ordered = sorted(rows, key=lambda r: (str(r["uuid"]), int(r["round"]), r["t_s"], int(r["slot"])))
    return rng.sample(ordered, min(5, len(ordered)))


def candidates(results, rows, seed=0):
    """Descriptive residual patterns only; no recommendation language."""
    rng, selected = random.Random(seed), []
    for result in results:
        residual = result["residual"]
        ci = residual["interval"]
        if (residual["status"] != "estimated" or not ci or not all(map(math.isfinite, ci))
                or not (ci[0] > 0 or ci[1] < 0) or min(residual["lower_n"], residual["upper_n"]) < 20):
            continue
        measure = _BY_NAME[result["measure"]]
        eligible = [r for r in rows if r["group"] == result["group"]
                    and _finite(measure["from"](r)) and _finite(r.get("p_stored"))]
        high = lambda r: measure["from"](r) > residual["split"]
        positive = residual["difference"] > 0
        selected.append({**result, "pattern_half": "upper" if positive else "lower",
                         "followed": _pick([r for r in eligible if high(r) == positive], rng),
                         "did_not": _pick([r for r in eligible if high(r) != positive], rng)})
    return selected


def labels(contexts, friends):
    """Return roster names or stable anonymous numbers across replay slots."""
    friends = {name.casefold() for name in friends}
    contexts = list(contexts)
    named_ids = {p["player_id"] for ctx in contexts for p in ctx["players"].values()
                 if p["name"].casefold() in friends}
    strangers = {p["player_id"] for ctx in contexts for p in ctx["players"].values()
                 if p["player_id"] not in named_ids}
    # JSON can carry integer IDs as strings; keep their numeric ordering.
    def order(pid):
        text = str(pid)
        return (0, int(text)) if text.isdecimal() else (1, text)
    numbers = {pid: i for i, pid in enumerate(sorted(strangers, key=order), 1)}
    names = {p["player_id"]: p["name"] for ctx in contexts for p in ctx["players"].values()
             if p["name"].casefold() in friends}
    return {ctx["match"]["uuid"]: {
        int(slot): names[p["player_id"]] if p["player_id"] in named_ids else f"player {numbers[p['player_id']]}"
        for slot, p in ctx["players"].items()} for ctx in contexts}


def _output(out):
    path = Path(out).resolve()
    if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("output must be under the system temp folder")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clear_line(row):
    counts = [r["clear_line"] for r in row.get("engagement_rows", []) if r.get("clear_line") is not None]
    seen = sum(c > 0 for c in counts)
    return sum(c == 1 for c in counts) / seen if seen else None


def write_csv(out, rows):
    """Export only explicit scalars; never serialize raw contexts or row dicts."""
    path = _output(out) / "clutches.csv"
    fields = ["uuid", "round", "situation_id", "kind", "group", "side", "n_enemies", "own_alive",
              "t_s", "t_end_window", "won", "alive_at_end", "label", "agent", "p_stored", "p_fit"]
    columns = fields + [m["name"] for m in MEASURES] + ["clear_line_isolation"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            data = {key: row.get(key) for key in fields}
            data.update({m["name"]: m["from"](row) for m in MEASURES})
            data["clear_line_isolation"] = _clear_line(row)
            writer.writerow(data)
    return path


def _text(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _number(value):
    return f"{value:.4g}" if _finite(value) else "—"


def _ci(interval, status):
    return f"[{_number(interval[0])}, {_number(interval[1])}]" if interval else status


def _table(lines, headers, records):
    lines.extend(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"])
    lines.extend("| " + " | ".join(_text(c) for c in record) + " |" for record in records)
    lines.append("")


def _example(row, measure):
    agents = sorted(set((row.get("enemy_agents") or {}).values()))
    enemy = "; " + ", ".join("the enemy " + _text(a) for a in agents) if agents else ""
    link = f"/replays/{row['uuid']}?round={int(row['round'])}&t={row['t_s']:g}"
    return (f"- {_text(row['label'])} ({_text(row['agent'])}) [{row['group']}]({link}), "
            f"outcome {'won' if row['won'] else 'lost'}, "
            f"{measure['name']}={_number(measure['from'](row))}{enemy}")


def _counts(rows, key):
    situations, player_rows = defaultdict(set), Counter()
    for row in rows:
        category = key(row)
        situations[category].add(row["situation_id"])
        player_rows[category] += 1
    return {k: (len(v), player_rows[k]) for k, v in situations.items()}


def _by_n(row):
    return f"{2 if row['kind'] == '2vN' else 1}v{row['n_enemies']}"


def _coverage_lines(coverage, prefix=""):
    lines = []
    for key, value in sorted(coverage.items()):
        label = prefix + str(key)
        if isinstance(value, dict):
            lines.extend(_coverage_lines(value, label + " / "))
        elif _finite(value):
            lines.append(f"- {_text(label)}: {_number(value)}")
    return lines


def write_report(out, rows, results, coverage, cutoffs, model_note):
    """Write report.md, including every measure/group even when unestimable."""
    path = _output(out) / "report.md"
    stored = any(_finite(r.get("p_stored")) for r in rows)
    comparisons = {(r["measure"], r["group"]): r for r in results}
    lines = [SIGHT_NOTE, "", "# Clutch research", "", _text(model_note), ""]
    if not stored and model_note != NO_EXPORT:
        lines.extend([NO_EXPORT, ""])
    lines.extend(["## Coverage", "", *_coverage_lines(coverage), "", "## Situation counts", ""])
    by_n, by_group = _counts(rows, _by_n), _counts(rows, lambda r: r["group"])
    lines.extend(["### By N", ""])
    _table(lines, ["N", "Situations", "Player rows"], [(k, *v) for k, v in sorted(by_n.items())])
    lines.extend(["### By comparison group", ""])
    _table(lines, ["Group", "Situations", "Player rows"], [(g, *by_group.get(g, (0, 0))) for g in GROUPS])
    lines.extend(["## Cutoff reconciliation", "", "Eligible plus cutoff situations, counted once per situation_id.", ""])
    excluded = _counts(cutoffs, _by_n)
    _table(lines, ["N", "Eligible", "Cutoff", "Total"], [
        (k, by_n.get(k, (0, 0))[0], excluded.get(k, (0, 0))[0],
         by_n.get(k, (0, 0))[0] + excluded.get(k, (0, 0))[0]) for k in sorted(set(by_n) | set(excluded))])
    lines.extend(["## Measures", "", "Nulls drop from that measure only. Differences are won minus lost medians; "
                  "95% intervals resample whole matches (1,000 draws, seed 0). "
                  "Intervals require at least two matches overall and on each side of the contrast. "
                  "Constant measures have no interval. Median ties enter the lower half; bools split by value.", ""])
    if stored:
        lines.extend(["Residual difference is mean(outcome − p_stored) in the upper half minus the lower half.", ""])
    ordered = []
    for measure in MEASURES:
        for group in GROUPS:
            result = comparisons.get((measure["name"], group))
            if result is None:
                result = compare(rows, measure, group)
            ordered.append(result)
            lines.extend([f"### {measure['name']} / {group}", "", f"Unit: {measure['unit']}", ""])
            headers = ["n", "Matches", "Won n", "Won matches", "Lost n", "Lost matches",
                       "Won median", "Lost median", "Difference", "95% interval"]
            values = [result["n"], result["matches"], result["n_won"], result["won_matches"],
                      result["n_lost"], result["lost_matches"], _number(result["won_median"]),
                      _number(result["lost_median"]), _number(result["difference"]),
                      _ci(result["interval"], result["status"])]
            if stored:
                residual = result["residual"]
                headers += ["Residual n", "Residual matches", "Lower n", "Lower matches", "Upper n", "Upper matches",
                            "Split", "Residual difference", "Residual 95% interval"]
                values += [residual["n"], residual["matches"], residual["lower_n"], residual["lower_matches"],
                           residual["upper_n"], residual["upper_matches"], _number(residual["split"]),
                           _number(residual["difference"]), _ci(residual["interval"], residual["status"])]
            _table(lines, headers, [values])
    lines.extend(["## Calibration", "", "Forecasts and observed outcomes use each row's own side frame.", ""])
    for key in (("p_stored", "p_fit") if stored else ("p_fit",)):
        lines.extend([f"### {key}", ""])
        _table(lines, ["Bin (0–9)", "n", "Predicted", "Observed"], [
            (r["bin"], r["n"], _number(r["predicted"]), _number(r["actual"])) for r in calibration(rows, key)])
    if stored:
        lines.extend(["## Candidate patterns for the owner", ""])
        selected = candidates(ordered, rows)
        if not selected:
            lines.extend(["No intervals meet the selection rule.", ""])
        for candidate in selected:
            residual = candidate["residual"]
            lines.extend([f"### {candidate['measure']} / {candidate['group']}", "",
                          f"Residual difference {_number(residual['difference'])}, n={residual['n']} "
                          f"(lower {residual['lower_n']}, upper {residual['upper_n']}), "
                          f"95% interval {_ci(residual['interval'], residual['status'])}.", "",
                          f"Pattern followed ({candidate['pattern_half']} half):", ""])
            measure = _BY_NAME[candidate["measure"]]
            lines.extend(_example(r, measure) for r in candidate["followed"])
            lines.extend(["", "Pattern not followed:", ""])
            lines.extend(_example(r, measure) for r in candidate["did_not"])
            lines.append("")
    lines.extend(["## Isolation examples", ""])
    rng = random.Random(0)
    for group in GROUPS:
        eligible = [r for r in rows if r["group"] == group]
        if not eligible:
            continue
        lines.extend([f"### {group}", ""])
        for won in (True, False):
            lines.extend(_example(r, _BY_NAME["one_at_a_time"])
                         for r in _pick([r for r in eligible if bool(r["won"]) == won], rng))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
