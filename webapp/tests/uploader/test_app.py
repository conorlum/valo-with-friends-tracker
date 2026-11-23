"""App wiring is exercised without a GUI, registry writes, or network access."""

import logging
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from valo_uploader import __version__, instance
from valo_uploader import main as app
from valo_uploader.api import BadKey, Offline
from valo_uploader.config import Config
from valo_uploader.keywindow import clipboard_key, initial_key, mask
from valo_uploader.paths import app_dir
from valo_uploader.tray import menu_model


KEY = "vwf1_" + "x" * 39 + "ABCD"
ATTEMPT = "0123456789abcdef"


@pytest.fixture(autouse=True)
def restore_logging():
    logger = logging.getLogger("valo_uploader")
    level, propagate, handlers = logger.level, logger.propagate, list(logger.handlers)
    yield
    for handler in list(logger.handlers):
        if handler not in handlers:
            handler.close()
            logger.removeHandler(handler)
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


@pytest.fixture
def rig(monkeypatch, tmp_path):
    env = {"APPDATA": str(tmp_path / "roaming"), "LOCALAPPDATA": str(tmp_path / "local")}
    calls = []
    config = Config()
    engine = Mock()
    engine.lock = threading.RLock()
    engine.config = config
    engine.status.return_value = dict(state="idle", queue_length=0, last_stored=[],
                                      needs_key=False, paused=False)
    engine.idle.return_value = True
    closed = threading.Event()
    cleaned = threading.Event()
    tray = Mock()
    tray.run.side_effect = lambda ready: ready()
    tray.stop.side_effect = closed.set
    monkeypatch.setattr(app, "ensure_installed", lambda *a: calls.append("install") or "installed")
    monkeypatch.setattr(app.instance, "acquire", lambda **kw: calls.append("acquire") or object())
    released = Mock()
    monkeypatch.setattr(app.instance, "release", released)
    monkeypatch.setattr(app, "cleanup_stale", Mock())
    monkeypatch.setattr(app, "recover_update", Mock(return_value="healthy"))
    monkeypatch.setattr(app, "load_key", lambda **kw: KEY)
    monkeypatch.setattr(app, "save_key", Mock())
    monkeypatch.setattr(app, "clipboard_key", lambda: None)
    monkeypatch.setattr(app, "ask_for_key", Mock(return_value=None))
    monkeypatch.setattr(app, "Client", Mock())
    monkeypatch.setattr(app, "Engine", Mock(return_value=engine))
    monkeypatch.setattr(app, "State", Mock())
    monkeypatch.setattr(app, "Watcher", Mock())
    monkeypatch.setattr(app, "Tray", Mock(return_value=tray))
    monkeypatch.setattr(app, "Config", SimpleNamespace(load=lambda **kw: config))
    monkeypatch.setattr(app, "run", lambda engine, stop, after_tick: None)
    monkeypatch.setattr(app, "maybe_update", Mock(return_value="idle"))
    monkeypatch.setattr(app, "after_update_cleanup", lambda *a, **kw: cleaned.set())
    return SimpleNamespace(env=env, calls=calls, config=config, engine=engine, tray=tray,
                           closed=closed, cleaned=cleaned, released=released)


def start(rig, argv=(), **kwargs):
    return app.main(list(argv), env=rig.env, frozen=kwargs.pop("frozen", False), **kwargs)


