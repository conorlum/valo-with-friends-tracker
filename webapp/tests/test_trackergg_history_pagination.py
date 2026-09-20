"""Paging a tracker.gg match history has to distinguish "that is all there is"
from "that is all we got", on every path.

The shape these guard against was measured, not imagined. Clicking tracker.gg's
"Load More" REPLACES `stats.standardProfileMatches[0].matches` in
`window.__INITIAL_STATE__` with the fetched page rather than appending to it:
the array is 20 long before the click and 20 long after, with entirely
different contents. A loop that accumulated from `__INITIAL_STATE__` and
guarded itself with "did the state change?" would pass that guard on every
iteration, run forever, keep only the last page, and report success. So
discovery accumulates from the captured XHR bodies, and every stop is named.
"""

import pytest

from app.adapters.trackergg_browserstate_source import (
    MATCHES_PER_HISTORY_PAGE,
    DiscoveryResult,
    DiscoveryStatus,
    ProfilePrivateError,
    discover_match_ids_paginated,
)


def _match(match_id, mode="Competitive"):
    return {"attributes": {"id": match_id}, "metadata": {"modeName": mode}}


def _payload(ids, next_cursor, mode="Competitive"):
    """One history API `data` object, as tracker.gg returns it."""
    return {
        "matches": [_match(i, mode) for i in ids],
        "metadata": {"schema": "riot-api", "next": next_cursor},
    }


def _page_ids(page_index, size=MATCHES_PER_HISTORY_PAGE):
    return [f"m{page_index}-{i}" for i in range(size)]


class FakeLocator:
    def __init__(self, page, present=True):
        self._page = page
        self._present = present

    @property
    def first(self):
        return self

    def count(self):
        return 1 if (self._present and self._page.load_more_present) else 0

    def is_visible(self):
        return self._page.load_more_present

    def is_enabled(self):
        return self._page.load_more_present

    def scroll_into_view_if_needed(self):
        pass

    def click(self):
        self._page.click()


class FakePage:
    """Stands in for a Playwright Page over a tracker.gg history view.

    `pages[0]` is what the server rendered into __INITIAL_STATE__; each click
    emits the next payload to the registered response listener, exactly as the
    real Load More XHR does.
    """

    def __init__(self, pages, *, private=False, no_container=False,
                 fire_xhr=True, drop_control_after=None):
        self.pages = pages
        self.private = private
        self.no_container = no_container
        self.fire_xhr = fire_xhr
        self.drop_control_after = drop_control_after
        self.cursor = 0          # index of the last page delivered
        self.clicks = 0
        self.waits = []
        self._listeners = []
        self.load_more_present = True

    # -- Playwright surface used by discover_match_ids_paginated --------------
    def on(self, event, fn):
        if event == "response":
            self._listeners.append(fn)

    def remove_listener(self, event, fn):
        if fn in self._listeners:
            self._listeners.remove(fn)

    def goto(self, url, **kwargs):
        self.url = url

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def evaluate(self, script):
        if self.no_container:
            return {"stats": {"standardProfileMatches": [], "standardProfiles": []}}
        profiles = [{"errors": [{"code": "CollectorResultStatus::Private"}]}] if self.private else []
        return {
            "stats": {
                "standardProfileMatches": [self.pages[0]],
                "standardProfiles": profiles,
            }
        }

    def get_by_role(self, role, name=None):
        return FakeLocator(self)

    # -- the Load More behaviour under test ----------------------------------
    def click(self):
        self.clicks += 1
        if self.drop_control_after is not None and self.clicks >= self.drop_control_after:
            self.load_more_present = False
        self.cursor += 1
        if self.cursor >= len(self.pages):
            # Out of scripted pages: behave like an exhausted control.
            self.load_more_present = False
            return
        if not self.fire_xhr:
            return
        payload = self.pages[self.cursor]
        self._emit(payload)
        if (payload.get("metadata") or {}).get("next") is None:
            self.load_more_present = False

    def _emit(self, data):
        class FakeResponse:
            url = "https://api.tracker.gg/api/v2/valorant/standard/matches/riot/X?next=1"

            def json(self_inner):
                return {"data": data}

        for fn in list(self._listeners):
            fn(FakeResponse())


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """Pacing is asserted separately; don't actually wait 5-12s per page."""
    monkeypatch.setattr(
        "app.adapters.trackergg_browserstate_source.time.sleep", lambda _s: None
    )


