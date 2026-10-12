# AI coach - design specification

Date: October 12, 2026. Status: design only; no application changes. Branch `worktree-ai-coach`, cut from
`origin/main` at `c1b6659`.

## Outcome

A player opens a match that has a replay, picks themselves, and reads a coaching report written in the owner's
voice: three things they did well and three things to improve. Every point links to each moment in the match
where it happened, and each link moves the replay to that round and second. Readers mark points right or wrong,
and that feedback is used, with the owner deciding, to improve the coach.

The coach is a language model working from computed facts. It combines three things the site already has:
the condensed replay (positions, kills, utility, damage, health), map control (including what each team knew),
and Impact scores.

## Decisions (from the design interview, 2026-10-12)

| # | Decision |
| --- | --- |
| Subject | One player per report. A single/team toggle is shown; team is disabled behind a "coming soon" banner. |
| Phases | 1: match report. 2: cross-match report. 3: per-round findings. The owner says when to move on. |
| Report | Three good points, three to improve. A point links to every instance of the thing in the match, not a sample. |
| Finding | Code supplies a brief and flagged moments; the model can also query round data to test its own ideas. |
| Grounding | The coach says only what the data backs. No free impressions, ever. |
| Knowable | A mistake must be knowable at the time. Knowable means what the player's team had seen or heard, assuming perfect comms for enemy locations. |
| Impact | The main pointer to where to look, not the only one. Quoted as support, not as the explanation. |
| Topics | Whatever the data can back up. Nothing about aim, comms or anything else the replay does not record. |
| Teammates | Named as fact when it explains a moment. Advice is always addressed to the coached player. |
| Coverage | Matches with a replay only. Incomplete data is still coached, and the report says what was missing. |
| Training | A playbook written from the owner's principles, plus a feedback loop. No fine-tuning. |
| Publishing | Straight to the site with no review step. Anyone can read any report. |
| Versions | Each report is stamped with the playbook version. Regenerating keeps the earlier versions. |
| Feedback | Per point only: right, wrong or badly put, with a note. A disputed point stays visible. |
| Prompt out | A button on a report, visible to everyone for now, that produces text to paste into a Claude session. |
| Trust | Feedback is a claim to check. Where it lacks context or conflicts, the session shows that and the owner decides. |
| Running | A `coach` shell command on the owner's PC, like `matches`, using headless Claude Code on the owner's subscription. Manual first, automatic for friend-group players later. |
| Page | A Coach column beside Watch. The coaching page is the replay viewer's code with a coach panel. |

## Phase 1 scope

In: the match brief, the query commands, the coach run, storage, the coaching page, feedback, prompt out.

Out, and not designed here beyond what phase 1 must leave ready: cross-match reports, per-round findings, team
reports, automatic runs. Impact scoring is read and never changed, so `SCORING-RELEASE-PROCESS.md` does not apply.

## Architecture

Four parts. Only the third calls a model.

```
friends DB ──> 1. match brief ──┐
     ^                          ├──> 3. coach run ──> checker ──> coach tables ──> 4. coaching page
     └──────── 2. query commands┘        ^                                              │
                                    playbook + voice                    feedback ──> prompt out ──> Claude session
```

### 1. Match brief (`app/coach/brief.py`, no model)

Input: a linked replay and one player. Output: one JSON document the run hands to the model.

- **Match frame**: map, sides per half, score by round, the player's agent, teammates and opponents by name.
- **Per round**: outcome, economy state of both teams, the player's Impact and its components, their kills and
  deaths with times, whether each death was traded, utility they used and what it hit, plant and defuse.
- **Ranked moments**: the player's rounds ordered by the size of their Impact swing, both directions. This is
  the main list of places to look.
- **Flags**: moments found by detectors (below), each with round, time and the measured values.
- **Data gaps**: per round, which sources are absent or stale (control, team knowledge, player state, kill
  lines, timing gaps). Copied into the stored report.

Detectors are pure functions over one round, each with its own tests and its thresholds declared in one module.
The first set, chosen to cover the owner's stated example and the things Impact cannot see:

- Death with no teammate inside trade range, and death that was traded.
- The team had seen N enemies in one area while the player stayed far from the other (the free rotate or fast lurk).
- Utility that hit no one, and utility that hit and was followed by a kill.
- Death while the team held a man advantage.
- First contact of the round involving the player.

The list grows from feedback. Thresholds (trade range, "far", N) are set in the implementation plan from a look
at real rounds, not guessed here.

### 2. Query commands (`scripts/coach_query.py`, read-only)

Small commands the model calls during a run. Every answer is computed from stored data.

- `where <round> <t>`: every living player's position, named map area and facing.
- `knew <round> <t> <side>`: the enemies that side had seen or heard so far, with when and where.
- `distance <round> <t> <player> <player>`: metres between two players.
- `control <round> <t0> <t1> <area>`: which side held the area over the span.
- `timeline <round> [t0 t1]`: kills, damage, utility, plant and defuse in order.
- `impact <round>`: the player's Impact row for the round.

Each call is appended to a run log with an id, its arguments and its result. A point's evidence cites log ids.
That log is what makes grounding checkable.

When a source is missing for a round, the command says so plainly instead of returning an empty answer.

### 3. Coach run (`scripts/coach.ps1`, `scripts/coach_report.py`, `app/coach/`)

`coach <match> <player>` on the owner's PC, against the friends database through `webapp/.env.remote`, the
same way `matches` reaches it.

1. Build the brief.
2. Start headless Claude Code with the playbook, the voice samples, the brief, and permission to run the query
   commands and nothing else.
