"""The 2D replay viewer's data path: exporter contract, condenser and linker.

See docs/replay-viewer-plan.md. Nothing here touches the database, and nothing here
feeds Impact scoring: replay code never imports `app.scoring.impact`,
`kill_order_leverage` or `win_probability`.
"""
