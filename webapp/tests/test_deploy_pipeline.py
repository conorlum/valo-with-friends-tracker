"""Offline pipeline contract, using the site's frozen route shapes."""
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "deploy_pipeline", ROOT / "webapp/scripts/deploy_pipeline.py"
)
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)
ROUTES = json.loads((Path(__file__).parent / "fixtures/deploy/routes.json").read_text())
SHA = "a" * 40
OLD = "b" * 40
SERVICES = ("tasks", "worker", "site", "demo")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= 30
        self.now += seconds


class Site:
    def __init__(self):
        self.clock = Clock()
        self.begin = self.clock()
        # Synthetic addresses and credentials only; no deployed service data.
        self.env = {"SITE_URL": "https://site.invalid", "DEMO_URL": "https://demo.invalid",
                    "REPLAY_ADMIN_TOKEN": "synthetic-admin"}
        for service in SERVICES:
            self.env["RENDER_DEPLOY_HOOK_" + service.upper()] = (
                "https://hooks.invalid/" + service + "?key=synthetic"
            )
        self.row = copy.deepcopy(ROUTES["drain"]["response"]["drain"])
        self.calls = []
        self.polls = []
        self.hooks = []
        self.live_at = {}
        self.commits = dict.fromkeys(SERVICES, OLD)
        self.delays = dict.fromkeys(SERVICES, 60)
        self.busy_until = self.begin
        self.age = 0
        self.quiet_after = 2
        self.uploads = lambda: True
        self.cancel_for = 0
        self.force_at = None
        self.superseded = None
        self.lose_on = None
        self.raise_hook = None
        self.raise_route = None
        self.fail_request = lambda route, data: None
        self.resumed = []
        self.probe_delay = 0
        self.probe_commit = OLD
        self.probe_start = None
        self.stale_tasks = False
        self.stale_seen = False
        self.latency = lambda method, url: 0
        self.restart_errors = False
        self.poll_restart_service = None
        self.gh_calls = []
        self.gh_fails = False
        self.output = io.StringIO()

    def gh(self, args):
        self.gh_calls.append(args)
        if self.gh_fails:
            raise RuntimeError(self.env["REPLAY_ADMIN_TOKEN"])

    def http(self, method, url, *, data=None, headers=None, timeout):
        assert timeout == 60
        self.calls.append((self.clock(), method, url, copy.deepcopy(data)))
        route = urlsplit(url).path.rsplit("/", 1)[-1]
        if route == "poll":
            self.polls.append(self.clock())
            if len(self.polls) > 1:
                assert self.polls[-1] - self.polls[-2] < 7200, "drain expired"
        self.clock.now += self.latency(method, url)
        if urlsplit(url).hostname == "hooks.invalid":
            service = route
            assert method == "POST"
            assert parse_qs(urlsplit(url).query)["ref"] == [SHA]
            assert not headers, "do not send the admin credential to a hook"
            assert self.row["phase"] == "deploying"
            if self.raise_hook == service:
                raise RuntimeError(url + self.env["REPLAY_ADMIN_TOKEN"])
            if self.hooks:
                assert self.commits[self.hooks[-1][0]] == SHA
            self.hooks.append((service, self.clock()))
            self.live_at[service] = self.clock() + self.delays[service]
            return {}
        if route == "health":
            assert method == "GET" and not headers
            service = "demo" if url.startswith(self.env["DEMO_URL"]) else "site"
            self.update_commits()
            if self.restart_errors and self.commits[service] != SHA:
                raise HTTPError(url, 503, "restarting", {}, None)
            return {"status": "ok", "commit": self.commits[service]}
        assert headers == {"Authorization": "Bearer " + self.env["REPLAY_ADMIN_TOKEN"]}
        assert method == ROUTES[route]["method"]
        assert urlsplit(url).path == ROUTES[route]["path"]
        assert data["run_id"] == "1"
        if route != "drain" and self.lose_on == route:
            self.row["run_id"] = "replacement-run"
            raise HTTPError(url, 409, "different owner", {}, None)
        if self.raise_route == route:
            raise RuntimeError(url + self.env["REPLAY_ADMIN_TOKEN"])
        failure = self.fail_request(route, data)
        if failure == "timeout":
            raise TimeoutError(url + self.env["REPLAY_ADMIN_TOKEN"])
        if failure:
            raise HTTPError(url, failure, self.env["REPLAY_ADMIN_TOKEN"], {}, None)
        answer = copy.deepcopy(ROUTES[route]["response"])
        if route == "drain":
            if self.clock() < self.busy_until:
                answer["result"] = "busy"
                return answer
            self.row["started_at"] = datetime.fromtimestamp(
                self.clock() - self.age, timezone.utc
            ).isoformat()
            self.row["skip_drain"] = data["skip_drain"]
            answer.update(result="took_over" if self.superseded else "started",
                          superseded=self.superseded, drain=copy.deepcopy(self.row))
        elif route == "poll":
            self.update_commits()
            if (self.hooks and self.hooks[-1][0] == self.poll_restart_service
                    and self.commits[self.poll_restart_service] != SHA):
                raise HTTPError(url, 503, "restarting", {}, None)
            answer["drain"] = copy.deepcopy(self.row)
            answer["now"] = self.clock()
            answer["status"].update(quiet=len(self.polls) >= self.quiet_after,
                                      uploads_quiet=self.uploads())
            if self.force_at is not None and self.clock() < self.force_at + self.cancel_for:
                answer["status"]["waiting"] = {"worker_cancelling": 1}
            answer["commits"]["worker"] = self.commits["worker"]
            answer["commits"]["tasks"] = None
            if data.get("tasks"):
                if self.probe_start is None:
                    self.probe_start = self.clock()
                if self.clock() >= self.probe_start + self.probe_delay:
                    commit = self.commits["tasks"] if "tasks" in self.live_at else self.probe_commit
                    started = self.clock() + 0.001
                    if self.stale_tasks and "tasks" in self.live_at and not self.stale_seen:
                        started = self.hooks[0][1] - 1
                        self.stale_seen = True
                        commit = SHA
                    answer["commits"]["tasks"] = {"commit": commit, "started_at": started}
        elif route == "force":
            self.force_at = self.clock()
            self.row["forced"] = True
        elif route == "deploying":
            self.row["phase"] = "deploying"
            answer["drain"] = copy.deepcopy(self.row)
        elif route == "resume":
            self.resumed.append(data["outcome"])
            self.row["outcome"] = data["outcome"]
            self.row["ended_at"] = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
            answer["drain"] = copy.deepcopy(self.row)
        return answer

    def update_commits(self):
        for service, at in self.live_at.items():
            if self.clock() >= at:
                self.commits[service] = SHA

    def run(self, *flags):
        result = pipeline.main(["--run-id", "1", "--sha", SHA, *flags], self.env,
                               self.http, self.clock, self.clock.sleep, self.gh, self.output)
        text = self.output.getvalue()
        assert self.env["REPLAY_ADMIN_TOKEN"] not in text
        assert all(url not in text for name, url in self.env.items() if "HOOK" in name)
        assert "https://" not in text
        return result

    def events(self, route):
        return [(at, data) for at, _, url, data in self.calls
                if urlsplit(url).path == ROUTES[route]["path"]]


