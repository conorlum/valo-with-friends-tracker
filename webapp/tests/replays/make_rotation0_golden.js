"use strict";

// Regenerate only from the pinned base:
// git show 54e44c74:webapp/app/static/js/replay.js > <temp>/rotation0-base.js
// node tests/replays/make_rotation0_golden.js <temp>/rotation0-base.js > tests/fixtures/replay/rotation0_calls.json
// In PowerShell, use Python subprocess/write_text to preserve UTF-8 without redirection transcoding.
const path = require("path");
const {recordRounds} = require("./viewer_recorder.js");
if (require.main === module) {
  if (!process.argv[2]) throw new Error("Supply replay.js extracted from commit 54e44c74");
  process.stdout.write(JSON.stringify({base: "54e44c74", rounds: recordRounds(path.resolve(process.argv[2]))}) + "\n");
}
