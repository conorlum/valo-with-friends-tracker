"""Excludes tracker.gg's placeholder rounds for surrendered matches.

When a match is surrendered, tracker.gg still reports round summaries for the
rounds that were never played, padding the match out to its notional length.
Those rounds carry no kill events and a stats row per player that is all
zeros -- 202 such rounds across 37 matches as of writing, all of them from
the tracker.gg pipeline.

Nobody played them, so counting them inflates every "per round played"
denominator (the Most Active percentage, the Scavenger credits-per-round
threshold) and dilutes average Impact with zero-impact rows. In the worst
case in this DB, a 4-round surrendered match counted as 14, taking a player's
average Impact from 629.75 down to 179.93.

`app/adapters/trackergg_browserstate_source.py` skips these at ingest
(SURRENDERED_ROUND_RESULT) so no new ones are written. The rows already in
the DB are kept rather than deleted -- their "Team A/B Surrendered Win"
outcome is the only record that a match ended in a surrender at all -- and
filtered out at query time with the predicate below.
"""

from sqlalchemy import or_

from app.models import Round

# The outcome strings the adapter's _outcome_string() built from tracker.gg's
# "Surrendered" roundResult, for rounds ingested before it learned to skip them.
SURRENDER_OUTCOME_LIKE = "%Surrendered Win"

# NULL-safe on purpose: Round.outcome is nullable, and a bare NOT LIKE would
# evaluate to NULL for those rows and silently drop them from every query.
NOT_A_SURRENDER_ROUND = or_(
    Round.outcome.is_(None),
    ~Round.outcome.like(SURRENDER_OUTCOME_LIKE),
)