3. The model returns the report as JSON in a fixed shape: six points, each with a polarity, a theme, a title,
   a body, and a list of instances. An instance is a round, a time (or span), a one-line caption and the log ids
   it rests on.
4. The checker (below) passes or rejects each point.
5. A rejected point is sent back once with the reason. A point that fails twice is dropped, and the report is
   stored with fewer points and says so.
6. Store the report as the next version for that match and player.

**The checker** is plain code and decides what may be published:

- Every instance's round exists in the replay and its time lies inside the round.
- Every cited log id exists in this run's log.
- Every number in the point's text appears in a cited result.
- Every player named is in the match.
- A point that calls something a mistake cites a `knew` result from before the moment, unless the round has no
  team-knowledge data, in which case the point must say so.

**Themes.** Each point carries one theme from a short list defined in the playbook (for example trading,
rotation, utility, post-plant). Phase 2 counts themes across matches, so the list is part of the playbook
version and a point with an unknown theme is rejected.

**Playbook** (`webapp/coach/playbook.md`, in this public repository): the owner's principles in the owner's
words, the theme list, and the rules above stated for the model. It carries a version number, bumped by hand on
any edit. It contains no player names, match ids or credentials.

**Voice samples**: a few sentences of the owner giving feedback, stored in the database (`coach_notes`), not in
the repository.

### 4. Site

**Storage** (one migration, `0021`):

- `coach_reports`: match, player, kind (`match`), version (1, 2, ... per match and player), playbook version,
  model name, data gaps, created time.
- `coach_points`: report, polarity (`good` or `improve`), order, theme, title, body, instances (JSON).
- `coach_feedback`: point, author player, verdict (`right`, `wrong`, `badly_put`), note, created time, handled time.
- `coach_notes`: kind (`voice`), body, active flag.

**Coach column.** In the match list and the session detail table, beside Watch: a link when the match has at
least one report, a dash otherwise.

**Coaching page**, `GET /replays/{match_uuid}/coach?player=&version=`. It is `replays/replay.html` and
`replay.js` with a coach panel switched on, not a copy, so it follows every later change to the viewer.

- Player picker listing the players who have a report for this match.
- Single/team toggle; team is disabled and shows "coming soon".
- The six points, good then improve. Each lists all its instances; clicking one loads that round and seeks to
  that second in the map beside it.
- The report's playbook version, its data gaps, and a version picker when earlier versions exist.
- Per point: the verdict buttons and a note box for logged-in viewers, and the feedback already given. A point
  with any `wrong` verdict shows a "disputed" mark and stays in place.
- A "Prompt out" button.

**Feedback**, `POST /replays/{match_uuid}/coach/points/{id}/feedback`. Logged-in viewers only.

**Prompt out**, `GET /replays/{match_uuid}/coach/prompt?report=`. Plain text for the report on screen:

- The report, its playbook version and its data gaps.
- Each point with its instances, followed by the feedback on it, quoted and labelled as untrusted reader text.
- Instructions to the session: check each piece of feedback against the data with the query commands; list
  feedback that lacks context and feedback that conflicts; propose playbook changes as an edit for the owner to
  accept or refuse; change nothing without the owner's decision.

After the owner decides, the session marks that report's feedback handled (`scripts/coach_feedback.py handled`).

**Demo mode.** Every coach route returns 404 when `demo_mode` is on, as the replay routes do. The demo database
never holds a report.

## Known limits, stated so nobody is surprised

- **Login has no password.** A visitor logs in by picking a name. "Friend-group members only" therefore cannot
  be enforced for feedback: it is limited to logged-in viewers, and the author is whoever they said they were.
  This is one more reason feedback is checked rather than obeyed.
- **Feedback is reader-typed text that ends up in front of a model.** Prompt out quotes it as data and tells the
  session to treat it as claims. The owner approves every playbook change.
- **Team knowledge is per team.** What one player personally saw is not stored. The reports state the perfect
  comms assumption wherever it matters.
- **Impact sees kills and economy.** Positioning or utility play that led to no kill shows up only through the
  detectors and the model's own queries.
- **A published point can be wrong.** That is accepted: publishing is how reports get reviewed.

## What the owner still owes, at build time

1. About ten lines of what matters most in a player's game. These, not general advice, are the playbook's core.
2. Three to five sentences in the owner's own voice giving a friend feedback.
3. Before automatic runs: which players count as the friend group for coaching. It cannot be
   `tracked_players.json`, which only chooses what to crawl.

## Later phases, and what phase 1 leaves ready for them

- **Phase 2, cross-match report.** One player across their coached matches: which themes recur, on which maps
  and sides, with links to every instance. It reads stored points and their themes, so it needs no new data
  from phase 1 beyond what is specified above. Kind `cross_match` in `coach_reports`.
- **Phase 3, per-round findings.** A note per round on the coaching page. It reuses the brief, the query
  commands and the checker with a different output shape.
- **Team reports and automatic runs** come after, each with its own design.

The gate from one phase to the next is the owner's say-so. No numeric bar is set.

## Testing

- Detectors and query commands: unit tests over small hand-built rounds whose answer is known by construction,
  each fixture checked to really contain the relationship it claims.
- Checker: one test per rule, with a passing and a failing point.
- Storage and routes: version numbering, latest-version selection, feedback permissions, demo-mode 404s.
- Coaching page: a rendered-page test that the panel appears, instances carry round and time, and the team
  toggle is disabled.
- The model's output is not unit-tested. It is judged by the owner reading reports on their own matches.

## Implementation plans

One spec, three plans, built in this order so the first report is read on the site:

1. Match brief, detectors and query commands.
2. Storage and the coaching page, shown with a hand-written report until plan 3 lands.
3. The coach run, the checker, the playbook, feedback handling and prompt out.
