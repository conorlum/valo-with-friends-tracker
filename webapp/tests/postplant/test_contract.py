"""The post-plant contract end to end, offline: worker task -> JSON -> the site's trusted gaps -> the store."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "replays"))
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from test_gaps_store import _current, session_factory, stored_round  # noqa: E402,F401  (fixtures)
from test_gaps_task import _planted_task  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixtures)

from app.control.task import compute_task  # noqa: E402
from app.models.replay_postplant import ReplayRoundPostplant  # noqa: E402
from app.postplant import extract  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402
from app.services import replay_gaps  # noqa: E402

REPO = HERE.parents[2]


def test_a_workers_postplant_is_trusted_and_stored_as_the_extractor_made_it(
        session_factory, stored_round, tmp_path, monkeypatch):
    replay_id, n = stored_round
    session = session_factory()
    fingerprint, _ = _current(session, replay_id, n)
    session.close()
    made, real = [], extract.round_postplant
    monkeypatch.setattr(extract, "round_postplant", lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    task = _planted_task(tmp_path, monkeypatch)
    task["gaps"] = {"replay_id": replay_id, "round": n, "fingerprint": fingerprint}
    result = compute_task(task)
    assert result["gaps"]["run"]["status"] == "ok" and result["gaps"]["postplant"]["status"] == "ok"
    wire = json.loads(json.dumps({"gaps": result["gaps"]}))          # as it comes over HTTP
    flight = remote.InFlight("job", replay_id, n, fingerprint, "Ascent", 0.0)
    gaps = remote._trusted_gaps(wire, flight)
    assert gaps is not None, "the site trusts the worker's section"
    counts = {"gaps_dropped": 0, "gaps_stored": 0, "gaps_skipped": 0}
    outcome = remote._keep_gaps(session_factory, flight, wire, counts)
    assert outcome == "stored" and counts["gaps_stored"] == 1
    row = session_factory().get(ReplayRoundPostplant, (replay_id, n))
    assert row.status == "ok" and row.error is None
    assert row.fingerprint == replay_gaps.gap_fingerprint(fingerprint, "Ascent")
    assert row.gaps_revision == replay_gaps.GAPS_REVISION
    assert len(made) == 1 and row.data == json.loads(json.dumps(made[0])) and row.data["plant"] is not None
    assert row.data["v"] == extract.VERSION


def test_the_worker_image_ships_the_postplant_package():
    dockerfile = (REPO / "replay_worker" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY webapp/app/postplant /srv/webapp/app/postplant" in dockerfile.splitlines()
