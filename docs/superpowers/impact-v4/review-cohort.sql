-- Impact v4 review cohort: the three rule-exercising matches (process §F1; plan r5 §3.2). READ-ONLY.
--
-- Run against the REHEARSAL RESTORE, never production, so every pick exists in the database the reviews run on:
--   "$PGBIN/psql" -v ON_ERROR_STOP=1 -At -f ../docs/superpowers/impact-v4/review-cohort.sql "$REH"
--
-- Each pick is the newest qualifying match, excluding rc3's thirteen and every earlier pick, so the three are
-- distinct. Tested 2026-09-21 on the local corpus valo_v4: a=3206, b=3205 (3206 excluded), c=3202. For (a),
-- impact_v4 removed 2 assists on 3206 through the scorer itself, which confirms the SQL picks what the code removes.
-- Production's picks will differ; record them in impact-v4/README.md.

\set base '3104,3129,3130,3131,3113,3118,3121,3114,3115,3116,3117,3120,3133'

-- (a) an assist on a kill made after the round was decided, credited to a player whose scoreboard shows an
--     assist that round, so remove_post_decided_assists demonstrably removes it. Declaration 12's three rules:
--     defused and at/after the defuse; a real plant (not a Time Win) at/after plant+45; a Time Win after 100 s.
SELECT max(r.match_id) AS a_post_decided_assist
FROM kill_events k
JOIN rounds r ON r.id = k.round_id
CROSS JOIN LATERAL json_array_elements_text(k.source_meta -> 'assistants') AS a(name)
JOIN match_players mp ON mp.match_id = r.match_id
JOIN players p ON p.id = mp.player_id AND lower(p.display_name) = lower(a.name)
JOIN round_player_stats s ON s.round_id = r.id AND s.match_player_id = mp.id AND s.assists > 0
WHERE r.match_id <> ALL (string_to_array(:'base', ',')::int[])
  AND (   (r.defused AND r.defuse_time IS NOT NULL AND k.event_time_seconds >= r.defuse_time)
       OR (r.planted AND r.plant_time IS NOT NULL AND coalesce(r.outcome, '') NOT LIKE '%Time Win%'
           AND k.event_time_seconds >= r.plant_time + 45)
       OR (coalesce(r.outcome, '') LIKE '%Time Win%' AND k.event_time_seconds > 100))
\gset

-- (b) a kill after a defuse, excluding (a)
SELECT max(r.match_id) AS b_post_defuse_kill
FROM kill_events k JOIN rounds r ON r.id = k.round_id
WHERE r.match_id <> ALL (string_to_array(:'base', ',')::int[]) AND r.match_id <> :a_post_decided_assist
  AND r.defused AND r.defuse_time IS NOT NULL AND k.event_time_seconds >= r.defuse_time
\gset

-- (c) a Time Win round with a kill after 100 s, excluding (a) and (b)
SELECT max(r.match_id) AS c_time_win_post_100s_kill
FROM kill_events k JOIN rounds r ON r.id = k.round_id
WHERE r.match_id <> ALL (string_to_array(:'base', ',')::int[])
  AND r.match_id NOT IN (:a_post_decided_assist, :b_post_defuse_kill)
  AND coalesce(r.outcome, '') LIKE '%Time Win%' AND k.event_time_seconds > 100
\gset

SELECT :'a_post_decided_assist' AS a, :'b_post_defuse_kill' AS b, :'c_time_win_post_100s_kill' AS c,
       :'base' || ',' || :'a_post_decided_assist' || ',' || :'b_post_defuse_kill' || ','
       || :'c_time_win_post_100s_kill' AS cohort;
