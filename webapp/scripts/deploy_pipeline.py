"""Drain and deploy one commit, with standard-library CLI adapters.

main's adapters are deliberately small: http(method, url, *, data, headers,
timeout) returns decoded JSON or raises an HTTPError; clock returns epoch seconds;
sleep takes seconds; gh takes an argument list; out is a text stream. Tests supply
all of them, so importing this module has no side effects.
"""
import argparse
from datetime import datetime
import json
import os
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


SERVICES = ("tasks", "worker", "site", "demo")
POLL_S = 30
HTTP_TIMEOUT_S = 60
DRAIN_S = 90 * 60
BUSY_S = 90 * 60
DEPLOY_S = 30 * 60
UPLOAD_S = 15 * 60
CANCEL_S = 5 * 60
PROBE_S = 6 * 60
RETRY_S = 5 * 60


class Superseded(Exception):
    """The server says this run no longer owns the drain."""


class Failed(Exception):
    """An operation exceeded its allowed wait or returned an invalid answer."""


def build_args(inputs: dict) -> list[str]:
    """GitHub booleans may arrive as strings; 'false' must stay false."""
    return ["--" + name.replace("_", "-") for name in ("skip_drain", "dry_run")
            if str(inputs.get(name, False)).lower() == "true"]


def _http(method, url, *, data=None, headers=None, timeout=HTTP_TIMEOUT_S):
    body = None if data is None else json.dumps(data).encode("utf-8")
    request_headers = dict(headers or {})
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
        return json.loads(body) if body else {}


def _gh(args):
    # Neither gh's output nor its exception (which may include credentials) is logged.
    subprocess.run(["gh", *args], check=True, capture_output=True,
                   timeout=HTTP_TIMEOUT_S)