def test_happy_path():
    site = Site()
    assert site.run() == 0
    assert [name for name, _ in site.hooks] == list(SERVICES)
    assert site.events("deploying")[0][0] >= site.polls[1]
    assert site.events("resume")[0][1]["outcome"] == "ok"
    assert not site.events("force")
    assert all("deploying " + name in site.output.getvalue() for name in SERVICES)


@pytest.mark.parametrize("case,outcome,result", [
    ("ok", "ok", 0), ("dry_run", "dry_run", 0),
    ("unreadable", "dry_run", 1), ("failed", "failed", 1),
])
@pytest.mark.parametrize("error", [502, "timeout"])
def test_resume_retries_same_outcome(case, outcome, result, error):
    site = Site()
    if case == "failed":
        site.raise_hook = "worker"
    if case == "unreadable":
        site.probe_commit = None
    site.fail_request = lambda route, data: error if route == "resume" and len(site.events("resume")) == 1 else None
    assert site.run(*(["--dry-run"] if case in ("dry_run", "unreadable") else [])) == result
    assert site.resumed == [outcome]
    attempts = site.events("resume")
    assert [data["outcome"] for _, data in attempts] == [outcome, outcome]
    assert attempts[1][0] - attempts[0][0] == 30


def test_resume_unavailable_for_five_minutes_exits_failed_without_changing_outcome():
    site = Site()
    site.fail_request = lambda route, data: 502 if route == "resume" else None
    assert site.run() == 1
    attempts = site.events("resume")
    assert len(attempts) == 11
    assert attempts[-1][0] - attempts[0][0] == 300
    assert all(data["outcome"] == "ok" for _, data in attempts)
    assert not site.resumed
    assert "resume failed" in site.output.getvalue()


