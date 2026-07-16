"use strict";

const { spawnSync } = require("child_process");

// Run the command; retry on nonzero exit, up to `retries` extra attempts.
function runWithRetries(command, retries) {
  let attempts = 0;
  let code = 1;
  let output = "";
  while (attempts <= retries) {
    attempts += 1;
    const child = spawnSync(command[0], command.slice(1), { encoding: "utf8" });
    code = child.status === null ? 1 : child.status;
    output = `${child.stdout || ""}${child.stderr || ""}`;
    if (code === 0) break; // attempts stop at the first success
  }
  return { code, attempts, output };
}

module.exports = { runWithRetries };
