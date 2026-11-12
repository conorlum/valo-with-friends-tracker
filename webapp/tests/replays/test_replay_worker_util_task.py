"""The `util` Workflow task against a fake worker link, its registration, and the image that carries its sources."""

import ast
import hashlib
import json
import shlex
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from app.critutil import build_digest, corpus  # noqa: E402
from app.services import util_values  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from tests.critutil.test_corpus_codec import AT, original_inputs, parts_for  # noqa: E402
from tests.critutil.test_build import FRIEND, fake_gate  # noqa: E402
from tests.critutil.test_values import MODEL  # noqa: E402

WEBAPP = REPO / "webapp"


class FakeLink:
    def __init__(self, files):
        self.files, self.posted, self.result, self.order, self.fetched = files, {}, None, [], []

    def fetch(self, name, dest):
        self.fetched.append(name)
        if name not in self.files:
            raise OSError(f"404 {name}")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_file(self, route, src):
        self.order.append(route)
        self.posted[route] = Path(src).read_bytes()

    def post_result(self, result):
        self.order.append("result")
        self.result = result

    def post_started(self):
        self.order.append("started")


@pytest.fixture
def toy(tmp_path, monkeypatch):
    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(True))
    stored, ctx, rows = original_inputs()
    model = {**MODEL, "key": "toy-win"}
    parts = parts_for(ctx, {FRIEND}, rows)
    encoded = corpus.encode_context(ctx, {FRIEND}, model, rows, set(), False, parts)
    path = tmp_path / "in.tar"
    corpus.pack_inputs(encoded, {corpus.row_key(*r[:4]): corpus.encode_round(r[4]) for r in rows}, {}, path)
    return path.read_bytes()


ARGS = {"job_id": "j", "token": "t", "key": "k", "ref": "abc"}


def mine():
    return build_digest.digest(WEBAPP)


def test_util_posts_the_output_then_an_envelope_that_matches_it(toy):
    link = FakeLink({"inputs": toy})
    tasks.util({**ARGS, "digest": mine()}, link)
    assert link.order == ["started", "output", "result"]
    expected = corpus.compute(corpus.unpack_inputs(_write(toy)))
    out = link.posted["output"]
    assert json.loads(out) == json.loads(corpus.encode_output({"win": expected["win"], "values": expected["values"]}))
    env = link.result
    assert env["status"] == "ok" and env["values_key"] == expected["values_key"]
    assert env["bytes"] == len(out) and env["sha256"] == hashlib.sha256(out).hexdigest()
    assert env["refit"] is False and env["digest"] == mine() and env["version"] == util_values.VERSION
    assert env["key"] == "k"


def _write(data):
    import tempfile
    f = Path(tempfile.mkdtemp()) / "x.tar"
    f.write_bytes(data)
    return f


def test_wrong_digest_is_a_deploy_failure_without_fetching(toy):
    link = FakeLink({"inputs": toy})
    tasks.util({**ARGS, "digest": "0" * 16}, link)
    assert link.fetched == [] and link.order == ["started", "result"]
    assert link.result["status"] == "failed" and link.result["error_kind"] == "deploy"
    assert mine() in link.result["error"] and "0" * 16 in link.result["error"]


def test_a_corrupt_tar_is_an_infra_failure():
    link = FakeLink({"inputs": b"not a tar at all"})
    tasks.util({**ARGS, "digest": mine()}, link)
    assert "output" not in link.order
    assert link.result["status"] == "failed" and link.result["error_kind"] == "infra"
    assert link.result["error"].split(":")[0]


def test_a_missing_input_is_an_infra_failure():
    link = FakeLink({})
    tasks.util({**ARGS, "digest": mine()}, link)
    assert link.result["error_kind"] == "infra" and link.result["error"].startswith("OSError")


def test_the_util_task_is_registered_with_render():
    assert tasks.TASKS["util"] is tasks.util
    tree = ast.parse((REPO / "replay_tasks" / "main.py").read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kw = {k.arg: k.value for k in node.keywords}
            if "name" in kw and "timeout_seconds" in kw:
                found[kw["name"].value] = kw["timeout_seconds"].value
    assert found.get("util") == 1800


def test_every_build_source_is_copied_by_the_worker_image():
    copied = []
    for line in (REPO / "replay_worker" / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("COPY ") and "--from" not in line:
            copied.extend(shlex.split(line)[1:-1])
    for source in build_digest.SOURCES:
        path = f"webapp/{source}"
        assert any(path == c or path.startswith(c.rstrip("/") + "/") for c in copied), path


def test_the_review_workflow_watches_every_added_source():
    text = (REPO / ".github" / "workflows" / "replay-worker-review.yml").read_text(encoding="utf-8")
    for p in ("util_values.py", "util_model.py", "services/__init__.py", "coach/__init__.py", "coach/areas.py",
              "static/js/replay.js"):
        assert p in text, p
