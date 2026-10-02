#!/bin/sh
# The replay worker's entrypoint (replay_worker/Dockerfile). It starts as root only to hand the .vrf
# archive's disk to the `worker` user: a Render persistent disk mounts root-owned. Then it drops to
# `worker` for good and runs the command (the image's CMD). A failed chown is logged, never fatal: the
# server's own probe then finds the disk unwritable and runs with the archive off.
# PROVISIONAL(D7): root entrypoint + setpriv, untested in a container (no Docker on the dev machine).
set -u
if [ -n "${REPLAY_ARCHIVE_DIR:-}" ] && [ -d "$REPLAY_ARCHIVE_DIR" ]; then
  chown worker:worker "$REPLAY_ARCHIVE_DIR" || echo "archive: chown of $REPLAY_ARCHIVE_DIR failed" >&2
fi
if [ "$(id -u)" = "0" ]; then
  if setpriv --reuid=worker --regid=worker --init-groups true; then
    exec setpriv --reuid=worker --regid=worker --init-groups "$@"
  fi
  # Uploads matter more than the drop: run on, loudly, rather than not at all.
  echo "entrypoint: setpriv could not drop to the worker user; running as root" >&2
fi
exec "$@"
