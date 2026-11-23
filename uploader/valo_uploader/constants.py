"""Timing and application defaults shared by the uploader and updater."""

PROCESS_NAME = "VALORANT-Win64-Shipping.exe"
PROCESS_POLL_S = 5
ACTIVE_SCAN_S = 10
IDLE_SCAN_S = 1800
STATUS_POLL_S = 10
REFUSED_RETRY_S = 600
REFUSED_WINDOW_S = 5 * 3600
FAILED_RETRY_S = 86400
KEEP_DAYS_DEFAULT = 14
DELETE_BATCH = 5  # entries per sweep that may be hashed and checked with the site
STABLE_GAP_S = 5
SITE = "https://valowithfriendstracker.onrender.com"
UPDATE_EVERY_S = 6 * 3600
REPO = "conorlum/valo-with-friends-tracker"
STARTED_OK_WAIT_S = 90
