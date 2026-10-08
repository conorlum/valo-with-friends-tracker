# The replay upload worker (Stage 3). Build context: the repository root.
#   docker build -f replay_worker/Dockerfile -t replay-worker .
# Written 2026-09-25 and NOT built yet: Docker doesn't run on the development machine, so the
# first build is Render's (docs/replay-viewer-plan.md, W-c). The parser pin is
# webapp/replay_parser.json, the single source for this image and scripts/build_replay_parser.ps1.

# ---- 1. the pinned parser source, patched from replay_parser.json ------------------------------
FROM python:3.13-slim AS source
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY webapp/replay_parser.json /pin/replay_parser.json
COPY replay_worker/apply_parser_pin.py /pin/apply_parser_pin.py
RUN python /pin/apply_parser_pin.py /pin/replay_parser.json /parser

# ---- 2. a Linux build of the parser ---------------------------------------------------------------
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS parser
COPY --from=source /parser /parser
WORKDIR /parser
# Every export's manifest names the parser commit, and condense checks it against the pin (D7).
RUN dotnet publish src/CliReader -c Release -o /out

# ---- 3. the runtime: .NET runtime + Python, the condenser, the server ------------------------------
FROM mcr.microsoft.com/dotnet/runtime:10.0
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home /srv worker
# Map control's own interpreter (docs/map-control-worker-plan.md): numpy, scipy and Pillow at
# webapp/requirements.txt's versions, wheels only (a missing wheel fails the build rather than
# compiling). The server keeps running on the system python3, which has none of them.
RUN python3 -m venv /opt/control-venv \
 && /opt/control-venv/bin/pip install --no-cache-dir --only-binary=:all: numpy==2.4.6 scipy==1.18.1 Pillow==10.4.0
COPY --from=parser /out /opt/parser
# Only what the condenser reads: app/replays (stdlib only), the vendored map/agent tables and the pin;
# and the control engine and the gap detector, run only by the control venv's children.
COPY webapp/app/__init__.py /srv/webapp/app/__init__.py
COPY webapp/app/replays /srv/webapp/app/replays
COPY webapp/app/control /srv/webapp/app/control
COPY webapp/app/gaps /srv/webapp/app/gaps
COPY webapp/app/static/data /srv/webapp/app/static/data
COPY webapp/replay_parser.json /srv/webapp/replay_parser.json
COPY replay_worker /srv/replay_worker
# The smoke test: the control venv imports the engine, or the build fails here (not silently later).
RUN PYTHONPATH=/srv/webapp:/srv /opt/control-venv/bin/python -c "import numpy, scipy, PIL, app.control.engine, app.control.task, app.gaps.detect, app.gaps.cache, app.gaps.rows, app.gaps.backshots, replay_worker.control_job, replay_worker.height_job, app.control.height_job, app.replays.height_inputs, app.control.feature_job, app.replays.map_feature_artifacts"
RUN mkdir -p /jobs/control_cache && chown -R worker /jobs
# No `USER worker` here: the entrypoint starts as root only to chown the .vrf archive's disk (Render
# mounts it root-owned), then drops to `worker` with setpriv before the server starts. This guard fails
# the build if the image ever stops shipping setpriv (util-linux).
RUN command -v setpriv
WORKDIR /srv
# No DB credentials and no secrets: the worker only parses. REPLAY_MEMORY_CAP_MB caps the
# parser's address space; set it from the Stage 3 container measurement (decision 9).
# Decision 10 (approved D10), from the six competitive files (60-91 MB): the cap is
# max(80 MB, 2 x 90.5 MB), rounded up to 200 MB (the user's call, 2026-10-01; it must match the web
# app's replay_upload_max_bytes); the timeout max(180 s, 3 x the slowest parse), the slowest
# estimated at 79 s from the 52 s measured locally on the 60 MB file, scaled by size (a 200 MB file
# scales to ~175 s, still under 240 s).
ENV REPLAY_PARSER_CMD='["/opt/parser/CliReader", "export", "{vrf}", "--output", "{out}"]' \
    REPLAY_WORKER_TMP=/jobs \
    REPLAY_TIMEOUT_S=240 \
    REPLAY_MAX_BYTES=200000000 \
    REPLAY_QUEUE_SIZE=5 \
    REPLAY_MEMORY_CAP_MB=0 \
    REPLAY_WORKER_HOST=0.0.0.0 \
    REPLAY_WORKER_PORT=8080 \
    REPLAY_CONTROL_CMD='["/opt/control-venv/bin/python", "-m", "replay_worker.control_job"]' \
    REPLAY_CONTROL_WORKERS=2 \
    REPLAY_HEIGHT_CMD='["/opt/control-venv/bin/python", "-m", "replay_worker.height_job"]' \
    CONTROL_CACHE_DIR=/jobs/control_cache \
    PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["sh", "/srv/replay_worker/entrypoint.sh"]
CMD ["python3", "-m", "replay_worker.server"]
