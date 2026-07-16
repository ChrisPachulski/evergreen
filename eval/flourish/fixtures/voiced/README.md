# hushcron

Cron fails the way a houseplant dies: quietly, in a corner, weeks before anyone notices.

hushcron wraps the command in your crontab. When the command succeeds, hushcron says nothing
at all; when it fails, the failure gets retried and then mailed to a human. That is the whole
tool. What follows is, regrettably, organized for the person who wrote it.

## Where the code lives

`index.js` is the bin entry: it splits argv on `--`, treats everything after the separator as
the wrapped command, and hands off to the two lib modules. `lib/runner.js` runs the command
synchronously with `spawnSync`, capturing stdout and stderr together into one output blob.
`lib/notify.js` holds the transport table and the dispatch function.

## Retry mechanics

A run is retried only on nonzero exit, up to N extra attempts. N comes from `--retries` on the
command line or the `HUSHCRON_RETRIES` environment variable (the flag wins); the default is 0
— one attempt, no retries. Attempts stop at the first success.

## The mail transport

The only transport is local `mail(1)`. The failure report — command, attempt count, combined
output — goes to `HUSHCRON_MAILTO`, defaulting to `root`, with the subject
`[hushcron] failed: <command>`. `notify()` dispatches by transport name and throws on any name
it does not know.

### Knobs

| Variable | Meaning |
| --- | --- |
| `HUSHCRON_RETRIES` | Extra attempts after a failure. Default `0`. `--retries` overrides it. |
| `HUSHCRON_MAILTO` | Recipient of the failure mail. Default `root`. On Debian/Ubuntu the `mail` binary is not installed by default — you need `bsd-mailx` or `s-nail`, and hushcron does not check for it. |

## Installing and running

`npm install -g hushcron`, then put it in front of the command in your crontab:

    hushcron --retries 2 -- pg_dump --file /backups/nightly.sql mydb

### Exit behavior

Usage errors (no `--` separator, or nothing after it) exit `64`. Otherwise the wrapped
command's exit code passes through untouched, so downstream cron tooling still sees the real
status.
