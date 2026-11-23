"use strict";

function bindAppKey(root, {fetch: fetchKey = globalThis.fetch, clipboard = globalThis.navigator?.clipboard} = {}) {
  const button = root.querySelector("[data-app-key-copy]");
  const status = root.querySelector("[data-app-key-status]");
  if (!button || !status) return;
  button.addEventListener("click", async function () {
    button.disabled = true;
    status.textContent = "Creating key…";
    try {
      const response = await fetchKey("/replays/upload/app-key", {method: "POST", cache: "no-store"});
      const body = await response.json();
      if (!response.ok) {
        status.textContent = body.error || "Couldn't create key: try again";
        return;
      }
      try {
        await clipboard.writeText(body.key);
        status.textContent = "Key copied: now run the app";
      } catch (_) {
        status.textContent = "Couldn't copy: allow clipboard access and try again";
      }
    } catch (_) {
      status.textContent = "Couldn't create key: try again";
    } finally {
      button.disabled = false;
    }
  });
}

if (typeof module !== "undefined") module.exports = {bindAppKey};
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", function () { bindAppKey(document); });
}