def test_resume_superseded_during_retry_exits_zero():
    site = Site()
    site.fail_request = lambda route, data: (502 if len(site.events("resume")) == 1 else 409) if route == "resume" else None
    assert site.run() == 0
    assert len(site.events("resume")) == 2
    assert all(data["outcome"] == "ok" for _, data in site.events("resume"))
    assert not site.resumed
    assert "superseded" in site.output.getvalue()


def test_slow_resume_failures_count_toward_five_minute_limit():
    site = Site()
    site.latency = lambda method, url: 60 if url.endswith("/resume") else 0
    site.fail_request = lambda route, data: "timeout" if route == "resume" else None
    assert site.run() == 1
    assert site.clock() - site.events("resume")[0][0] == 300
    assert all(data["outcome"] == "ok" for _, data in site.events("resume"))


@pytest.mark.parametrize("error", [502, "timeout"])
def test_two_transient_polls_mid_drain_do_not_fail_deploy(error):
    site = Site()
    site.quiet_after = 5
    site.fail_request = lambda route, data: error if route == "poll" and len(site.polls) in (2, 3) else None
    assert site.run() == 0
    assert site.polls[:5] == [site.begin + n * 30 for n in range(5)]
    assert [name for name, _ in site.hooks] == list(SERVICES)
    assert site.resumed == ["ok"]


def test_continuous_poll_failure_for_five_minutes_resumes_failed():
    site = Site()
    site.quiet_after = 10000
    site.fail_request = lambda route, data: 502 if route == "poll" and len(site.polls) >= 2 else None
    assert site.run() == 1
    assert site.events("resume")[0][0] - site.polls[1] == 300
    assert site.resumed == ["failed"]
    assert not site.hooks


def test_successful_poll_resets_continuous_failure_clock():
    site = Site()
    site.quiet_after = 15
    site.fail_request = lambda route, data: 502 if route == "poll" and len(site.polls) in {*range(2, 8), *range(9, 15)} else None
    assert site.run() == 0
    assert site.resumed == ["ok"]


def test_poll_superseded_during_retry_never_resumes():
    site = Site()
    site.quiet_after = 10000
    site.fail_request = lambda route, data: {2: 502, 3: 409}.get(len(site.polls)) if route == "poll" else None
    assert site.run() == 0
    assert site.clock() - site.begin == 60
    assert not site.events("resume")
    assert "superseded" in site.output.getvalue()


def test_drain_poll_retries_keep_takeovers_original_server_clock():
    site = Site()
    site.age = pipeline.DRAIN_S - 600
    site.quiet_after = 10000
    site.fail_request = lambda route, data: 502 if route == "poll" and len(site.polls) in (19, 20) else None
    assert site.run() == 0
    assert site.force_at - site.begin == 600


def test_dry_probe_retries_keep_original_six_minute_deadline():
    site = Site()
    site.quiet_after = 1
    site.probe_delay = 10000
    site.fail_request = lambda route, data: 502 if route == "poll" and site.clock() >= site.begin + 300 else None
    assert site.run("--dry-run") == 1
    assert site.clock() - site.begin == 360
    assert site.resumed == ["dry_run"]