def test_selftest_has_no_application_side_effects(rig, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(app.tempfile, "gettempdir", lambda: str(tmp_path))
    for name in ("ensure_installed", "cleanup_stale", "load_key", "Tray", "configure_logging",
                 "maybe_update", "State"):
        monkeypatch.setattr(app, name, Mock(side_effect=AssertionError(name)))
    monkeypatch.setattr(app.instance, "acquire", Mock(side_effect=AssertionError("acquire")))
    assert start(rig, ["--selftest"], frozen=True) == 0
    line = f"ValoUploader {__version__}\n"
    assert capsys.readouterr().out == line
    assert (tmp_path / "ValoUploader-selftest.txt").read_text() == line
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ValoUploader-selftest.txt"]


@pytest.mark.parametrize("text", [None, "", "hello", KEY + "junk", "vwf1_short"])
def test_initial_key_rejects_other_clipboard_text(text):
    assert initial_key(text) is None


def test_clipboard_and_mask():
    assert initial_key(KEY) == KEY
    assert clipboard_key(lambda: KEY) == KEY
    assert clipboard_key(lambda: "unrelated clipboard") is None
    assert clipboard_key(lambda: 1 / 0) is None
    assert mask(KEY) == "vwf1_\u2026ABCD"
    assert KEY[5:-4] not in mask(KEY)


@pytest.mark.parametrize("state,label,pause", [("paused", "Paused", "Resume"),
                                               ("needs_key", "Needs key", "Pause"),
                                               ("uploading", "Uploading", "Pause")])
def test_menu_model(state, label, pause):
    rows = menu_model(dict(state=state, paused=state == "paused", queue_length=2,
                           last_stored=["sample.replay"]), True)
    assert rows[0][0].startswith("Status: " + label)
    assert rows[0][2] is False
    assert [row[0] for row in rows[1:]] == ["Last uploads", pause, "Open log",
                                                         "Start with Windows", "Change key", "Quit"]
    assert rows[1][1][0][0] == "sample.replay"
    assert rows[4][3] is True


@pytest.mark.parametrize("state,label", [("paused", "Paused"), ("needs_key", "Needs key"),
                                         ("uploading", "Uploading"), ("queued", "Queued"),
                                         ("idle", "Idle")])
def test_update_message_sits_beside_status_never_replacing_it(state, label):
    status = dict(state=state, paused=state == "paused", queue_length=2 if state == "queued" else 0,
                  last_stored=[])
    plain = menu_model(status, False)
    shown = menu_model({**status, "message": "Updated to v9.9.9"}, False)
    assert shown[0] == plain[0]
    assert shown[0][0].startswith("Status: " + label)
    assert shown[1] == ("Updated to v9.9.9", None, False, None)
    assert shown[2:] == plain[1:]


def test_tray_keeps_current_status_after_update_message():
    from valo_uploader.tray import Tray

    current = dict(state="uploading", paused=False, queue_length=1, last_stored=[])
    tray = Tray(status=lambda: current, run_key_on=lambda: False, on_pause=None, on_open_log=None,
                on_startup=None, on_change_key=None, on_quit=None)
    tray.icon = Mock()
    tray.set_message("Updated to v9.9.9")
    for state in ("uploading", "paused", "needs_key"):
        current = dict(current, state=state, paused=state == "paused")
        tray.refresh()
        assert tray.rows()[0][0].startswith("Status: " + {"uploading": "Uploading",
                                                          "paused": "Paused",
                                                          "needs_key": "Needs key"}[state])
        assert tray.icon.title.startswith("ValoUploader - " + {"uploading": "Uploading",
                                                               "paused": "Paused",
                                                               "needs_key": "Needs key"}[state])
        assert "Updated" not in tray.icon.title
        assert tray.rows()[1][0] == "Updated to v9.9.9"


def test_install_precedes_guard_and_relaunch_exits(rig, monkeypatch):
    monkeypatch.setattr(app.instance, "acquire", lambda **kw: rig.calls.append("acquire"))
    assert start(rig, frozen=True) == 0
    assert rig.calls == ["install", "acquire"]
    app.Tray.assert_not_called()
    rig.calls.clear()
    monkeypatch.setattr(app, "ensure_installed", lambda *a: rig.calls.append("install") or "relaunched")
    assert start(rig, frozen=True) == 0
    assert rig.calls == ["install"]


def test_recovery_runs_before_install_and_guard_when_frozen(rig, monkeypatch):
    monkeypatch.setattr(app, "recover_update",
                        Mock(side_effect=lambda *a, **kw: rig.calls.append("recover") or "healthy"))
    assert start(rig, frozen=True) == 0
    assert rig.calls == ["recover", "install", "acquire"]
    path = app.recover_update.call_args.args[0]
    assert path == app.install_dir(rig.env) / "ValoUploader.exe"


def test_recovery_is_skipped_after_update_and_when_not_frozen(rig):
    assert start(rig, ["--after-update", ATTEMPT, "--parent-pid", "1"], frozen=True) == 0
    assert start(rig) == 0
    app.recover_update.assert_not_called()


def test_recovery_failure_cannot_stop_startup(rig):
    app.recover_update.side_effect = RuntimeError("boom")
    assert start(rig, frozen=True) == 0
    assert rig.calls == ["install", "acquire"]


def test_python_run_skips_install_cleanup_and_update(rig, monkeypatch):
    monkeypatch.setattr(app, "run", lambda engine, stop, after_tick: after_tick(100))
    assert start(rig) == 0
    assert rig.calls == ["acquire"]
    app.cleanup_stale.assert_not_called()
    app.maybe_update.assert_not_called()
    rig.released.assert_called_once()


def test_log_redacts_key_from_runner_failure(rig, monkeypatch):
    def run(*args, **kw):
        raise ValueError(KEY)
    monkeypatch.setattr(app, "run", run)
    assert start(rig) == 0
    text = (app_dir(rig.env) / "uploader.log").read_text()
    assert "vwf1_***" in text
    assert KEY not in text


@pytest.mark.parametrize("corrupt", [False, True])
def test_no_key_or_decrypt_failure_prompts_and_saves(rig, monkeypatch, corrupt):
    def load(**kw):
        if corrupt:
            raise ValueError("cannot decrypt")
        return None
    monkeypatch.setattr(app, "load_key", load)
    monkeypatch.setattr(app, "clipboard_key", lambda: KEY)
    app.ask_for_key.return_value = KEY
    assert start(rig) == 0
    app.ask_for_key.assert_called_once()
    assert app.ask_for_key.call_args.args[0] == KEY
    validate = app.ask_for_key.call_args.args[1]
    validate(KEY)
    app.Client.return_value.key_info.assert_called_once()
    app.save_key.assert_called_once_with(KEY, env=rig.env)


def test_cancel_first_key_does_not_start_tray(rig, monkeypatch):
    monkeypatch.setattr(app, "load_key", lambda **kw: None)
    assert start(rig) == 0
    app.Tray.assert_not_called()
    rig.released.assert_called_once()


def test_ack_only_after_ready_and_cleanup_is_background(rig, monkeypatch):
    ack = app_dir(rig.env) / ("ack-" + ATTEMPT)
    cleanup_threads = []
    def cleanup(*a, **kw):
        assert ack.exists()
        cleanup_threads.append(threading.get_ident())
        rig.cleaned.set()
    monkeypatch.setattr(app, "after_update_cleanup", cleanup)
    def tray_run(ready):
        assert not ack.exists()
        ready()
        assert ack.exists()
        assert rig.cleaned.wait(2)
    rig.tray.run.side_effect = tray_run
    assert start(rig, ["--after-update", ATTEMPT, "--parent-pid", "1"], frozen=True) == 0
    assert cleanup_threads[0] != threading.get_ident()
    rig.tray.set_message.assert_called_with(f"Updated to v{__version__}")
    # The renamed .old image can have an old mtime while its parent still runs.
    # Only the post-ack cleanup may remove it during an update startup.
    app.cleanup_stale.assert_not_called()


def test_tray_failure_before_ready_never_acknowledges(rig):
    rig.tray.run.side_effect = RuntimeError("tray failed")
    with pytest.raises(RuntimeError, match="tray failed"):
        start(rig, ["--after-update", ATTEMPT, "--parent-pid", "1"], frozen=True)
    assert not (app_dir(rig.env) / ("ack-" + ATTEMPT)).exists()
    assert not rig.cleaned.is_set()
    rig.released.assert_called_once()


@pytest.mark.parametrize("result", ["updated", "rolled_back", "recovery_needed"])
@pytest.mark.parametrize("quit_first", [False, True])
def test_main_joins_runner_through_handoff(rig, monkeypatch, result, quit_first):
    swapping, finish, returned = threading.Event(), threading.Event(), threading.Event()
    stop_seen = []
    def update(*args, stop_app, release_mutex, **kw):
        if quit_first:
            app.Tray.call_args.kwargs["on_quit"]()
        stop_app()
        release_mutex()
        swapping.set()
        assert finish.wait(3)
        return result
    def run(engine, stop, after_tick):
        after_tick(100)
        stop_seen.append(stop.is_set())
    monkeypatch.setattr(app, "maybe_update", update)
    monkeypatch.setattr(app, "run", run)
    rig.tray.run.side_effect = lambda ready: rig.closed.wait(3)
    errors = []
    def invoke():
        try:
            start(rig, frozen=True)
        except BaseException as exc:
            errors.append(exc)
        finally:
            returned.set()
    worker = threading.Thread(target=invoke)
    worker.start()
    try:
        assert swapping.wait(2)
        assert not returned.is_set()
    finally:
        finish.set()
        worker.join(3)
    assert returned.is_set() and not errors
    assert stop_seen == [True]
    rig.released.assert_called_once()


def test_after_update_retries_guard(rig, monkeypatch):
    acquire = Mock(side_effect=[None, None, object()])
    monkeypatch.setattr(app.instance, "acquire", acquire)
    monkeypatch.setattr(app.time, "sleep", lambda seconds: None)
    assert start(rig, ["--after-update", ATTEMPT, "--parent-pid", "1"], frozen=True) == 0
    assert acquire.call_count == 3


def test_after_update_guard_retry_is_bounded(rig, monkeypatch):
    monkeypatch.setattr(app.instance, "acquire", Mock(return_value=None))
    monkeypatch.setattr(app.time, "monotonic", Mock(side_effect=[0, 0, 31]))
    monkeypatch.setattr(app.time, "sleep", lambda seconds: None)
    assert start(rig, ["--after-update", ATTEMPT, "--parent-pid", "1"], frozen=True) == 0
    assert app.instance.acquire.call_count == 2
    app.Tray.assert_not_called()


@pytest.mark.parametrize("failure", [BadKey(KEY), Offline(KEY)])
def test_key_window_validation_can_retry_without_revealing_key(monkeypatch, failure):
    from valo_uploader.keywindow import ask_for_key

    commands, texts, variables = {}, [], []
    class Variable:
        def __init__(self, value=""):
            self.value = value
            variables.append(self)
        def get(self):
            return self.value
        def set(self, value):
            self.value = value
    class Widget:
        def __init__(self, *args, **kwargs):
            texts.append(kwargs.get("text", ""))
            if "command" in kwargs:
                commands[kwargs["text"]] = kwargs["command"]
        def pack(self, **kwargs):
            pass
    class Root:
        destroyed = False
        def title(self, title):
            pass
        def resizable(self, *args):
            pass
        def protocol(self, *args):
            pass
        def destroy(self):
            self.destroyed = True
        def mainloop(self):
            commands["Save"]()
            assert not self.destroyed
            assert variables[1].value and KEY not in variables[1].value
            commands["Save"]()
            assert self.destroyed

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(
        Tk=Root, StringVar=Variable, ttk=SimpleNamespace(
            Frame=Widget, Label=Widget, Entry=Widget, Button=Widget)))
    validate = Mock(side_effect=[failure, None])
    assert ask_for_key(KEY, validate) == KEY
    assert validate.call_count == 2
    assert "Use this key? " + mask(KEY) in texts
    assert all(KEY not in text for text in texts)