class Pipeline:
    def __init__(self, args, env, http, clock, sleep, gh, out):
        self.args, self.env = args, env
        self.http, self.clock, self.sleep = http, clock, sleep
        self.gh, self.out = gh, out
        self.owned = False
        self.last_poll = None
        self.probe_requested_at = None
        self.poll_failed_since = None
        self.exit_code = 0

    def log(self, message):
        # Only fixed messages and validated commit ids reach here. Never stringify
        # an exception: urllib errors often contain the full credential-bearing URL.
        for key, value in self.env.items():
            if value and (key in ("SITE_URL", "DEMO_URL", "REPLAY_ADMIN_TOKEN")
                          or key.startswith("RENDER_DEPLOY_HOOK_")):
                message = message.replace(value, "[redacted]")
        print(message, file=self.out, flush=True)

    def request(self, method, url, data=None, headers=None):
        try:
            return self.http(method, url, data=data, headers=headers,
                             timeout=HTTP_TIMEOUT_S)
        except HTTPError as error:
            if error.code == 409:
                raise Superseded() from None
            raise

    def admin(self, route, **fields):
        return self.request(
            "POST", self.env["SITE_URL"].rstrip("/") + "/admin/deploy/" + route,
            {"run_id": self.args.run_id, **fields},
            {"Authorization": "Bearer " + self.env["REPLAY_ADMIN_TOKEN"]},
        )

    def poll(self, tasks=False):
        # Measure from request start so slow calls consume the polling interval
        # instead of adding another unconditional 30-second sleep.
        self.last_poll = self.clock()
        if tasks and self.probe_requested_at is None:
            self.probe_requested_at = self.last_poll
        try:
            answer = self.admin("poll", tasks=tasks)
        except Exception:
            if self.poll_failed_since is None:
                self.poll_failed_since = self.last_poll
            raise
        self.poll_failed_since = None
        return answer

    def retry_poll(self, tasks=False, deadline=None):
        """Retry transport failures without restarting the caller's wait clock.

        None means the phase deadline expired before a poll succeeded. The caller
        still applies its normal upload/cancel/probe limit; a continuous five-minute
        outage fails even if that limit falls at the same time.
        """
        while True:
            try:
                return self.poll(tasks=tasks)
            except Superseded:
                raise
            except Exception:
                retry_deadline = self.poll_failed_since + RETRY_S
                if self.clock() >= retry_deadline:
                    self.log("owner poll failed")
                    raise Failed() from None
                if deadline is not None and self.clock() >= deadline:
                    return None
                self.pause(min(retry_deadline, deadline) if deadline is not None
                           else retry_deadline)

    def heartbeat(self):
        if self.last_poll is None or self.clock() - self.last_poll >= POLL_S:
            self.retry_poll(tasks=self.args.dry_run)

    def pause(self, deadline=None):
        delay = max(0, POLL_S - (self.clock() - self.last_poll))
        if deadline is not None:
            delay = min(delay, max(0, deadline - self.clock()))
        if delay:
            self.sleep(delay)

    def owner_call(self, route):
        self.heartbeat()
        return self.admin(route)

    def uploads(self, limit=None):
        deadline = None if limit is None else self.clock() + limit
        while True:
            answer = self.retry_poll(deadline=deadline)
            if answer is not None and answer["status"]["uploads_quiet"]:
                return
            if deadline is not None and self.clock() >= deadline:
                self.log("upload wait limit reached")
                return
            self.pause(deadline)

    def cancellations(self):
        deadline = self.clock() + CANCEL_S
        while True:
            answer = self.retry_poll(deadline=deadline)
            if answer is not None and not answer["status"]["waiting"].get("worker_cancelling"):
                return
            if self.clock() >= deadline:
                self.log("worker cancel wait limit reached")
                return
            self.pause(deadline)

    def drain(self):
        deadline = self.clock() + BUSY_S
        while True:
            answer = self.admin("drain", sha=self.args.sha, skip_drain=self.args.skip_drain)
            if answer["result"] != "busy":
                if answer["result"] not in ("started", "took_over"):
                    raise Failed()
                self.owned = True
                break
            if self.clock() >= deadline:
                self.log("another deploy never finished")
                raise Failed()
            self.sleep(min(POLL_S, deadline - self.clock()))

        previous = answer.get("superseded")
        if previous and str(previous) != self.args.run_id:
            try:
                result = self.gh(["run", "cancel", str(previous)])
                if getattr(result, "returncode", 0):
                    raise Failed()
            except Exception:
                self.log("previous run cancel failed")

        if self.args.skip_drain:
            return
        started = datetime.fromisoformat(answer["drain"]["started_at"]).timestamp()
        while True:
            answer = self.retry_poll(tasks=self.args.dry_run)
            if answer["status"]["quiet"]:
                return
            if answer["now"] - started >= DRAIN_S:
                if not self.args.dry_run:
                    # At the drain deadline R13 still protects active uploads.
                    self.uploads()
                    self.owner_call("force")
                    self.cancellations()
                return
            self.pause()

    def health(self, service):
        key = "DEMO_URL" if service == "demo" else "SITE_URL"
        answer = self.request("GET", self.env[key].rstrip("/") + "/health")
        if answer.get("status") != "ok":
            raise Failed()
        return answer.get("commit")

    def hook(self, service):
        self.heartbeat()
        self.log("deploying " + service)
        # A secret pasted with a trailing newline is not a valid URL: strip it.
        url = self.env["RENDER_DEPLOY_HOOK_" + service.upper()].strip()
        separator = "&" if "?" in url else "?"
        try:
            self.request("POST", url + separator + "ref=" + quote(self.args.sha, safe=""))
        except Superseded:
            raise
        except Exception as error:
            # Say why without the error's text, which can hold the hook URL: only the
            # HTTP status, or the error's class name.
            reason = ("HTTP " + str(int(error.code)) if isinstance(error, HTTPError)
                      else type(error).__name__)
            self.log(service + " deploy hook failed: " + reason)
            raise Failed() from None
        # A finished probe must have started strictly after the hook completed.
        return self.clock()

    def wait_commit(self, service, hooked_at):
        deadline = self.clock() + DEPLOY_S
        while True:
            try:
                answer = self.poll(tasks=service == "tasks")
                if service == "tasks":
                    probe = answer["commits"]["tasks"]
                    commit = (probe["commit"] if probe is not None
                              and probe["started_at"] > hooked_at else None)
                elif service == "worker":
                    commit = answer["commits"]["worker"]
                else:
                    commit = self.health(service)
                if commit == self.args.sha and self.clock() <= deadline:
                    return
            except Superseded:
                raise
            except Exception:
                # The site's owner poll and health endpoints can both disappear
                # during a restart. Keep renewing ownership and retry within this
                # service's single deadline, without logging the exception body.
                pass
            if self.clock() >= deadline:
                self.log(service + " commit wait failed")
                raise Failed()
            self.pause(deadline)

    @staticmethod
    def readable(commit):
        return isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", commit) is not None

    def dry_run(self):
        deadline = (self.probe_requested_at if self.probe_requested_at is not None
                    else self.clock()) + PROBE_S
        answer = None
        while True:
            answer = self.retry_poll(tasks=True, deadline=deadline)
            if (answer is not None and answer["commits"]["tasks"] is not None
                    or self.clock() >= deadline):
                break
            self.pause(deadline)
        probe = answer["commits"]["tasks"] if answer is not None else None
        commits = {"tasks": probe["commit"] if probe is not None else None,
                   "worker": answer["commits"]["worker"] if answer is not None else None}
        for service in ("site", "demo"):
            self.heartbeat()
            try:
                commits[service] = self.health(service)
            except Superseded:
                raise
            except Exception:
                commits[service] = None
        for service in SERVICES:
            value = commits[service] if self.readable(commits[service]) else "unreadable"
            self.log(service + " commit: " + value)
        self.exit_code = 0 if all(self.readable(c) for c in commits.values()) else 1
        return "dry_run"

    def resume(self, outcome):
        """The result is settled before cleanup; a transport error cannot change it."""
        deadline = self.clock() + RETRY_S
        while True:
            attempted_at = self.clock()
            try:
                self.admin("resume", outcome=outcome)
                return True
            except Superseded:
                raise
            except Exception:
                if self.clock() >= deadline:
                    self.log("resume failed")
                    return False
                delay = min(max(0, POLL_S - (self.clock() - attempted_at)),
                            deadline - self.clock())
                if delay:
                    self.sleep(delay)

    def execute(self):
        required = ["SITE_URL", "DEMO_URL", "REPLAY_ADMIN_TOKEN"]
        if not self.args.dry_run:
            required += ["RENDER_DEPLOY_HOOK_" + service.upper() for service in SERVICES]
        if any(not self.env.get(key) for key in required):
            self.log("missing deploy configuration")
            raise Failed()
        self.drain()
        if self.args.dry_run:
            return self.dry_run()
        self.owner_call("deploying")
        for service in SERVICES:
            if service in ("worker", "site"):
                self.uploads(UPLOAD_S)
            hooked_at = self.hook(service)
            self.wait_commit(service, hooked_at)
        return "ok"


def main(argv=None, env=None, http=None, clock=None, sleep=None, gh=None, out=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--skip-drain", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    pipeline = Pipeline(args, os.environ if env is None else env,
                        _http if http is None else http,
                        time.time if clock is None else clock,
                        time.sleep if sleep is None else sleep,
                        _gh if gh is None else gh,
                        sys.stdout if out is None else out)
    try:
        outcome = pipeline.execute()
        exit_code = pipeline.exit_code
    except Superseded:
        pipeline.log("superseded")
        return 0
    except Exception:
        pipeline.log("deploy failed")
        if not pipeline.owned:
            return 1
        outcome, exit_code = "failed", 1
    try:
        if not pipeline.resume(outcome):
            return 1
    except Superseded:
        pipeline.log("superseded")
        return 0
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
