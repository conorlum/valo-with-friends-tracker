"""ingest_replay.py: --preview and --dry-run on synthetic exports; a throwaway sqlite session only."""

import copy
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import ingest_replay  # noqa: E402
from replay_synthetic import AGENT_NAMES, MATCH_UUID, SyntheticMatch, team_of  # noqa: E402
from test_replay_link import LINK_KILLS, OFFSET, ROUNDS, WINNERS  # noqa: E402

from app.db import Base  # noqa: E402
from app.models import KillEvent, Match, MatchPlayer, Player, Round  # noqa: E402
from app.models.match import MatchSource, Team  # noqa: E402
from app.replays.contract import load_pin  # noqa: E402


def link_match():
    return SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(LINK_KILLS), winners=dict(WINNERS))


@pytest.fixture
def export(tmp_path):
    match = link_match()
    directory = match.write(tmp_path / "export")
    vrf = tmp_path / "archive" / f"{MATCH_UUID}.vrf"
    vrf.parent.mkdir()
    vrf.write_bytes(match.vrf_bytes)
    parser_dir = tmp_path / "parser"
    (parser_dir / "bin").mkdir(parents=True)
    pin = load_pin()
    (parser_dir / "bin" / "BUILD.json").write_text(json.dumps({"commit": pin.commit, "patch_hash": pin.patch_hash}))
    return {"dir": directory, "vrf": vrf, "parser": parser_dir, "match": match, "tmp": tmp_path}


def test_without_a_mode_it_stops_before_stage_2(export):
    with pytest.raises(SystemExit, match="Stage 2"):
        ingest_replay.main(["--export-dir", str(export["dir"])])


