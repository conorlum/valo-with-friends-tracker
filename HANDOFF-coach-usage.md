# HANDOFF: make the coach use less usage

Goal: cut the tokens (and plan usage) one coach run takes, without making reports worse.
Started 2026-11-15. Nothing built yet. Usage tracking that this work measures with is PR #234
(`scripts/coach_report.py`: a `usage:` line per run, `<run dir>/usage.json`, `~/.valo-coach/usage.jsonl`).

## Where the usage goes (measured, one run)

Match `1d95930d-1058-4ed5-a181-fabb63169d11`, player NPrightdolphin#NA1 (player 5), 2026-11-15, published v1:

- **Model: `claude-fable-5-2`.** No `--model` was passed, so headless Claude Code used its default, the
  heaviest model. This is probably the biggest single lever.
- 2 calls (first prompt + one repair via `--resume`), **57 turns**, 4.5 min.
- Top-level `usage`: 18 fresh input, 0.16M cache writes, 0.70M cache reads, 0.02M output = 0.89M tokens.
- Almost everything is cache reads: every turn re-reads the whole growing context. Turns × context size is
  what drives the cost.
- The run log (`log.jsonl`) has 240 entries: 185 are the brief's flags, logged by `runner._brief_with_flag_ids`
  and not model calls. The model's own queries were about **55**: `knew` 17 (77k chars of answers, about 4.5k
  each), `timeline` 20 (67k), `where` 17 (43k), `distance` 1. That's about 186k chars of answers fed back into
  context, about 50k tokens that every later turn re-reads.
- `q` takes **one query per invocation**, so each query is its own Bash tool call and its own turn.
- The playbook (`webapp/coach/playbook.md`) is about 18.6k chars and goes into every first prompt (cached after
  the first turn).