@pytest.mark.parametrize("stage", ["uploads", "cancellations", "probe"])
@pytest.mark.parametrize("persistent", [False, True])
def test_wait_phase_polls_retry_and_abort_only_after_continuous_failure(stage, persistent):
    site = Site()
    if stage == "cancellations":
        site.age = pipeline.DRAIN_S
        site.quiet_after = 10000
    if stage == "probe":
        site.quiet_after = 1
        site.probe_delay = 150
    failures = []

    def fail(route, data):
        in_stage = (
            (stage == "uploads" and len(site.hooks) == 1 and not data.get("tasks"))
            or (stage == "cancellations" and site.force_at is not None and not site.hooks)
            or (stage == "probe" and len(site.polls) >= 2)
        )
        if route == "poll" and in_stage and (persistent or len(failures) < 2):
            failures.append(site.clock())
            return 502

    site.fail_request = fail
    assert site.run(*(["--dry-run"] if stage == "probe" else [])) == (1 if persistent else 0)
    if persistent:
        assert site.events("resume")[0][0] - failures[0] == 300
        assert site.resumed == ["failed"]
    else:
        assert len(failures) == 2 and failures[1] - failures[0] == 30
        assert site.resumed == ["dry_run" if stage == "probe" else "ok"]


@pytest.mark.parametrize("stage", ["uploads", "cancellations"])
def test_poll_retry_does_not_restart_phase_deadline(stage):
    site = Site()
    if stage == "cancellations":
        site.age = pipeline.DRAIN_S
        site.quiet_after = 10000
        site.cancel_for = 360
    else:
        site.uploads = lambda: not site.hooks or len(site.hooks) >= 2

    def fail(route, data):
        if route != "poll":
            return
        if stage == "cancellations":
            if site.force_at is not None and not site.hooks and site.clock() >= site.force_at + 240:
                return 502
        elif len(site.hooks) == 1 and not data.get("tasks") and site.clock() >= site.hooks[0][1] + 60 + 840:
            return 502

    site.fail_request = fail
    assert site.run() == 0
    if stage == "cancellations":
        assert site.events("deploying")[0][0] - site.force_at == 300
    else:
        assert site.hooks[1][1] - site.hooks[0][1] == 60 + 900


def test_never_quiet_protects_uploads_before_force():
    site = Site()
    site.quiet_after = 10000
    site.uploads = lambda: site.clock() >= site.begin + pipeline.DRAIN_S + 60
    assert site.run() == 0
    assert site.force_at == site.begin + pipeline.DRAIN_S + 60
    assert site.begin + pipeline.DRAIN_S in site.polls


def test_takeover_keeps_clock_and_cancels_old_run():
    site = Site()
    site.age = pipeline.DRAIN_S - 600
    site.superseded = "previous-run"
    site.quiet_after = 10000
    site.gh_fails = True
    assert site.run() == 0
    assert site.force_at == site.begin + 600
    assert site.gh_calls == [["run", "cancel", "previous-run"]]
    assert "cancel failed" in site.output.getvalue()


@pytest.mark.parametrize("route", ["poll", "deploying", "force", "resume"])
def test_superseded_never_resumes_again(route):
    site = Site()
    site.lose_on = route
    if route == "force":
        site.age = pipeline.DRAIN_S
        site.quiet_after = 10000
    assert site.run() == 0
    assert len(site.events("resume")) == (1 if route == "resume" else 0)
    assert "superseded" in site.output.getvalue()


def test_busy_retries_then_drains():
    site = Site()
    site.busy_until += 90
    assert site.run() == 0
    assert len(site.events("drain")) == 4
    assert site.events("drain")[-1][0] == site.begin + 90


def test_long_run_keeps_owner_lease_with_slow_http_and_restarts():
    site = Site()
    site.quiet_after = 64
    site.delays = dict.fromkeys(SERVICES, 1740)
    site.latency = lambda method, url: 30 if url.endswith("/poll") else 60
    site.restart_errors = True
    assert site.run() == 0
    assert site.clock() - site.begin >= 2.5 * 3600
    assert max(b - a for a, b in zip(site.polls, site.polls[1:])) <= 90