# --------------------------------------------------------------------------
# Reaching past 20 -- the cap this work exists to remove
# --------------------------------------------------------------------------

def test_pagination_reaches_past_the_twenty_match_batch():
    pages = [_payload(_page_ids(i), next_cursor=i + 1) for i in range(5)]
    page = FakePage(pages)

    result = discover_match_ids_paginated(page, "Deep#NA1", 100)

    assert result.reached == 100
    assert result.requested == 100
    assert result.status is DiscoveryStatus.COMPLETE
    assert result.is_conclusive
    assert len(set(result.match_ids)) == 100, "IDs must be deduped, not merely counted"
    assert page.clicks == 4, "20 server-rendered + 4 pages of 20"


def test_reaching_the_target_mid_page_truncates_to_exactly_the_request():
    pages = [_payload(_page_ids(i), next_cursor=i + 1) for i in range(5)]

    result = discover_match_ids_paginated(FakePage(pages), "Deep#NA1", 55)

    assert result.reached == 55
    assert result.status is DiscoveryStatus.COMPLETE
    # Ordering preserved, most recent first.
    assert result.match_ids[0] == "m0-0"
    assert result.match_ids[54] == "m2-14"


def test_initial_state_is_never_reread_after_a_click():
    """The measured trap: __INITIAL_STATE__ is REPLACED per click, so a reader
    that consulted it again would see 20 forever. Discovery must accumulate
    from the response bodies -- proven here by a FakePage whose evaluate()
    always returns page 0 while the XHRs deliver pages 1..n."""
    pages = [_payload(_page_ids(i), next_cursor=i + 1) for i in range(4)]
    page = FakePage(pages)

    result = discover_match_ids_paginated(page, "Deep#NA1", 80)

    assert result.reached == 80
    # If page 0 had been re-read, these later IDs could never appear.
    assert "m3-19" in result.match_ids


# --------------------------------------------------------------------------
# Exhausted history -- COMPLETE, not truncated
# --------------------------------------------------------------------------

def test_exhausted_history_reports_exhausted_not_incomplete():
    """A player with genuinely less history than requested is a conclusive
    answer: `next: null` is tracker.gg saying that is all of it."""
    pages = [
        _payload(_page_ids(0), next_cursor=1),
        _payload(_page_ids(1, size=6), next_cursor=None),
    ]
    page = FakePage(pages)

    result = discover_match_ids_paginated(page, "Shallow#NA1", 100)

    assert result.reached == 26
    assert result.requested == 100
    assert result.status is DiscoveryStatus.EXHAUSTED
    assert result.is_conclusive, "end of history is an answer, not a shortfall"
    assert "end of history" in result.reason


def test_single_page_with_null_cursor_is_exhausted_without_clicking():
    page = FakePage([_payload(_page_ids(0, size=16), next_cursor=None)])

    result = discover_match_ids_paginated(page, "Yosher#Toshi", 100)

    assert result.reached == 16
    assert result.status is DiscoveryStatus.EXHAUSTED
    assert page.clicks == 0, "no cursor means no reason to click"


# --------------------------------------------------------------------------
# Repeated cursor -- the loop that looks busy and collects nothing
# --------------------------------------------------------------------------

def test_repeated_page_stops_as_incomplete():
    """Same IDs handed back again: the cursor did not advance. Continuing
    would spin forever while reporting progress."""
    repeat = _page_ids(0)
    pages = [_payload(repeat, next_cursor=1), _payload(repeat, next_cursor=2)]
    page = FakePage(pages)

    result = discover_match_ids_paginated(page, "Stuck#NA1", 100)

    assert result.reached == 20
    assert result.status is DiscoveryStatus.INCOMPLETE
    assert not result.is_conclusive
    assert "no new matches" in result.reason
    assert page.clicks == 1, "must stop on the first repeat, not keep clicking"


def test_non_advancing_cursor_value_stops_as_incomplete():
    pages = [
        _payload(_page_ids(0), next_cursor=1),
        _payload(_page_ids(1), next_cursor=1),   # fresh IDs, but cursor stuck at 1
        _payload(_page_ids(2), next_cursor=1),
    ]
    page = FakePage(pages)

    result = discover_match_ids_paginated(page, "Stuck#NA1", 100)

    assert result.status is DiscoveryStatus.INCOMPLETE
    assert "repeated at 1" in result.reason
    assert page.clicks == 1


