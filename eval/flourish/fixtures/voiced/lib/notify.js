"use strict";

const { spawnSync } = require("child_process");

// One transport. Mail is what every box already has; anything fancier is
// someone else's daemon.
const TRANSPORTS = {
  mail(report) {
    const to = process.env.HUSHCRON_MAILTO || "root";
    const body = `command: ${report.command}\nattempts: ${report.attempts}\n\n${report.output}`;
    spawnSync("mail", ["-s", `[hushcron] failed: ${report.command}`, to], {
      input: body,
      encoding: "utf8",
    });
  },
};

function notify(name, report) {
  const transport = TRANSPORTS[name];
  if (!transport) {
    throw new Error(`unknown transport: ${name} (only "mail" is implemented)`);
  }
  transport(report);
}

module.exports = { notify };