def test_tasks_ignores_probe_started_before_hook():
    site = Site()
    site.delays["tasks"] = 0
    site.stale_tasks = True
    assert site.run() == 0
    assert site.stale_seen
    assert site.hooks[1][1] > site.hooks[0][1]


@pytest.mark.parametrize("cancel_for,expected", [(60, 60), (360, 300)])
def test_force_waits_for_cancels_with_five_minute_limit(cancel_for, expected):
    site = Site()
    site.age = pipeline.DRAIN_S
    site.quiet_after = 10000
    site.cancel_for = cancel_for
    assert site.run() == 0
    assert site.events("deploying")[0][0] == site.force_at + expected


def test_worker_timeout_stops_later_services_and_resumes_failed():
    site = Site()
    site.delays["worker"] = 1801
    assert site.run() == 1
    assert [name for name, _ in site.hooks] == ["tasks", "worker"]
    assert site.events("resume")[-1][1]["outcome"] == "failed"
    assert site.events("resume")[-1][0] - site.hooks[-1][1] == 1800


def test_owner_poll_errors_during_restart_are_retried():
    site = Site()
    site.poll_restart_service = "worker"
    assert site.run() == 0
    assert [name for name, _ in site.hooks] == list(SERVICES)


@pytest.mark.parametrize("route", [None, "poll", "deploying"])
def test_exception_after_drain_resumes_failed_without_secret_output(route):
    site = Site()
    site.raise_route = route
    site.raise_hook = "worker" if route is None else None
    assert site.run() == 1
    assert site.events("resume")[-1][1]["outcome"] == "failed"


@pytest.mark.parametrize("probe_delay,commit,result", [(150, OLD, 0), (150, None, 1), (361, OLD, 1)])
def test_dry_run_waits_for_first_probe_and_prints_all_commits(probe_delay, commit, result):
    site = Site()
    site.probe_delay = probe_delay
    site.probe_commit = commit
    assert site.run("--dry-run") == result
    assert not site.hooks and not site.events("force")
    for service in SERVICES:
        assert service + " commit:" in site.output.getvalue()
    assert site.events("resume")[-1][1]["outcome"] == "dry_run"
    if result == 0:
        assert site.clock() >= site.begin + probe_delay


def test_dry_run_never_forces_even_at_drain_limit():
    site = Site()
    site.quiet_after = 10000
    site.age = pipeline.DRAIN_S
    assert site.run("--dry-run") == 0
    assert not site.events("force") and not site.events("deploying")


def test_dry_run_rejects_unreadable_health_and_reports_four_commits():
    site = Site()
    site.commits["demo"] = None
    assert site.run("--dry-run") == 1
    assert site.events("resume")[-1][1]["outcome"] == "dry_run"
    assert "demo commit: unreadable" in site.output.getvalue()
    assert sum(" commit:" in line for line in site.output.getvalue().splitlines()) == 4


def test_missing_configuration_never_takes_a_drain():
    site = Site()
    del site.env["RENDER_DEPLOY_HOOK_SITE"]
    assert site.run() == 1
    assert not site.calls


def test_commit_output_cannot_echo_a_credential_that_looks_like_a_sha():
    site = Site()
    site.env["REPLAY_ADMIN_TOKEN"] = OLD
    assert site.run("--dry-run") == 0


def test_busy_eighty_minutes_then_all_limits_fit_job_budget():
    site = Site()
    site.busy_until += 80 * 60
    site.quiet_after = 10000
    site.cancel_for = 360
    site.delays = dict.fromkeys(SERVICES, 1800)
    site.uploads = lambda: site.clock() >= site.busy_until + pipeline.DRAIN_S + 900 if not site.hooks else False
    assert site.run() == 0
    assert site.clock() - site.begin <= 360 * 60


def test_busy_ninety_one_minutes_fails_without_owning_or_resuming():
    site = Site()
    site.busy_until += 91 * 60
    assert site.run() == 1
    assert site.clock() - site.begin == 90 * 60
    assert not site.events("resume")
    assert "another deploy never finished" in site.output.getvalue()