A second player (IP Thoaiyama#Phan) used 0.93M tokens in one call and 62 turns before its repair call failed:
the same shape. Its next call and two more players' first calls then exited 1 with empty stderr: the reason was
on stdout, which the script discarded. That's fixed in PR #234. A Fable limit is a plausible cause but
unconfirmed.

**The rerun (same match, about 30 min later) silently ran on `claude-opus-5-5`, not Fable**, with no `--model`
passed, so the CLI's default changed between runs. Per player (top-level `usage`):

| Player | Model | Turns | Total tokens | Cache writes | Output | Published |
|---|---|---|---|---|---|---|
| player 5 | Fable 5.2 | 57 | 0.89M | 0.16M | 0.025M | 6 points, 0 dropped |
| player 73 | Opus 5.5 | 57 | 1.08M | 0.11M | 0.014M | 6 points, 0 dropped, 1 unchecked |
| player 451 | Opus 5.5 | 57 | 2.49M | 0.14M | 0.022M | 6 points, 0 dropped |
| player 16 | Opus 5.5 | 69 | 2.02M | 0.15M | 0.025M | 6 points, 0 dropped |

The Opus runs used as many turns as Fable or more, and more cache reads. Switching model saves plan usage only
if Opus weighs less per token against the plan, while the turn count is model-independent. That makes lever 2
(batching) more important than first thought. Compare report quality across these four before choosing a model.

## Model comparison (2026-11-15, player 5, same match and prompt, n=1 each)

Fable is the published v1. Opus, Sonnet and Haiku were `--dry-run`, run one after another; copies are in the
session scratchpad `compare/` (temporary). Tokens are the top-level `usage`.

| Model | Total | Cache reads | Cache writes | Output | Turns | `q` queries | Unchecked |
|---|---|---|---|---|---|---|---|
| Fable 5.2 | 0.89M | 0.70M | 0.16M | 0.025M | 57 | 55 | 0 |
| Opus 5.5 | 2.72M | 2.53M | 0.17M | 0.023M | 24 | 85 | 0 |
| Sonnet 5.5 | 0.47M | 0.33M | 0.12M | 0.015M | 33 | 31 | 2 |
| Haiku 5.5 | 1.46M | 1.26M | 0.16M | 0.038M | 39 | 41 | 1 |

Quality (read by Claude, owner to confirm):
- Fable and Opus: same tier. Both find patterns across rounds (trades, info util that led to kills, dying alone
  after the plant with a man advantage, lone contact out of trade range), speak to the player, and have 0
  unchecked. Opus added a good rotation point (r27).
- Sonnet: noticeably worse. It recites the data ("has a value of 10.57", "the util facts flag a cage as
  wasted") and writes in the third person. One point isn't coaching ("Multi-kill defence rounds"), and two points
  failed the checker. One new, valid point: the ult held through five rounds.
- Haiku: worst. Every point is a single moment (1 instance each), so it finds no patterns. The writing is a
  timestamp log ("the player"), and one point failed the checker. It also isn't cheap: 1.46M tokens, more than Fable.

Usage-number quirk: `by_model` (from `modelUsage`) is 1.6–1.9× the top-level figure in all four runs. This fits
the resumed repair call's `modelUsage` including the first call's totals, so `by_model` double-counts; still
to confirm.

## Levers, roughly in order of expected payoff

1. **Pick the model explicitly.** `coach.ps1 -Model` already exists. Try Opus 5.5 and Sonnet 5.5 against Fable on
   the same match and player, and compare report quality (checker pass rate, `unchecked` count, the owner's
   read). If one is good enough, make it the default in `coach_report.py` instead of the CLI default.
   This is the owner's call on quality.
2. **Batch queries in `q`.** Let one invocation take several queries (e.g. `./q batch` with one per line on
   stdin, or repeated `--` groups), each still logged with its own id so `checker.trusted_log` keeps working.
   Tell the model in `app/coach/prompt.py` to batch. About 55 turns could become about 10–15; the cache reads
   shrink roughly in proportion.
3. **Front-load what every run asks.** Look across several runs' `log.jsonl` for queries the model nearly always
   makes (e.g. `timeline` for each round with a flag, `knew` at each flag's time). Put those answers into the
   brief with logged ids, the way flags already are, so the model doesn't spend turns fetching them.
4. **Trim answers.** `knew` averages about 4.5k chars. Check which fields the model actually cites
   (`payload.json` evidence ids → log entries) and drop or compress the rest (shorter keys, rounded numbers,
   omit empty lists). Any field the checker reads must stay.
5. **Cheaper repair.** The repair call `--resume`s the whole session. Consider a fresh, small call holding only
   the failing points, their problems and the cited log entries. Only where that doesn't weaken the checker's
   evidence rule.
6. **Playbook size.** It's cached, so trimming it saves little per turn. Lowest priority, and only the owner
   edits it (bump `version`).

## Open question: are the usage numbers right?

In the same run's `usage.json`, the per-model total (`by_model`, from the CLI's `modelUsage`) is bigger than
the top-level one (from `usage`): 45,886 vs 24,820 output tokens, 1.12M vs 0.70M cache reads. Either the
resumed second call's `modelUsage` repeats the first call's totals (so `by_model` double-counts), or `usage`
covers less than the whole call (so the headline undercounts). Settle this first with a two-call probe that
records each call's raw JSON, and fix `add_usage` before comparing levers. Otherwise before/after numbers
can't be trusted.

## How to measure each change

- Same match, same player, one change at a time. Runs go one after another, never two at once.
- Compare from `~/.valo-coach/usage.jsonl`: total tokens, cache writes, output, turns. Also compare quality:
  points published, `dropped_points`, `unchecked`.
- `-DryRun` stores nothing, so experiments don't add report versions on the site.
- Watch `/usage` in Claude Code before and after for the plan-% view. The CLI's JSON doesn't report it.

## Constraints (from CLAUDE.md, keep)

- Never add a flag that skips permission checks. The model keeps only Bash with `./q`.
- The checker's evidence rules don't loosen to save tokens.
- Playbook edits are the owner's. No names or match ids in the public repo.
