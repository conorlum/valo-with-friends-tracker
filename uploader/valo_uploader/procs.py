"""Read the process list without interacting with the game."""

import csv
import os
import subprocess

from .constants import PROCESS_NAME


def valorant_running(run=subprocess.run) -> bool:
    try:
        options = dict(capture_output=True, text=True, timeout=5, check=False)
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        # The default table clips long image names (this one is 27 characters);
        # CSV output keeps the whole name in the first field.
        result = run(["tasklist", "/FI", "IMAGENAME eq " + PROCESS_NAME, "/NH", "/FO", "CSV"],
                     **options)
        if result.returncode != 0:
            return False
        wanted = PROCESS_NAME.casefold()
        return any(row and row[0].strip().casefold() == wanted
                   for row in csv.reader(result.stdout.splitlines()))
    except Exception:
        return False
