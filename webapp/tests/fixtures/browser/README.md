# Browser test assets

Pinned copies of the versions referenced by `app/templates/base.html`, intercepted
by the browser test fixture so acceptance tests make no external requests:

- htmx 1.9.12: https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js (Zero-Clause BSD).
- Chart.js 4.4.4: https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js
  (MIT, including bundled `@kurkle/color` 0.3.2).

The upstream license texts are adjacent to these files. These copies are test
fixtures; production asset delivery is unchanged. Update them deliberately when
the application's pinned versions change.