def test_safety_cap_is_incomplete_never_success():
    pages = [_payload(_page_ids(i), next_cursor=i + 1) for i in range(30)]
    page = FakePage(pages)

    result = discover_match_ids_paginated(page, "Endless#NA1", 10_000, max_pages=4)

    assert result.status is DiscoveryStatus.INCOMPLETE
    assert not result.is_conclusive
    assert "safety cap" in result.reason
    assert result.reached < result.requested


# --------------------------------------------------------------------------
# The zero-players: a named cause, never a silent success
# --------------------------------------------------------------------------

def test_zero_matches_under_any_act_reports_no_history():
    """SambuUwU#NA1's observed shape: the page renders, no private flag, and
    zero competitive matches across every act."""
    page = FakePage([_payload([], next_cursor=None)])

    result = discover_match_ids_paginated(page, "SambuUwU#NA1", 100)

    assert result.reached == 0
    assert result.status is DiscoveryStatus.NO_HISTORY
    assert result.status is not DiscoveryStatus.COMPLETE
    assert "zero Competitive matches" in result.reason


def test_missing_match_container_reports_no_history():
    page = FakePage([_payload([], next_cursor=None)], no_container=True)

    result = discover_match_ids_paginated(page, "Ghost#NA1", 50)

    assert result.status is DiscoveryStatus.NO_HISTORY
    assert result.reached == 0


def test_private_profile_is_its_own_status():
    page = FakePage([_payload(_page_ids(0), next_cursor=1)], private=True)

    result = discover_match_ids_paginated(page, "Hidden#NA1", 100)

    assert result.status is DiscoveryStatus.PRIVATE
    assert result.reached == 0
    assert "private" in result.reason


def test_non_competitive_rows_are_filtered_out():
    page = FakePage([_payload(["dm1", "dm2"], next_cursor=None, mode="Deathmatch")])

    result = discover_match_ids_paginated(page, "Casual#NA1", 50)

    assert result.match_ids == []
    assert result.status is DiscoveryStatus.NO_HISTORY


# --------------------------------------------------------------------------
# Control/transport anomalies are INCOMPLETE, not exhaustion
# --------------------------------------------------------------------------

def test_click_that_fires_no_request_is_incomplete():
    pages = [_payload(_page_ids(0), next_cursor=1), _payload(_page_ids(1), next_cursor=2)]
    page = FakePage(pages, fire_xhr=False)

    result = discover_match_ids_paginated(page, "Silent#NA1", 100)

    assert result.status is DiscoveryStatus.INCOMPLETE
    assert "fired no history request" in result.reason


def test_control_vanishing_while_cursor_says_more_is_incomplete():
    """The DOM and metadata.next agreed in every observed case, so a missing
    button with a live cursor is an anomaly -- not an end of history."""
    pages = [_payload(_page_ids(0), next_cursor=1)]
    page = FakePage(pages)
    page.load_more_present = False

    result = discover_match_ids_paginated(page, "Odd#NA1", 100)

    assert result.status is DiscoveryStatus.INCOMPLETE
    assert "no usable Load More control" in result.reason


def test_zero_requested_is_a_noop():
    page = FakePage([_payload(_page_ids(0), next_cursor=1)])

    result = discover_match_ids_paginated(page, "Deep#NA1", 0)

    assert result.match_ids == []
    assert result.status is DiscoveryStatus.COMPLETE
    assert page.clicks == 0


# --------------------------------------------------------------------------
# Pacing and reporting
# --------------------------------------------------------------------------

def test_pacing_sleeps_between_pages(monkeypatch):
    """A history page load is a request like any other -- it gets the same
    5-12s spacing as a match fetch."""
    slept = []
    monkeypatch.setattr(
        "app.adapters.trackergg_browserstate_source.time.sleep", lambda s: slept.append(s)
    )
    pages = [_payload(_page_ids(i), next_cursor=i + 1) for i in range(4)]

    discover_match_ids_paginated(FakePage(pages), "Deep#NA1", 80)

    assert len(slept) == 2, "paced between pages, not after the last one"
    assert all(5 <= s <= 12 for s in slept), slept


def test_summary_always_shows_reached_over_requested():
    result = DiscoveryResult(
        riot_id="NPrightdolphin#NA1",
        requested=100,
        match_ids=["a"] * 20,
        status=DiscoveryStatus.INCOMPLETE,
        reason="cursor stuck",
        pages_fetched=1,
    )

    summary = result.summary()

    assert "20/100" in summary
    assert "INCOMPLETE" in summary
    assert "cursor stuck" in summary
    assert not result.is_conclusive
