"""Run the clipboard interaction in Node with a minimal DOM."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("scenario", ["success", "forbidden", "clipboard-rejected"])
def test_copy_app_key(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = Path(__file__).resolve().parents[2] / "app/static/js/upload_app_key.js"
    script = r"""
const assert = require('node:assert/strict');
const {bindAppKey} = require(process.argv[1]);
const scenario = process.argv[2];
const key = 'vwf1_' + 'A'.repeat(43);
class Element {
  constructor(attributes) { this.attributes = attributes; this.textContent = ''; this.handlers = {}; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
}
const button = new Element({'data-app-key-copy': ''});
const status = new Element({'data-app-key-status': ''});
const root = {querySelector: selector => ({'[data-app-key-copy]': button, '[data-app-key-status]': status}[selector])};
let copied = [], requests = 0;
bindAppKey(root, {
  fetch: async (url, options) => {
    requests++;
    assert.equal(url, '/replays/upload/app-key');
    assert.equal(options.method, 'POST');
    return {ok: scenario !== 'forbidden', status: scenario === 'forbidden' ? 403 : 200,
      json: async () => scenario === 'forbidden' ? {error: 'Enter the invite code first.'} : {key, label: 'unnamed'}};
  },
  clipboard: {writeText: async value => {
    if (scenario === 'clipboard-rejected') throw Error('denied');
    copied.push(value);
  }}
});
(async () => {
  await button.handlers.click();
  assert.equal(requests, 1);
  if (scenario === 'success') {
    assert.deepEqual(copied, [key]);
    assert.equal(status.textContent, 'Key copied: now run the app');
    await button.handlers.click();
    assert.equal(requests, 2, 'each click must mint again');
  } else if (scenario === 'forbidden') {
    assert.deepEqual(copied, []);
    assert.equal(status.textContent, 'Enter the invite code first.');
  } else {
    assert.equal(status.textContent, "Couldn't copy: allow clipboard access and try again");
  }
  for (const element of [button, status]) {
    assert(!element.textContent.includes(key));
    assert(!JSON.stringify(element.attributes).includes(key));
    for (const value of Object.values(element)) if (typeof value === 'string') assert(!value.includes(key));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", script, str(source), scenario],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
