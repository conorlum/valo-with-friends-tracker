"""Notification-free tray UI, with a GUI-independent menu description."""

import threading


def menu_model(status, run_key_on) -> list[tuple]:
    """Rows are (label, action or submenu rows, enabled, checked or None)."""
    labels = {"paused": "Paused", "needs_key": "Needs key", "uploading": "Uploading",
              "queued": "Queued", "idle": "Idle"}
    text = labels[status["state"]]
    if status["queue_length"]:
        text += f" ({status['queue_length']} queued)"
    uploads = [(name, None, False, None) for name in reversed(status["last_stored"][-5:])]
    message = [(status["message"], None, False, None)] if status.get("message") else []
    return [("Status: " + text, None, False, None),
            *message,
            ("Last uploads", uploads or [("None yet", None, False, None)], True, None),
            ("Resume" if status["paused"] else "Pause", "pause", True, None),
            ("Open log", "open_log", True, None),
            ("Start with Windows", "startup", True, bool(run_key_on)),
            ("Change key", "change_key", True, None),
            ("Quit", "quit", True, None)]


class Tray:
    def __init__(self, *, status, run_key_on, on_pause, on_open_log, on_startup,
                 on_change_key, on_quit):
        self.status = status
        self.run_key_on = run_key_on
        self.actions = dict(pause=on_pause, open_log=on_open_log, startup=on_startup,
                            change_key=on_change_key, quit=on_quit)
        self.message = None
        self.stopped = threading.Event()
        self.icon = None

    def rows(self):
        status = dict(self.status())
        if self.message:
            status["message"] = self.message
        return menu_model(status, self.run_key_on())

    def set_message(self, text):
        self.message = text
        self.refresh()

    def refresh(self):
        icon = self.icon
        if icon is not None and not self.stopped.is_set():
            icon.title = "ValoUploader - " + self.rows()[0][0].removeprefix("Status: ")
            icon.update_menu()

    def stop(self):
        self.stopped.set()
        if self.icon is not None:
            self.icon.stop()

    def run(self, ready):
        run_tray(self, ready)


def run_tray(tray, ready):
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill="#172534")
    draw.polygon([(13, 17), (25, 17), (33, 36), (41, 17), (53, 17),
                  (37, 49), (28, 49)], fill="#ff5265")

    def action_callback(name):
        def callback(icon, item):
            tray.actions[name]()
            tray.refresh()
        return callback

    def checked_callback(value):
        def checked(item):
            return value
        return checked

    def items(rows):
        for label, action, enabled, checked in rows:
            if isinstance(action, list):
                callback = pystray.Menu(*items(action))
            elif action is None:
                callback = None
            else:
                callback = action_callback(action)
            check = None if checked is None else checked_callback(checked)
            yield pystray.MenuItem(label, callback, enabled=enabled, checked=check)

    menu = pystray.Menu(lambda: items(tray.rows()))
    icon = pystray.Icon("ValoUploader", image, "ValoUploader", menu)
    tray.icon = icon

    def setup(icon):
        if tray.stopped.is_set():
            icon.stop()
            return
        try:
            icon.visible = True
            ready()
            tray.refresh()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Tray setup failed")
            tray.stop()

    try:
        icon.run(setup=setup)
    finally:
        tray.icon = None