def test_preview_writes_a_page_and_passes_the_scripted_checks(export, capsys):
    out = export["tmp"] / "preview"
    code = ingest_replay.main(["--export-dir", str(export["dir"]), "--preview", "--vrf", str(export["vrf"]),
                               "--parser-dir", str(export["parser"]), "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "FAIL" not in printed and printed.count("PASS") == 13  # 8 from pass 5, 5 from pass 6
    page = (out / "preview.html").read_text(encoding="utf-8")
    assert "<canvas" in page and "data:image/png;base64," in page
    assert MATCH_UUID not in page.lower() and "00000000-0000-4000-8000-0000000000" not in page
    assert "WARNING" not in printed


def test_preview_without_the_vrf_or_build_warns_but_runs(export, capsys):
    out = export["tmp"] / "preview"
    code = ingest_replay.main(["--export-dir", str(export["dir"]), "--preview", "--vrf", str(export["tmp"] / "no.vrf"),
                               "--parser-dir", str(export["tmp"] / "none"), "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 0
    assert "source hash is NOT checked" in printed and "parser build is NOT checked" in printed


def test_preview_reports_a_failing_check_and_still_writes_the_page(export, capsys):
    match = export["match"]
    movement = [r for r in match.movement() if r["shooter_character_net_guid"] != match.pawn(2, 3)]
    directory = match.write(export["tmp"] / "gappy", movement=movement)
    out = export["tmp"] / "preview"
    code = ingest_replay.main(["--export-dir", str(directory), "--preview", "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 1
    assert "FAIL  every present player has a track in every round" in printed
    assert (out / "preview.html").exists()


def test_preview_takes_a_map_when_actor_paths_do_not_name_it(export, capsys):
    match = SyntheticMatch(map_code="Nowhere")
    directory = match.write(export["tmp"] / "nomap")
    out = export["tmp"] / "preview"
    assert ingest_replay.main(["--export-dir", str(directory), "--preview", "--out", str(out)]) == 3
    assert ingest_replay.main(["--export-dir", str(directory), "--preview", "--map", "Ascent", "--out", str(out)]) == 1
    assert "FAIL  map discovered from the export or the .vrf" in capsys.readouterr().out


def test_map_is_for_preview_only(export):
    with pytest.raises(SystemExit):
        ingest_replay.main(["--export-dir", str(export["dir"]), "--dry-run", "--map", "Ascent"])


def test_dry_run_requires_the_vrf_and_the_build(export):
    with pytest.raises(SystemExit, match=r"\.vrf"):
        ingest_replay.main(["--export-dir", str(export["dir"]), "--dry-run", "--vrf", str(export["tmp"] / "no.vrf")],
                           loader_factory=lambda: pytest.fail("loaded"))
    with pytest.raises(SystemExit, match="BUILD.json"):
        ingest_replay.main(["--export-dir", str(export["dir"]), "--dry-run", "--vrf", str(export["vrf"]),
                            "--parser-dir", str(export["tmp"] / "none")], loader_factory=lambda: pytest.fail("loaded"))


def test_dry_run_refuses_an_export_of_another_file(export, tmp_path, capsys):
    other = tmp_path / "other.vrf"
    other.write_bytes(b"different bytes")
    code = ingest_replay.main(["--export-dir", str(export["dir"]), "--dry-run", "--vrf", str(other),
                               "--parser-dir", str(export["parser"])], loader_factory=lambda: pytest.fail("loaded"))
    assert code == 3
    assert "source_sha256" in capsys.readouterr().err


# ------------------------------------------------------------ dry run against a throwaway sqlite session


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Player.__table__, Match.__table__, MatchPlayer.__table__,
                                             Round.__table__, KillEvent.__table__])
    db = sessionmaker(bind=engine)()
    match = Match(external_id=MATCH_UUID, source=MatchSource.SCRAPED, map_name="Ascent",
                  team1_rounds_won=sum(w == "Red" for w in WINNERS.values()),
                  team2_rounds_won=sum(w == "Blue" for w in WINNERS.values()))
    db.add(match)
    db.flush()
    mps = []
    for slot in range(10):
        player = Player(display_name=f"synthetic-{slot}")
        db.add(player)
        db.flush()
        mp = MatchPlayer(match_id=match.id, player_id=player.id, agent=AGENT_NAMES[slot],
                         team=Team.TEAM_1 if team_of(slot) == 0 else Team.TEAM_2)
        db.add(mp)
        db.flush()
        mps.append(mp.id)
    for n, script in LINK_KILLS.items():
        rnd = Round(match_id=match.id, round_number=n,
                    outcome=f"Team {'A' if WINNERS[n] == 'Red' else 'B'} Elimination Win")
        db.add(rnd)
        db.flush()
        for t, killer, victim in script:
            db.add(KillEvent(round_id=rnd.id, killer_match_player_id=mps[killer], death_match_player_id=mps[victim],
                             weapon="Vandal", event_time_seconds=round(t + OFFSET, 3)))
    db.commit()
    yield db
    db.close()


def test_load_link_candidates_reads_the_rows_the_linker_compares(session):
    candidates, owners = ingest_replay.load_link_candidates(session, MATCH_UUID.upper())
    [match] = candidates
    assert owners == {}
    assert (match.team1_rounds_won, match.team2_rounds_won) == (13, 2)
    assert [r.number for r in match.rounds] == list(range(1, ROUNDS + 1))
    assert match.mode == "competitive"
    assert len(match.kills) == sum(len(s) for s in LINK_KILLS.values())
    assert {p.team for p in match.players} == {"team-1", "team-2"}
    assert all(p.riot_subject is None for p in match.players)


def test_dry_run_links_and_prints_no_identities(export, session, capsys):
    code = ingest_replay.main(
        ["--export-dir", str(export["dir"]), "--dry-run", "--vrf", str(export["vrf"]),
         "--parser-dir", str(export["parser"])],
        loader_factory=lambda: (lambda uuid: ingest_replay.load_link_candidates(session, uuid)))
    printed = capsys.readouterr().out
    assert code == 0, printed
    body = json.loads(printed[printed.index("{"):])
    assert body["status"] == "linked"
    assert body["report"]["clock_offset"] == pytest.approx(OFFSET)
    assert "00000000-0000-4000-8000-0000000000" not in printed and "synthetic-" not in printed


def test_dry_run_never_writes(export, session):
    before = [session.query(t).count() for t in (Player, Match, MatchPlayer, Round, KillEvent)]
    ingest_replay.main(["--export-dir", str(export["dir"]), "--dry-run", "--vrf", str(export["vrf"]),
                        "--parser-dir", str(export["parser"])],
                       loader_factory=lambda: (lambda uuid: ingest_replay.load_link_candidates(session, uuid)))
    assert [session.query(t).count() for t in (Player, Match, MatchPlayer, Round, KillEvent)] == before
    assert not session.dirty and not session.new