@pytest.mark.parametrize("upload_duration,expected", [(120, 120), (1000, 900)])
def test_upload_during_tasks_holds_worker_hook_with_limit(upload_duration, expected):
    site = Site()
    site.delays["tasks"] = 0
    site.uploads = lambda: not site.hooks or site.clock() >= site.hooks[0][1] + upload_duration
    assert site.run() == 0
    assert site.hooks[1][1] - site.hooks[0][1] == expected


def test_upload_during_worker_holds_site_hook():
    site = Site()
    site.delays["worker"] = 0
    site.uploads = lambda: len(site.hooks) < 2 or site.clock() >= site.hooks[1][1] + 120
    assert site.run() == 0
    assert site.hooks[2][1] - site.hooks[1][1] == 120


def test_skip_drain_still_owns_row_without_wait_or_force():
    site = Site()
    site.quiet_after = 10000
    assert site.run("--skip-drain") == 0
    assert site.events("drain")[0][1]["skip_drain"] is True
    assert site.events("deploying")[0][0] == site.begin
    assert not site.events("force")


def test_hook_without_query_and_self_takeover():
    site = Site()
    site.env["RENDER_DEPLOY_HOOK_TASKS"] = "https://hooks.invalid/tasks"
    site.superseded = "1"
    assert site.run() == 0
    assert not site.gh_calls


@pytest.mark.parametrize("status,reason", [(404, "HTTP 404"), (None, "RuntimeError")])
def test_a_failed_hook_says_which_service_and_why_without_the_url(status, reason):
    site = Site()
    plain = site.http

    def http(method, url, **kwargs):
        if urlsplit(url).hostname == "hooks.invalid" and status is not None:
            raise HTTPError(url, status, url, {}, None)
        return plain(method, url, **kwargs)

    site.http = http
    site.raise_hook = "tasks"
    assert site.run() == 1
    printed = site.output.getvalue()
    assert "tasks deploy hook failed: " + reason + "\n" in printed
    assert "hooks.invalid" not in printed and "synthetic" not in printed
    assert site.events("resume")[-1][1]["outcome"] == "failed"


def test_a_hook_secret_with_a_trailing_newline_still_deploys():
    site = Site()
    site.env["RENDER_DEPLOY_HOOK_TASKS"] += "\n"
    assert site.run() == 0
    assert [service for service, _ in site.hooks] == list(SERVICES)


def test_input_flags_and_workflow_triggers_contract():
    assert pipeline.build_args({"skip_drain": "false", "dry_run": "true"}) == ["--dry-run"]
    assert pipeline.build_args({"skip_drain": True, "dry_run": False}) == ["--skip-drain"]
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    active = "\n".join(line for line in workflow.splitlines() if not line.lstrip().startswith("#"))
    assert "workflow_dispatch:" in active
    # D1: every merge to main deploys, except merges touching only docs, Markdown or tests.
    lines = [line.strip() for line in active.splitlines()]
    assert "push:" in lines and "branches: [main]" in lines and "paths-ignore:" in lines
    ignored = lines[lines.index("paths-ignore:") + 1:lines.index("paths-ignore:") + 4]
    assert ignored == ["- 'docs/**'", "- '**/*.md'", "- 'webapp/tests/**'"]
    assert "api.render.com" not in workflow
    assert "timeout-minutes: 360" in active
    assert 'python-version: "3.13"' in active
    assert "contents: read" in active and "actions: write" in active
    assert "${{ github.run_id }}" in active and "${{ github.sha }}" in active
    for flag in ("dry_run", "skip_drain"):
        assert 'if [ "${{ inputs.' + flag + ' }}" = "true" ]; then' in active
        assert "--" + flag.replace("_", "-") in active
    for name in Site().env:
        assert name + ": ${{ secrets." + name + " }}" in active
    assert "GH_TOKEN: ${{ github.token }}" in active


def test_blueprint_services_leave_deploys_to_the_pipeline():
    # The Action deploys them after a drain; Render auto-deploying too would restart them mid-work.
    blueprint = (ROOT / "render.yaml").read_text()
    assert blueprint.count("autoDeploy: false") == 2
    assert "autoDeploy: true" not in blueprint