def test_change_key_and_pause_actions(rig):
    replaced = threading.Event()
    rig.engine.key_replaced.side_effect = lambda client: replaced.set()

    def tray_run(ready):
        assert threading.current_thread() is threading.main_thread()
        callbacks = app.Tray.call_args.kwargs
        callbacks["on_pause"]()
        assert rig.config.paused
        app.ask_for_key.return_value = KEY
        callbacks["on_change_key"]()
        assert replaced.wait(2)
        rig.engine.key_replaced.assert_called_once_with(app.Client.return_value)
    rig.tray.run.side_effect = tray_run
    assert start(rig) == 0
    app.save_key.assert_called_once_with(KEY, env=rig.env)
    assert (app_dir(rig.env) / "settings.json").exists()


def test_pause_never_waits_for_the_engine_lock(rig):
    """R2.1 F5: a tick holds the engine lock through an upload; the tray's Pause must not wait for it."""
    held, release = threading.Event(), threading.Event()

    def hold():
        with rig.engine.lock:
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(2)
    outcome = []

    def tray_run(ready):
        callbacks = app.Tray.call_args.kwargs
        worker = threading.Thread(target=callbacks["on_pause"], daemon=True)
        worker.start()
        worker.join(1)
        outcome.append(not worker.is_alive())     # done while the lock is still held elsewhere
        release.set()
        worker.join(3)
    rig.tray.run.side_effect = tray_run
    assert start(rig) == 0
    holder.join(3)
    assert outcome == [True] and rig.config.paused


