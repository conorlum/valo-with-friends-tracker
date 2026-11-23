"""Explicit key entry; GUI imports stay inside the two GUI functions."""

from .api import BadKey, Offline
from .keystore import looks_like_key


def initial_key(clipboard_text):
    return clipboard_text if looks_like_key(clipboard_text) else None


def mask(key):
    return "vwf1_\u2026" + key[-4:]


def tk_clipboard():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    finally:
        root.destroy()


def clipboard_key(read=tk_clipboard):
    try:
        return initial_key(read())
    except Exception:
        return None


def ask_for_key(initial: str | None, validate) -> str | None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("ValoUploader key")
    root.resizable(False, False)
    panel = ttk.Frame(root, padding=16)
    panel.pack(fill="both", expand=True)
    initial = initial_key(initial)
    value = tk.StringVar(value=initial or "")
    error = tk.StringVar()
    result = None
    if initial:
        ttk.Label(panel, text="Use this key? " + mask(initial)).pack(anchor="w")
    else:
        ttk.Label(panel, text="Paste your upload key:").pack(anchor="w")
    # Even a clipboard suggestion can be replaced by pasting another key.
    ttk.Entry(panel, textvariable=value, show="*", width=52).pack(pady=8)
    ttk.Label(panel, textvariable=error, wraplength=390).pack(anchor="w")

    def save():
        nonlocal result
        key = value.get().strip()
        if not looks_like_key(key):
            error.set("Paste a valid upload key (vwf1_...).")
            return
        try:
            validate(key)
        except BadKey:
            error.set("This key was not accepted. Check it and try again.")
            return
        except Offline:
            error.set("Cannot reach the site. Check your connection and try again.")
            return
        except Exception:
            error.set("Could not validate this key. Please try again.")
            return
        result = key
        root.destroy()

    buttons = ttk.Frame(panel)
    buttons.pack(anchor="e", pady=(8, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="left", padx=4)
    ttk.Button(buttons, text="Save", command=save).pack(side="left")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return result
