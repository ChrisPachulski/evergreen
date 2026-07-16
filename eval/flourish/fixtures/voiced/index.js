#!/usr/bin/env node
// hushcron: wrap a cron job; stay silent on success, speak on failure.
"use strict";

const { runWithRetries } = require("./lib/runner");
const { notify } = require("./lib/notify");

function parseOpts(args) {
  const opts = { retries: Number(process.env.HUSHCRON_RETRIES || 0) };
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--retries") {
      opts.retries = Number(args[i + 1]);
      i += 1;
    }
  }
  return opts;
}

function main() {
  const argv = process.argv.slice(2);
  const sep = argv.indexOf("--");
  if (sep === -1 || sep === argv.length - 1) {
    console.error("usage: hushcron [--retries N] -- <command> [args...]");
    process.exit(64); // EX_USAGE
  }
  const opts = parseOpts(argv.slice(0, sep));
  const command = argv.slice(sep + 1);
  const result = runWithRetries(command, opts.retries);
  if (result.code !== 0) {
    notify("mail", {
      command: command.join(" "),
      attempts: result.attempts,
      output: result.output,
    });
  }
  process.exit(result.code); // the wrapped command's exit code passes through untouched
}

main();