def test_change_key_replacement_runs_off_the_tray_thread_and_may_wait_for_the_lock(rig):
    entered, release = threading.Event(), threading.Event()
    seen = []

    def key_replaced(client):
        seen.append(threading.current_thread())
        entered.set()
        release.wait(5)           # as if waiting for a tick's lock

    rig.engine.key_replaced.side_effect = key_replaced
    returned = []

    def tray_run(ready):
        callbacks = app.Tray.call_args.kwargs
        app.ask_for_key.return_value = KEY
        callbacks["on_change_key"]()      # must return while key_replaced is still waiting
        returned.append(entered.wait(2) and not release.is_set())
        release.set()
    rig.tray.run.side_effect = tray_run
    assert start(rig) == 0
    assert returned == [True]
    assert seen and seen[0] is not threading.main_thread()


def test_quit_during_an_upload_closes_the_tray_at_once_and_main_still_joins_the_runner(rig, monkeypatch):
    uploading, finish, finished = threading.Event(), threading.Event(), threading.Event()

    def run(engine, stop, after_tick):
        uploading.set()
        finish.wait(5)
        finished.set()

    monkeypatch.setattr(app, "run", run)
    order = []

    def tray_run(ready):
        assert uploading.wait(2)
        app.Tray.call_args.kwargs["on_quit"]()
        order.append(("tray closed", rig.closed.is_set(), finished.is_set()))
        threading.Timer(0.2, finish.set).start()
    rig.tray.run.side_effect = tray_run
    assert start(rig) == 0
    assert order == [("tray closed", True, False)]
    assert finished.is_set()          # main returned only after the runner did


