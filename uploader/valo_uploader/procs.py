"""Read the process list without interacting with the game."""

import os
import subprocess

from .constants import PROCESS_NAME


def valorant_running(run=subprocess.run) -> bool:
    try:
        options = dict(capture_output=True, text=True, timeout=5, check=False)
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = run(["tasklist", "/FI", "IMAGENAME eq " + PROCESS_NAME, "/NH"], **options)
        return result.returncode == 0 and any(
            line.split()[0].casefold() == PROCESS_NAME.casefold()
            for line in result.stdout.splitlines() if line.split()
        )
    except Exception:
        return False