def test_retry_generation_is_applied_at_start_and_saves_the_config(rig):
    saved = []
    rig.engine.apply_retry_generation.side_effect = lambda save: (save(), saved.append(True))
    assert start(rig) == 0
    rig.engine.apply_retry_generation.assert_called_once()
    assert saved and (app_dir(rig.env) / "settings.json").exists()


def test_a_relaunch_by_update_recovery_exits_before_the_instance_guard(rig, monkeypatch):
    monkeypatch.setattr(app, "recover_update",
                        Mock(side_effect=lambda *a, **kw: rig.calls.append("recover") or "relaunched"))
    assert start(rig, frozen=True) == 0
    assert rig.calls == ["recover"]
    assert app.recover_update.call_args.kwargs["running_exe"] == app.Path(app.sys.executable)


def test_instance_fake_allows_foreign_thread_release():
    class Kernel:
        refs = 0
        error = 0
        closed_by = None
        def CreateMutexW(self, security, owned, name):
            assert owned is False and name == "Local\\ValoUploader"
            self.error = 183 if self.refs else 0
            self.refs += 1
            return 42
        def GetLastError(self):
            return self.error
        def CloseHandle(self, handle):
            self.closed_by = threading.get_ident()
            self.refs -= 1
            return 1
    kernel = Kernel()
    handle = instance.acquire(kernel32=kernel)
    assert instance.acquire(kernel32=kernel) is None
    assert kernel.refs == 1
    worker = threading.Thread(target=instance.release, args=(handle,))
    worker.start()
    worker.join()
    assert kernel.closed_by != threading.get_ident()
    second = instance.acquire(kernel32=kernel)
    assert second is not None
    instance.release(second)


@pytest.mark.parametrize("result", ["updated", "rolled_back", "recovery_needed"])
def test_updater_result_alone_ends_runner_and_tray(rig, monkeypatch, result):
    observed = []
    monkeypatch.setattr(app, "maybe_update", Mock(return_value=result))
    def run(engine, stop, after_tick):
        after_tick(100)
        observed.append(stop.is_set())
    monkeypatch.setattr(app, "run", run)
    rig.tray.run.side_effect = lambda ready: rig.closed.wait(2)
    assert start(rig, frozen=True) == 0
    assert observed == [True]
    assert rig.closed.is_set()


def test_tray_adapter_uses_supported_callbacks_and_no_notifications(monkeypatch):
    import inspect
    from valo_uploader.tray import Tray

    def item(label, action, *, enabled, checked):
        if callable(action):
            assert len(inspect.signature(action).parameters) == 2
        if checked is not None:
            assert len(inspect.signature(checked).parameters) == 1
        return SimpleNamespace(label=label, action=action, checked=checked)

    class Menu:
        def __init__(self, *rows):
            self.rows = rows
        def evaluate(self):
            return list(self.rows[0]()) if callable(self.rows[0]) else self.rows

    class Icon:
        def __init__(self, name, image, title, menu):
            self.menu, self.title = menu, title
            self.visible = False
        def run(self, setup):
            setup(self)
            rows = self.menu.evaluate()
            assert rows[4].checked(None) is True
            rows[2].action(self, rows[2])
            rows[6].action(self, rows[6])
        def update_menu(self):
            pass
        def stop(self):
            pass

    monkeypatch.setitem(sys.modules, "pystray", SimpleNamespace(Icon=Icon, Menu=Menu, MenuItem=item))
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=SimpleNamespace(new=Mock()),
                                                           ImageDraw=SimpleNamespace(Draw=Mock())))
    paused, ready = Mock(), Mock()
    tray = Tray(status=lambda: dict(state="idle", paused=False, queue_length=0, last_stored=[]),
                run_key_on=lambda: True, on_pause=paused, on_open_log=Mock(), on_startup=Mock(),
                on_change_key=Mock(), on_quit=lambda: tray.stop())
    tray.run(ready)
    ready.assert_called_once()
    paused.assert_called_once()
    assert tray.stopped.is_set()


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows named-object guard")
def test_real_instance_can_release_on_another_thread():
    handle = instance.acquire()
    assert handle is not None
    try:
        assert instance.acquire() is None
        worker = threading.Thread(target=instance.release, args=(handle,))
        worker.start()
        worker.join()
        handle = instance.acquire()
        assert handle is not None
    finally:
        instance.release(handle)
